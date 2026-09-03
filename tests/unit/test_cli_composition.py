import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.config import load_settings
from app.domain.models import BalanceSnapshot, FrozenUsageReport
from app.integrations.deepseek import DeepSeekClient
from app.jobs import cli


NOW = datetime(2026, 7, 16, 18, 15, tzinfo=timezone.utc)


def make_settings(owner_chat_id=None):
    values = {
        "TELEGRAM_BOT_TOKEN": "777:token",
        "TELEGRAM_BOT_USERNAME": "@BrevitySisterBot",
        "WEBHOOK_SECRET_TOKEN": "secret",
        "WEBHOOK_URL": "https://example.test/telegram/webhook",
        "DB_HOST": "localhost",
        "DB_NAME": "brevity",
        "DB_USER": "brevity",
        "DB_PASSWORD": "password",
        "DEEPSEEK_API_KEY": "deepseek",
        "DEEPSEEK_MODEL": "deepseek-v4-flash",
    }
    if owner_chat_id is not None:
        values["OWNER_CHAT_ID"] = str(owner_chat_id)
    return load_settings(values)


def test_cli_exposes_usage_commands() -> None:
    assert {"usage", "usage-report"} <= cli.COMMANDS


def test_usage_returns_local_and_balance_data_without_constructing_telegram(
    monkeypatch,
) -> None:
    copy = object()
    class FakeService:
        def inspect(self, now):
            assert now == NOW
            return {"balance_available": False, "week": {"llm_call_count": 3}}

    monkeypatch.setattr(cli, "MySqlUsageRepository", lambda connect: object())
    monkeypatch.setattr(
        cli,
        "UsageReportService",
        lambda repository, balance_client, loaded_copy: (
            FakeService() if loaded_copy is copy else None
        ),
    )
    monkeypatch.setattr(
        cli,
        "TelegramClient",
        lambda token: (_ for _ in ()).throw(AssertionError("Telegram not allowed")),
    )
    monkeypatch.setattr(cli.BotCopy, "load", lambda path: copy)

    result = cli._run_command(
        "usage", make_settings(), SimpleNamespace(connect=lambda: None), NOW
    )

    assert result == {
        "balance_available": False,
        "week": {"llm_call_count": 3},
    }


def test_usage_report_requires_owner_chat_id_before_telegram_send(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "TelegramClient",
        lambda token: (_ for _ in ()).throw(AssertionError("must fail before Telegram")),
    )

    with pytest.raises(RuntimeError, match="OWNER_CHAT_ID"):
        cli._run_command(
            "usage-report",
            make_settings(),
            SimpleNamespace(connect=lambda: None),
            NOW,
        )


def test_main_reports_missing_owner_without_traceback_or_database(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "load_settings", lambda: make_settings())
    monkeypatch.setattr(
        cli,
        "MySqlDatabase",
        lambda settings: (_ for _ in ()).throw(AssertionError("database not allowed")),
    )

    assert cli.main(["usage-report"]) == 2
    captured = capsys.readouterr()
    assert "OWNER_CHAT_ID" in captured.err
    assert "Traceback" not in captured.err


def test_usage_report_retries_use_stable_weekly_update_id_and_send_once(
    monkeypatch,
) -> None:
    week_start = datetime(2026, 8, 31, 0, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    balance = BalanceSnapshot(
        provider="deepseek",
        currency="CNY",
        total_balance=Decimal("49"),
        granted_balance=Decimal("0"),
        topped_up_balance=Decimal("49"),
        is_available=True,
        report_week_start=week_start,
        report_text="frozen weekly report",
        captured_at=NOW,
    )
    frozen = FrozenUsageReport(
        week_start=week_start,
        report_text="frozen weekly report",
        balance=balance,
    )

    class FakeService:
        def prepare_weekly(self, now):
            return frozen

    class FakeEffects:
        def __init__(self):
            self.records = {}
            self.update_ids = []

        def begin(self, effect_key, update_id, method, payload_hash, now):
            self.update_ids.append(update_id)
            if effect_key in self.records:
                return self.records[effect_key]
            self.records[effect_key] = ("sending", None)
            return None

        def mark_sent(self, effect_key, message_id, now):
            self.records[effect_key] = ("sent", message_id)

        def mark_review_required(self, effect_key, error, now):
            self.records[effect_key] = ("review_required", None)

    class FakeTelegram:
        def __init__(self):
            self.sent = []

        def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
            self.sent.append((chat_id, text))
            return 77

    effects = FakeEffects()
    telegram = FakeTelegram()
    monkeypatch.setattr(cli.BotCopy, "load", lambda path: object())
    monkeypatch.setattr(cli, "MySqlUsageRepository", lambda connect: object())
    monkeypatch.setattr(
        cli,
        "UsageReportService",
        lambda repository, balance_client, copy: FakeService(),
    )
    monkeypatch.setattr(cli, "MySqlTelegramEffectRepository", lambda connect: effects)
    monkeypatch.setattr(cli, "TelegramClient", lambda token: telegram)

    for _ in range(2):
        result = cli._run_command(
            "usage-report",
            make_settings(12345),
            SimpleNamespace(connect=lambda: None),
            NOW,
        )
        assert result == {
            "command": "usage-report",
            "status": "sent",
            "week_start": week_start.isoformat(),
        }

    assert effects.update_ids == [-20260831, -20260831]
    assert telegram.sent == [(12345, "frozen weekly report")]


@pytest.mark.parametrize("stored_status", ["sending", "review_required"])
def test_usage_report_marks_existing_unresolved_effect_unhealthy(
    monkeypatch, stored_status
) -> None:
    week_start = datetime(2026, 8, 31, 0, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    frozen = FrozenUsageReport(
        week_start=week_start,
        report_text="frozen weekly report",
        balance=None,
    )

    class FakeService:
        def prepare_weekly(self, now):
            return frozen

    class FakeEffects:
        def begin(self, effect_key, update_id, method, payload_hash, now):
            assert effect_key == "update:-20260831:send_message:0"
            return stored_status, None

        def mark_sent(self, effect_key, message_id, now):
            raise AssertionError("unresolved effect must not be marked sent")

        def mark_review_required(self, effect_key, error, now):
            raise AssertionError("existing effect must not be rewritten")

    class FakeTelegram:
        def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
            raise AssertionError("unresolved effect must not be resent")

    monkeypatch.setattr(cli.BotCopy, "load", lambda path: object())
    monkeypatch.setattr(cli, "MySqlUsageRepository", lambda connect: object())
    monkeypatch.setattr(
        cli,
        "UsageReportService",
        lambda repository, balance_client, copy: FakeService(),
    )
    monkeypatch.setattr(
        cli, "MySqlTelegramEffectRepository", lambda connect: FakeEffects()
    )
    monkeypatch.setattr(cli, "TelegramClient", lambda token: FakeTelegram())

    result = cli._run_command(
        "usage-report",
        make_settings(12345),
        SimpleNamespace(connect=lambda: None),
        NOW,
    )

    assert result == {
        "command": "usage-report",
        "healthy": False,
        "status": "unresolved",
        "week_start": week_start.isoformat(),
    }


def test_main_returns_nonzero_for_unresolved_usage_report(monkeypatch, capsys) -> None:
    settings = make_settings(12345)
    database = SimpleNamespace(connect=lambda: None)

    class FakeLocks:
        def acquire(self, key, now):
            return "owner-token"

        def release(self, key, owner_token):
            assert (key, owner_token) == (
                "brevity:usage-report",
                "owner-token",
            )

    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "MySqlDatabase", lambda loaded: database)
    monkeypatch.setattr(cli, "MySqlJobLockRepository", lambda connect: FakeLocks())
    monkeypatch.setattr(
        cli,
        "_run_command",
        lambda *args, **kwargs: {
            "command": "usage-report",
            "healthy": False,
            "status": "unresolved",
        },
    )

    assert cli.main(["usage-report"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "unresolved"


def test_recover_composes_one_catalog_and_dispatches_all_handlers_once(monkeypatch) -> None:
    copy = object()
    load_calls = []
    summary_kwargs = {}
    handler_kwargs = {}
    handler_calls = []

    monkeypatch.setattr(
        cli.BotCopy,
        "load",
        lambda path: load_calls.append(path) or copy,
    )
    monkeypatch.setattr(
        cli,
        "SummaryService",
        lambda **kwargs: summary_kwargs.update(kwargs) or object(),
    )

    def handler_factory(name):
        def build(**kwargs):
            handler_kwargs[name] = kwargs

            def handle(update):
                handler_calls.append((name, update))

            return handle

        return build

    monkeypatch.setattr(cli, "GroupUpdateHandler", handler_factory("group"))
    monkeypatch.setattr(cli, "OnDemandSummaryHandler", handler_factory("private"))
    monkeypatch.setattr(cli, "MembershipUpdateHandler", handler_factory("membership"))
    monkeypatch.setattr(cli, "MySqlUpdateRepository", lambda connect: object())

    class FakeUpdateService:
        def __init__(self, repository, handler) -> None:
            self.handler = handler

        def recover(self, now):
            self.handler({"update_id": 44})
            return 1

    monkeypatch.setattr(cli, "UpdateService", FakeUpdateService)
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "777:token",
            "TELEGRAM_BOT_USERNAME": "@BrevitySisterBot",
            "WEBHOOK_SECRET_TOKEN": "secret",
            "WEBHOOK_URL": "https://example.test/telegram/webhook",
            "DB_HOST": "localhost",
            "DB_NAME": "brevity",
            "DB_USER": "brevity",
            "DB_PASSWORD": "password",
            "DEEPSEEK_API_KEY": "deepseek",
            "DEEPSEEK_MODEL": "deepseek-v4-flash",
        }
    )

    result = cli._run_command(
        "recover",
        settings,
        SimpleNamespace(connect=lambda: None),
        NOW,
    )

    assert result == 1
    assert load_calls == [cli.PROJECT_ROOT / "content" / "bot_copy.ru.toml"]
    assert summary_kwargs["copy"] is copy
    assert isinstance(summary_kwargs["llm"], DeepSeekClient)
    assert handler_kwargs["group"]["copy"] is copy
    assert handler_kwargs["private"]["copy"] is copy
    assert handler_kwargs["group"]["telegram_bot_username"] == "BrevitySisterBot"
    assert handler_kwargs["group"]["access"].bot_user_id == 777
    assert handler_kwargs["private"]["bot_user_id"] == 777
    assert [name for name, _update in handler_calls] == [
        "group",
        "private",
        "membership",
    ]
    assert handler_calls[0][1] is handler_calls[1][1] is handler_calls[2][1]
