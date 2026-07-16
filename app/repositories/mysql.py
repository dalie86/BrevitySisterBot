from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymysql
from pymysql.connections import Connection

from app.config import Settings
from app.domain.models import QueuedUpdate, StoredMessage, UpdateStatus

ConnectionFactory = Callable[[], Any]


def migration_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir() if path.suffix == ".sql")


class MySqlDatabase:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def connect(self) -> Connection:
        return pymysql.connect(
            host=self.settings.db_host,
            user=self.settings.db_user,
            password=self.settings.db_password,
            database=self.settings.db_name,
            charset="utf8mb4",
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def execute_script(self, sql: str) -> None:
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                for statement in _split_sql_statements(sql):
                    cursor.execute(statement)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def applied_migrations(self) -> dict[str, str]:
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        filename VARCHAR(255) PRIMARY KEY,
                        checksum CHAR(64) NOT NULL,
                        applied_at DATETIME(6) NOT NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                cursor.execute("SELECT filename, checksum FROM schema_migrations")
                rows = cursor.fetchall()
            connection.commit()
            return {str(row["filename"]): str(row["checksum"]) for row in rows}
        finally:
            connection.close()

    def record_migration(self, filename: str, checksum: str) -> None:
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO schema_migrations (filename, checksum, applied_at)
                    VALUES (%s, %s, %s)
                    """,
                    (filename, checksum, datetime.now(timezone.utc).replace(tzinfo=None)),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def apply_migrations(database: MySqlDatabase, directory: Path) -> list[str]:
    applied_by_name = database.applied_migrations()
    applied: list[str] = []
    for path in migration_files(directory):
        content = path.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        existing_checksum = applied_by_name.get(path.name)
        if existing_checksum == checksum:
            continue
        if existing_checksum is not None:
            raise RuntimeError(f"Applied migration changed: {path.name}")
        database.execute_script(content.decode("utf-8"))
        database.record_migration(path.name, checksum)
        applied.append(path.name)
    return applied


class MySqlUpdateRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def enqueue(self, update_id: int, payload: dict) -> bool:
        connection = self.connection_factory()
        cursor = connection.cursor()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cursor.execute(
            """
            INSERT IGNORE INTO telegram_updates (
                update_id, payload_json, status, attempt_count,
                available_at, created_at, updated_at
            ) VALUES (%s, %s, 'pending', 0, %s, %s, %s)
            """,
            (
                update_id,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
                now,
            ),
        )
        connection.commit()
        return cursor.rowcount == 1

    def claim(
        self,
        update_id: int,
        now: datetime,
        lease_seconds: int,
    ) -> QueuedUpdate | None:
        connection = self.connection_factory()
        cursor = connection.cursor()
        db_now = _to_mysql_datetime(now)
        cursor.execute(
            """
            UPDATE telegram_updates
            SET status = 'processing',
                attempt_count = attempt_count + 1,
                claimed_at = %s,
                updated_at = %s
            WHERE update_id = %s
              AND (
                status IN ('pending', 'retry')
                OR (status = 'processing' AND claimed_at < DATE_SUB(%s, INTERVAL %s SECOND))
              )
              AND available_at <= %s
            """,
            (db_now, db_now, update_id, db_now, lease_seconds, db_now),
        )
        if cursor.rowcount != 1:
            connection.commit()
            return None
        cursor.execute(
            """
            SELECT update_id, payload_json, status, attempt_count, available_at, claimed_at
            FROM telegram_updates
            WHERE update_id = %s
            """,
            (update_id,),
        )
        row = cursor.fetchone()
        connection.commit()
        return _queued_update_from_row(row) if row else None

    def claim_available(self, now: datetime, limit: int) -> list[QueuedUpdate]:
        connection = self.connection_factory()
        cursor = connection.cursor()
        db_now = _to_mysql_datetime(now)
        cursor.execute(
            """
            SELECT update_id
            FROM telegram_updates
            WHERE status IN ('pending', 'retry')
              AND available_at <= %s
            ORDER BY available_at, update_id
            LIMIT %s
            """,
            (db_now, limit),
        )
        update_ids = [int(row["update_id"]) for row in cursor.fetchall()]
        connection.commit()
        claimed: list[QueuedUpdate] = []
        for update_id in update_ids:
            item = self.claim(update_id, now, lease_seconds=300)
            if item is not None:
                claimed.append(item)
        return claimed

    def complete(self, update_id: int, now: datetime) -> None:
        connection = self.connection_factory()
        cursor = connection.cursor()
        db_now = _to_mysql_datetime(now)
        cursor.execute(
            """
            UPDATE telegram_updates
            SET status = 'completed',
                completed_at = %s,
                claimed_at = NULL,
                last_error_code = NULL,
                last_error_message = NULL,
                updated_at = %s
            WHERE update_id = %s
            """,
            (db_now, db_now, update_id),
        )
        connection.commit()

    def retry(
        self,
        update_id: int,
        now: datetime,
        available_at: datetime,
        error: str,
    ) -> None:
        connection = self.connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE telegram_updates
            SET status = 'retry',
                available_at = %s,
                claimed_at = NULL,
                last_error_message = %s,
                updated_at = %s
            WHERE update_id = %s
            """,
            (
                _to_mysql_datetime(available_at),
                error[:1000],
                _to_mysql_datetime(now),
                update_id,
            ),
        )
        connection.commit()


class MySqlMessageRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def add(self, message: StoredMessage) -> bool:
        connection = self.connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT IGNORE INTO messages (
                chat_id, telegram_message_id, user_id, username,
                full_name, text, sent_at, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                message.chat_id,
                message.telegram_message_id,
                message.user_id,
                message.username,
                message.full_name,
                message.text,
                message.sent_at.astimezone(timezone.utc).replace(tzinfo=None),
                datetime.now(timezone.utc).replace(tzinfo=None),
            ),
        )
        connection.commit()
        return cursor.rowcount == 1

    def list_between(
        self,
        chat_id: int,
        start: datetime,
        end: datetime,
    ) -> list[StoredMessage]:
        connection = self.connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT telegram_message_id, chat_id, user_id, username, full_name, text, sent_at
            FROM messages
            WHERE chat_id = %s
              AND sent_at >= %s
              AND sent_at < %s
            ORDER BY sent_at, telegram_message_id
            """,
            (chat_id, _to_mysql_datetime(start), _to_mysql_datetime(end)),
        )
        rows = cursor.fetchall()
        return [
            StoredMessage(
                telegram_message_id=int(row["telegram_message_id"]),
                chat_id=int(row["chat_id"]),
                user_id=int(row["user_id"]) if row["user_id"] is not None else None,
                username=str(row["username"]),
                full_name=str(row["full_name"]),
                text=str(row["text"]),
                sent_at=_from_mysql_datetime(row["sent_at"]),
            )
            for row in rows
        ]


class MySqlTrackedChatRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def enable(self, chat_id: int, title: str, now: datetime) -> None:
        connection = self.connection_factory()
        cursor = connection.cursor()
        db_now = _to_mysql_datetime(now)
        cursor.execute(
            """
            INSERT INTO tracked_chats (
                chat_id, title, enabled, timezone, scheduled_publish_time,
                created_at, updated_at
            ) VALUES (%s, %s, 1, 'Europe/Moscow', '21:00:00', %s, %s)
            ON DUPLICATE KEY UPDATE
                title = VALUES(title),
                enabled = 1,
                updated_at = VALUES(updated_at)
            """,
            (chat_id, title, db_now, db_now),
        )
        connection.commit()

    def disable(self, chat_id: int, now: datetime) -> None:
        connection = self.connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE tracked_chats
            SET enabled = 0, updated_at = %s
            WHERE chat_id = %s
            """,
            (_to_mysql_datetime(now), chat_id),
        )
        connection.commit()

    def is_tracked(self, chat_id: int) -> bool:
        connection = self.connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT 1 AS tracked FROM tracked_chats WHERE chat_id = %s",
            (chat_id,),
        )
        return cursor.fetchone() is not None


class MySqlMemberRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def observe(
        self,
        chat_id: int,
        user_id: int,
        username: str,
        full_name: str,
        now: datetime,
    ) -> None:
        connection = self.connection_factory()
        cursor = connection.cursor()
        db_now = _to_mysql_datetime(now)
        cursor.execute(
            """
            INSERT INTO chat_members (
                chat_id, user_id, username, full_name, first_seen_at, last_seen_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                username = VALUES(username),
                full_name = VALUES(full_name),
                last_seen_at = VALUES(last_seen_at)
            """,
            (chat_id, user_id, username, full_name, db_now, db_now),
        )
        connection.commit()

    def confirm_summary_link(self, chat_id: int, user_id: int, now: datetime) -> None:
        connection = self.connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE chat_members
            SET summary_linked_at = %s
            WHERE chat_id = %s AND user_id = %s
            """,
            (_to_mysql_datetime(now), chat_id, user_id),
        )
        connection.commit()


def _split_sql_statements(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def _to_mysql_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _from_mysql_datetime(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value.replace(tzinfo=timezone.utc)


def _queued_update_from_row(row: dict[str, Any]) -> QueuedUpdate:
    raw_payload = row["payload_json"]
    payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    return QueuedUpdate(
        update_id=int(row["update_id"]),
        payload=payload,
        status=UpdateStatus(str(row["status"])),
        attempt_count=int(row["attempt_count"]),
        available_at=_from_mysql_datetime(row["available_at"]),
        claimed_at=(
            _from_mysql_datetime(row["claimed_at"])
            if row.get("claimed_at") is not None
            else None
        ),
    )
