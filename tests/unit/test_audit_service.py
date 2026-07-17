from datetime import datetime, timezone

from app.jobs.audit import OperationalAuditService


NOW = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)


class FakeMaintenance:
    def __init__(self) -> None:
        self.purged = None

    def purge_expired(self, now, retention_days):
        self.purged = (now, retention_days)
        return {
            "archived_messages": 4,
            "archived_summaries": 2,
            "telegram_updates": 7,
            "summary_selections": 3,
            "chat_share_requests": 5,
        }

    def audit(self, now):
        return {
            "queued_updates": 1,
            "stale_updates": 0,
            "stale_publications": 2,
            "unverified_archives": 0,
        }


def test_purge_reports_each_retention_bucket() -> None:
    store = FakeMaintenance()
    service = OperationalAuditService(store, retention_days=30)

    report = service.purge(NOW)

    assert report["archived_messages"] == 4
    assert report["telegram_updates"] == 7
    assert report["chat_share_requests"] == 5
    assert store.purged == (NOW, 30)


def test_audit_marks_system_unhealthy_when_work_is_stale() -> None:
    service = OperationalAuditService(FakeMaintenance(), retention_days=30)

    report = service.audit(NOW)

    assert report["healthy"] is False
    assert report["stale_publications"] == 2
