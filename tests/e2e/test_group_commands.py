from datetime import datetime, timezone

from app.integrations.telegram import parse_update
from app.services.group_updates import GroupUpdateHandler


class FakeChats:
    def __init__(self) -> None:
        self.tracked = set()
        self.enabled = {}

    def enable(self, chat_id, title, now) -> None:
        self.tracked.add(chat_id)
        self.enabled[chat_id] = True

    def disable(self, chat_id, now) -> None:
        self.enabled[chat_id] = False

    def is_tracked(self, chat_id) -> bool:
        return chat_id in self.tracked


class FakeMembers:
    def __init__(self) -> None:
        self.observed = []
        self.summary_links = []

    def observe(self, chat_id, user_id, username, full_name, now) -> None:
        self.observed.append((chat_id, user_id))

    def confirm_summary_link(self, chat_id, user_id, now) -> None:
        self.summary_links.append((chat_id, user_id))


class FakeMessages:
    def __init__(self) -> None:
        self.saved = []

    def add(self, message) -> bool:
        self.saved.append(message)
        return True


class FakeTelegram:
    def __init__(self) -> None:
        self.status = "administrator"
        self.sent = []

    def get_chat_member(self, chat_id, user_id) -> str:
        return self.status

    def send_message(self, chat_id, text, reply_markup=None) -> int:
        self.sent.append((chat_id, text))
        return len(self.sent)


def group_update(text: str, message_id: int = 10) -> dict:
    return {
        "update_id": message_id,
        "message": {
            "message_id": message_id,
            "date": 1784210000,
            "chat": {"id": -1001, "type": "supergroup", "title": "Demo"},
            "from": {"id": 42, "first_name": "Pavel", "is_bot": False},
            "text": text,
        },
    }


def build_handler():
    chats = FakeChats()
    members = FakeMembers()
    messages = FakeMessages()
    telegram = FakeTelegram()
    summary_requests = []
    handler = GroupUpdateHandler(
        chats=chats,
        members=members,
        messages=messages,
        telegram=telegram,
        summary_request=lambda chat_id, user_id: summary_requests.append((chat_id, user_id)),
        now=lambda: datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )
    return handler, chats, members, messages, telegram, summary_requests


def test_enable_daily_requires_admin_and_tracks_group() -> None:
    handler, chats, _, _, telegram, _ = build_handler()
    telegram.status = "member"

    handler(parse_update(group_update("/enable_daily")))
    assert chats.tracked == set()
    assert "администратор" in telegram.sent[-1][1].lower()

    telegram.status = "administrator"
    handler(parse_update(group_update("/enable_daily", message_id=11)))
    assert chats.tracked == {-1001}
    assert chats.enabled[-1001] is True


def test_tracked_disabled_chat_continues_buffering_messages() -> None:
    handler, chats, members, messages, _, _ = build_handler()
    chats.tracked.add(-1001)
    chats.enabled[-1001] = False

    handler(parse_update(group_update("Обычное сообщение")))

    assert messages.saved[0].text == "Обычное сообщение"
    assert members.observed == [(-1001, 42)]


def test_group_summary_registers_private_link_and_dispatches_request() -> None:
    handler, chats, members, _, _, summary_requests = build_handler()
    chats.tracked.add(-1001)

    handler(parse_update(group_update("/summary")))

    assert members.summary_links == [(-1001, 42)]
    assert summary_requests == [(-1001, 42)]
