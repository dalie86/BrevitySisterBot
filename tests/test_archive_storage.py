import tempfile
import unittest
from datetime import datetime

from archive_storage import ArchiveStorage, ArchivedSummary
from storage import StoredMessage


class ArchiveStorageTests(unittest.TestCase):
    def test_archive_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = ArchiveStorage(f"{tmpdir}/archive.db")
            archived_at = datetime(2026, 4, 21, 21, 10, 0)
            messages = [
                StoredMessage(
                    telegram_message_id=1,
                    chat_id=10,
                    user_id=20,
                    username="@alice",
                    full_name="Alice",
                    text="Первое сообщение",
                    sent_at=datetime(2026, 4, 21, 10, 0, 0),
                ),
                StoredMessage(
                    telegram_message_id=2,
                    chat_id=10,
                    user_id=21,
                    username="@bob",
                    full_name="Bob",
                    text="Второе сообщение",
                    sent_at=datetime(2026, 4, 21, 10, 5, 0),
                ),
            ]

            inserted = archive.archive_messages(
                messages,
                archived_date="2026-04-21",
                archived_at=archived_at,
            )
            self.assertEqual(inserted, 2)

            archive.archive_summary(
                ArchivedSummary(
                    chat_id=10,
                    summary_date="2026-04-21",
                    summary_text="Итоговая сводка",
                    prepared_at=datetime(2026, 4, 21, 20, 45, 0),
                    archived_at=archived_at,
                )
            )

            with archive._connect() as connection:
                message_count = connection.execute(
                    "SELECT COUNT(*) FROM archived_messages"
                ).fetchone()[0]
                summary_count = connection.execute(
                    "SELECT COUNT(*) FROM archived_summaries"
                ).fetchone()[0]

            self.assertEqual(message_count, 2)
            self.assertEqual(summary_count, 1)


if __name__ == "__main__":
    unittest.main()
