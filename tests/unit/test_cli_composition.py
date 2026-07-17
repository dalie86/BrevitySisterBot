from datetime import datetime, timezone
from types import SimpleNamespace

from app.config import load_settings
from app.jobs import cli


NOW = datetime(2026, 7, 16, 18, 15, tzinfo=timezone.utc)


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
            "GROQ_API_KEY": "groq",
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
