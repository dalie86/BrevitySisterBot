from __future__ import annotations

from datetime import datetime, timezone

from dotenv import load_dotenv

from app.config import load_settings
from app.integrations.telegram import TelegramClient, parse_update
from app.repositories.mysql import (
    MySqlDatabase,
    MySqlMemberRepository,
    MySqlMessageRepository,
    MySqlTrackedChatRepository,
    MySqlUpdateRepository,
)
from app.services.group_updates import GroupUpdateHandler
from app.services.updates import UpdateService
from app.web.app import create_application

load_dotenv()
settings = load_settings()
database = MySqlDatabase(settings)
telegram = TelegramClient(settings.telegram_bot_token)
chats = MySqlTrackedChatRepository(database.connect)
members = MySqlMemberRepository(database.connect)
messages = MySqlMessageRepository(database.connect)


def _health_check() -> bool:
    connection = database.connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() is not None
    finally:
        connection.close()


def _pending_summary_request(chat_id: int, user_id: int) -> None:
    del user_id
    telegram.send_message(
        chat_id,
        "Запрос принят. Модуль суммаризации подключается на следующем этапе.",
    )


group_handler = GroupUpdateHandler(
    chats=chats,
    members=members,
    messages=messages,
    telegram=telegram,
    summary_request=_pending_summary_request,
    now=lambda: datetime.now(timezone.utc),
)


def _business_handler(payload: dict) -> None:
    group_handler(parse_update(payload))


application = create_application(
    webhook_secret=settings.webhook_secret_token,
    update_service=UpdateService(
        MySqlUpdateRepository(database.connect),
        _business_handler,
    ),
    health_check=_health_check,
)
