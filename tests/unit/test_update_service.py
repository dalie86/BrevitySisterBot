from datetime import datetime, timedelta, timezone

from app.domain.models import QueuedUpdate, UpdateStatus
from app.services.updates import UpdateService


class FakeUpdateRepository:
    def __init__(self) -> None:
        self.records: dict[int, QueuedUpdate] = {}
        self.retries: list[tuple[int, datetime, str]] = []
        self.completed: list[int] = []

    def enqueue(self, update_id: int, payload: dict) -> bool:
        if update_id in self.records:
            return False
        now = datetime.now(timezone.utc)
        self.records[update_id] = QueuedUpdate(
            update_id=update_id,
            payload=payload,
            status=UpdateStatus.PENDING,
            attempt_count=0,
            available_at=now,
        )
        return True

    def claim(self, update_id: int, now: datetime, lease_seconds: int) -> QueuedUpdate | None:
        record = self.records.get(update_id)
        if record is None or record.status == UpdateStatus.COMPLETED:
            return None
        claimed = QueuedUpdate(
            update_id=record.update_id,
            payload=record.payload,
            status=UpdateStatus.PROCESSING,
            attempt_count=record.attempt_count + 1,
            available_at=record.available_at,
            claimed_at=now,
        )
        self.records[update_id] = claimed
        return claimed

    def complete(self, update_id: int, now: datetime) -> None:
        self.completed.append(update_id)
        record = self.records[update_id]
        self.records[update_id] = QueuedUpdate(
            update_id=record.update_id,
            payload=record.payload,
            status=UpdateStatus.COMPLETED,
            attempt_count=record.attempt_count,
            available_at=record.available_at,
            claimed_at=record.claimed_at,
        )

    def retry(
        self,
        update_id: int,
        now: datetime,
        available_at: datetime,
        error: str,
    ) -> None:
        self.retries.append((update_id, available_at, error))
        record = self.records[update_id]
        self.records[update_id] = QueuedUpdate(
            update_id=record.update_id,
            payload=record.payload,
            status=UpdateStatus.RETRY,
            attempt_count=record.attempt_count,
            available_at=available_at,
        )


def test_accept_persists_before_processing() -> None:
    repository = FakeUpdateRepository()
    observed = []

    def handler(payload: dict) -> None:
        observed.append(repository.records[int(payload["update_id"])].status)

    service = UpdateService(repository, handler)
    result = service.accept({"update_id": 101}, datetime(2026, 7, 16, tzinfo=timezone.utc))

    assert result.inserted is True
    assert result.processed is True
    assert observed == [UpdateStatus.PROCESSING]
    assert repository.completed == [101]


def test_accept_schedules_retry_after_handler_failure() -> None:
    repository = FakeUpdateRepository()

    def handler(payload: dict) -> None:
        raise RuntimeError("interrupted")

    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    result = UpdateService(repository, handler).accept({"update_id": 102}, now)

    assert result.inserted is True
    assert result.processed is False
    assert repository.records[102].status == UpdateStatus.RETRY
    assert repository.retries == [(102, now + timedelta(minutes=5), "interrupted")]


def test_duplicate_accept_does_not_process_twice() -> None:
    repository = FakeUpdateRepository()
    calls = []
    service = UpdateService(repository, lambda payload: calls.append(payload["update_id"]))
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)

    first = service.accept({"update_id": 103}, now)
    duplicate = service.accept({"update_id": 103}, now)

    assert first.processed is True
    assert duplicate.inserted is False
    assert calls == [103]
