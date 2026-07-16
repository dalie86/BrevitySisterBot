from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

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
    ) -> None:
        self.updates = updates
        self.handler = handler
        self.lease_seconds = lease_seconds
        self.retry_delay = retry_delay

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
        return self._process_claimed(update.update_id, update.payload, now)

    def recover(self, now: datetime, limit: int = 100) -> int:
        processed = 0
        for update in self.updates.claim_available(now, limit):
            if self._process_claimed(update.update_id, update.payload, now):
                processed += 1
        return processed

    def _process_claimed(self, update_id: int, payload: dict, now: datetime) -> bool:
        try:
            self.handler(payload)
        except Exception as exc:
            self.updates.retry(
                update_id,
                now,
                now + self.retry_delay,
                str(exc),
            )
            return False
        self.updates.complete(update_id, now)
        return True
