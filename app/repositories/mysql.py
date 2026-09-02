from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql
from pymysql.connections import Connection

from app.config import Settings
from app.domain.models import (
    BalanceSnapshot,
    GeneratedSummary,
    QueuedUpdate,
    ReservationResult,
    ReservationState,
    StoredMessage,
    UpdateStatus,
    UsageTotals,
)

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

    def execute_script(self, sql: str, migration_name: str | None = None) -> None:
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                for statement in _split_sql_statements(sql):
                    try:
                        cursor.execute(statement)
                    except pymysql.MySQLError as exc:
                        if not _is_tolerated_duplicate_ddl(
                            migration_name, statement, exc
                        ):
                            raise
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

    def verify_migration(self, filename: str) -> None:
        if filename not in {
            "002_private_summary_onboarding.sql",
            "003_deepseek_usage.sql",
        }:
            return
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                if filename == "003_deepseek_usage.sql":
                    cursor.execute(
                        """
                        SELECT table_name, column_name, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = DATABASE()
                          AND table_name IN (
                            'prepared_summaries', 'llm_balance_snapshots'
                          )
                        """
                    )
                    columns = {
                        (str(row["table_name"]), str(row["column_name"])): str(
                            row["is_nullable"]
                        )
                        for row in cursor.fetchall()
                    }
                    cursor.execute(
                        """
                        SELECT table_name, index_name, non_unique
                        FROM information_schema.statistics
                        WHERE table_schema = DATABASE()
                          AND table_name = 'llm_balance_snapshots'
                        """
                    )
                    indexes = {
                        (str(row["table_name"]), str(row["index_name"])): int(
                            row["non_unique"]
                        )
                        for row in cursor.fetchall()
                    }
                    _verify_migration_003_schema(columns, indexes)
                    return
                cursor.execute(
                    """
                    SELECT table_name, column_name, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                      AND table_name IN (
                        'tracked_chats', 'chat_share_requests', 'summary_selections'
                      )
                    """
                )
                columns = {
                    (str(row["table_name"]), str(row["column_name"])): str(
                        row["is_nullable"]
                    )
                    for row in cursor.fetchall()
                }
                cursor.execute(
                    """
                    SELECT table_name, index_name, non_unique
                    FROM information_schema.statistics
                    WHERE table_schema = DATABASE()
                      AND table_name IN (
                        'tracked_chats', 'chat_share_requests', 'summary_selections'
                      )
                    """
                )
                indexes = {
                    (str(row["table_name"]), str(row["index_name"])): int(
                        row["non_unique"]
                    )
                    for row in cursor.fetchall()
                }
            _verify_migration_002_schema(columns, indexes)
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
        database.execute_script(content.decode("utf-8"), migration_name=path.name)
        database.verify_migration(path.name)
        database.record_migration(path.name, checksum)
        applied.append(path.name)
    return applied


class MySqlUpdateRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def enqueue(self, update_id: int, payload: dict, now: datetime) -> bool:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
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
                    db_now,
                    db_now,
                    db_now,
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
            stale_before = db_now - timedelta(minutes=5)
            cursor.execute(
                """
                SELECT update_id
                FROM telegram_updates
                WHERE (
                    status IN ('pending', 'retry')
                    OR (status = 'processing' AND claimed_at < %s)
                )
                  AND available_at <= %s
                ORDER BY available_at, update_id
                LIMIT %s
                """,
                (stale_before, db_now, limit),
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

    def fail(self, update_id: int, now: datetime, error: str) -> None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            cursor.execute(
                """
                UPDATE telegram_updates
                SET status = 'failed',
                    claimed_at = NULL,
                    last_error_message = %s,
                    completed_at = %s,
                    updated_at = %s
                WHERE update_id = %s
                """,
                (error[:1000], db_now, db_now, update_id),
            )
            connection.commit()


class MySqlTelegramEffectRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def begin(
        self,
        effect_key: str,
        update_id: int,
        method: str,
        payload_hash: str,
        now: datetime,
    ) -> tuple[str, int | None] | None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            cursor.execute(
                """
                INSERT IGNORE INTO telegram_effects (
                    effect_key, update_id, method, payload_hash, status,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, 'sending', %s, %s)
                """,
                (effect_key, update_id, method, payload_hash, db_now, db_now),
            )
            if cursor.rowcount == 1:
                connection.commit()
                return None
            cursor.execute(
                """
                SELECT status, telegram_message_id, payload_hash
                FROM telegram_effects
                WHERE effect_key = %s
                """,
                (effect_key,),
            )
            row = cursor.fetchone()
            connection.commit()
            if row is None:
                raise RuntimeError("Telegram effect disappeared after duplicate insert")
            if str(row["payload_hash"]) != payload_hash:
                raise RuntimeError("Telegram effect payload changed for the same update")
            message_id = row["telegram_message_id"]
            return (
                str(row["status"]),
                int(message_id) if message_id is not None else None,
            )

    def mark_sent(
        self,
        effect_key: str,
        telegram_message_id: int,
        now: datetime,
    ) -> None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE telegram_effects
                SET status = 'sent', telegram_message_id = %s,
                    last_error_message = NULL, updated_at = %s
                WHERE effect_key = %s
                """,
                (telegram_message_id, _to_mysql_datetime(now), effect_key),
            )
            connection.commit()

    def mark_review_required(self, effect_key: str, error: str, now: datetime) -> None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE telegram_effects
                SET status = 'review_required', last_error_message = %s, updated_at = %s
                WHERE effect_key = %s
                """,
                (error[:1000], _to_mysql_datetime(now), effect_key),
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

    def enable(self, chat_id: int, title: str, now: datetime) -> str:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            private_link_token = secrets.token_hex(16)
            cursor.execute(
                """
            INSERT INTO tracked_chats (
                chat_id, title, enabled, timezone, scheduled_publish_time,
                private_link_token, created_at, updated_at
            ) VALUES (%s, %s, 1, 'Europe/Moscow', '21:00:00', %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                title = VALUES(title),
                enabled = 1,
                updated_at = VALUES(updated_at)
            """,
                (chat_id, title, private_link_token, db_now, db_now),
            )
            cursor.execute(
                "SELECT private_link_token FROM tracked_chats WHERE chat_id = %s",
                (chat_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Tracked chat disappeared after enable")
            connection.commit()
            return str(row["private_link_token"])

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

    def tracked_count(self) -> int:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT COUNT(*) AS tracked_count FROM tracked_chats")
            row = cursor.fetchone()
            return int(row["tracked_count"]) if row is not None else 0

    def resolve_private_link(self, token: str) -> tuple[int, str] | None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT chat_id, title, private_link_token
                FROM tracked_chats
                WHERE private_link_token = %s
                """,
                (token,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return int(row["chat_id"]), str(row["title"])


class MySqlChatShareRequestRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def get_or_create(self, user_id: int, idempotency_key: str, now: datetime) -> int:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            for attempt in range(5):
                request_id = secrets.randbelow(2**32) - 2**31
                try:
                    cursor.execute(
                        """
                        INSERT INTO chat_share_requests (
                            request_id, user_id, idempotency_key, expires_at, created_at
                        ) VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            request_id,
                            user_id,
                            idempotency_key,
                            db_now + timedelta(hours=1),
                            db_now,
                        ),
                    )
                except pymysql.err.IntegrityError as exc:
                    if exc.args[0] != 1062:
                        raise
                    cursor.execute(
                        """
                        SELECT request_id
                        FROM chat_share_requests
                        WHERE idempotency_key = %s
                        """,
                        (idempotency_key,),
                    )
                    row = cursor.fetchone()
                    if row is not None:
                        connection.commit()
                        return int(row["request_id"])
                    if attempt < 4:
                        continue
                    raise
                connection.commit()
                return request_id
        raise RuntimeError("Unable to create chat share request")

    def reserve(
        self,
        request_id: int,
        user_id: int,
        chat_id: int,
        update_id: int,
        now: datetime,
    ) -> ReservationResult:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT reserved_update_id, reserved_chat_id, used_at, expires_at
                FROM chat_share_requests
                WHERE request_id = %s
                  AND user_id = %s
                FOR UPDATE
                """,
                (request_id, user_id),
            )
            row = cursor.fetchone()
            db_now = _to_mysql_datetime(now)
            if row is None:
                connection.commit()
                return ReservationResult(ReservationState.UNAVAILABLE)
            owner = row["reserved_update_id"]
            reserved_chat_id = row["reserved_chat_id"]
            if owner == update_id and row["used_at"] is not None:
                connection.commit()
                return ReservationResult(
                    ReservationState.FINALIZED, int(reserved_chat_id)
                )
            if row["used_at"] is not None or row["expires_at"] < db_now:
                connection.commit()
                return ReservationResult(ReservationState.UNAVAILABLE)
            if owner is None:
                cursor.execute(
                    """
                    UPDATE chat_share_requests
                    SET reserved_update_id = %s, reserved_chat_id = %s
                    WHERE request_id = %s AND user_id = %s
                    """,
                    (update_id, chat_id, request_id, user_id),
                )
                connection.commit()
                return ReservationResult(ReservationState.ACQUIRED, chat_id)
            if owner == update_id and int(reserved_chat_id) == chat_id:
                connection.commit()
                return ReservationResult(ReservationState.RESUMED, chat_id)
            connection.commit()
            return ReservationResult(ReservationState.UNAVAILABLE)

    def finalize(
        self, request_id: int, user_id: int, update_id: int, now: datetime
    ) -> bool:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            cursor.execute(
                """
                UPDATE chat_share_requests
                SET used_at = %s
                WHERE request_id = %s
                  AND user_id = %s
                  AND reserved_update_id = %s
                  AND used_at IS NULL
                """,
                (db_now, request_id, user_id, update_id),
            )
            consumed = cursor.rowcount == 1
            connection.commit()
            return consumed


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

    def remember_group(
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
                    chat_id, user_id, username, full_name,
                    first_seen_at, last_seen_at, summary_linked_at,
                    last_membership_check_at, last_membership_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    username = VALUES(username),
                    full_name = VALUES(full_name),
                    last_seen_at = VALUES(last_seen_at),
                    summary_linked_at = VALUES(summary_linked_at),
                    last_membership_check_at = VALUES(last_membership_check_at),
                    last_membership_status = VALUES(last_membership_status)
                """,
                (
                    chat_id,
                    user_id,
                    username,
                    full_name,
                    db_now,
                    db_now,
                    db_now,
                    db_now,
                    "member",
                ),
            )
            connection.commit()

    def record_membership(
        self,
        chat_id: int,
        user_id: int,
        status: str,
        is_member: bool,
        now: datetime,
    ) -> None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE chat_members
                SET last_membership_check_at = %s,
                    last_membership_status = %s,
                    summary_linked_at = CASE
                        WHEN %s = 1 THEN summary_linked_at ELSE NULL END
                WHERE chat_id = %s AND user_id = %s
                """,
                (
                    _to_mysql_datetime(now),
                    status,
                    int(is_member),
                    chat_id,
                    user_id,
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
                    fallback_type, latency_ms, input_tokens, output_tokens,
                    input_characters, cache_hit_input_tokens,
                    cache_miss_input_tokens, llm_call_count,
                    estimated_cost_microusd, prepared_at, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
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
                    latency_ms = VALUES(latency_ms),
                    input_tokens = VALUES(input_tokens),
                    output_tokens = VALUES(output_tokens),
                    input_characters = VALUES(input_characters),
                    cache_hit_input_tokens = VALUES(cache_hit_input_tokens),
                    cache_miss_input_tokens = VALUES(cache_miss_input_tokens),
                    llm_call_count = VALUES(llm_call_count),
                    estimated_cost_microusd = VALUES(estimated_cost_microusd),
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
                    summary.latency_ms,
                    summary.input_tokens,
                    summary.output_tokens,
                    summary.input_characters,
                    summary.cache_hit_input_tokens,
                    summary.cache_miss_input_tokens,
                    summary.llm_call_count,
                    summary.estimated_cost_microusd,
                    db_now,
                    db_now,
                    db_now,
                ),
            )
            connection.commit()


class MySqlUsageRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def aggregate(
        self, provider: str, start: datetime, end: datetime
    ) -> UsageTotals:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(llm_call_count), 0) AS llm_call_count,
                    COALESCE(SUM(GREATEST(COALESCE(llm_call_count, 0) - 1, 0)), 0)
                        AS repair_call_count,
                    COALESCE(SUM(cache_hit_input_tokens), 0)
                        AS cache_hit_input_tokens,
                    COALESCE(SUM(cache_miss_input_tokens), 0)
                        AS cache_miss_input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(estimated_cost_microusd), 0)
                        AS estimated_cost_microusd
                FROM prepared_summaries
                WHERE provider = %s
                  AND prepared_at >= %s
                  AND prepared_at < %s
                """,
                (provider, _to_mysql_datetime(start), _to_mysql_datetime(end)),
            )
            row = cursor.fetchone() or {}
            return UsageTotals(
                llm_call_count=int(row.get("llm_call_count") or 0),
                repair_call_count=int(row.get("repair_call_count") or 0),
                cache_hit_input_tokens=int(
                    row.get("cache_hit_input_tokens") or 0
                ),
                cache_miss_input_tokens=int(
                    row.get("cache_miss_input_tokens") or 0
                ),
                output_tokens=int(row.get("output_tokens") or 0),
                estimated_cost_microusd=int(
                    row.get("estimated_cost_microusd") or 0
                ),
            )

    def previous_snapshot(
        self, provider: str, currency: str, week_start: datetime
    ) -> BalanceSnapshot | None:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT provider, currency, total_balance, granted_balance,
                       topped_up_balance, is_available, report_week_start,
                       report_text, captured_at
                FROM llm_balance_snapshots
                WHERE provider = %s
                  AND currency = %s
                  AND report_week_start < %s
                ORDER BY report_week_start DESC
                LIMIT 1
                """,
                (provider, currency, _to_mysql_datetime(week_start)),
            )
            row = cursor.fetchone()
            return _balance_snapshot_from_row(row) if row else None

    def get_or_create_snapshot(self, snapshot: BalanceSnapshot) -> BalanceSnapshot:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_week_start = _to_mysql_datetime(snapshot.report_week_start)
            cursor.execute(
                """
                INSERT IGNORE INTO llm_balance_snapshots (
                    provider, currency, total_balance, granted_balance,
                    topped_up_balance, is_available, report_week_start,
                    report_text, captured_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    snapshot.provider,
                    snapshot.currency,
                    snapshot.total_balance,
                    snapshot.granted_balance,
                    snapshot.topped_up_balance,
                    int(snapshot.is_available),
                    db_week_start,
                    snapshot.report_text,
                    _to_mysql_datetime(snapshot.captured_at),
                ),
            )
            cursor.execute(
                """
                SELECT provider, currency, total_balance, granted_balance,
                       topped_up_balance, is_available, report_week_start,
                       report_text, captured_at
                FROM llm_balance_snapshots
                WHERE provider = %s AND currency = %s AND report_week_start = %s
                """,
                (snapshot.provider, snapshot.currency, db_week_start),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Balance snapshot insert could not be read back")
            connection.commit()
            return _balance_snapshot_from_row(row)


class MySqlSummarySelectionRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def get_or_create(
        self,
        user_id: int,
        chat_id: int,
        idempotency_key: str,
        now: datetime,
    ) -> str:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            db_now = _to_mysql_datetime(now)
            for attempt in range(5):
                token = secrets.token_hex(16)
                try:
                    cursor.execute(
                        """
                        INSERT INTO summary_selections (
                            selection_token, user_id, chat_id, idempotency_key,
                            expires_at, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            token,
                            user_id,
                            chat_id,
                            idempotency_key,
                            db_now + timedelta(hours=1),
                            db_now,
                        ),
                    )
                except pymysql.err.IntegrityError as exc:
                    if exc.args[0] != 1062:
                        raise
                    cursor.execute(
                        """
                        SELECT selection_token
                        FROM summary_selections
                        WHERE idempotency_key = %s
                        """,
                        (idempotency_key,),
                    )
                    row = cursor.fetchone()
                    if row is not None:
                        connection.commit()
                        return str(row["selection_token"])
                    if attempt < 4:
                        continue
                    raise
                connection.commit()
                return token
        raise RuntimeError("Unable to create summary selection")

    def reserve(
        self, token: str, user_id: int, update_id: int, now: datetime
    ) -> ReservationResult:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT chat_id, reserved_update_id, consumed_at, expires_at
                FROM summary_selections
                WHERE selection_token = %s
                  AND user_id = %s
                FOR UPDATE
                """,
                (token, user_id),
            )
            row = cursor.fetchone()
            if row is None:
                connection.commit()
                return ReservationResult(ReservationState.UNAVAILABLE)
            chat_id = int(row["chat_id"])
            owner = row["reserved_update_id"]
            if owner == update_id and row["consumed_at"] is not None:
                connection.commit()
                return ReservationResult(ReservationState.FINALIZED, chat_id)
            if row["consumed_at"] is not None or row["expires_at"] < _to_mysql_datetime(now):
                connection.commit()
                return ReservationResult(ReservationState.UNAVAILABLE)
            if owner is None:
                cursor.execute(
                    """
                    UPDATE summary_selections
                    SET reserved_update_id = %s
                    WHERE selection_token = %s AND user_id = %s
                    """,
                    (update_id, token, user_id),
                )
                connection.commit()
                return ReservationResult(ReservationState.ACQUIRED, chat_id)
            if owner == update_id:
                connection.commit()
                return ReservationResult(ReservationState.RESUMED, chat_id)
            connection.commit()
            return ReservationResult(ReservationState.UNAVAILABLE)

    def finalize(self, token: str, user_id: int, update_id: int, now: datetime) -> bool:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE summary_selections
                SET consumed_at = %s
                WHERE selection_token = %s
                  AND user_id = %s
                  AND reserved_update_id = %s
                  AND consumed_at IS NULL
                """,
                (_to_mysql_datetime(now), token, user_id, update_id),
            )
            finalized = cursor.rowcount == 1
            connection.commit()
            return finalized


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
            cursor.execute(
                """
                SELECT id
                FROM scheduled_publications
                WHERE summary_date = %s
                  AND available_at <= %s
                  AND status IN ('pending', 'retry')
                ORDER BY id
                LIMIT %s
                """,
                (summary_date, db_now, limit),
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
                      AND status IN ('pending', 'retry')
                    """,
                    (db_now, db_now, publication_id),
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

    def mark_publication_review_required(
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
                SET status = 'review_required', claimed_at = NULL,
                    last_error_message = %s, updated_at = %s
                WHERE id = %s
                """,
                (error[:1000], db_now, publication_id),
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
                  AND lifecycle.automatic_outcome IN (
                    'summary_sent', 'automatic_summary_skipped'
                  )
                """,
                (summary_date,),
            )
            deleted = int(cursor.rowcount)
            cursor.execute(
                """
                DELETE summaries
                FROM prepared_summaries AS summaries
                JOIN archived_summaries AS archived
                  ON archived.source_summary_id = summaries.id
                JOIN daily_lifecycle AS lifecycle
                  ON lifecycle.chat_id = summaries.chat_id
                 AND lifecycle.summary_date = summaries.summary_date
                WHERE summaries.summary_date = %s
                  AND lifecycle.archive_verified = 1
                  AND lifecycle.archive_completed_at IS NOT NULL
                  AND lifecycle.automatic_outcome IN (
                    'summary_sent', 'automatic_summary_skipped'
                  )
                """,
                (summary_date,),
            )
            db_now = _to_mysql_datetime(now)
            cursor.execute(
                """
                UPDATE daily_lifecycle
                SET working_cleanup_completed_at = %s, updated_at = %s
                WHERE summary_date = %s
                  AND archive_verified = 1
                  AND archive_completed_at IS NOT NULL
                  AND automatic_outcome IN (
                    'summary_sent', 'automatic_summary_skipped'
                  )
                """,
                (db_now, db_now, summary_date),
            )
            connection.commit()
            return deleted

    def preview_cleanup(self, summary_date: str) -> int:
        with _managed_connection(self.connection_factory) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) AS item_count
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
                  AND lifecycle.automatic_outcome IN (
                    'summary_sent', 'automatic_summary_skipped'
                  )
                """,
                (summary_date,),
            )
            row = cursor.fetchone()
            return int(row["item_count"]) if row else 0

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
                "prepared_summaries": (
                    """
                    DELETE summaries
                    FROM prepared_summaries AS summaries
                    WHERE summaries.summary_date < %s
                      AND (
                        summaries.status = 'sent'
                        OR EXISTS (
                            SELECT 1 FROM archived_summaries AS archived
                            WHERE archived.source_summary_id = summaries.id
                        )
                      )
                    """,
                    (cutoff.date(),),
                ),
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
                "chat_share_requests": (
                    "DELETE FROM chat_share_requests WHERE expires_at < %s",
                    (db_now,),
                ),
                "telegram_updates": (
                    """
                    DELETE FROM telegram_updates
                    WHERE status IN ('completed', 'failed') AND updated_at < %s
                    """,
                    (cutoff,),
                ),
                "telegram_effects": (
                    """
                    DELETE FROM telegram_effects
                    WHERE status IN ('sent', 'review_required') AND updated_at < %s
                    """,
                    (cutoff,),
                ),
                "scheduled_publications": (
                    """
                    DELETE FROM scheduled_publications
                    WHERE status = 'sent' AND updated_at < %s
                    """,
                    (cutoff,),
                ),
                "daily_lifecycle": (
                    """
                    DELETE FROM daily_lifecycle
                    WHERE archive_verified = 1
                      AND working_cleanup_completed_at IS NOT NULL
                      AND updated_at < %s
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
                    WHERE status = 'review_required'
                       OR (status = 'sending' AND claimed_at < %s)
                    """,
                    (db_now - timedelta(minutes=10),),
                ),
                "failed_updates": self._count(
                    cursor,
                    """
                    SELECT COUNT(*) AS item_count FROM telegram_updates
                    WHERE status = 'failed' AND updated_at >= %s
                    """,
                    (db_now - timedelta(days=1),),
                ),
                "review_required_effects": self._count(
                    cursor,
                    """
                    SELECT COUNT(*) AS item_count FROM telegram_effects
                    WHERE status = 'review_required'
                    """,
                    (),
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


def _is_tolerated_duplicate_ddl(
    migration_name: str | None,
    statement: str,
    error: pymysql.MySQLError,
) -> bool:
    if migration_name not in {
        "002_private_summary_onboarding.sql",
        "003_deepseek_usage.sql",
    } or not error.args:
        return False
    error_code = int(error.args[0])
    normalized = " ".join(statement.upper().split())
    if error_code == 1050:
        return normalized.startswith("CREATE TABLE CHAT_SHARE_REQUESTS ") or normalized.startswith(
            "CREATE TABLE IF NOT EXISTS LLM_BALANCE_SNAPSHOTS "
        )
    if error_code == 1060:
        return normalized.startswith("ALTER TABLE ") and " ADD COLUMN " in normalized
    if error_code == 1061:
        return normalized.startswith("ALTER TABLE ") and (
            " ADD UNIQUE KEY " in normalized or " ADD KEY " in normalized
        )
    return False


def _verify_migration_003_schema(
    columns: dict[tuple[str, str], str],
    indexes: dict[tuple[str, str], int],
) -> None:
    required_columns = {
        ("prepared_summaries", "cache_hit_input_tokens"): "YES",
        ("prepared_summaries", "cache_miss_input_tokens"): "YES",
        ("prepared_summaries", "llm_call_count"): "YES",
        ("prepared_summaries", "estimated_cost_microusd"): "YES",
        ("llm_balance_snapshots", "snapshot_id"): "NO",
        ("llm_balance_snapshots", "provider"): "NO",
        ("llm_balance_snapshots", "currency"): "NO",
        ("llm_balance_snapshots", "total_balance"): "NO",
        ("llm_balance_snapshots", "granted_balance"): "NO",
        ("llm_balance_snapshots", "topped_up_balance"): "NO",
        ("llm_balance_snapshots", "is_available"): "NO",
        ("llm_balance_snapshots", "report_week_start"): "NO",
        ("llm_balance_snapshots", "report_text"): "NO",
        ("llm_balance_snapshots", "captured_at"): "NO",
    }
    missing_columns = [
        f"{table}.{column} ({nullable})"
        for (table, column), nullable in required_columns.items()
        if columns.get((table, column)) != nullable
    ]
    if missing_columns:
        details = ", ".join(missing_columns)
        raise RuntimeError(f"Migration 003 schema verification failed: {details}")
    required_indexes = {
        ("llm_balance_snapshots", "PRIMARY"): 0,
        ("llm_balance_snapshots", "uq_llm_balance_report_week"): 0,
    }
    missing_indexes = [
        f"{table}.{index}"
        for (table, index), non_unique in required_indexes.items()
        if indexes.get((table, index)) != non_unique
    ]
    if missing_indexes:
        details = ", ".join(missing_indexes)
        raise RuntimeError(f"Migration 003 schema verification failed: {details}")


def _verify_migration_002_schema(
    columns: dict[tuple[str, str], str],
    indexes: dict[tuple[str, str], int],
) -> None:
    required_columns = {
        ("tracked_chats", "private_link_token"): "NO",
        ("chat_share_requests", "request_id"): "NO",
        ("chat_share_requests", "user_id"): "NO",
        ("chat_share_requests", "expires_at"): "NO",
        ("chat_share_requests", "used_at"): "YES",
        ("chat_share_requests", "created_at"): "NO",
        ("chat_share_requests", "idempotency_key"): "NO",
        ("chat_share_requests", "reserved_update_id"): "YES",
        ("chat_share_requests", "reserved_chat_id"): "YES",
        ("summary_selections", "selection_token"): "NO",
        ("summary_selections", "user_id"): "NO",
        ("summary_selections", "chat_id"): "NO",
        ("summary_selections", "expires_at"): "NO",
        ("summary_selections", "created_at"): "NO",
        ("summary_selections", "idempotency_key"): "NO",
        ("summary_selections", "reserved_update_id"): "YES",
        ("summary_selections", "consumed_at"): "YES",
    }
    missing_columns = [
        f"{table}.{column} ({nullable})"
        for (table, column), nullable in required_columns.items()
        if columns.get((table, column)) != nullable
    ]
    required_indexes = {
        ("tracked_chats", "uq_tracked_chats_private_link"): 0,
        ("chat_share_requests", "PRIMARY"): 0,
        ("chat_share_requests", "idx_chat_share_requests_user"): 1,
        ("chat_share_requests", "uq_chat_share_requests_idempotency"): 0,
        ("chat_share_requests", "idx_chat_share_requests_reservation"): 1,
        ("summary_selections", "PRIMARY"): 0,
        ("summary_selections", "idx_summary_selections_user"): 1,
        ("summary_selections", "uq_summary_selections_idempotency"): 0,
        ("summary_selections", "idx_summary_selections_reservation"): 1,
    }
    missing_indexes = [
        f"{table}.{index} ({non_unique})"
        for (table, index), non_unique in required_indexes.items()
        if indexes.get((table, index)) != non_unique
    ]
    if missing_columns or missing_indexes:
        details = ", ".join(missing_columns + missing_indexes)
        raise RuntimeError(f"Migration 002 schema verification failed: {details}")


def _to_mysql_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _from_mysql_datetime(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value.replace(tzinfo=timezone.utc)


def _balance_snapshot_from_row(row: dict[str, Any]) -> BalanceSnapshot:
    return BalanceSnapshot(
        provider=str(row["provider"]),
        currency=str(row["currency"]),
        total_balance=Decimal(str(row["total_balance"])),
        granted_balance=Decimal(str(row["granted_balance"])),
        topped_up_balance=Decimal(str(row["topped_up_balance"])),
        is_available=bool(row["is_available"]),
        report_week_start=_from_mysql_datetime(row["report_week_start"]),
        report_text=str(row["report_text"]),
        captured_at=_from_mysql_datetime(row["captured_at"]),
    )


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
