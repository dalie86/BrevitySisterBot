from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from app.domain.models import LlmResult, QueuedUpdate, StoredMessage


class Clock(Protocol):
    def now(self) -> datetime:
        raise NotImplementedError


class UpdateRepository(Protocol):
    def enqueue(self, update_id: int, payload: dict) -> bool:
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


class SummaryRepository(Protocol):
    def find_by_source_hash(self, chat_id: int, source_hash: str) -> str | None:
        raise NotImplementedError


class TelegramGateway(Protocol):
    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict | None = None,
    ) -> int:
        raise NotImplementedError

    def get_chat_member(self, chat_id: int, user_id: int) -> str:
        raise NotImplementedError


class LlmGateway(Protocol):
    def generate_summary(self, system_prompt: str, user_prompt: str) -> LlmResult:
        raise NotImplementedError
