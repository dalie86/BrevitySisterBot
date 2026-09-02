from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_bot_username: str
    webhook_secret_token: str
    webhook_url: str
    db_host: str
    db_name: str
    db_user: str
    db_password: str
    deepseek_api_key: str
    deepseek_model: str = "deepseek-v4-flash"
    timezone: str = "Europe/Moscow"
    owner_chat_id: int | None = None
    automatic_min_messages: int = 15
    on_demand_min_messages: int = 5
    archive_retention_days: int = 30


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    values = dict(os.environ if environ is None else environ)
    required = (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_USERNAME",
        "WEBHOOK_SECRET_TOKEN",
        "WEBHOOK_URL",
        "DB_HOST",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "DEEPSEEK_API_KEY",
    )
    missing = [name for name in required if not values.get(name, "").strip()]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")

    telegram_bot_username = values["TELEGRAM_BOT_USERNAME"].strip().removeprefix("@")
    if not telegram_bot_username:
        raise RuntimeError("Missing required settings: TELEGRAM_BOT_USERNAME")

    return Settings(
        telegram_bot_token=values["TELEGRAM_BOT_TOKEN"].strip(),
        telegram_bot_username=telegram_bot_username,
        webhook_secret_token=values["WEBHOOK_SECRET_TOKEN"].strip(),
        webhook_url=values["WEBHOOK_URL"].strip(),
        db_host=values["DB_HOST"].strip(),
        db_name=values["DB_NAME"].strip(),
        db_user=values["DB_USER"].strip(),
        db_password=values["DB_PASSWORD"],
        deepseek_api_key=values["DEEPSEEK_API_KEY"].strip(),
        deepseek_model=values.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip(),
        timezone=values.get("BOT_TIMEZONE", "Europe/Moscow").strip(),
        owner_chat_id=int(values["OWNER_CHAT_ID"]) if values.get("OWNER_CHAT_ID") else None,
        automatic_min_messages=int(values.get("AUTOMATIC_MIN_MESSAGES", "15")),
        on_demand_min_messages=int(values.get("ON_DEMAND_MIN_MESSAGES", "5")),
        archive_retention_days=int(values.get("ARCHIVE_RETENTION_DAYS", "30")),
    )
