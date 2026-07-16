from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
            return [(int(row["chat_id"]), str(row["title"])) for row in cursor.fetchall()]


class MySqlSummaryRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def find_by_source_hash(
        self,
        chat_id: int,
        source_hash: str,
        kind: Any,
    ) -> str | None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT summary_text
                FROM prepared_summaries
                WHERE chat_id = %s
                  AND source_hash = %s
                  AND summary_kind = %s
                  AND status IN ('prepared', 'sent')
                  AND summary_text IS NOT NULL
                  AND summary_text <> ''
                ORDER BY id DESC
                LIMIT 1
                """,
                (chat_id, source_hash, str(getattr(kind, "value", kind))),
            )
            row = cursor.fetchone()
            return str(row["summary_text"]) if row else None

    def begin_generation(
        self,
        chat_id: int,
        summary_date: str,
        kind: Any,
        source_hash: str,
        requester_user_id: int | None,
        now: datetime,
    ) -> bool:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            kind_value = str(getattr(kind, "value", kind))
            cursor.execute(
                """
                INSERT IGNORE INTO prepared_summaries (
                    chat_id, summary_date, summary_kind, requester_user_id,
                    source_hash, status, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, 'preparing', %s, %s)
                """,
                (
                    chat_id,
                    summary_date,
                    kind_value,
                    requester_user_id,
                    source_hash,
                    db_now,
                    db_now,
                ),
            )
            if cursor.rowcount == 1:
                connection.commit()
                return True
            cursor.execute(
                """
                UPDATE prepared_summaries
                SET requester_user_id = %s, updated_at = %s
                WHERE chat_id = %s
                  AND summary_date = %s
                  AND summary_kind = %s
                  AND source_hash = %s
                  AND status = 'preparing'
                  AND updated_at < %s
                """,
                (
                    requester_user_id,
                    db_now,
                    chat_id,
                    summary_date,
                    kind_value,
                    source_hash,
                    db_now - timedelta(minutes=5),
                ),
            )
            acquired = cursor.rowcount == 1
            connection.commit()
            return acquired

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


class MySqlLifecycleStore:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        timezone_name: str = "Europe/Moscow",
    ) -> None:
        self.connection_factory = connection_factory
        self.timezone = ZoneInfo(timezone_name)

    def enabled_chats(self) -> list[tuple[int, str]]:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT chat_id, title FROM tracked_chats WHERE enabled = 1 ORDER BY chat_id"
            )
            return [(int(row["chat_id"]), str(row["title"])) for row in cursor.fetchall()]

    def record_skipped(
        self,
        chat_id: int,
        summary_date: str,
        message_count: int,
        now: datetime,
    ) -> None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            cursor.execute(
                """
                INSERT INTO daily_lifecycle (
                    chat_id, summary_date, source_message_count,
                    automatic_outcome, created_at, updated_at
                ) VALUES (%s, %s, %s, 'automatic_summary_skipped', %s, %s)
                ON DUPLICATE KEY UPDATE
                    source_message_count = VALUES(source_message_count),
                    automatic_outcome = VALUES(automatic_outcome),
                    updated_at = VALUES(updated_at)
                """,
                (chat_id, summary_date, message_count, db_now, db_now),
            )
            connection.commit()

    def enqueue_publication(
        self,
        chat_id: int,
        summary_date: str,
        source_hash: str,
        now: datetime,
    ) -> None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            cursor.execute(
                """
                INSERT IGNORE INTO scheduled_publications (
                    chat_id, summary_date, prepared_summary_id, status,
                    attempt_count, available_at, created_at, updated_at
                )
                SELECT chat_id, summary_date, id, 'pending', 0, %s, %s, %s
                FROM prepared_summaries
                WHERE chat_id = %s
                  AND summary_date = %s
                  AND summary_kind = 'scheduled'
                  AND source_hash = %s
                  AND status = 'prepared'
                ORDER BY id DESC
                LIMIT 1
                """,
                (db_now, db_now, db_now, chat_id, summary_date, source_hash),
            )
            if cursor.rowcount != 1:
                cursor.execute(
                    """
                    SELECT 1 AS exists_flag FROM scheduled_publications
                    WHERE chat_id = %s AND summary_date = %s
                    """,
                    (chat_id, summary_date),
                )
                if cursor.fetchone() is None:
                    raise RuntimeError("Prepared summary was not found for publication")
            cursor.execute(
                """
                INSERT INTO daily_lifecycle (
                    chat_id, summary_date, automatic_outcome,
                    summary_prepared_at, created_at, updated_at
                ) VALUES (%s, %s, 'summary_prepared', %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    automatic_outcome = VALUES(automatic_outcome),
                    summary_prepared_at = VALUES(summary_prepared_at),
                    updated_at = VALUES(updated_at)
                """,
                (chat_id, summary_date, db_now, db_now, db_now),
            )
            connection.commit()

    def claim_publications(
        self,
        summary_date: str,
        now: datetime,
        limit: int,
    ) -> list[tuple[int, int, str]]:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            stale_before = db_now - timedelta(minutes=5)
            cursor.execute(
                """
                SELECT id
                FROM scheduled_publications
                WHERE summary_date = %s
                  AND available_at <= %s
                  AND (
                    status IN ('pending', 'retry')
                    OR (status = 'sending' AND claimed_at < %s)
                  )
                ORDER BY id
                LIMIT %s
                """,
                (summary_date, db_now, stale_before, limit),
            )
            ids = [int(row["id"]) for row in cursor.fetchall()]
            claimed: list[tuple[int, int, str]] = []
            for publication_id in ids:
                cursor.execute(
                    """
                    UPDATE scheduled_publications
                    SET status = 'sending',
                        attempt_count = attempt_count + 1,
                        claimed_at = %s,
                        updated_at = %s
                    WHERE id = %s
                      AND (
                        status IN ('pending', 'retry')
                        OR (status = 'sending' AND claimed_at < %s)
                      )
                    """,
                    (db_now, db_now, publication_id, stale_before),
                )
                if cursor.rowcount != 1:
                    continue
                cursor.execute(
                    """
                    SELECT publications.id, publications.chat_id, summaries.summary_text
                    FROM scheduled_publications AS publications
                    JOIN prepared_summaries AS summaries
                      ON summaries.id = publications.prepared_summary_id
                    WHERE publications.id = %s
                    """,
                    (publication_id,),
                )
                row = cursor.fetchone()
                if row and row["summary_text"]:
                    claimed.append((int(row["id"]), int(row["chat_id"]), str(row["summary_text"])))
            connection.commit()
            return claimed

    def mark_publication_sent(
        self,
        publication_id: int,
        telegram_message_id: int,
        now: datetime,
    ) -> None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            cursor.execute(
                """
                UPDATE scheduled_publications
                SET status = 'sent', telegram_message_id = %s,
                    sent_at = %s, claimed_at = NULL, updated_at = %s
                WHERE id = %s AND status = 'sending'
                """,
                (telegram_message_id, db_now, db_now, publication_id),
            )
            cursor.execute(
                """
                UPDATE prepared_summaries AS summaries
                JOIN scheduled_publications AS publications
                  ON publications.prepared_summary_id = summaries.id
                SET summaries.status = 'sent',
                    summaries.sent_at = %s,
                    summaries.updated_at = %s
                WHERE publications.id = %s
                """,
                (db_now, db_now, publication_id),
            )
            cursor.execute(
                """
                UPDATE daily_lifecycle AS lifecycle
                JOIN scheduled_publications AS publications
                  ON publications.chat_id = lifecycle.chat_id
                 AND publications.summary_date = lifecycle.summary_date
                SET lifecycle.summary_sent_at = %s,
                    lifecycle.automatic_outcome = 'summary_sent',
                    lifecycle.updated_at = %s
                WHERE publications.id = %s
                """,
                (db_now, db_now, publication_id),
            )
            connection.commit()

    def mark_publication_retry(
        self,
        publication_id: int,
        now: datetime,
        error: str,
    ) -> None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            cursor.execute(
                """
                UPDATE scheduled_publications
                SET status = 'retry', available_at = %s, claimed_at = NULL,
                    last_error_message = %s, updated_at = %s
                WHERE id = %s
                """,
                (db_now + timedelta(minutes=5), error[:1000], db_now, publication_id),
            )
            connection.commit()

    def archive_day(self, summary_date: str, now: datetime, retention_days: int) -> int:
        start, end = self._utc_day_bounds(summary_date)
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            expires_at = db_now + timedelta(days=retention_days)
            cursor.execute(
                """
                INSERT IGNORE INTO archived_messages (
                    chat_id, telegram_message_id, user_id, username, full_name,
                    text, sent_at, archived_date, archived_at, expires_at
                )
                SELECT chat_id, telegram_message_id, user_id, username, full_name,
                       text, sent_at, %s, %s, %s
                FROM messages
                WHERE sent_at >= %s AND sent_at < %s
                """,
                (summary_date, db_now, expires_at, start, end),
            )
            expected = self._counts_for_working(cursor, start, end)
            archived = self._counts_for_archive(cursor, summary_date)
            if expected != {chat_id: archived.get(chat_id, 0) for chat_id in expected}:
                raise RuntimeError("Archive verification count mismatch")
            cursor.execute(
                """
                SELECT COUNT(*) AS missing_count
                FROM messages AS messages
                LEFT JOIN archived_messages AS archived
                  ON archived.chat_id = messages.chat_id
                 AND archived.telegram_message_id = messages.telegram_message_id
                WHERE messages.sent_at >= %s
                  AND messages.sent_at < %s
                  AND archived.id IS NULL
                """,
                (start, end),
            )
            if int(cursor.fetchone()["missing_count"]) != 0:
                raise RuntimeError("Archive verification identity mismatch")
            cursor.execute(
                """
                INSERT IGNORE INTO archived_summaries (
                    source_summary_id, chat_id, summary_date, summary_kind,
                    summary_text, provider, model, fallback_type,
                    prepared_at, sent_at, archived_at, expires_at
                )
                SELECT id, chat_id, summary_date, summary_kind, summary_text,
                       provider, model, fallback_type, prepared_at, sent_at, %s, %s
                FROM prepared_summaries
                WHERE summary_date = %s
                  AND summary_text IS NOT NULL
                  AND summary_text <> ''
                """,
                (db_now, expires_at, summary_date),
            )
            for chat_id, count in expected.items():
                self._mark_archive_verified(
                    cursor,
                    chat_id,
                    summary_date,
                    count,
                    db_now,
                )
            connection.commit()
            return sum(expected.values())
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def cleanup_verified(self, summary_date: str, now: datetime) -> int:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                DELETE messages
                FROM messages
                JOIN archived_messages AS archived
                  ON archived.chat_id = messages.chat_id
                 AND archived.telegram_message_id = messages.telegram_message_id
                JOIN daily_lifecycle AS lifecycle
                  ON lifecycle.chat_id = messages.chat_id
                 AND lifecycle.summary_date = archived.archived_date
                WHERE archived.archived_date = %s
                  AND lifecycle.archive_verified = 1
                  AND lifecycle.archive_completed_at IS NOT NULL
                """,
                (summary_date,),
            )
            deleted = int(cursor.rowcount)
            db_now = _to_mysql_datetime(now)
            cursor.execute(
                """
                UPDATE daily_lifecycle
                SET working_cleanup_completed_at = %s, updated_at = %s
                WHERE summary_date = %s
                  AND archive_verified = 1
                  AND archive_completed_at IS NOT NULL
                """,
                (db_now, db_now, summary_date),
            )
            connection.commit()
            return deleted

    def _utc_day_bounds(self, summary_date: str) -> tuple[datetime, datetime]:
        local_start = datetime.fromisoformat(summary_date).replace(tzinfo=self.timezone)
        local_end = local_start + timedelta(days=1)
        return (
            local_start.astimezone(timezone.utc).replace(tzinfo=None),
            local_end.astimezone(timezone.utc).replace(tzinfo=None),
        )

    @staticmethod
    def _counts_for_working(cursor: Any, start: datetime, end: datetime) -> dict[int, int]:
        cursor.execute(
            """
            SELECT chat_id, COUNT(*) AS item_count
            FROM messages
            WHERE sent_at >= %s AND sent_at < %s
            GROUP BY chat_id
            """,
            (start, end),
        )
        return {int(row["chat_id"]): int(row["item_count"]) for row in cursor.fetchall()}

    @staticmethod
    def _counts_for_archive(cursor: Any, summary_date: str) -> dict[int, int]:
        cursor.execute(
            """
            SELECT chat_id, COUNT(*) AS item_count
            FROM archived_messages
            WHERE archived_date = %s
            GROUP BY chat_id
            """,
            (summary_date,),
        )
        return {int(row["chat_id"]): int(row["item_count"]) for row in cursor.fetchall()}

    @staticmethod
    def _mark_archive_verified(
        cursor: Any,
        chat_id: int,
        summary_date: str,
        count: int,
        now: datetime,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO daily_lifecycle (
                chat_id, summary_date, source_message_count,
                archive_completed_at, archived_message_count,
                archive_verified, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
            ON DUPLICATE KEY UPDATE
                source_message_count = VALUES(source_message_count),
                archive_completed_at = VALUES(archive_completed_at),
                archived_message_count = VALUES(archived_message_count),
                archive_verified = 1,
                updated_at = VALUES(updated_at)
            """,
            (chat_id, summary_date, count, now, count, now, now),
        )


class MySqlJobLockRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def acquire(self, lock_name: str, now: datetime, lease_seconds: int = 600) -> str | None:
        owner_token = str(uuid.uuid4())
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            cursor.execute(
                "DELETE FROM job_locks WHERE lock_name = %s AND expires_at < %s",
                (lock_name, db_now),
            )
            cursor.execute(
                """
                INSERT IGNORE INTO job_locks (
                    lock_name, owner_token, acquired_at, expires_at
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    lock_name,
                    owner_token,
                    db_now,
                    db_now + timedelta(seconds=lease_seconds),
                ),
            )
            acquired = cursor.rowcount == 1
            connection.commit()
            return owner_token if acquired else None

    def release(self, lock_name: str, owner_token: str) -> None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                "DELETE FROM job_locks WHERE lock_name = %s AND owner_token = %s",
                (lock_name, owner_token),
            )
            connection.commit()


class MySqlMaintenanceStore:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def purge_expired(self, now: datetime, retention_days: int) -> dict[str, int]:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            cutoff = db_now - timedelta(days=retention_days)
            statements = {
                "archived_messages": (
                    "DELETE FROM archived_messages WHERE expires_at < %s",
                    (db_now,),
                ),
                "archived_summaries": (
                    "DELETE FROM archived_summaries WHERE expires_at < %s",
                    (db_now,),
                ),
                "summary_selections": (
                    "DELETE FROM summary_selections WHERE expires_at < %s",
                    (db_now,),
                ),
                "telegram_updates": (
                    """
                    DELETE FROM telegram_updates
                    WHERE status IN ('completed', 'failed') AND updated_at < %s
                    """,
                    (cutoff,),
                ),
            }
            deleted = {}
            for name, (sql, params) in statements.items():
                cursor.execute(sql, params)
                deleted[name] = int(cursor.rowcount)
            connection.commit()
            return deleted

    def audit(self, now: datetime) -> dict[str, int]:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            counters = {
                "queued_updates": self._count(
                    cursor,
                    """
                    SELECT COUNT(*) AS item_count FROM telegram_updates
                    WHERE status IN ('pending', 'retry') AND available_at <= %s
                    """,
                    (db_now,),
                ),
                "stale_updates": self._count(
                    cursor,
                    """
                    SELECT COUNT(*) AS item_count FROM telegram_updates
                    WHERE status = 'processing' AND claimed_at < %s
                    """,
                    (db_now - timedelta(minutes=10),),
                ),
                "stale_publications": self._count(
                    cursor,
                    """
                    SELECT COUNT(*) AS item_count FROM scheduled_publications
                    WHERE status = 'sending' AND claimed_at < %s
                    """,
                    (db_now - timedelta(minutes=10),),
                ),
                "unverified_archives": self._count(
                    cursor,
                    """
                    SELECT COUNT(*) AS item_count FROM daily_lifecycle
                    WHERE archive_completed_at IS NOT NULL AND archive_verified = 0
                    """,
                    (),
                ),
            }
            return counters

    @staticmethod
    def _count(cursor: Any, sql: str, params: tuple[Any, ...]) -> int:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return int(row["item_count"]) if row else 0


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
            _from_mysql_datetime(row["claimed_at"]) if row.get("claimed_at") is not None else None
        ),
    )
