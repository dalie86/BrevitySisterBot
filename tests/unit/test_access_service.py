import pytest

from app.domain.models import TelegramMembership
from app.services.access import AccessDenied, AccessService


class FakeTelegram:
    def __init__(self, memberships: dict[int, TelegramMembership]) -> None:
        self.memberships = memberships
        self.calls = []

    def get_chat_member(self, chat_id: int, user_id: int) -> TelegramMembership:
        self.calls.append((chat_id, user_id))
        return self.memberships[user_id]


class FakeChats:
    def __init__(self) -> None:
        self.enabled = {}
        self.next_token = "a" * 32

    def enable(self, chat_id: int, title: str, now) -> str:
        self.enabled[chat_id] = (True, title)
        return self.next_token

    def disable(self, chat_id: int, now) -> None:
        enabled, title = self.enabled.get(chat_id, (True, str(chat_id)))
        self.enabled[chat_id] = (False, title)


def test_only_administrator_can_enable_group() -> None:
    telegram = FakeTelegram(
        {
            42: TelegramMembership("member", True),
            777: TelegramMembership("administrator", True),
        }
    )
    chats = FakeChats()
    service = AccessService(chats, telegram, bot_user_id=777)

    with pytest.raises(AccessDenied) as raised:
        service.enable_daily(-1001, 42, "Demo", now=None)

    assert raised.value.copy_key == "admin_required"


def test_enable_requires_bot_admin_before_database_change() -> None:
    telegram = FakeTelegram(
        {
            42: TelegramMembership("administrator", True),
            777: TelegramMembership("member", True),
        }
    )
    chats = FakeChats()
    service = AccessService(chats, telegram, bot_user_id=777)

    with pytest.raises(AccessDenied) as raised:
        service.enable_daily(-1001, 42, "Demo", now=None)

    assert raised.value.copy_key == "bot_admin_required"
    assert chats.enabled == {}
    assert telegram.calls == [(-1001, 42), (-1001, 777)]


def test_enable_returns_persistent_group_token() -> None:
    telegram = FakeTelegram(
        {
            42: TelegramMembership("administrator", True),
            777: TelegramMembership("administrator", True),
        }
    )
    chats = FakeChats()
    service = AccessService(chats, telegram, bot_user_id=777)

    token = service.enable_daily(-1001, 42, "Demo", now=None)

    assert token == "a" * 32
    assert chats.enabled[-1001] == (True, "Demo")


def test_creator_can_disable_group() -> None:
    telegram = FakeTelegram({42: TelegramMembership("creator", True)})
    chats = FakeChats()
    chats.enabled[-1001] = (True, "Demo")

    AccessService(chats, telegram, bot_user_id=777).disable_daily(
        -1001, 42, now=None
    )

    assert chats.enabled[-1001] == (False, "Demo")


@pytest.mark.parametrize("status", ["creator", "administrator", "member", "restricted"])
def test_current_member_statuses_can_read_summary(status: str) -> None:
    service = AccessService(
        FakeChats(),
        FakeTelegram({42: TelegramMembership(status, True)}),
        bot_user_id=777,
    )

    assert service.verify_member(-1001, 42) is True


@pytest.mark.parametrize("status", ["left", "kicked"])
def test_departed_member_cannot_read_summary(status: str) -> None:
    service = AccessService(
        FakeChats(),
        FakeTelegram({42: TelegramMembership(status, False)}),
        bot_user_id=777,
    )

    assert service.verify_member(-1001, 42) is False
