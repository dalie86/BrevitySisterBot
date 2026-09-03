from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from app.config import Settings, load_settings
from app.content import BotCopy
from app.integrations.deepseek import DeepSeekClient
from app.integrations.telegram import TelegramClient, parse_update
from app.repositories.mysql import (
    MySqlDatabase,
    MySqlChatShareRequestRepository,
    MySqlJobLockRepository,
    MySqlLifecycleStore,
    MySqlMaintenanceStore,
    MySqlMemberRepository,
    MySqlMessageRepository,
    MySqlSummaryRepository,
    MySqlSummarySelectionRepository,
    MySqlTelegramEffectRepository,
    MySqlTrackedChatRepository,
    MySqlUpdateRepository,
    MySqlUsageRepository,
)
from app.jobs.audit import OperationalAuditService
from app.services.access import AccessService
from app.services.group_updates import GroupUpdateHandler
from app.services.lifecycle import JobReport, LifecycleService
from app.services.membership_updates import MembershipUpdateHandler
from app.services.on_demand import OnDemandSummaryHandler, OnDemandSummaryService
from app.services.summaries import SummaryService
from app.services.telegram_effects import DurableTelegramGateway, telegram_update_scope
from app.services.usage_reports import UsageReportService
from app.services.updates import UpdateService

COMMANDS = {
    "recover",
    "prepare",
    "publish",
    "archive",
    "cleanup",
    "purge",
    "audit",
    "usage",
    "usage-report",
}
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    dry_run = arguments == ["cleanup", "--dry-run"]
    if not dry_run and (len(arguments) != 1 or arguments[0] not in COMMANDS):
        print(
            f"Usage: python -m app.jobs.cli {'|'.join(sorted(COMMANDS))} [--dry-run for cleanup]",
            file=sys.stderr,
        )
        return 2
    load_dotenv()
    try:
        settings = load_settings()
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    command = arguments[0]
    if command == "usage-report" and settings.owner_chat_id is None:
        print("OWNER_CHAT_ID is required for usage-report", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    database = MySqlDatabase(settings)
    locks = MySqlJobLockRepository(database.connect)
    owner_token = locks.acquire(f"brevity:{command}", now)
    if owner_token is None:
        print(json.dumps({"command": command, "status": "already_running"}))
        return 0
    try:
        result = _run_command(command, settings, database, now, dry_run=dry_run)
    finally:
        locks.release(f"brevity:{command}", owner_token)

    if isinstance(result, JobReport):
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
        return 1 if result.errors else 0
    if isinstance(result, dict):
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1 if result.get("healthy") is False else 0
    print(json.dumps({"processed_updates": result}))
    return 0


def _run_command(
    command: str,
    settings: Settings,
    database: MySqlDatabase,
    now: datetime,
    *,
    dry_run: bool = False,
) -> JobReport | int | dict:
    if command in {"usage", "usage-report"}:
        if command == "usage-report" and settings.owner_chat_id is None:
            raise RuntimeError("OWNER_CHAT_ID is required for usage-report")
        balance_client = DeepSeekClient(
            settings.deepseek_api_key, settings.deepseek_model
        )
        copy = BotCopy.load(PROJECT_ROOT / "content" / "bot_copy.ru.toml")
        usage = UsageReportService(
            MySqlUsageRepository(database.connect), balance_client, copy
        )
        if command == "usage":
            return usage.inspect(now)

        frozen = usage.prepare_weekly(now)
        raw_telegram = TelegramClient(settings.telegram_bot_token)
        telegram = DurableTelegramGateway(
            raw=raw_telegram,
            effects=MySqlTelegramEffectRepository(database.connect),
            now=lambda: datetime.now(timezone.utc),
        )
        weekly_update_id = -int(frozen.week_start.strftime("%Y%m%d"))
        with telegram_update_scope(weekly_update_id):
            telegram_message_id = telegram.send_message(
                settings.owner_chat_id, frozen.report_text
            )
        if telegram_message_id == 0:
            return {
                "command": "usage-report",
                "healthy": False,
                "status": "unresolved",
                "week_start": frozen.week_start.isoformat(),
            }
        return {
            "command": "usage-report",
            "status": "sent",
            "week_start": frozen.week_start.isoformat(),
        }

    copy = BotCopy.load(PROJECT_ROOT / "content" / "bot_copy.ru.toml")
    bot_user_id = int(settings.telegram_bot_token.split(":", 1)[0])
    raw_telegram = TelegramClient(settings.telegram_bot_token)
    messages = MySqlMessageRepository(database.connect)
    members = MySqlMemberRepository(database.connect)
    chats = MySqlTrackedChatRepository(database.connect)
    summary_repository = MySqlSummaryRepository(database.connect)
    selections = MySqlSummarySelectionRepository(database.connect)
    share_requests = MySqlChatShareRequestRepository(database.connect)
    summaries = SummaryService(
        messages=messages,
        summaries=summary_repository,
        llm=DeepSeekClient(settings.deepseek_api_key, settings.deepseek_model),
        copy=copy,
        timezone_name=settings.timezone,
        automatic_min_messages=settings.automatic_min_messages,
        on_demand_min_messages=settings.on_demand_min_messages,
    )
    if command in {"purge", "audit"}:
        maintenance = OperationalAuditService(
            MySqlMaintenanceStore(database.connect),
            settings.archive_retention_days,
        )
        return maintenance.purge(now) if command == "purge" else maintenance.audit(now)
    if command == "recover":
        telegram = DurableTelegramGateway(
            raw=raw_telegram,
            effects=MySqlTelegramEffectRepository(database.connect),
            now=lambda: datetime.now(timezone.utc),
        )
        on_demand_requests = OnDemandSummaryService(
            summaries=summaries,
            telegram=telegram,
            copy=copy,
            now=lambda: datetime.now(timezone.utc),
        )
        access = AccessService(chats, telegram, bot_user_id)
        group_handler = GroupUpdateHandler(
            chats=chats,
            members=members,
            messages=messages,
            telegram=telegram,
            access=access,
            copy=copy,
            telegram_bot_username=settings.telegram_bot_username,
            summary_request=lambda chat_id, user_id: on_demand_requests.request(
                chat_id=chat_id,
                requester_user_id=user_id,
                destination_chat_id=chat_id,
            ),
            now=lambda: datetime.now(timezone.utc),
        )
        private_handler = OnDemandSummaryHandler(
            chats=chats,
            members=members,
            share_requests=share_requests,
            selections=selections,
            telegram=telegram,
            summaries=summaries,
            copy=copy,
            bot_user_id=bot_user_id,
            now=lambda: datetime.now(timezone.utc),
        )
        membership_handler = MembershipUpdateHandler(
            members=members,
            now=lambda: datetime.now(timezone.utc),
        )

        def handle(payload: dict) -> None:
            with telegram_update_scope(int(payload["update_id"])):
                update = parse_update(payload)
                group_handler(update)
                private_handler(update)
                membership_handler(update)

        return UpdateService(
            MySqlUpdateRepository(database.connect),
            handle,
        ).recover(now)

    lifecycle = LifecycleService(
        store=MySqlLifecycleStore(database.connect, settings.timezone),
        summaries=summaries,
        telegram=raw_telegram,
        timezone_name=settings.timezone,
        retention_days=settings.archive_retention_days,
    )
    if command == "cleanup":
        return lifecycle.cleanup_day(now, dry_run=dry_run)
    return {
        "prepare": lifecycle.prepare_day,
        "publish": lifecycle.publish_day,
        "archive": lifecycle.archive_day,
    }[command](now)


if __name__ == "__main__":
    raise SystemExit(main())
