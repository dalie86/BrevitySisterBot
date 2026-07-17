import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pymysql

from app.domain.models import StoredMessage
from app.repositories.mysql import (
    MySqlMaintenanceStore,
    MySqlMemberRepository,
    MySqlMessageRepository,
    MySqlTelegramEffectRepository,
    MySqlTrackedChatRepository,
    MySqlUpdateRepository,
)
from app.repositories import mysql


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


class RecordingCursor:
    def __init__(
        self,
        rowcount: int = 1,
        fetchone_result: dict | None = None,
        fetchall_result: list[dict] | None = None,
    ) -> None:
        self.rowcount = rowcount
        self.executions: list[tuple[str, tuple]] = []
        self.fetchone_result = fetchone_result
        self.fetchall_result = fetchall_result or []

    def execute(self, sql: str, params: tuple) -> None:
        self.executions.append((sql, params))

    def fetchone(self) -> dict | None:
        return self.fetchone_result

    def fetchall(self) -> list[dict]:
        return self.fetchall_result


class RecordingConnection:
    def __init__(
        self,
        rowcount: int = 1,
        fetchone_result: dict | None = None,
        fetchall_result: list[dict] | None = None,
    ) -> None:
        self.cursor_instance = RecordingCursor(rowcount, fetchone_result, fetchall_result)
        self.commits = 0
        self.closed = False

    def cursor(self) -> RecordingCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


def test_enqueue_serializes_payload_and_uses_duplicate_safe_insert() -> None:
    connection = RecordingConnection()
    repository = MySqlUpdateRepository(lambda: connection)

    inserted = repository.enqueue(123, {"update_id": 123, "message": {"text": "Привет"}})

    assert inserted is True
    sql, params = connection.cursor_instance.executions[0]
    assert "INSERT IGNORE INTO telegram_updates" in sql
    assert json.loads(params[1])["update_id"] == 123
    assert connection.commits == 1
    assert connection.closed is True


def test_add_message_uses_chat_and_telegram_message_identity() -> None:
    connection = RecordingConnection()
    repository = MySqlMessageRepository(lambda: connection)
    message = StoredMessage(
        telegram_message_id=7,
        chat_id=-1001,
        user_id=42,
        username="@pavel",
        full_name="Pavel",
        text="Привет",
        sent_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )

    inserted = repository.add(message)

    assert inserted is True
    sql, params = connection.cursor_instance.executions[0]
    assert "INSERT IGNORE INTO messages" in sql
    assert params[0:2] == (-1001, 7)
    assert connection.commits == 1
    assert connection.closed is True


def test_claim_marks_update_processing_and_returns_domain_record() -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    connection = RecordingConnection(
        fetchone_result={
            "update_id": 123,
            "payload_json": '{"update_id":123}',
            "status": "processing",
            "attempt_count": 1,
            "available_at": now.replace(tzinfo=None),
            "claimed_at": now.replace(tzinfo=None),
        }
    )
    repository = MySqlUpdateRepository(lambda: connection)

    claimed = repository.claim(123, now, lease_seconds=60)

    assert claimed is not None
    assert claimed.update_id == 123
    assert claimed.payload == {"update_id": 123}
    assert claimed.attempt_count == 1
    assert "UPDATE telegram_updates" in connection.cursor_instance.executions[0][0]
    assert connection.commits == 1
    update_params = connection.cursor_instance.executions[0][1]
    assert update_params[3] == datetime(2026, 7, 16, 11, 59)
    assert connection.closed is True


def test_claim_available_includes_expired_processing_leases() -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    connection = RecordingConnection(fetchall_result=[])
    repository = MySqlUpdateRepository(lambda: connection)

    repository.claim_available(now, limit=10)

    sql, params = connection.cursor_instance.executions[0]
    assert "status = 'processing'" in sql
    assert params[0] == datetime(2026, 7, 16, 11, 55)


def test_list_between_converts_mysql_rows_to_messages() -> None:
    sent_at = datetime(2026, 7, 16, 12, 0)
    connection = RecordingConnection(
        fetchall_result=[
            {
                "telegram_message_id": 7,
                "chat_id": -1001,
                "user_id": 42,
                "username": "@pavel",
                "full_name": "Pavel",
                "text": "Привет",
                "sent_at": sent_at,
            }
        ]
    )
    repository = MySqlMessageRepository(lambda: connection)

    messages = repository.list_between(
        -1001,
        datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 17, 0, 0, tzinfo=timezone.utc),
    )

    assert len(messages) == 1
    assert messages[0].telegram_message_id == 7
    assert messages[0].sent_at.tzinfo == timezone.utc
    assert "ORDER BY sent_at" in connection.cursor_instance.executions[0][0]
    assert connection.closed is True


def test_member_repository_returns_linked_groups() -> None:
    connection = RecordingConnection(
        fetchall_result=[
            {"chat_id": -1001, "title": "Group 1"},
            {"chat_id": -1002, "title": "Group 2"},
        ]
    )
    repository = MySqlMemberRepository(lambda: connection)

    groups = repository.linked_groups(42)

    assert groups == [(-1001, "Group 1"), (-1002, "Group 2")]
    assert "summary_linked_at IS NOT NULL" in connection.cursor_instance.executions[0][0]
    assert connection.closed is True


def test_chat_repository_preserves_private_token_on_reenable() -> None:
    existing_token = "a" * 32
    connection = RecordingConnection(
        fetchone_result={
            "chat_id": -1001,
            "title": "Demo",
            "private_link_token": existing_token,
        }
    )
    repository = MySqlTrackedChatRepository(lambda: connection)

    with patch("app.repositories.mysql.secrets.token_hex", return_value="b" * 32):
        token = repository.enable(-1001, "Demo", NOW)

    assert token == existing_token
    insert_sql, insert_params = connection.cursor_instance.executions[0]
    assert "private_link_token" in insert_sql
    assert "private_link_token = VALUES(private_link_token)" not in insert_sql
    assert "b" * 32 in insert_params
    select_sql, select_params = connection.cursor_instance.executions[1]
    assert "SELECT private_link_token" in select_sql
    assert select_params == (-1001,)
    assert connection.commits == 1


def test_chat_repository_resolves_private_token() -> None:
    connection = RecordingConnection(
        fetchone_result={
            "chat_id": -1001,
            "title": "Demo",
            "private_link_token": "a" * 32,
        }
    )
    repository = MySqlTrackedChatRepository(lambda: connection)

    assert repository.resolve_private_link("a" * 32) == (-1001, "Demo")

    sql, params = connection.cursor_instance.executions[0]
    assert "private_link_token = %s" in sql
    assert params == ("a" * 32,)


class DuplicateOnceCursor(RecordingCursor):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def execute(self, sql: str, params: tuple) -> None:
        super().execute(sql, params)
        self.attempts += 1
        if self.attempts == 1:
            raise pymysql.err.IntegrityError(1062, "Duplicate entry")


def test_share_request_retries_duplicate_and_expires_after_one_hour() -> None:
    connection = RecordingConnection()
    connection.cursor_instance = DuplicateOnceCursor()
    repository = mysql.MySqlChatShareRequestRepository(lambda: connection)

    with patch(
        "app.repositories.mysql.secrets.randbelow",
        side_effect=[0, 2**32 - 1],
    ):
        request_id = repository.create(42, NOW)

    assert request_id == 2**31 - 1
    assert len(connection.cursor_instance.executions) == 2
    _, first_params = connection.cursor_instance.executions[0]
    _, second_params = connection.cursor_instance.executions[1]
    assert first_params[0] == -(2**31)
    assert second_params == (
        2**31 - 1,
        42,
        NOW.replace(tzinfo=None) + timedelta(hours=1),
        NOW.replace(tzinfo=None),
    )
    assert connection.commits == 1


def test_share_request_is_consumed_once() -> None:
    connection = RecordingConnection(rowcount=1)
    repository = mysql.MySqlChatShareRequestRepository(lambda: connection)

    assert repository.consume(-77, 42, NOW) is True

    sql, params = connection.cursor_instance.executions[0]
    assert "used_at IS NULL" in sql
    assert "expires_at >=" in sql
    assert params == (NOW.replace(tzinfo=None), -77, 42, NOW.replace(tzinfo=None))


def test_remember_group_links_active_member() -> None:
    connection = RecordingConnection()
    repository = MySqlMemberRepository(lambda: connection)

    repository.remember_group(-1001, 42, "pavel", "Pavel", NOW)

    sql, params = connection.cursor_instance.executions[0]
    assert "INSERT INTO chat_members" in sql
    assert "summary_linked_at = VALUES(summary_linked_at)" in sql
    assert "last_membership_check_at = VALUES(last_membership_check_at)" in sql
    assert "last_membership_status = VALUES(last_membership_status)" in sql
    assert params[-1] == "member"


def test_departure_revokes_but_does_not_insert_unknown_member() -> None:
    connection = RecordingConnection(rowcount=1)
    repository = MySqlMemberRepository(lambda: connection)

    repository.record_membership(-1001, 42, "left", False, NOW)

    sql, params = connection.cursor_instance.executions[0]
    assert "UPDATE chat_members" in sql
    assert "summary_linked_at = CASE" in sql
    assert "ELSE NULL END" in sql
    assert "INSERT" not in sql
    assert params == (NOW.replace(tzinfo=None), "left", 0, -1001, 42)


def test_maintenance_purges_expired_share_requests() -> None:
    connection = RecordingConnection()
    repository = MySqlMaintenanceStore(lambda: connection)

    deleted = repository.purge_expired(NOW, retention_days=30)

    assert deleted["chat_share_requests"] == 1
    statements = {
        sql.strip(): params for sql, params in connection.cursor_instance.executions
    }
    assert (
        "DELETE FROM chat_share_requests WHERE expires_at < %s" in statements
    )
    assert statements[
        "DELETE FROM chat_share_requests WHERE expires_at < %s"
    ] == (NOW.replace(tzinfo=None),)


def test_telegram_effect_is_persisted_before_external_send() -> None:
    connection = RecordingConnection(rowcount=1)
    repository = MySqlTelegramEffectRepository(lambda: connection)

    existing = repository.begin(
        "update:123:send_message:0",
        123,
        "send_message",
        "a" * 64,
        datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )

    assert existing is None
    assert "status" in connection.cursor_instance.executions[0][0]
    assert "'sending'" in connection.cursor_instance.executions[0][0]
    assert connection.commits == 1
    assert connection.closed is True
