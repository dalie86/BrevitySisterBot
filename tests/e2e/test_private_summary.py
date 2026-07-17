from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.content import BotCopy
from app.domain.models import GeneratedSummary, SummaryKind, SummaryStatus, TelegramMembership
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

    def is_tracked(self, chat_id):
        return chat_id in self.tracked

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

    def create(self, user_id, now):
        self.created.append((user_id, now))
        return self.next_id

    def consume(self, request_id, user_id, now):
        key = (request_id, user_id)
        if key not in self.consumable:
            return False
        self.consumable.remove(key)
        return True


class FakeSelections:
    def __init__(self) -> None:
        self.items = {}
        self.counter = 0

    def create(self, user_id, chat_id, now):
        self.counter += 1
        token = f"opaque-{self.counter}"
        self.items[(token, user_id)] = chat_id
        return token

    def resolve(self, token, user_id, now):
        return self.items.get((token, user_id))


class FakeTelegram:
    def __init__(self) -> None:
        self.sent = []
        self.memberships = {}
        self.answered_callbacks = []
        self.membership_error: Exception | None = None

    def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append((chat_id, text, reply_markup, parse_mode))
        return len(self.sent)

    def get_chat_member(self, chat_id, user_id):
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
            text="<b>📝 Сводка за сегодня</b>\n\nГлавное",
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


def test_deep_link_remembers_group_and_immediately_generates() -> None:
    handler, chats, members, _, telegram, summaries, _ = make_handler()
    chats.private_links["a" * 32] = (-1001, "Demo")
    allow_group(telegram)

    handler(private_message("/start summary_" + "a" * 32))

    assert members.remembered == [(-1001, 42)]
    assert summaries.calls[0][0:3] == (-1001, SummaryKind.ON_DEMAND, 42)


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


def test_valid_chat_shared_removes_keyboard_and_generates() -> None:
    handler, chats, members, share_requests, telegram, summaries, _ = make_handler()
    share_requests.consumable.add((-77, 42))
    chats.tracked[-1001] = "Demo"
    allow_group(telegram)

    handler(chat_shared_update())

    assert telegram.sent[0][2] == {"remove_keyboard": True}
    assert telegram.sent[0][1] == COPY.value("messages", "summary_accepted")
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


def test_known_groups_are_rechecked_before_delivery() -> None:
    handler, _, members, _, telegram, summaries, _ = make_handler()
    members.groups = [(-1001, "Visible"), (-1002, "Departed")]
    allow_group(telegram, -1001)
    telegram.memberships[-1002, 42] = TelegramMembership("left", False)
    telegram.memberships[-1002, BOT_USER_ID] = TelegramMembership("administrator", True)

    handler(private_message("/summary"))

    assert summaries.calls[0][0] == -1001
    assert all("Departed" not in sent[1] for sent in telegram.sent)
    assert (-1002, 42, "left", False) in members.memberships


def test_multiple_valid_groups_show_opaque_buttons() -> None:
    handler, _, members, _, telegram, summaries, selections = make_handler()
    members.groups = [(-1001, "Group 1"), (-1002, "Group 2")]
    allow_group(telegram, -1001)
    allow_group(telegram, -1002)

    handler(private_message("/summary"))

    assert telegram.sent[-1][1] == COPY.value("messages", "choose_group_known")
    buttons = [row[0] for row in telegram.sent[-1][2]["inline_keyboard"]]
    assert [button["text"] for button in buttons] == ["Group 1", "Group 2"]
    assert all(button["callback_data"].startswith("summary_group:opaque-") for button in buttons)
    assert all("-100" not in button["callback_data"] for button in buttons)
    assert selections.counter == 2
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


def test_telegram_membership_failures_propagate() -> None:
    handler, _, members, _, telegram, _, _ = make_handler()
    members.groups = [(-1001, "Demo")]
    telegram.membership_error = TelegramApiError("temporary")

    with pytest.raises(TelegramApiError, match="temporary"):
        handler(private_message("/summary"))
