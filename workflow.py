from __future__ import annotations

import os
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional in bare test environments
    def load_dotenv(*_args, **_kwargs):
        return False

from storage import Storage
from summarizer import build_summary

load_dotenv()

BOT_TIMEZONE = ZoneInfo(os.getenv("BOT_TIMEZONE", "Europe/Moscow"))
COLLECT_INTERVAL_MINUTES = int(os.getenv("COLLECT_INTERVAL_MINUTES", "60"))
COLLECT_START_HOUR = int(os.getenv("COLLECT_START_HOUR", "10"))
COLLECT_START_MINUTE = int(os.getenv("COLLECT_START_MINUTE", "0"))
COLLECT_END_HOUR = int(os.getenv("COLLECT_END_HOUR", "20"))
COLLECT_END_MINUTE = int(os.getenv("COLLECT_END_MINUTE", "59"))
FINAL_COLLECT_HOUR = int(os.getenv("FINAL_COLLECT_HOUR", "20"))
FINAL_COLLECT_MINUTE = int(os.getenv("FINAL_COLLECT_MINUTE", "40"))
PREPARE_HOUR = int(os.getenv("PREPARE_HOUR", "20"))
PREPARE_MINUTE = int(os.getenv("PREPARE_MINUTE", "45"))
SEND_HOUR = int(os.getenv("SUMMARY_HOUR", "21"))
SEND_MINUTE = int(os.getenv("SUMMARY_MINUTE", "0"))
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
MAX_SUMMARY_LENGTH = 2000
STATE_LAST_UPDATE_ID = "last_update_id"


def now_msk() -> datetime:
    return datetime.now(BOT_TIMEZONE)


def day_start(now: Optional[datetime] = None) -> datetime:
    current = now or now_msk()
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def final_collect_cutoff(now: Optional[datetime] = None) -> datetime:
    start = day_start(now)
    return datetime.combine(
        start.date(),
        time(hour=FINAL_COLLECT_HOUR, minute=FINAL_COLLECT_MINUTE),
        tzinfo=BOT_TIMEZONE,
    )




def collect_window_start(now: Optional[datetime] = None) -> datetime:
    start = day_start(now)
    return datetime.combine(
        start.date(),
        time(hour=COLLECT_START_HOUR, minute=COLLECT_START_MINUTE),
        tzinfo=BOT_TIMEZONE,
    )


def collect_window_end(now: Optional[datetime] = None) -> datetime:
    start = day_start(now)
    return datetime.combine(
        start.date(),
        time(hour=COLLECT_END_HOUR, minute=COLLECT_END_MINUTE),
        tzinfo=BOT_TIMEZONE,
    )


def should_run_regular_collect(now: Optional[datetime] = None) -> bool:
    current = now or now_msk()
    return collect_window_start(current) <= current <= collect_window_end(current)


def format_collect_window() -> str:
    return (
        f"{COLLECT_START_HOUR:02d}:{COLLECT_START_MINUTE:02d}-"
        f"{COLLECT_END_HOUR:02d}:{COLLECT_END_MINUTE:02d}"
    )


def send_time(now: Optional[datetime] = None) -> datetime:
    start = day_start(now)
    return datetime.combine(
        start.date(),
        time(hour=SEND_HOUR, minute=SEND_MINUTE),
        tzinfo=BOT_TIMEZONE,
    )


def summary_window(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    current = now or now_msk()
    start = day_start(current)
    return start, min(current, final_collect_cutoff(current))


def summary_date(now: Optional[datetime] = None) -> str:
    current = now or now_msk()
    return current.date().isoformat()


def truncate_summary(summary: str) -> str:
    if len(summary) <= MAX_SUMMARY_LENGTH:
        return summary
    return summary[:MAX_SUMMARY_LENGTH].rstrip()


def build_summary_for_chat(
    storage: Storage,
    *,
    chat_id: int,
    chat_title: str,
    now: Optional[datetime] = None,
) -> str:
    current = now or now_msk()
    start, end = summary_window(current)
    messages = storage.get_messages_between(chat_id=chat_id, start=start, end=end)
    return truncate_summary(
        build_summary(messages, chat_title=chat_title, start=start, end=end)
    )


def cleanup_old_data(storage: Storage, now: Optional[datetime] = None) -> None:
    current = now or now_msk()
    start = day_start(current)
    storage.delete_messages_before(start - timedelta(days=2))
    storage.delete_prepared_summaries_before(
        (start - timedelta(days=7)).date().isoformat()
    )
