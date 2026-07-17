import importlib
import sys

from app.content import BotCopy
from app.repositories.mysql import MySqlChatShareRequestRepository
from app.services.access import AccessService
from app.services.membership_updates import MembershipUpdateHandler


def _import_production(monkeypatch):
    environment = {
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
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("app.web.production", None)
    return importlib.import_module("app.web.production")


def test_production_composition_loads_one_catalog_and_all_handlers(monkeypatch) -> None:
    production = _import_production(monkeypatch)

    assert isinstance(production.copy, BotCopy)
    assert production.summaries.copy is production.copy
    assert production.group_handler.copy is production.copy
    assert production.private_summary_handler.copy is production.copy
    assert isinstance(production.share_requests, MySqlChatShareRequestRepository)
    assert isinstance(production.access, AccessService)
    assert production.access.bot_user_id == 777
    assert production.private_summary_handler.bot_user_id == 777
    assert isinstance(production.membership_handler, MembershipUpdateHandler)


def test_production_dispatches_each_handler_once(monkeypatch) -> None:
    production = _import_production(monkeypatch)
    calls = []
    monkeypatch.setattr(production, "group_handler", lambda update: calls.append(("group", update)))
    monkeypatch.setattr(
        production,
        "private_summary_handler",
        lambda update: calls.append(("private", update)),
    )
    monkeypatch.setattr(
        production,
        "membership_handler",
        lambda update: calls.append(("membership", update)),
    )

    production._business_handler({"update_id": 44})

    assert [name for name, _update in calls] == ["group", "private", "membership"]
    assert calls[0][1] is calls[1][1] is calls[2][1]
