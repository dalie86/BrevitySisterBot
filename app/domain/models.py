from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable


class UpdateStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    RETRY = "retry"
    FAILED = "failed"


class SummaryKind(str, Enum):
    SCHEDULED = "scheduled"
    ON_DEMAND = "on_demand"


class SummaryStatus(str, Enum):
    PREPARING = "preparing"
    PREPARED = "prepared"
    SENDING = "sending"
    SENT = "sent"
    RETRY = "retry"
    FAILED = "failed"


class ReservationState(str, Enum):
    ACQUIRED = "acquired"
    RESUMED = "resumed"
    FINALIZED = "finalized"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ReservationResult:
    state: ReservationState
    chat_id: int | None = None


@dataclass(frozen=True)
class TelegramMembership:
    status: str
    is_member: bool

    @property
    def can_read(self) -> bool:
        return self.status in {"creator", "administrator", "member"} or (
            self.status == "restricted" and self.is_member
        )

    @property
    def is_admin(self) -> bool:
        return self.status in {"creator", "administrator"}


@dataclass(frozen=True)
class StoredMessage:
    telegram_message_id: int
    chat_id: int
    user_id: int | None
    username: str
    full_name: str
    text: str
    sent_at: datetime


@dataclass(frozen=True)
class QueuedUpdate:
    update_id: int
    payload: dict
    status: UpdateStatus
    attempt_count: int
    available_at: datetime
    claimed_at: datetime | None = None


@dataclass(frozen=True)
class LlmResult:
    content: str
    provider: str
    model: str
    duration_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class GeneratedSummary:
    chat_id: int
    summary_date: str
    kind: SummaryKind
    source_hash: str
    text: str
    status: SummaryStatus
    provider: str | None = None
    model: str | None = None
    fallback_type: str | None = None
    outcome: str = "generated"
    message_count: int = 0


def build_source_hash(
    messages: Iterable[tuple[int, str, datetime]],
) -> str:
    digest = hashlib.sha256()
    for message_id, text, sent_at in messages:
        digest.update(str(message_id).encode("utf-8"))
        digest.update(b"\0")
        digest.update(sent_at.isoformat().encode("utf-8"))
        digest.update(b"\0")
        digest.update(text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
