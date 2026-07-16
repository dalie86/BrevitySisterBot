import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch


if "telegram" not in sys.modules:
    telegram_module = ModuleType("telegram")
    telegram_module.Bot = object
    telegram_module.Update = object
    sys.modules["telegram"] = telegram_module

    constants_module = ModuleType("telegram.constants")

    class _ChatType:
        GROUP = "group"
        SUPERGROUP = "supergroup"

    constants_module.ChatType = _ChatType
    sys.modules["telegram.constants"] = constants_module

    error_module = ModuleType("telegram.error")

    class TelegramError(Exception):
        pass

    error_module.TelegramError = TelegramError
    sys.modules["telegram.error"] = error_module

import collect_updates


class _FakeStorage:
    def __init__(self) -> None:
        self.state = {}

    def get_state(self, key: str):
        return self.state.get(key)

    def set_state(self, key: str, value: str) -> None:
        self.state[key] = value


class CollectUpdatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_swallows_telegram_error(self) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = collect_updates.TelegramError("blocked by user")

        await collect_updates._reply(bot, 123, "hello")

        bot.send_message.assert_awaited_once()

    async def test_run_advances_offset_even_when_update_processing_fails(self) -> None:
        storage = _FakeStorage()
        bot = AsyncMock()
        update1 = SimpleNamespace(update_id=100)
        update2 = SimpleNamespace(update_id=101)
        bot.get_updates = AsyncMock(side_effect=[[update1, update2], []])

        process_mock = AsyncMock(side_effect=[RuntimeError("boom"), None])
        now = SimpleNamespace(strftime=lambda _fmt: "12:00")

        with patch("collect_updates.Storage", return_value=storage), patch(
            "collect_updates.Bot", return_value=bot
        ), patch("collect_updates.BOT_TOKEN", "test-token"), patch(
            "collect_updates.now_msk", return_value=now
        ), patch("collect_updates.should_run_regular_collect", return_value=True), patch(
            "collect_updates._process_update", process_mock
        ):
            await collect_updates.run()

        self.assertEqual(storage.state[collect_updates.STATE_LAST_UPDATE_ID], "102")
        self.assertEqual(process_mock.await_count, 2)


if __name__ == "__main__":
    unittest.main()
