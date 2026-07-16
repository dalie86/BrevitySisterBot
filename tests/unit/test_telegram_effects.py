from datetime import datetime, timezone

from app.services.telegram_effects import (
    DurableTelegramGateway,
    telegram_update_scope,
)


class FakeEffects:
    def __init__(self) -> None:
        self.records = {}

    def begin(self, effect_key, update_id, method, payload_hash, now):
        record = self.records.get(effect_key)
        if record is not None:
            return record
        self.records[effect_key] = ("sending", None)
        return None

    def mark_sent(self, effect_key, telegram_message_id, now):
        self.records[effect_key] = ("sent", telegram_message_id)

    def mark_review_required(self, effect_key, error, now):
        self.records[effect_key] = ("review_required", None)


class FakeTelegram:
    def __init__(self) -> None:
        self.sent = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return 77

    def get_chat_member(self, chat_id, user_id):
        return "member"

    def answer_callback_query(self, callback_id):
        return None


def test_recovered_update_does_not_repeat_started_send() -> None:
    raw = FakeTelegram()
    effects = FakeEffects()
    gateway = DurableTelegramGateway(
        raw=raw,
        effects=effects,
        now=lambda: datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )

    with telegram_update_scope(123):
        first_id = gateway.send_message(42, "Готово")
    with telegram_update_scope(123):
        recovered_id = gateway.send_message(42, "Готово")

    assert first_id == 77
    assert recovered_id == 77
    assert raw.sent == [(42, "Готово", None)]


def test_ambiguous_started_effect_is_skipped_on_recovery() -> None:
    raw = FakeTelegram()
    effects = FakeEffects()
    effects.records["update:123:send_message:0"] = ("sending", None)
    gateway = DurableTelegramGateway(
        raw=raw,
        effects=effects,
        now=lambda: datetime.now(timezone.utc),
    )

    with telegram_update_scope(123):
        message_id = gateway.send_message(42, "Готово")

    assert message_id == 0
    assert raw.sent == []
