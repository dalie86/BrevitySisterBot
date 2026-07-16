from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from app.domain.models import QueuedUpdate
from app.domain.ports import UpdateRepository

UpdateHandler = Callable[[dict], None]


@dataclass(frozen=True)
class AcceptResult:
    update_id: int
    inserted: bool
    processed: bool


class UpdateService:
    def __init__(
        self,
        updates: UpdateRepository,
        handler: UpdateHandler,
        *,
        lease_seconds: int = 300,
        retry_delay: timedelta = timedelta(minutes=5),
        max_attempts: int = 5,
    ) -> None:
        self.updates = updates
        self.handler = handler
        self.lease_seconds = lease_seconds
        self.retry_delay = retry_delay
        self.max_attempts = max_attempts

    def accept(self, payload: dict, now: datetime) -> AcceptResult:
        update_id = int(payload["update_id"])
        inserted = self.updates.enqueue(update_id, payload)
        if not inserted:
            return AcceptResult(update_id=update_id, inserted=False, processed=False)
        processed = self.process(update_id, now)
        return AcceptResult(update_id=update_id, inserted=True, processed=processed)

    def process(self, update_id: int, now: datetime) -> bool:
        update = self.updates.claim(update_id, now, self.lease_seconds)
        if update is None:
            return False
        return self.process_claimed(update, now)

    def recover(self, now: datetime, limit: int = 100) -> int:
        processed = 0
        for update in self.updates.claim_available(now, limit):
            if self.process_claimed(update, now):
                processed += 1
        return processed

    def process_claimed(self, update: QueuedUpdate, now: datetime) -> bool:
        try:
            self.handler(update.payload)
        except Exception as exc:
            if update.attempt_count >= self.max_attempts:
                self.updates.fail(update.update_id, now, str(exc))
                return False
            self.updates.retry(
                update.update_id,
                now,
                now + self.retry_delay,
                str(exc),
            )
            return False
        self.updates.complete(update.update_id, now)
        return True
