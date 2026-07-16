import json
from datetime import datetime, timezone

from app.domain.models import StoredMessage
from app.repositories.mysql import (
    MySqlMessageRepository,
    MySqlUpdateRepository,
)


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
