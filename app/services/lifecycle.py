from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, Sequence
from zoneinfo import ZoneInfo

from app.domain.models import SummaryKind
from app.domain.ports import TelegramGateway
from app.services.summaries import SummaryService


class LifecycleStore(Protocol):
    def enabled_chats(self) -> Sequence[tuple[int, str]]:
        raise NotImplementedError

    def record_skipped(
        self,
        chat_id: int,
        summary_date: str,
        message_count: int,
        now: datetime,
    ) -> None:
        raise NotImplementedError

    def enqueue_publication(
        self,
        chat_id: int,
        summary_date: str,
        source_hash: str,
        now: datetime,
    ) -> None:
        raise NotImplementedError

    def claim_publications(
        self,
        summary_date: str,
        now: datetime,
        limit: int,
    ) -> Sequence[tuple[int, int, str]]:
        raise NotImplementedError

    def mark_publication_sent(
        self,
        publication_id: int,
        telegram_message_id: int,
        now: datetime,
    ) -> None:
        raise NotImplementedError

    def mark_publication_review_required(
        self,
        publication_id: int,
        now: datetime,
        error: str,
    ) -> None:
        raise NotImplementedError

    def archive_day(self, summary_date: str, now: datetime, retention_days: int) -> int:
        raise NotImplementedError

    def cleanup_verified(self, summary_date: str, now: datetime) -> int:
        raise NotImplementedError

    def preview_cleanup(self, summary_date: str) -> int:
        raise NotImplementedError


@dataclass(frozen=True)
class JobReport:
    processed_chats: int = 0
    prepared_summaries: int = 0
    skipped_chats: int = 0
    sent_summaries: int = 0
    archived_messages: int = 0
    deleted_messages: int = 0
    errors: int = 0


class LifecycleService:
    def __init__(
        self,
        *,
        store: LifecycleStore,
        summaries: SummaryService,
        telegram: TelegramGateway,
        timezone_name: str,
        retention_days: int,
    ) -> None:
        self.store = store
        self.summaries = summaries
        self.telegram = telegram
        self.timezone = ZoneInfo(timezone_name)
        self.retention_days = retention_days

    def prepare_day(self, now: datetime) -> JobReport:
        summary_date = self._summary_date(now)
        prepared = 0
        skipped = 0
        errors = 0
        chats = list(self.store.enabled_chats())
        for chat_id, _title in chats:
            try:
                result = self.summaries.generate(
                    chat_id,
                    SummaryKind.SCHEDULED,
                    None,
                    now,
                )
                if result.outcome == "insufficient_messages":
                    self.store.record_skipped(
                        chat_id,
                        summary_date,
                        result.message_count,
                        now,
                    )
                    skipped += 1
                    continue
                self.store.enqueue_publication(
                    chat_id,
                    summary_date,
                    result.source_hash,
                    now,
                )
                prepared += 1
            except Exception:
                errors += 1
        return JobReport(
            processed_chats=len(chats),
            prepared_summaries=prepared,
            skipped_chats=skipped,
            errors=errors,
        )

    def publish_day(self, now: datetime) -> JobReport:
        publications = list(self.store.claim_publications(self._summary_date(now), now, limit=100))
        sent = 0
        errors = 0
        for publication_id, chat_id, text in publications:
            try:
                telegram_message_id = self.telegram.send_message(
                    chat_id,
                    text,
                    parse_mode="HTML",
                )
                self.store.mark_publication_sent(
                    publication_id,
                    telegram_message_id,
                    now,
                )
                sent += 1
            except Exception as exc:
                self.store.mark_publication_review_required(publication_id, now, str(exc))
                errors += 1
        return JobReport(
            processed_chats=len(publications),
            sent_summaries=sent,
            errors=errors,
        )

    def archive_day(self, now: datetime) -> JobReport:
        local_date = now.astimezone(self.timezone).date()
        count = 0
        for summary_date in (local_date - timedelta(days=1), local_date):
            count += self.store.archive_day(
                summary_date.isoformat(),
                now,
                self.retention_days,
            )
        return JobReport(archived_messages=count)

    def cleanup_day(self, now: datetime, *, dry_run: bool = False) -> JobReport:
        previous_date = now.astimezone(self.timezone).date() - timedelta(days=1)
        if dry_run:
            count = self.store.preview_cleanup(previous_date.isoformat())
        else:
            count = self.store.cleanup_verified(previous_date.isoformat(), now)
        return JobReport(deleted_messages=count)

    def _summary_date(self, now: datetime) -> str:
        return now.astimezone(self.timezone).date().isoformat()
