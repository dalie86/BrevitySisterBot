from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from storage import StoredMessage


@dataclass
class ArchivedSummary:
    chat_id: int
    summary_date: str
    summary_text: str
    prepared_at: datetime
    archived_at: datetime


class ArchiveStorage:
    def __init__(self, db_path: str = "archive.db") -> None:
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
                CREATE TABLE IF NOT EXISTS archived_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER,
                    username TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    archived_date TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_archived_messages_chat_message
                ON archived_messages (chat_id, telegram_message_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS archived_summaries (
                    chat_id INTEGER NOT NULL,
                    summary_date TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    prepared_at TEXT NOT NULL,
                    archived_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, summary_date)
                )
                """
            )

    def archive_messages(
        self,
        messages: Sequence[StoredMessage],
        *,
        archived_date: str,
        archived_at: datetime,
    ) -> int:
        if not messages:
            return 0

        with self._connect() as connection:
            before_changes = connection.total_changes
            connection.executemany(
                """
                INSERT INTO archived_messages (
                    telegram_message_id,
                    chat_id,
                    user_id,
                    username,
                    full_name,
                    text,
                    sent_at,
                    archived_date,
                    archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, telegram_message_id) DO NOTHING
                """,
                [
                    (
                        message.telegram_message_id,
                        message.chat_id,
                        message.user_id,
                        message.username,
                        message.full_name,
                        message.text,
                        message.sent_at.isoformat(timespec="seconds"),
                        archived_date,
                        archived_at.isoformat(timespec="seconds"),
                    )
                    for message in messages
                ],
            )
            inserted = connection.total_changes - before_changes
        return inserted

    def archive_summary(self, summary: ArchivedSummary) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO archived_summaries (
                    chat_id,
                    summary_date,
                    summary_text,
                    prepared_at,
                    archived_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, summary_date) DO UPDATE SET
                    summary_text = excluded.summary_text,
                    prepared_at = excluded.prepared_at,
                    archived_at = excluded.archived_at
                """,
                (
                    summary.chat_id,
                    summary.summary_date,
                    summary.summary_text,
                    summary.prepared_at.isoformat(timespec="seconds"),
                    summary.archived_at.isoformat(timespec="seconds"),
                ),
            )
