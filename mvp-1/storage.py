from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class StoredMessage:
    telegram_message_id: int
    chat_id: int
    user_id: Optional[int]
    username: str
    full_name: str
    text: str
    sent_at: datetime


class Storage:
    def __init__(self, db_path: str = "bot.db") -> None:
        self.db_path = Path(db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_message_id INTEGER NOT NULL DEFAULT 0,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER,
                    username TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sent_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(connection, "messages", "telegram_message_id", "INTEGER NOT NULL DEFAULT 0")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_chat_message
                ON messages (chat_id, telegram_message_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tracked_chats (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS prepared_summaries (
                    chat_id INTEGER NOT NULL,
                    summary_date TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    prepared_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, summary_date)
                )
                """
            )

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
            )

    def enable_chat(self, chat_id: int, title: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tracked_chats (chat_id, title, enabled, created_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = excluded.title,
                    enabled = 1
                """,
                (chat_id, title, datetime.utcnow().isoformat(timespec="seconds")),
            )

    def disable_chat(self, chat_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE tracked_chats SET enabled = 0 WHERE chat_id = ?",
                (chat_id,),
            )

    def get_enabled_chat_ids(self) -> list[int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT chat_id FROM tracked_chats WHERE enabled = 1"
            ).fetchall()
        return [row["chat_id"] for row in rows]

    def is_chat_enabled(self, chat_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT enabled FROM tracked_chats WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return bool(row and row["enabled"])

    def is_chat_tracked(self, chat_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM tracked_chats WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return bool(row)

    def add_message(self, message: StoredMessage) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (
                    telegram_message_id,
                    chat_id,
                    user_id,
                    username,
                    full_name,
                    text,
                    sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, telegram_message_id) DO NOTHING
                """,
                (
                    message.telegram_message_id,
                    message.chat_id,
                    message.user_id,
                    message.username,
                    message.full_name,
                    message.text,
                    message.sent_at.isoformat(timespec="seconds"),
                ),
            )

    def get_messages_between(
        self,
        chat_id: int,
        start: datetime,
        end: datetime,
    ) -> list[StoredMessage]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT telegram_message_id, chat_id, user_id, username, full_name, text, sent_at
                FROM messages
                WHERE chat_id = ?
                  AND sent_at >= ?
                  AND sent_at < ?
                ORDER BY sent_at ASC
                """,
                (
                    chat_id,
                    start.isoformat(timespec="seconds"),
                    end.isoformat(timespec="seconds"),
                ),
            ).fetchall()
        return [
            StoredMessage(
                telegram_message_id=row["telegram_message_id"],
                chat_id=row["chat_id"],
                user_id=row["user_id"],
                username=row["username"],
                full_name=row["full_name"],
                text=row["text"],
                sent_at=datetime.fromisoformat(row["sent_at"]),
            )
            for row in rows
        ]

    def delete_messages_before(self, cutoff: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM messages WHERE sent_at < ?",
                (cutoff.isoformat(timespec="seconds"),),
            )

    def delete_messages_between(self, start: datetime, end: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM messages
                WHERE sent_at >= ? AND sent_at < ?
                """,
                (
                    start.isoformat(timespec="seconds"),
                    end.isoformat(timespec="seconds"),
                ),
            )

    def save_prepared_summary(
        self,
        *,
        chat_id: int,
        summary_date: str,
        summary_text: str,
        prepared_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO prepared_summaries (
                    chat_id,
                    summary_date,
                    summary_text,
                    prepared_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, summary_date) DO UPDATE SET
                    summary_text = excluded.summary_text,
                    prepared_at = excluded.prepared_at
                """,
                (
                    chat_id,
                    summary_date,
                    summary_text,
                    prepared_at.isoformat(timespec="seconds"),
                ),
            )

    def get_prepared_summary(self, *, chat_id: int, summary_date: str) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT summary_text
                FROM prepared_summaries
                WHERE chat_id = ? AND summary_date = ?
                """,
                (chat_id, summary_date),
            ).fetchone()
        if not row:
            return None
        return str(row["summary_text"])

    def get_prepared_summary_record(
        self,
        *,
        chat_id: int,
        summary_date: str,
    ) -> Optional[sqlite3.Row]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT summary_text, prepared_at
                FROM prepared_summaries
                WHERE chat_id = ? AND summary_date = ?
                """,
                (chat_id, summary_date),
            ).fetchone()
        return row

    def delete_prepared_summary(self, *, chat_id: int, summary_date: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM prepared_summaries
                WHERE chat_id = ? AND summary_date = ?
                """,
                (chat_id, summary_date),
            )

    def delete_prepared_summaries_before(self, summary_date: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM prepared_summaries WHERE summary_date < ?",
                (summary_date,),
            )

    def get_state(self, key: str) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM bot_state WHERE key = ?",
                (key,),
            ).fetchone()
        if not row:
            return None
        return str(row["value"])

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO bot_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value
                """,
                (key, value),
            )

    def get_enabled_chats(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT chat_id, title FROM tracked_chats WHERE enabled = 1 ORDER BY chat_id"
            ).fetchall()
        return rows
