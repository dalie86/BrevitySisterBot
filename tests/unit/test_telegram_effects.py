from datetime import datetime, timezone

from app.services.telegram_effects import (
    DurableTelegramGateway,
    _payload_hash,
    telegram_update_scope,
)


class FakeEffects:
    def __init__(self) -> None:
        self.records = {}
        self.payload_hashes = []
        self.expected_payload_hashes = {}

    def begin(self, effect_key, update_id, method, payload_hash, now):
        self.payload_hashes.append(payload_hash)
        expected_payload_hash = self.expected_payload_hashes.get(effect_key)
        if expected_payload_hash is not None and payload_hash != expected_payload_hash:
            raise RuntimeError("payload changed")
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

    def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append((chat_id, text, reply_markup, parse_mode))
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
    assert raw.sent == [(42, "Готово", None, None)]


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


def test_plain_text_recovery_preserves_legacy_payload_hash() -> None:
    raw = FakeTelegram()
    effects = FakeEffects()
    effect_key = "update:127:send_message:0"
    legacy_payload_hash = _payload_hash(
        {"chat_id": 42, "text": "Готово", "reply_markup": None}
    )
    effects.expected_payload_hashes[effect_key] = legacy_payload_hash
    effects.records[effect_key] = ("sent", 77)
    gateway = DurableTelegramGateway(
        raw=raw,
        effects=effects,
        now=lambda: datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )

    with telegram_update_scope(127):
        recovered_id = gateway.send_message(42, "Готово")

    assert recovered_id == 77
    assert effects.payload_hashes == [legacy_payload_hash]
    assert raw.sent == []


def test_recovered_html_send_uses_same_payload_hash() -> None:
    raw = FakeTelegram()
    effects = FakeEffects()
    gateway = DurableTelegramGateway(
        raw=raw,
        effects=effects,
        now=lambda: datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )

    with telegram_update_scope(124):
        first_id = gateway.send_message(42, "<b>Готово</b>", parse_mode="HTML")
    with telegram_update_scope(124):
        recovered_id = gateway.send_message(42, "<b>Готово</b>", parse_mode="HTML")

    assert first_id == 77
    assert recovered_id == 77
    assert raw.sent == [(42, "<b>Готово</b>", None, "HTML")]
    assert len(effects.payload_hashes) == 2
    assert effects.payload_hashes[0] == effects.payload_hashes[1]


def test_parse_mode_changes_durable_payload_hash() -> None:
    raw = FakeTelegram()
    effects = FakeEffects()
    gateway = DurableTelegramGateway(
        raw=raw,
        effects=effects,
        now=lambda: datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )

    with telegram_update_scope(125):
        gateway.send_message(42, "Готово")
    with telegram_update_scope(126):
        gateway.send_message(42, "Готово", parse_mode="HTML")

    assert effects.payload_hashes[0] != effects.payload_hashes[1]


def test_ephemeral_delivery_is_durable_and_hash_includes_recipient() -> None:
    class EphemeralTelegram(FakeTelegram):
        def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, *, receiver_user_id=None):
            self.sent.append((chat_id, text, receiver_user_id))
            return 987

    raw = EphemeralTelegram()
    effects = FakeEffects()
    gateway = DurableTelegramGateway(raw=raw, effects=effects, now=lambda: datetime.now(timezone.utc))

    for _ in range(2):
        with telegram_update_scope(123):
            assert gateway.send_message(-1001, "Готово", receiver_user_id=42) == 987
    with telegram_update_scope(124):
        gateway.send_message(-1001, "Готово", receiver_user_id=43)

    assert raw.sent == [(-1001, "Готово", 42), (-1001, "Готово", 43)]
    assert effects.payload_hashes[0] == effects.payload_hashes[1]
    assert effects.payload_hashes[0] != effects.payload_hashes[2]

    gateway.send_message(-1001, "Без update", receiver_user_id=44)
    assert raw.sent[-1] == (-1001, "Без update", 44)
