from datetime import datetime, timezone
from pathlib import Path

from app.content import BotCopy
from app.domain.models import TelegramMembership
from app.integrations.telegram import parse_update
from app.services.access import AccessService
from app.services.group_updates import GroupUpdateHandler
from app.services.telegram_effects import DurableTelegramGateway, telegram_update_scope


class FakeChats:
    def __init__(self) -> None:
        self.tracked = set()
        self.enabled = {}
        self.next_token = "a" * 32
        self.enable_calls = []

    def enable(self, chat_id, title, now) -> str:
        self.enable_calls.append((chat_id, title))
        self.tracked.add(chat_id)
        self.enabled[chat_id] = True
        return self.next_token

    def disable(self, chat_id, now) -> None:
        self.enabled[chat_id] = False

    def is_tracked(self, chat_id) -> bool:
        return chat_id in self.tracked

    def tracked_count(self) -> int:
        return len(self.tracked)


class FakeMembers:
    def __init__(self) -> None:
        self.observed = []
        self.summary_links = []
        self.remembered = []

    def observe(self, chat_id, user_id, username, full_name, now) -> None:
        self.observed.append((chat_id, user_id))

    def confirm_summary_link(self, chat_id, user_id, now) -> None:
        self.summary_links.append((chat_id, user_id))

    def remember_group(self, chat_id, user_id, username, full_name, now) -> None:
        self.remembered.append((chat_id, user_id))


class FakeMessages:
    def __init__(self) -> None:
        self.saved = []

    def add(self, message) -> bool:
        self.saved.append(message)
        return True


class FakeTelegram:
    def __init__(self) -> None:
        self.memberships = {
            42: TelegramMembership("administrator", True),
            777: TelegramMembership("administrator", True),
        }
        self.sent = []

    def get_chat_member(self, chat_id, user_id) -> TelegramMembership:
        return self.memberships[user_id]

    def send_message(self, chat_id, text, reply_markup=None, parse_mode=None) -> int:
        self.sent.append((chat_id, text, reply_markup, parse_mode))
        return len(self.sent)

    def answer_callback_query(self, callback_id) -> None:
        return None


class FakeEffects:
    def __init__(self) -> None:
        self.records = {}
        self.payload_hashes = []

    def begin(self, effect_key, update_id, method, payload_hash, now):
        self.payload_hashes.append(payload_hash)
        record = self.records.get(effect_key)
        if record is not None:
            return record
        self.records[effect_key] = ("sending", None)
        return None

    def mark_sent(self, effect_key, telegram_message_id, now) -> None:
        self.records[effect_key] = ("sent", telegram_message_id)

    def mark_review_required(self, effect_key, error, now) -> None:
        self.records[effect_key] = ("review_required", None)


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


def build_handler(telegram=None):
    chats = FakeChats()
    members = FakeMembers()
    messages = FakeMessages()
    telegram = telegram or FakeTelegram()
    summary_requests = []
    access = AccessService(chats, telegram, bot_user_id=777)
    handler = GroupUpdateHandler(
        chats=chats,
        members=members,
        messages=messages,
        telegram=telegram,
        access=access,
        copy=BotCopy.load(Path("content/bot_copy.ru.toml")),
        telegram_bot_username="BrevitySisterBot",
        summary_request=lambda chat_id, user_id: summary_requests.append((chat_id, user_id)),
        now=lambda: datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )
    return handler, chats, members, messages, telegram, summary_requests


def test_enable_daily_requires_admin_and_tracks_group() -> None:
    handler, chats, members, _, telegram, _ = build_handler()
    telegram.memberships[42] = TelegramMembership("member", True)

    handler(parse_update(group_update("/enable_daily")))
    assert chats.tracked == set()
    assert members.remembered == []
    assert "администратор" in telegram.sent[-1][1].lower()

    telegram.memberships[42] = TelegramMembership("administrator", True)
    handler(parse_update(group_update("/enable_daily", message_id=11)))
    assert chats.tracked == {-1001}
    assert chats.enabled[-1001] is True
    assert members.remembered == [(-1001, 42)]


def test_enable_publishes_private_summary_deep_link() -> None:
    handler, chats, _, _, telegram, _ = build_handler()
    chats.next_token = "a" * 32

    handler(parse_update(group_update("/enable_daily")))

    chat_id, text, markup, parse_mode = telegram.sent[-1]
    assert chat_id == -1001
    assert text.startswith("Готово — ежедневные сводки включены")
    assert markup["inline_keyboard"][0][0] == {
        "text": "Получить сводку лично",
        "url": "https://t.me/BrevitySisterBot?start=summary_" + "a" * 32,
    }
    assert parse_mode is None


def test_enable_addressed_to_this_bot_is_accepted_case_insensitively() -> None:
    handler, chats, _, _, _, _ = build_handler()

    handler(parse_update(group_update("/enable_daily@bReViTySiStErBoT")))

    assert chats.enabled[-1001] is True


def test_enable_addressed_to_other_bot_does_not_mutate_group() -> None:
    handler, chats, _, _, telegram, _ = build_handler()

    handler(parse_update(group_update("/enable_daily@OtherBot")))

    assert chats.tracked == set()
    assert telegram.sent == []


def test_tracked_disabled_chat_continues_buffering_messages() -> None:
    handler, chats, members, messages, _, _ = build_handler()
    chats.tracked.add(-1001)
    chats.enabled[-1001] = False

    handler(parse_update(group_update("Обычное сообщение")))

    assert messages.saved[0].text == "Обычное сообщение"
    assert members.remembered == [(-1001, 42)]


def test_group_summary_registers_private_link_and_dispatches_request() -> None:
    handler, chats, members, _, _, summary_requests = build_handler()
    chats.tracked.add(-1001)

    handler(parse_update(group_update("/summary")))

    assert members.remembered == [(-1001, 42)]
    assert members.summary_links == [(-1001, 42)]
    assert summary_requests == [(-1001, 42)]


def test_summary_addressed_to_this_bot_is_accepted_case_insensitively() -> None:
    handler, chats, members, _, _, summary_requests = build_handler()
    chats.tracked.add(-1001)

    handler(parse_update(group_update("/summary@BREVITYSISTERBOT")))

    assert members.remembered == [(-1001, 42)]
    assert summary_requests == [(-1001, 42)]


def test_summary_addressed_to_other_bot_is_not_dispatched() -> None:
    handler, chats, members, messages, _, summary_requests = build_handler()
    chats.tracked.add(-1001)

    handler(parse_update(group_update("/summary@OtherBot")))

    assert members.remembered == []
    assert messages.saved == []
    assert summary_requests == []


def test_recovered_enable_replays_one_stable_onboarding_effect() -> None:
    raw = FakeTelegram()
    effects = FakeEffects()

    def now() -> datetime:
        return datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)

    telegram = DurableTelegramGateway(raw=raw, effects=effects, now=now)
    handler, chats, _, _, _, _ = build_handler(telegram)
    update = parse_update(group_update("/enable_daily"))

    with telegram_update_scope(10):
        handler(update)
    with telegram_update_scope(10):
        handler(update)

    assert chats.enable_calls == [(-1001, "Demo"), (-1001, "Demo")]
    assert len(effects.records) == 1
    assert len(effects.payload_hashes) == 2
    assert effects.payload_hashes[0] == effects.payload_hashes[1]
    assert raw.sent == [
        (
            -1001,
            BotCopy.load(Path("content/bot_copy.ru.toml")).value(
                "messages", "daily_enabled"
            ),
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "Получить сводку лично",
                            "url": (
                                "https://t.me/BrevitySisterBot?start=summary_"
                                + "a" * 32
                            ),
                        }
                    ]
                ]
            },
            None,
        )
    ]


def test_disable_daily_uses_configured_copy_and_keeps_group_tracked() -> None:
    handler, chats, _, _, telegram, _ = build_handler()
    chats.tracked.add(-1001)
    chats.enabled[-1001] = True

    handler(parse_update(group_update("/disable_daily")))

    assert chats.enabled[-1001] is False
    assert chats.is_tracked(-1001) is True
    assert telegram.sent[-1][1].startswith("Ежедневные сводки отключены")
