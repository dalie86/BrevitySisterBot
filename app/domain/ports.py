from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from app.domain.models import (
    BalanceSnapshot,
    GeneratedSummary,
    LlmResult,
    LlmBalance,
    QueuedUpdate,
    ReservationResult,
    StoredMessage,
    SummaryKind,
    TelegramMembership,
    UsageTotals,
)


class Clock(Protocol):
    def now(self) -> datetime:
        raise NotImplementedError


class UpdateRepository(Protocol):
    def enqueue(self, update_id: int, payload: dict, now: datetime) -> bool:
        raise NotImplementedError

    def claim(
        self,
        update_id: int,
        now: datetime,
        lease_seconds: int,
    ) -> QueuedUpdate | None:
        raise NotImplementedError

    def claim_available(self, now: datetime, limit: int) -> list[QueuedUpdate]:
        raise NotImplementedError

    def complete(self, update_id: int, now: datetime) -> None:
        raise NotImplementedError

    def retry(
        self,
        update_id: int,
        now: datetime,
        available_at: datetime,
        error: str,
    ) -> None:
        raise NotImplementedError

    def fail(self, update_id: int, now: datetime, error: str) -> None:
        raise NotImplementedError


class MessageRepository(Protocol):
    def add(self, message: StoredMessage) -> bool:
        raise NotImplementedError

    def list_between(
        self,
        chat_id: int,
        start: datetime,
        end: datetime,
    ) -> Sequence[StoredMessage]:
        raise NotImplementedError


class ChatRepository(Protocol):
    def enable(self, chat_id: int, title: str, now: datetime) -> str:
        raise NotImplementedError

    def disable(self, chat_id: int, now: datetime) -> None:
        raise NotImplementedError

    def is_tracked(self, chat_id: int) -> bool:
        raise NotImplementedError

    def tracked_count(self) -> int:
        raise NotImplementedError

    def resolve_private_link(self, token: str) -> tuple[int, str] | None:
        raise NotImplementedError


class ChatShareRequestRepository(Protocol):
    def get_or_create(self, user_id: int, idempotency_key: str, now: datetime) -> int:
        raise NotImplementedError

    def reserve(
        self,
        request_id: int,
        user_id: int,
        chat_id: int,
        update_id: int,
        now: datetime,
    ) -> ReservationResult:
        raise NotImplementedError

    def finalize(
        self, request_id: int, user_id: int, update_id: int, now: datetime
    ) -> bool:
        raise NotImplementedError


class MemberRepository(Protocol):
    def observe(
        self,
        chat_id: int,
        user_id: int,
        username: str,
        full_name: str,
        now: datetime,
    ) -> None:
        raise NotImplementedError

    def confirm_summary_link(self, chat_id: int, user_id: int, now: datetime) -> None:
        raise NotImplementedError

    def remember_group(
        self,
        chat_id: int,
        user_id: int,
        username: str,
        full_name: str,
        now: datetime,
    ) -> None:
        raise NotImplementedError

    def record_membership(
        self,
        chat_id: int,
        user_id: int,
        status: str,
        is_member: bool,
        now: datetime,
    ) -> None:
        raise NotImplementedError

    def linked_groups(self, user_id: int) -> Sequence[tuple[int, str]]:
        raise NotImplementedError


class SummarySelectionRepository(Protocol):
    def get_or_create(
        self,
        user_id: int,
        chat_id: int,
        idempotency_key: str,
        now: datetime,
    ) -> str:
        raise NotImplementedError

    def reserve(
        self, token: str, user_id: int, update_id: int, now: datetime
    ) -> ReservationResult:
        raise NotImplementedError

    def finalize(self, token: str, user_id: int, update_id: int, now: datetime) -> bool:
        raise NotImplementedError


class SummaryRepository(Protocol):
    def find_by_source_hash(
        self,
        chat_id: int,
        source_hash: str,
        kind: SummaryKind,
    ) -> str | None:
        raise NotImplementedError

    def begin_generation(
        self,
        chat_id: int,
        summary_date: str,
        kind: SummaryKind,
        source_hash: str,
        requester_user_id: int | None,
        now: datetime,
    ) -> bool:
        raise NotImplementedError

    def save(
        self,
        summary: GeneratedSummary,
        requester_user_id: int | None,
        now: datetime,
    ) -> None:
        raise NotImplementedError


class TelegramGateway(Protocol):
    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ) -> int:
        raise NotImplementedError

    def get_chat_member(self, chat_id: int, user_id: int) -> TelegramMembership:
        raise NotImplementedError

    def answer_callback_query(self, callback_id: str) -> None:
        raise NotImplementedError


class LlmGateway(Protocol):
    def generate_summary(self, system_prompt: str, user_prompt: str) -> LlmResult:
        raise NotImplementedError


class BalanceGateway(Protocol):
    def get_balance(self) -> tuple[bool, tuple[LlmBalance, ...]]:
        raise NotImplementedError


class UsageRepository(Protocol):
    def aggregate(
        self, provider: str, start: datetime, end: datetime
    ) -> UsageTotals:
        raise NotImplementedError

    def previous_snapshot(
        self, provider: str, week_start: datetime
    ) -> BalanceSnapshot | None:
        raise NotImplementedError

    def get_or_create_snapshot(self, snapshot: BalanceSnapshot) -> BalanceSnapshot:
        raise NotImplementedError
