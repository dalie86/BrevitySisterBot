from __future__ import annotations

import argparse
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from app.config import load_settings
from app.repositories.mysql import MySqlDatabase


@dataclass(frozen=True)
class MvpSnapshot:
    chats: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    summaries: list[dict[str, Any]]
    archived_messages: list[dict[str, Any]]
    archived_summaries: list[dict[str, Any]]


def read_snapshot(bot_db: Path, archive_db: Path | None = None) -> MvpSnapshot:
    with _read_only_sqlite(bot_db) as connection:
        chats = _rows(connection, "SELECT * FROM tracked_chats")
        messages = _rows(connection, "SELECT * FROM messages")
        summaries = _rows(connection, "SELECT * FROM prepared_summaries")
    archived_messages: list[dict[str, Any]] = []
    archived_summaries: list[dict[str, Any]] = []
    if archive_db is not None and archive_db.exists():
        with _read_only_sqlite(archive_db) as connection:
            archived_messages = _rows(connection, "SELECT * FROM archived_messages")
            archived_summaries = _rows(connection, "SELECT * FROM archived_summaries")
    return MvpSnapshot(
        chats=chats,
        messages=messages,
        summaries=summaries,
        archived_messages=archived_messages,
        archived_summaries=archived_summaries,
    )


def import_snapshot(
    snapshot: MvpSnapshot,
    connection_factory,
    *,
    timezone_name: str,
    retention_days: int,
) -> dict[str, int]:
    local_timezone = ZoneInfo(timezone_name)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = now + timedelta(days=retention_days)
    counts = {
        "chats": 0,
        "messages": 0,
        "summaries": 0,
        "archived_messages": 0,
        "archived_summaries": 0,
    }
    connection = connection_factory()
    try:
        cursor = connection.cursor()
        for row in snapshot.chats:
            created_at = _legacy_datetime(row["created_at"], local_timezone)
            cursor.execute(
                """
                INSERT IGNORE INTO tracked_chats (
                    chat_id, title, enabled, timezone, scheduled_publish_time,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, '21:00:00', %s, %s)
                """,
                (
                    row["chat_id"],
                    row["title"],
                    row["enabled"],
                    timezone_name,
                    created_at,
                    now,
                ),
            )
            counts["chats"] += int(cursor.rowcount)
        for row in snapshot.messages:
            cursor.execute(
                """
                INSERT IGNORE INTO messages (
                    chat_id, telegram_message_id, user_id, username,
                    full_name, text, sent_at, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row["chat_id"],
                    row["telegram_message_id"],
                    row["user_id"],
                    row["username"],
                    row["full_name"],
                    row["text"],
                    _legacy_datetime(row["sent_at"], local_timezone),
                    now,
                ),
            )
            counts["messages"] += int(cursor.rowcount)
        for row in snapshot.summaries:
            source_hash = _legacy_summary_hash(row)
            prepared_at = _legacy_datetime(row["prepared_at"], local_timezone)
            cursor.execute(
                """
                INSERT IGNORE INTO prepared_summaries (
                    chat_id, summary_date, summary_kind, source_hash,
                    summary_text, status, provider, model, fallback_type,
                    prepared_at, created_at, updated_at
                ) VALUES (%s, %s, 'scheduled', %s, %s, 'prepared',
                          'legacy', 'mvp-0.1.0', 'legacy_import', %s, %s, %s)
                """,
                (
                    row["chat_id"],
                    row["summary_date"],
                    source_hash,
                    row["summary_text"],
                    prepared_at,
                    prepared_at,
                    now,
                ),
            )
            counts["summaries"] += int(cursor.rowcount)
        for row in snapshot.archived_messages:
            cursor.execute(
                """
                INSERT IGNORE INTO archived_messages (
                    chat_id, telegram_message_id, user_id, username, full_name,
                    text, sent_at, archived_date, archived_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row["chat_id"],
                    row["telegram_message_id"],
                    row["user_id"],
                    row["username"],
                    row["full_name"],
                    row["text"],
                    _legacy_datetime(row["sent_at"], local_timezone),
                    row["archived_date"],
                    _legacy_datetime(row["archived_at"], local_timezone),
                    expires_at,
                ),
            )
            counts["archived_messages"] += int(cursor.rowcount)
        for row in snapshot.archived_summaries:
            source_summary_id = int(_legacy_summary_hash(row)[:15], 16)
            cursor.execute(
                """
                INSERT IGNORE INTO archived_summaries (
                    source_summary_id, chat_id, summary_date, summary_kind,
                    summary_text, provider, model, fallback_type,
                    prepared_at, archived_at, expires_at
                ) VALUES (%s, %s, %s, 'scheduled', %s, 'legacy',
                          'mvp-0.1.0', 'legacy_import', %s, %s, %s)
                """,
                (
                    source_summary_id,
                    row["chat_id"],
                    row["summary_date"],
                    row["summary_text"],
                    _legacy_datetime(row["prepared_at"], local_timezone),
                    _legacy_datetime(row["archived_at"], local_timezone),
                    expires_at,
                ),
            )
            counts["archived_summaries"] += int(cursor.rowcount)
        connection.commit()
        return counts
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Import read-only Brevity MVP SQLite data")
    parser.add_argument("--bot-db", type=Path, required=True)
    parser.add_argument("--archive-db", type=Path)
    arguments = parser.parse_args()
    load_dotenv()
    settings = load_settings()
    snapshot = read_snapshot(arguments.bot_db, arguments.archive_db)
    counts = import_snapshot(
        snapshot,
        MySqlDatabase(settings).connect,
        timezone_name=settings.timezone,
        retention_days=settings.archive_retention_days,
    )
    print(counts)
    return 0


def _read_only_sqlite(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _rows(connection: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in connection.execute(sql).fetchall()]
    except sqlite3.OperationalError:
        return []


def _legacy_datetime(value: str, local_timezone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_timezone)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _legacy_summary_hash(row: dict[str, Any]) -> str:
    payload = f"legacy\0{row['chat_id']}\0{row['summary_date']}\0{row['summary_text']}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
