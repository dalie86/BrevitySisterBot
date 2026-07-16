from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path
from typing import Any

import pymysql
from pymysql.connections import Connection

from app.config import Settings
from app.domain.models import GeneratedSummary, QueuedUpdate, StoredMessage, UpdateStatus

ConnectionFactory = Callable[[], Any]


@contextmanager
def _managed_connection(connection_factory: ConnectionFactory):
    connection = connection_factory()
    try:
        yield connection
    except Exception:
        rollback = getattr(connection, "rollback", None)
        if rollback is not None:
            rollback()
        raise
    finally:
        connection.close()


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
        with _managed_connection(self.connection_factory) as connection:
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
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            stale_before = db_now - timedelta(seconds=lease_seconds)
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
                    OR (status = 'processing' AND claimed_at < %s)
                  )
                  AND available_at <= %s
                """,
                (db_now, db_now, update_id, stale_before, db_now),
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
        with _managed_connection(self.connection_factory) as connection:
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
        with _managed_connection(self.connection_factory) as connection:
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
        with _managed_connection(self.connection_factory) as connection:
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
        with _managed_connection(self.connection_factory) as connection:
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
        with _managed_connection(self.connection_factory) as connection:
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
        with _managed_connection(self.connection_factory) as connection:
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
        with _managed_connection(self.connection_factory) as connection:
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
        with _managed_connection(self.connection_factory) as connection:
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
        with _managed_connection(self.connection_factory) as connection:
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
        with _managed_connection(self.connection_factory) as connection:
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


class MySqlSummaryRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def find_by_source_hash(self, chat_id: int, source_hash: str) -> str | None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT summary_text
                FROM prepared_summaries
                WHERE chat_id = %s
                  AND source_hash = %s
                  AND status IN ('prepared', 'sent')
                  AND summary_text IS NOT NULL
                  AND summary_text <> ''
                ORDER BY id DESC
                LIMIT 1
                """,
                (chat_id, source_hash),
            )
            row = cursor.fetchone()
            return str(row["summary_text"]) if row else None

    def save(
        self,
        summary: GeneratedSummary,
        requester_user_id: int | None,
        now: datetime,
    ) -> None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            cursor.execute(
                """
                INSERT INTO prepared_summaries (
                    chat_id, summary_date, summary_kind, requester_user_id,
                    source_hash, summary_text, status, provider, model,
                    fallback_type, prepared_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    summary_text = IF(
                        VALUES(summary_text) IS NOT NULL AND VALUES(summary_text) <> '',
                        VALUES(summary_text),
                        summary_text
                    ),
                    status = VALUES(status),
                    provider = VALUES(provider),
                    model = VALUES(model),
                    fallback_type = VALUES(fallback_type),
                    prepared_at = VALUES(prepared_at),
                    updated_at = VALUES(updated_at)
                """,
                (
                    summary.chat_id,
                    summary.summary_date,
                    summary.kind.value,
                    requester_user_id,
                    summary.source_hash,
                    summary.text,
                    summary.status.value,
                    summary.provider,
                    summary.model,
                    summary.fallback_type,
                    db_now,
                    db_now,
                    db_now,
                ),
            )
            connection.commit()

    def linked_groups(self, user_id: int) -> list[tuple[int, str]]:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT members.chat_id, chats.title
                FROM chat_members AS members
                JOIN tracked_chats AS chats ON chats.chat_id = members.chat_id
                WHERE members.user_id = %s
                  AND members.summary_linked_at IS NOT NULL
                ORDER BY chats.title, members.chat_id
                """,
                (user_id,),
            )
            return [
                (int(row["chat_id"]), str(row["title"]))
                for row in cursor.fetchall()
            ]


class MySqlSummarySelectionRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def create(self, user_id: int, chat_id: int, now: datetime) -> str:
        token = secrets.token_hex(16)
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            cursor.execute(
                """
                INSERT INTO summary_selections (
                    selection_token, user_id, chat_id, expires_at, created_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (token, user_id, chat_id, db_now + timedelta(hours=1), db_now),
            )
            connection.commit()
        return token

    def resolve(self, token: str, user_id: int, now: datetime) -> int | None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT chat_id
                FROM summary_selections
                WHERE selection_token = %s
                  AND user_id = %s
                  AND expires_at >= %s
                """,
                (token, user_id, _to_mysql_datetime(now)),
            )
            row = cursor.fetchone()
            return int(row["chat_id"]) if row else None


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
