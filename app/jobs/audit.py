from __future__ import annotations

from datetime import datetime
from typing import Protocol


class MaintenanceStore(Protocol):
    def purge_expired(self, now: datetime, retention_days: int) -> dict[str, int]:
        raise NotImplementedError

    def audit(self, now: datetime) -> dict[str, int]:
        raise NotImplementedError


class OperationalAuditService:
    def __init__(self, store: MaintenanceStore, retention_days: int) -> None:
        self.store = store
        self.retention_days = retention_days

    def purge(self, now: datetime) -> dict[str, int]:
        return self.store.purge_expired(now, self.retention_days)

    def audit(self, now: datetime) -> dict[str, int | bool]:
        counters = self.store.audit(now)
        unhealthy_keys = (
            "stale_updates",
            "stale_publications",
            "unverified_archives",
            "failed_updates",
            "review_required_effects",
        )
        return {
            **counters,
            "healthy": not any(counters.get(key, 0) for key in unhealthy_keys),
        }
