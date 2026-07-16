from __future__ import annotations

from dotenv import load_dotenv

from app.config import load_settings
from app.repositories.mysql import MySqlDatabase, MySqlUpdateRepository
from app.services.updates import UpdateService
from app.web.app import create_application

load_dotenv()
settings = load_settings()
database = MySqlDatabase(settings)


def _health_check() -> bool:
    connection = database.connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() is not None
    finally:
        connection.close()


def _pending_business_handler(payload: dict) -> None:
    raise RuntimeError("Business update handler is not configured yet")


application = create_application(
    webhook_secret=settings.webhook_secret_token,
    update_service=UpdateService(
        MySqlUpdateRepository(database.connect),
        _pending_business_handler,
    ),
    health_check=_health_check,
)
