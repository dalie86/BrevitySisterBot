from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Protocol

from app.domain.models import TelegramMembership
from app.domain.ports import TelegramGateway

_current_update_id: ContextVar[int | None] = ContextVar("telegram_update_id", default=None)
_effect_counter: ContextVar[int] = ContextVar("telegram_effect_counter", default=0)


class TelegramEffectRepository(Protocol):
    def begin(
        self,
        effect_key: str,
        update_id: int,
        method: str,
        payload_hash: str,
        now: datetime,
    ) -> tuple[str, int | None] | None:
        raise NotImplementedError

    def mark_sent(
        self,
        effect_key: str,
        telegram_message_id: int,
        now: datetime,
    ) -> None:
        raise NotImplementedError

    def mark_review_required(self, effect_key: str, error: str, now: datetime) -> None:
        raise NotImplementedError


@contextmanager
def telegram_update_scope(update_id: int) -> Iterator[None]:
    update_token = _current_update_id.set(update_id)
    counter_token = _effect_counter.set(0)
    try:
        yield
    finally:
        _effect_counter.reset(counter_token)
        _current_update_id.reset(update_token)


class DurableTelegramGateway:
    def __init__(
        self,
        *,
        raw: TelegramGateway,
        effects: TelegramEffectRepository,
        now: Callable[[], datetime],
    ) -> None:
        self.raw = raw
        self.effects = effects
        self.now = now

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
        *,
        receiver_user_id: int | None = None,
    ) -> int:
        delivery_options = {}
        if receiver_user_id is not None:
            delivery_options["receiver_user_id"] = receiver_user_id
        update_id = _current_update_id.get()
        if update_id is None:
            return self.raw.send_message(chat_id, text, reply_markup, parse_mode, **delivery_options)

        sequence = _effect_counter.get()
        _effect_counter.set(sequence + 1)
        effect_key = f"update:{update_id}:send_message:{sequence}"
        payload = {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if receiver_user_id is not None:
            payload["receiver_user_id"] = receiver_user_id
        payload_hash = _payload_hash(payload)
        current = self.now()
        existing = self.effects.begin(
            effect_key,
            update_id,
            "send_message",
            payload_hash,
            current,
        )
        if existing is not None:
            status, telegram_message_id = existing
            if status == "sent" and telegram_message_id is not None:
                return telegram_message_id
            return 0

        try:
            telegram_message_id = self.raw.send_message(
                chat_id,
                text,
                reply_markup,
                parse_mode,
                **delivery_options,
            )
        except Exception as exc:
            self.effects.mark_review_required(effect_key, str(exc), self.now())
            raise
        self.effects.mark_sent(effect_key, telegram_message_id, self.now())
        return telegram_message_id

    def get_chat_member(self, chat_id: int, user_id: int) -> TelegramMembership:
        return self.raw.get_chat_member(chat_id, user_id)

    def answer_callback_query(self, callback_id: str) -> None:
        self.raw.answer_callback_query(callback_id)


def _payload_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
