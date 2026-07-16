from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    webhook_secret_token: str
    db_host: str
    db_name: str
    db_user: str
    db_password: str
    groq_api_key: str
    groq_model: str = "qwen/qwen3.6-27b"
    timezone: str = "Europe/Moscow"
    owner_chat_id: int | None = None
    automatic_min_messages: int = 15
    on_demand_min_messages: int = 5
    archive_retention_days: int = 30


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    values = dict(os.environ if environ is None else environ)
    required = (
        "TELEGRAM_BOT_TOKEN",
        "WEBHOOK_SECRET_TOKEN",
        "DB_HOST",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "GROQ_API_KEY",
    )
    missing = [name for name in required if not values.get(name, "").strip()]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")

    return Settings(
        telegram_bot_token=values["TELEGRAM_BOT_TOKEN"].strip(),
        webhook_secret_token=values["WEBHOOK_SECRET_TOKEN"].strip(),
        db_host=values["DB_HOST"].strip(),
        db_name=values["DB_NAME"].strip(),
        db_user=values["DB_USER"].strip(),
        db_password=values["DB_PASSWORD"],
        groq_api_key=values["GROQ_API_KEY"].strip(),
        groq_model=values.get("GROQ_MODEL", "qwen/qwen3.6-27b").strip(),
        timezone=values.get("BOT_TIMEZONE", "Europe/Moscow").strip(),
        owner_chat_id=int(values["OWNER_CHAT_ID"]) if values.get("OWNER_CHAT_ID") else None,
        automatic_min_messages=int(values.get("AUTOMATIC_MIN_MESSAGES", "15")),
        on_demand_min_messages=int(values.get("ON_DEMAND_MIN_MESSAGES", "5")),
        archive_retention_days=int(values.get("ARCHIVE_RETENTION_DAYS", "30")),
    )
