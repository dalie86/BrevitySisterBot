from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone

from dotenv import load_dotenv

from app.config import Settings, load_settings
from app.integrations.groq import GroqClient
from app.integrations.telegram import TelegramClient, parse_update
from app.repositories.mysql import (
    MySqlDatabase,
    MySqlJobLockRepository,
    MySqlLifecycleStore,
    MySqlMaintenanceStore,
    MySqlMemberRepository,
    MySqlMessageRepository,
    MySqlSummaryRepository,
    MySqlSummarySelectionRepository,
    MySqlTrackedChatRepository,
    MySqlUpdateRepository,
)
from app.jobs.audit import OperationalAuditService
from app.services.group_updates import GroupUpdateHandler
from app.services.lifecycle import JobReport, LifecycleService
from app.services.on_demand import OnDemandSummaryHandler, OnDemandSummaryService
from app.services.summaries import SummaryService
from app.services.updates import UpdateService

COMMANDS = {"recover", "prepare", "publish", "archive", "cleanup", "purge", "audit"}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1 or arguments[0] not in COMMANDS:
        print(f"Usage: python -m app.jobs.cli {'|'.join(sorted(COMMANDS))}", file=sys.stderr)
        return 2
    load_dotenv()
    try:
        settings = load_settings()
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    command = arguments[0]
    now = datetime.now(timezone.utc)
    database = MySqlDatabase(settings)
    locks = MySqlJobLockRepository(database.connect)
    owner_token = locks.acquire(f"brevity:{command}", now)
    if owner_token is None:
        print(json.dumps({"command": command, "status": "already_running"}))
        return 0
    try:
        result = _run_command(command, settings, database, now)
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
) -> JobReport | int | dict[str, int | bool]:
    telegram = TelegramClient(settings.telegram_bot_token)
    messages = MySqlMessageRepository(database.connect)
    members = MySqlMemberRepository(database.connect)
    chats = MySqlTrackedChatRepository(database.connect)
    summary_repository = MySqlSummaryRepository(database.connect)
    selections = MySqlSummarySelectionRepository(database.connect)
    summaries = SummaryService(
        messages=messages,
        summaries=summary_repository,
        llm=GroqClient(settings.groq_api_key, settings.groq_model),
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
        on_demand_requests = OnDemandSummaryService(
            summaries=summaries,
            telegram=telegram,
            now=lambda: datetime.now(timezone.utc),
        )
        group_handler = GroupUpdateHandler(
            chats=chats,
            members=members,
            messages=messages,
            telegram=telegram,
            summary_request=lambda chat_id, user_id: on_demand_requests.request(
                chat_id=chat_id,
                requester_user_id=user_id,
                destination_chat_id=chat_id,
            ),
            now=lambda: datetime.now(timezone.utc),
        )
        private_handler = OnDemandSummaryHandler(
            members=members,
            selections=selections,
            telegram=telegram,
            summaries=summaries,
            now=lambda: datetime.now(timezone.utc),
        )

        def handle(payload: dict) -> None:
            update = parse_update(payload)
            group_handler(update)
            private_handler(update)

        return UpdateService(
            MySqlUpdateRepository(database.connect),
            handle,
        ).recover(now)

    lifecycle = LifecycleService(
        store=MySqlLifecycleStore(database.connect, settings.timezone),
        summaries=summaries,
        telegram=telegram,
        timezone_name=settings.timezone,
        retention_days=settings.archive_retention_days,
    )
    return {
        "prepare": lifecycle.prepare_day,
        "publish": lifecycle.publish_day,
        "archive": lifecycle.archive_day,
        "cleanup": lifecycle.cleanup_day,
    }[command](now)


if __name__ == "__main__":
    raise SystemExit(main())
