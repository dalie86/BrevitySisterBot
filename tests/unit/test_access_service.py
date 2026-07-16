import pytest

from app.services.access import AccessDenied, AccessService


class FakeTelegram:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls = []

    def get_chat_member(self, chat_id: int, user_id: int) -> str:
        self.calls.append((chat_id, user_id))
        return self.status


class FakeChats:
    def __init__(self) -> None:
        self.enabled = {}

    def enable(self, chat_id: int, title: str, now) -> None:
        self.enabled[chat_id] = (True, title)

    def disable(self, chat_id: int, now) -> None:
        enabled, title = self.enabled.get(chat_id, (True, str(chat_id)))
        self.enabled[chat_id] = (False, title)


def test_only_administrator_can_enable_group() -> None:
    telegram = FakeTelegram("member")
    chats = FakeChats()
    service = AccessService(chats, telegram)

    with pytest.raises(AccessDenied):
        service.enable_daily(-1001, 42, "Demo", now=None)

    telegram.status = "administrator"
    service.enable_daily(-1001, 42, "Demo", now=None)

    assert chats.enabled[-1001] == (True, "Demo")


def test_creator_can_disable_group() -> None:
    telegram = FakeTelegram("creator")
    chats = FakeChats()
    chats.enabled[-1001] = (True, "Demo")

    AccessService(chats, telegram).disable_daily(-1001, 42, now=None)

    assert chats.enabled[-1001] == (False, "Demo")


@pytest.mark.parametrize("status", ["creator", "administrator", "member", "restricted"])
def test_current_member_statuses_can_read_summary(status: str) -> None:
    service = AccessService(FakeChats(), FakeTelegram(status))

    assert service.verify_member(-1001, 42) is True


@pytest.mark.parametrize("status", ["left", "kicked"])
def test_departed_member_cannot_read_summary(status: str) -> None:
    service = AccessService(FakeChats(), FakeTelegram(status))

    assert service.verify_member(-1001, 42) is False
