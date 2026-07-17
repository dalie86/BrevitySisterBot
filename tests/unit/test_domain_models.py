from datetime import datetime, timezone

from app.domain.models import (
    SummaryKind,
    SummaryStatus,
    UpdateStatus,
    build_source_hash,
)


def test_domain_status_values_are_stable() -> None:
    assert UpdateStatus.PENDING.value == "pending"
    assert UpdateStatus.RETRY.value == "retry"
    assert SummaryKind.ON_DEMAND.value == "on_demand"
    assert SummaryStatus.PREPARED.value == "prepared"


def test_source_hash_is_stable_and_sensitive_to_message_content() -> None:
    sent_at = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    first = build_source_hash([(10, "Привет", sent_at)])
    same = build_source_hash([(10, "Привет", sent_at)])
    changed = build_source_hash([(10, "Другой текст", sent_at)])

    assert first == same
    assert first != changed
    assert len(first) == 64
