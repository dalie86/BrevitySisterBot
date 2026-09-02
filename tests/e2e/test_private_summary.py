from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.content import BotCopy
from app.domain.models import (
    GeneratedSummary,
    ReservationResult,
    ReservationState,
    SummaryKind,
    SummaryStatus,
    TelegramMembership,
)
from app.integrations.telegram import (
    IncomingCallback,
    IncomingChatShared,
    IncomingMessage,
    IncomingUpdate,
    TelegramApiError,
)
from app.services.on_demand import OnDemandSummaryHandler


NOW = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
BOT_USER_ID = 777
COPY = BotCopy.load(Path("content/bot_copy.ru.toml"))


class FakeChats:
    def __init__(self) -> None:
        self.private_links: dict[str, tuple[int, str]] = {}
        self.tracked: dict[int, str] = {}
        self.tracked_group_count = 0

    def is_tracked(self, chat_id):
        self.events.append(("is_tracked", chat_id))
        return chat_id in self.tracked

    def tracked_count(self):
        return self.tracked_group_count

    def resolve_private_link(self, token):
        return self.private_links.get(token)


class FakeMembers:
    def __init__(self) -> None:
        self.groups: list[tuple[int, str]] = []
        self.remembered: list[tuple[int, int]] = []
        self.memberships: list[tuple[int, int, str, bool]] = []

    def linked_groups(self, user_id):
        return list(self.groups)

    def remember_group(self, chat_id, user_id, username, full_name, now):
        self.remembered.append((chat_id, user_id))

    def record_membership(self, chat_id, user_id, status, is_member, now):
        self.memberships.append((chat_id, user_id, status, is_member))


class FakeShareRequests:
    def __init__(self) -> None:
        self.next_id = -77
        self.created: list[tuple[int, datetime]] = []
        self.consumable: set[tuple[int, int]] = set()
        self.consumed: list[tuple[int, int]] = []
        self.by_key = {}
        self.reservations = {}

    def get_or_create(self, user_id, idempotency_key, now):
        self.created.append((user_id, now))
        self.by_key.setdefault(idempotency_key, self.next_id)
        self.consumable.add((self.by_key[idempotency_key], user_id))
        return self.by_key[idempotency_key]

    def create(self, user_id, now):
        self.created.append((user_id, now))
        return self.next_id

    def reserve(self, request_id, user_id, chat_id, update_id, now):
        key = (request_id, user_id)
        if key not in self.consumable:
            return ReservationResult(ReservationState.UNAVAILABLE)
        reservation = self.reservations.get(key)
        if reservation is None:
            self.reservations[key] = [update_id, chat_id, False]
            return ReservationResult(ReservationState.ACQUIRED, chat_id)
        if reservation[0] != update_id or reservation[1] != chat_id:
            return ReservationResult(ReservationState.UNAVAILABLE)
        state = ReservationState.FINALIZED if reservation[2] else ReservationState.RESUMED
        return ReservationResult(state, chat_id)

    def finalize(self, request_id, user_id, update_id, now):
        key = (request_id, user_id)
        reservation = self.reservations.get(key)
        if reservation is None or reservation[0] != update_id:
            return False
        reservation[2] = True
        self.consumed.append(key)
        return True


class FakeSelections:
    def __init__(self) -> None:
        self.items = {}
        self.counter = 0
        self.by_key = {}
        self.reservations = {}

    def create(self, user_id, chat_id, now):
        return self.get_or_create(user_id, chat_id, f"seed-{self.counter + 1}", now)

    def get_or_create(self, user_id, chat_id, idempotency_key, now):
        if idempotency_key in self.by_key:
            return self.by_key[idempotency_key]
        self.counter += 1
        token = f"opaque-{self.counter}"
        self.by_key[idempotency_key] = token
        self.items[(token, user_id)] = chat_id
        return token

    def reserve(self, token, user_id, update_id, now):
        key = (token, user_id)
        chat_id = self.items.get(key)
        if chat_id is None:
            return ReservationResult(ReservationState.UNAVAILABLE)
        reservation = self.reservations.get(key)
        if reservation is None:
            self.reservations[key] = [update_id, False]
            return ReservationResult(ReservationState.ACQUIRED, chat_id)
        if reservation[0] != update_id:
            return ReservationResult(ReservationState.UNAVAILABLE)
        state = ReservationState.FINALIZED if reservation[1] else ReservationState.RESUMED
        return ReservationResult(state, chat_id)

    def finalize(self, token, user_id, update_id, now):
        reservation = self.reservations[(token, user_id)]
        if reservation[0] != update_id:
            return False
        reservation[1] = True
        return True


class FakeTelegram:
    def __init__(self) -> None:
        self.sent = []
        self.events = []
        self.memberships = {}
        self.membership_calls = []
        self.answered_callbacks = []
        self.membership_error: Exception | None = None

    def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append((chat_id, text, reply_markup, parse_mode))
        self.events.append(("send_message", text))
        return len(self.sent)

    def get_chat_member(self, chat_id, user_id):
        self.membership_calls.append((chat_id, user_id))
        self.events.append(("get_chat_member", chat_id, user_id))
        if self.membership_error is not None:
            raise self.membership_error
        return self.memberships.get((chat_id, user_id), TelegramMembership("left", False))

    def answer_callback_query(self, callback_id):
        self.answered_callbacks.append(callback_id)


class FakeSummaryService:
    def __init__(self) -> None:
        self.calls = []

    def generate(self, chat_id, kind, requester_user_id, now):
        self.calls.append((chat_id, kind, requester_user_id, now))
        return GeneratedSummary(
            chat_id=chat_id,
            summary_date="2026-07-16",
            kind=kind,
            source_hash="hash",
            text=COPY.render(
                "summary", "document", headline="Главное", topics_block=""
            ),
            status=SummaryStatus.PREPARED,
        )


def private_message(text="/summary", user_id=42):
    return IncomingUpdate(
        update_id=1,
        message=IncomingMessage(
            message_id=1,
            chat_id=user_id,
            chat_type="private",
            chat_title="Private",
            user_id=user_id,
            username="@pavel",
            full_name="Pavel",
            sender_is_bot=False,
            text=text,
            sent_at=NOW,
        ),
    )


def chat_shared_update(request_id=-77, shared_chat_id=-1001, user_id=42):
    return IncomingUpdate(
        update_id=2,
        chat_shared=IncomingChatShared(
            request_id=request_id,
            shared_chat_id=shared_chat_id,
            title="Demo",
            user_id=user_id,
            private_chat_id=user_id,
        ),
    )


def make_handler():
    chats = FakeChats()
    members = FakeMembers()
    share_requests = FakeShareRequests()
    telegram = FakeTelegram()
    chats.events = telegram.events
    summaries = FakeSummaryService()
    selections = FakeSelections()
    handler = OnDemandSummaryHandler(
        chats=chats,
        members=members,
        share_requests=share_requests,
        selections=selections,
        telegram=telegram,
        summaries=summaries,
        copy=COPY,
        bot_user_id=BOT_USER_ID,
        now=lambda: NOW,
    )
    return handler, chats, members, share_requests, telegram, summaries, selections


def allow_group(telegram, chat_id=-1001, user_id=42):
    telegram.memberships[chat_id, user_id] = TelegramMembership("member", True)
    telegram.memberships[chat_id, BOT_USER_ID] = TelegramMembership("administrator", True)


def summary_start_callback():
    return IncomingUpdate(
        update_id=3,
        callback=IncomingCallback(
            callback_id="summary-start",
            user_id=42,
            chat_id=42,
            data="summary_start",
        ),
    )


def test_plain_private_start_welcomes_without_generating() -> None:
    handler, _, _, _, telegram, summaries, _ = make_handler()

    handler(private_message("/start"))

    assert telegram.sent == [
        (
            42,
            COPY.value("messages", "private_welcome"),
            {
                "inline_keyboard": [[{
                    "text": COPY.value("buttons", "start_summary"),
                    "callback_data": "summary_start",
                }]]
            },
            None,
        )
    ]
    assert summaries.calls == []


def test_private_start_button_shows_native_picker_without_known_groups() -> None:
    handler, _, _, _, telegram, summaries, _ = make_handler()

    handler(summary_start_callback())

    assert telegram.answered_callbacks == ["summary-start"]
    assert telegram.sent[-1][1] == COPY.value("messages", "choose_group_native")
    assert summaries.calls == []


def test_deep_link_remembers_group_and_immediately_generates() -> None:
    handler, chats, members, _, telegram, summaries, _ = make_handler()
    chats.private_links["a" * 32] = (-1001, "Demo")
    allow_group(telegram)

    handler(private_message("/start summary_" + "a" * 32))

    assert members.remembered == [(-1001, 42)]
    assert summaries.calls[0][0:3] == (-1001, SummaryKind.ON_DEMAND, 42)
    assert telegram.events[:3] == [
        ("send_message", COPY.value("messages", "summary_accepted")),
        ("get_chat_member", -1001, BOT_USER_ID),
        ("get_chat_member", -1001, 42),
    ]


def test_invalid_deep_link_shows_error_and_native_picker() -> None:
    handler, _, _, share_requests, telegram, summaries, _ = make_handler()

    handler(private_message("/start summary_unknown"))

    assert telegram.sent[0][1] == COPY.value("messages", "private_link_invalid")
    assert telegram.sent[1][1] == COPY.value("messages", "choose_group_native")
    assert share_requests.created == [(42, NOW)]
    assert summaries.calls == []


def test_no_known_groups_shows_native_picker() -> None:
    handler, _, _, share_requests, telegram, summaries, _ = make_handler()
    share_requests.next_id = -77

    handler(private_message("/summary"))

    button = telegram.sent[-1][2]["keyboard"][0][0]
    assert button["text"] == COPY.value("buttons", "choose_group")
    assert button["request_chat"] == {
        "request_id": -77,
        "chat_is_channel": False,
        "bot_is_member": True,
        "request_title": True,
    }
    assert telegram.sent[-1][2]["resize_keyboard"] is True
    assert telegram.sent[-1][2]["one_time_keyboard"] is True
    assert summaries.calls == []


def test_one_known_group_uses_native_picker_when_another_group_is_tracked() -> None:
    handler, chats, members, _, telegram, summaries, _ = make_handler()
    chats.tracked_group_count = 2
    members.groups = [(-1001, "One")]

    handler(private_message("/summary"))

    assert telegram.sent[-1][1] == COPY.value("messages", "choose_group_native")
    assert telegram.membership_calls == []
    assert summaries.calls == []


def test_one_known_group_is_direct_only_when_it_is_the_only_tracked_group() -> None:
    handler, chats, members, _, telegram, summaries, _ = make_handler()
    chats.tracked_group_count = 1
    members.groups = [(-1001, "One")]
    allow_group(telegram)

    handler(private_message("/summary"))

    assert summaries.calls[0][0] == -1001
    assert telegram.events[:3] == [
        ("send_message", COPY.value("messages", "summary_accepted")),
        ("get_chat_member", -1001, BOT_USER_ID),
        ("get_chat_member", -1001, 42),
    ]


def test_multiple_known_groups_show_buttons_without_membership_calls() -> None:
    handler, chats, members, _, telegram, summaries, selections = make_handler()
    chats.tracked_group_count = 2
    members.groups = [(-1001, "One"), (-1002, "Two")]

    handler(private_message("/summary"))

    buttons = [row[0] for row in telegram.sent[-1][2]["inline_keyboard"]]
    assert [button["text"] for button in buttons] == ["One", "Two"]
    assert all(button["callback_data"].startswith("summary_group:opaque-") for button in buttons)
    assert telegram.membership_calls == []
    assert summaries.calls == []
    assert selections.counter == 2


def test_valid_chat_shared_removes_keyboard_and_generates() -> None:
    handler, chats, members, share_requests, telegram, summaries, _ = make_handler()
    share_requests.consumable.add((-77, 42))
    chats.tracked[-1001] = "Demo"
    allow_group(telegram)

    handler(chat_shared_update())

    assert telegram.sent[0][2] == {"remove_keyboard": True}
    assert telegram.sent[0][1] == COPY.value("messages", "summary_accepted")
    assert telegram.events[:4] == [
        ("send_message", COPY.value("messages", "summary_accepted")),
        ("is_tracked", -1001),
        ("get_chat_member", -1001, BOT_USER_ID),
        ("get_chat_member", -1001, 42),
    ]
    assert members.remembered == [(-1001, 42)]
    assert summaries.calls[0][0] == -1001


def test_chat_shared_request_is_user_bound_and_one_use() -> None:
    handler, chats, members, share_requests, telegram, summaries, _ = make_handler()
    share_requests.consumable.add((-77, 42))
    chats.tracked[-1001] = "Demo"
    allow_group(telegram)

    handler(chat_shared_update(user_id=41))
    handler(chat_shared_update())
    handler(chat_shared_update())

    assert members.remembered == [(-1001, 42)]
    assert len(summaries.calls) == 1
    assert share_requests.consumed == [(-77, 42)]


def test_chat_shared_retry_keeps_request_until_terminal_action_succeeds() -> None:
    handler, chats, members, share_requests, telegram, summaries, _ = make_handler()
    share_requests.consumable.add((-77, 42))
    chats.tracked[-1001] = "Demo"
    allow_group(telegram)
    telegram.membership_error = TelegramApiError("temporary")

    with pytest.raises(TelegramApiError, match="temporary"):
        handler(chat_shared_update())

    assert share_requests.consumed == []

    telegram.membership_error = None
    handler(chat_shared_update())

    assert members.remembered == [(-1001, 42)]
    assert len(summaries.calls) == 1
    assert share_requests.consumed == [(-77, 42)]


@pytest.mark.parametrize(
    ("tracked", "bot_membership", "user_membership", "copy_key"),
    [
        (
            False,
            TelegramMembership("administrator", True),
            TelegramMembership("member", True),
            "selected_group_not_connected",
        ),
        (
            True,
            TelegramMembership("member", True),
            TelegramMembership("member", True),
            "group_access_unverifiable",
        ),
        (
            True,
            TelegramMembership("administrator", True),
            TelegramMembership("left", False),
            "membership_not_confirmed",
        ),
    ],
)
def test_invalid_chat_shared_selection_uses_exact_copy(
    tracked, bot_membership, user_membership, copy_key
) -> None:
    handler, chats, members, share_requests, telegram, summaries, _ = make_handler()
    share_requests.consumable.add((-77, 42))
    if tracked:
        chats.tracked[-1001] = "Demo"
    telegram.memberships[-1001, BOT_USER_ID] = bot_membership
    telegram.memberships[-1001, 42] = user_membership

    handler(chat_shared_update())

    assert telegram.sent[-1][1] == COPY.value("messages", copy_key)
    assert members.remembered == []
    assert summaries.calls == []


def test_multiple_valid_groups_show_opaque_buttons() -> None:
    handler, _, members, _, telegram, summaries, selections = make_handler()
    members.groups = [(-1001, "Group 1"), (-1002, "Group 2")]

    handler(private_message("/summary"))

    assert telegram.sent[-1][1] == COPY.value("messages", "choose_group_known")
    buttons = [row[0] for row in telegram.sent[-1][2]["inline_keyboard"]]
    assert [button["text"] for button in buttons] == ["Group 1", "Group 2"]
    assert all(button["callback_data"].startswith("summary_group:opaque-") for button in buttons)
    assert all("-100" not in button["callback_data"] for button in buttons)
    assert selections.counter == 2
    assert telegram.membership_calls == []
    assert summaries.calls == []


def test_callback_rechecks_bot_and_user_before_private_delivery() -> None:
    handler, _, _, _, telegram, summaries, selections = make_handler()
    token = selections.create(42, -1001, NOW)
    telegram.memberships[-1001, BOT_USER_ID] = TelegramMembership("administrator", True)
    telegram.memberships[-1001, 42] = TelegramMembership("left", False)

    handler(
        IncomingUpdate(
            update_id=3,
            callback=IncomingCallback(
                callback_id="callback",
                user_id=42,
                chat_id=42,
                data=f"summary_group:{token}",
            ),
        )
    )

    assert summaries.calls == []
    assert telegram.sent[-1][1] == COPY.value("messages", "membership_not_confirmed")
    assert telegram.answered_callbacks == ["callback"]


def test_callback_selection_is_consumed_once() -> None:
    handler, _, _, _, telegram, summaries, selections = make_handler()
    token = selections.create(42, -1001, NOW)
    allow_group(telegram)
    callback = IncomingUpdate(
        update_id=3,
        callback=IncomingCallback(
            callback_id="callback",
            user_id=42,
            chat_id=42,
            data=f"summary_group:{token}",
        ),
    )

    handler(callback)
    handler(callback)

    assert len(summaries.calls) == 1
    assert telegram.answered_callbacks == ["callback"]


def test_telegram_membership_failures_propagate() -> None:
    handler, chats, members, _, telegram, _, _ = make_handler()
    chats.tracked_group_count = 1
    members.groups = [(-1001, "Demo")]
    telegram.membership_error = TelegramApiError("temporary")

    with pytest.raises(TelegramApiError, match="temporary"):
        handler(private_message("/summary"))
