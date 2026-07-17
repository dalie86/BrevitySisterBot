import tempfile
import unittest
from datetime import datetime
from types import ModuleType
from unittest.mock import AsyncMock, patch
import sys

from storage import Storage, StoredMessage
from workflow import BOT_TIMEZONE, has_enough_messages_for_summary

if "telegram" not in sys.modules:
    telegram_module = ModuleType("telegram")
    telegram_module.Bot = object
    sys.modules["telegram"] = telegram_module

import send_summary


class DailyThresholdTests(unittest.TestCase):
    def test_has_enough_messages_for_summary_requires_15_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/bot.db")
            storage.enable_chat(10, "Test Chat")

            for idx in range(14):
                storage.add_message(
                    StoredMessage(
                        telegram_message_id=idx + 1,
                        chat_id=10,
                        user_id=100 + idx,
                        username=f"@u{idx}",
                        full_name=f"User {idx}",
                        text=f"Message {idx}",
                        sent_at=datetime(2026, 4, 22, 12, 0, idx, tzinfo=BOT_TIMEZONE),
                    )
                )

            now = datetime(2026, 4, 22, 20, 45, 0, tzinfo=BOT_TIMEZONE)
            self.assertFalse(
                has_enough_messages_for_summary(storage, chat_id=10, now=now)
            )

            storage.add_message(
                StoredMessage(
                    telegram_message_id=15,
                    chat_id=10,
                    user_id=200,
                    username="@final",
                    full_name="Final User",
                    text="Message 14",
                    sent_at=datetime(2026, 4, 22, 12, 1, 0, tzinfo=BOT_TIMEZONE),
                )
            )

            self.assertTrue(
                has_enough_messages_for_summary(storage, chat_id=10, now=now)
            )


class SendSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_skips_when_prepared_summary_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/bot.db")
            storage.enable_chat(10, "Test Chat")
            mock_bot = AsyncMock()
            fixed_now = datetime(2026, 4, 22, 21, 0, 0, tzinfo=BOT_TIMEZONE)

            with patch("send_summary.Storage", return_value=storage), patch(
                "send_summary.Bot", return_value=mock_bot
            ), patch("send_summary.BOT_TOKEN", "test-token"), patch(
                "send_summary.now_msk", return_value=fixed_now
            ):
                await send_summary.run()

            mock_bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
