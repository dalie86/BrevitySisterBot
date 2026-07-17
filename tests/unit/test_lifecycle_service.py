from datetime import datetime, timezone

from app.services.lifecycle import LifecycleService


NOW = datetime(2026, 7, 16, 18, 15, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self) -> None:
        self.enabled = [(-1001, "Group")]
        self.publications = [(7, -1001, "готовая сводка")]
        self.sent_ids = set()
        self.archive_verified = False
        self.deleted = 0
        self.review_required = []

    def enabled_chats(self):
        return list(self.enabled)

    def record_prepared(self, summary, now):
        return None

    def record_skipped(self, chat_id, summary_date, message_count, now):
        return None

    def enqueue_publication(self, chat_id, summary_date, source_hash, now):
        return None

    def claim_publications(self, summary_date, now, limit):
        return [item for item in self.publications if item[0] not in self.sent_ids]

    def mark_publication_sent(self, publication_id, telegram_message_id, now):
        self.sent_ids.add(publication_id)

    def mark_publication_review_required(self, publication_id, now, error):
        self.review_required.append((publication_id, error))

    def archive_day(self, summary_date, now, retention_days):
        self.archive_verified = True
        return 5

    def cleanup_verified(self, summary_date, now):
        if not self.archive_verified:
            return 0
        self.deleted = 5
        return self.deleted

    def preview_cleanup(self, summary_date):
        return 5 if self.archive_verified else 0


class FakeTelegram:
    def __init__(self) -> None:
        self.sent = []
        self.error = None

    def send_message(self, chat_id, text, reply_markup=None):
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text))
        return 100 + len(self.sent)


class FakeSummaries:
    def generate(self, chat_id, kind, requester_user_id, now):
        raise AssertionError("not used by these tests")


def make_service(store=None):
    selected_store = store or FakeStore()
    telegram = FakeTelegram()
    return (
        LifecycleService(
            store=selected_store,
            summaries=FakeSummaries(),
            telegram=telegram,
            timezone_name="Europe/Moscow",
            retention_days=30,
        ),
        selected_store,
        telegram,
    )


def test_repeated_publish_sends_once() -> None:
    lifecycle, _, telegram = make_service()

    first = lifecycle.publish_day(NOW)
    second = lifecycle.publish_day(NOW)

    assert first.sent_summaries == 1
    assert second.sent_summaries == 0
    assert telegram.sent == [(-1001, "готовая сводка")]


def test_cleanup_refuses_unverified_archive() -> None:
    lifecycle, store, _ = make_service()

    report = lifecycle.cleanup_day(NOW)

    assert report.deleted_messages == 0
    assert store.deleted == 0


def test_archive_then_cleanup_deletes_only_verified_rows() -> None:
    lifecycle, _, _ = make_service()

    archived = lifecycle.archive_day(NOW)
    cleaned = lifecycle.cleanup_day(NOW)

    assert archived.archived_messages == 10
    assert cleaned.deleted_messages == 5


def test_cleanup_dry_run_does_not_delete_rows() -> None:
    lifecycle, store, _ = make_service()
    store.archive_verified = True

    report = lifecycle.cleanup_day(NOW, dry_run=True)

    assert report.deleted_messages == 5
    assert store.deleted == 0


def test_ambiguous_publication_failure_requires_review_not_retry() -> None:
    lifecycle, store, telegram = make_service()
    telegram.error = TimeoutError("ambiguous")

    report = lifecycle.publish_day(NOW)

    assert report.errors == 1
    assert store.review_required == [(7, "ambiguous")]
    assert store.sent_ids == set()
