from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.content import BotCopy
from app.domain.models import GeneratedSummary, SummaryKind, SummaryStatus, TelegramMembership
from app.integrations.telegram import IncomingCallback, IncomingChatShared, IncomingMessage, IncomingUpdate
from app.services.on_demand import OnDemandSummaryHandler
from app.services.telegram_effects import DurableTelegramGateway, telegram_update_scope


NOW = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
BOT_USER_ID = 777
COPY = BotCopy.load(Path("content/bot_copy.ru.toml"))


class Chats:
    def __init__(self) -> None:
        self.tracked = {-1001: "One", -1002: "Two"}

    def is_tracked(self, chat_id):
        return chat_id in self.tracked

    def tracked_count(self):
        return len(self.tracked)

    def resolve_private_link(self, token):
        return None


class Members:
    def __init__(self, groups=()) -> None:
        self.groups = list(groups)
        self.remembered = []

    def linked_groups(self, user_id):
        return list(self.groups)

    def remember_group(self, chat_id, user_id, username, full_name, now):
        self.remembered.append((chat_id, user_id))

    def record_membership(self, chat_id, user_id, status, is_member, now):
        return None


class ShareRequests:
    def __init__(self) -> None:
        self.by_key = {}
        self.requests = {}

    def get_or_create(self, user_id, idempotency_key, now):
        if idempotency_key not in self.by_key:
            request_id = -77 + len(self.by_key)
            self.by_key[idempotency_key] = request_id
            self.requests[(request_id, user_id)] = {
                "owner": None,
                "chat_id": None,
                "finalized": False,
            }
        return self.by_key[idempotency_key]

    def reserve(self, request_id, user_id, chat_id, update_id, now):
        item = self.requests.get((request_id, user_id))
        if item is None:
            return SimpleNamespace(state="unavailable", chat_id=None)
        if item["owner"] == update_id and item["finalized"]:
            return SimpleNamespace(state="finalized", chat_id=item["chat_id"])
        if item["owner"] is None:
            item["owner"] = update_id
            item["chat_id"] = chat_id
            return SimpleNamespace(state="acquired", chat_id=chat_id)
        if item["owner"] == update_id and item["chat_id"] == chat_id:
            return SimpleNamespace(state="resumed", chat_id=chat_id)
        return SimpleNamespace(state="unavailable", chat_id=None)

    def finalize(self, request_id, user_id, update_id, now):
        item = self.requests[(request_id, user_id)]
        if item["owner"] != update_id:
            return False
        item["finalized"] = True
        return True


class Selections:
    def __init__(self) -> None:
        self.by_key = {}
        self.items = {}

    def get_or_create(self, user_id, chat_id, idempotency_key, now):
        if idempotency_key not in self.by_key:
            token = f"opaque-{len(self.by_key) + 1}"
            self.by_key[idempotency_key] = token
            self.items[(token, user_id)] = {
                "chat_id": chat_id,
                "owner": None,
                "finalized": False,
            }
        return self.by_key[idempotency_key]

    def reserve(self, token, user_id, update_id, now):
        item = self.items.get((token, user_id))
        if item is None:
            return SimpleNamespace(state="unavailable", chat_id=None)
        if item["owner"] == update_id and item["finalized"]:
            return SimpleNamespace(state="finalized", chat_id=item["chat_id"])
        if item["owner"] is None:
            item["owner"] = update_id
            return SimpleNamespace(state="acquired", chat_id=item["chat_id"])
        if item["owner"] == update_id:
            return SimpleNamespace(state="resumed", chat_id=item["chat_id"])
        return SimpleNamespace(state="unavailable", chat_id=None)

    def finalize(self, token, user_id, update_id, now):
        item = self.items[(token, user_id)]
        if item["owner"] != update_id:
            return False
        item["finalized"] = True
        return True


class RawTelegram:
    def __init__(self) -> None:
        self.sent = []
        self.answered = []

    def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append((chat_id, text, reply_markup, parse_mode))
        return len(self.sent)

    def get_chat_member(self, chat_id, user_id):
        if user_id == BOT_USER_ID:
            return TelegramMembership("administrator", True)
        return TelegramMembership("member", True)

    def answer_callback_query(self, callback_id):
        self.answered.append(callback_id)


class Effects:
    def __init__(self) -> None:
        self.records = {}
        self.hashes = []

    def begin(self, effect_key, update_id, method, payload_hash, now):
        self.hashes.append(payload_hash)
        existing = self.records.get(effect_key)
        if existing is not None and existing[2] != payload_hash:
            raise RuntimeError("Telegram effect payload changed for the same update")
        if existing is not None:
            return existing[:2]
        self.records[effect_key] = ("sending", None, payload_hash)
        return None

    def mark_sent(self, effect_key, telegram_message_id, now):
        self.records[effect_key] = ("sent", telegram_message_id, self.records[effect_key][2])

    def mark_review_required(self, effect_key, error, now):
        self.records[effect_key] = ("review_required", None, self.records[effect_key][2])


class Summaries:
    def __init__(self) -> None:
        self.calls = []
        self.fail_once = False

    def generate(self, chat_id, kind, requester_user_id, now):
        self.calls.append((chat_id, requester_user_id))
        if self.fail_once:
            self.fail_once = False
            raise TimeoutError("transient generation failure")
        return GeneratedSummary(
            chat_id=chat_id,
            summary_date="2026-07-16",
            kind=SummaryKind.ON_DEMAND,
            source_hash="hash",
            text="<b>done</b>",
            status=SummaryStatus.PREPARED,
        )


def make_handler(groups=()):
    raw = RawTelegram()
    effects = Effects()
    telegram = DurableTelegramGateway(raw=raw, effects=effects, now=lambda: NOW)
    shares = ShareRequests()
    selections = Selections()
    summaries = Summaries()
    handler = OnDemandSummaryHandler(
        chats=Chats(),
        members=Members(groups),
        share_requests=shares,
        selections=selections,
        telegram=telegram,
        summaries=summaries,
        copy=COPY,
        bot_user_id=BOT_USER_ID,
        now=lambda: NOW,
    )
    return handler, raw, effects, shares, selections, summaries


def private_summary(update_id=10):
    return IncomingUpdate(
        update_id=update_id,
        message=IncomingMessage(
            message_id=1,
            chat_id=42,
            chat_type="private",
            chat_title="Private",
            user_id=42,
            username="pavel",
            full_name="Pavel",
            sender_is_bot=False,
            text="/summary",
            sent_at=NOW,
        ),
    )


def callback(token, update_id):
    return IncomingUpdate(
        update_id=update_id,
        callback=IncomingCallback(
            callback_id=f"callback-{update_id}",
            user_id=42,
            chat_id=42,
            data=f"summary_group:{token}",
        ),
    )


def shared(request_id, update_id, chat_id=-1001):
    return IncomingUpdate(
        update_id=update_id,
        chat_shared=IncomingChatShared(
            request_id=request_id,
            shared_chat_id=chat_id,
            title="One",
            user_id=42,
            private_chat_id=42,
        ),
    )


def dispatch_twice(handler, update):
    with telegram_update_scope(update.update_id):
        handler(update)
    with telegram_update_scope(update.update_id):
        handler(update)


def test_native_picker_replay_has_stable_request_id_and_payload_hash() -> None:
    handler, raw, effects, shares, _, _ = make_handler()
    update = private_summary(10)

    dispatch_twice(handler, update)

    assert len(raw.sent) == 1
    assert len(shares.by_key) == 1
    assert effects.hashes[0] == effects.hashes[1]
    assert raw.sent[0][2]["keyboard"][0][0]["request_chat"]["request_id"] == -77

    with telegram_update_scope(12):
        handler(private_summary(12))

    assert len(raw.sent) == 2
    assert raw.sent[1][2]["keyboard"][0][0]["request_chat"]["request_id"] != -77


def test_known_group_picker_replay_has_stable_tokens_and_payload_hash() -> None:
    handler, raw, effects, _, selections, _ = make_handler(
        [(-1001, "One"), (-1002, "Two")]
    )
    update = private_summary(11)

    dispatch_twice(handler, update)

    assert len(raw.sent) == 1
    assert len(selections.by_key) == 2
    assert effects.hashes[0] == effects.hashes[1]
    callback_data = [row[0]["callback_data"] for row in raw.sent[0][2]["inline_keyboard"]]
    assert callback_data == ["summary_group:opaque-1", "summary_group:opaque-2"]

    with telegram_update_scope(13):
        handler(private_summary(13))

    assert len(raw.sent) == 2
    fresh_callback_data = [
        row[0]["callback_data"] for row in raw.sent[1][2]["inline_keyboard"]
    ]
    assert fresh_callback_data == ["summary_group:opaque-3", "summary_group:opaque-4"]


def test_callback_owner_retries_then_finalized_retry_is_silent_and_competitor_expires() -> None:
    handler, raw, _, _, selections, summaries = make_handler()
    token = selections.get_or_create(42, -1001, "seed", NOW)
    summaries.fail_once = True

    with pytest.raises(TimeoutError, match="transient generation failure"):
        with telegram_update_scope(20):
            handler(callback(token, 20))
    with telegram_update_scope(21):
        handler(callback(token, 21))
    with telegram_update_scope(20):
        handler(callback(token, 20))
    sent_after_success = len(raw.sent)
    with telegram_update_scope(20):
        handler(callback(token, 20))

    assert raw.sent[1][1] == COPY.value("messages", "group_choice_expired")
    assert len(raw.sent) == sent_after_success
    assert summaries.calls == [(-1001, 42), (-1001, 42)]


def test_chat_shared_reservation_blocks_competitor_and_owner_can_retry() -> None:
    handler, raw, _, shares, _, summaries = make_handler()
    request_id = shares.get_or_create(42, "seed", NOW)
    summaries.fail_once = True

    with pytest.raises(TimeoutError, match="transient generation failure"):
        with telegram_update_scope(30):
            handler(shared(request_id, 30))
    with telegram_update_scope(31):
        handler(shared(request_id, 31, chat_id=-1002))
    with telegram_update_scope(30):
        handler(shared(request_id, 30))
    sent_after_success = len(raw.sent)
    with telegram_update_scope(30):
        handler(shared(request_id, 30))

    assert summaries.calls == [(-1001, 42), (-1001, 42)]
    assert len(raw.sent) == sent_after_success
