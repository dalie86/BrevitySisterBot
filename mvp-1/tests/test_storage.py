import unittest
from datetime import datetime

from storage import Storage, StoredMessage


class StorageTests(unittest.TestCase):
    def test_storage_roundtrip(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp_dir:
            storage = Storage(f"{tmp_dir}/bot.db")
            storage.enable_chat(123, "Demo")
            storage.add_message(
                StoredMessage(
                    telegram_message_id=1,
                    chat_id=123,
                    user_id=1,
                    username="@demo",
                    full_name="Demo User",
                    text="Привет",
                    sent_at=datetime(2026, 4, 10, 12, 0, 0),
                )
            )

            messages = storage.get_messages_between(
                123,
                datetime(2026, 4, 10, 0, 0, 0),
                datetime(2026, 4, 11, 0, 0, 0),
            )

            self.assertEqual(len(messages), 1)
            self.assertEqual(storage.get_enabled_chat_ids(), [123])
            self.assertTrue(storage.is_chat_enabled(123))
            storage.set_state("last_update_id", "42")
            self.assertEqual(storage.get_state("last_update_id"), "42")
            storage.save_prepared_summary(
                chat_id=123,
                summary_date="2026-04-10",
                summary_text="готовая сводка",
                prepared_at=datetime(2026, 4, 10, 20, 40, 0),
            )
            self.assertEqual(
                storage.get_prepared_summary(chat_id=123, summary_date="2026-04-10"),
                "готовая сводка",
            )
            self.assertEqual(len(storage.get_enabled_chats()), 1)
            storage.disable_chat(123)
            self.assertTrue(storage.is_chat_tracked(123))
            self.assertFalse(storage.is_chat_enabled(123))


if __name__ == "__main__":
    unittest.main()
