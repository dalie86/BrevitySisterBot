from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from app.config import load_settings
from app.integrations.groq import GroqClient
from app.integrations.telegram import TelegramClient, parse_update
from app.repositories.mysql import (
    MySqlDatabase,
    MySqlMemberRepository,
    MySqlMessageRepository,
    MySqlSummaryRepository,
    MySqlSummarySelectionRepository,
    MySqlTrackedChatRepository,
    MySqlUpdateRepository,
)
from app.services.group_updates import GroupUpdateHandler
from app.services.on_demand import OnDemandSummaryHandler, OnDemandSummaryService
from app.services.summaries import SummaryService
from app.services.updates import UpdateService
from app.web.app import create_application

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
settings = load_settings()
database = MySqlDatabase(settings)
telegram = TelegramClient(settings.telegram_bot_token)
chats = MySqlTrackedChatRepository(database.connect)
members = MySqlMemberRepository(database.connect)
messages = MySqlMessageRepository(database.connect)
summary_repository = MySqlSummaryRepository(database.connect)
summary_selections = MySqlSummarySelectionRepository(database.connect)
summaries = SummaryService(
    messages=messages,
    summaries=summary_repository,
    llm=GroqClient(settings.groq_api_key, settings.groq_model),
    timezone_name=settings.timezone,
    automatic_min_messages=settings.automatic_min_messages,
    on_demand_min_messages=settings.on_demand_min_messages,
)
on_demand_requests = OnDemandSummaryService(
    summaries=summaries,
    telegram=telegram,
    now=lambda: datetime.now(timezone.utc),
)


def _health_check() -> bool:
    connection = database.connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() is not None
    finally:
        connection.close()


def _group_summary_request(chat_id: int, user_id: int) -> None:
    on_demand_requests.request(
        chat_id=chat_id,
        requester_user_id=user_id,
        destination_chat_id=chat_id,
    )


group_handler = GroupUpdateHandler(
    chats=chats,
    members=members,
    messages=messages,
    telegram=telegram,
    summary_request=_group_summary_request,
    now=lambda: datetime.now(timezone.utc),
)
private_summary_handler = OnDemandSummaryHandler(
    members=members,
    selections=summary_selections,
    telegram=telegram,
    summaries=summaries,
    now=lambda: datetime.now(timezone.utc),
)


def _business_handler(payload: dict) -> None:
    update = parse_update(payload)
    group_handler(update)
    private_summary_handler(update)


application = create_application(
    webhook_secret=settings.webhook_secret_token,
    update_service=UpdateService(
        MySqlUpdateRepository(database.connect),
        _business_handler,
    ),
    health_check=_health_check,
)
