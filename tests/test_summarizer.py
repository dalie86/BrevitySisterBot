import unittest
from datetime import datetime
from unittest.mock import patch

from storage import StoredMessage
from summarizer import build_summary


class SummarizerTests(unittest.TestCase):
    @patch("summarizer.request_summary_json")
    def test_build_summary_uses_required_format_and_char_winner(self, mock_request_summary_json) -> None:
        mock_request_summary_json.return_value = (
            '{"headline":"Команда согласовала релиз и обсудила баги в оплате.",'
            '"topics":["релиз","оплата","баги"]}'
        )
        messages = [
            StoredMessage(
                telegram_message_id=1,
                chat_id=1,
                user_id=1,
                username="@alice",
                full_name="Alice",
                text="Нужно сделать релиз сегодня? Я уже подготовила чеклист.",
                sent_at=datetime(2026, 4, 10, 10, 0, 0),
            ),
            StoredMessage(
                telegram_message_id=2,
                chat_id=1,
                user_id=2,
                username="@bob",
                full_name="Bob",
                text="Да, надо проверить баг в оплате и сверить логи перед выкаткой.",
                sent_at=datetime(2026, 4, 10, 10, 5, 0),
            ),
            StoredMessage(
                telegram_message_id=3,
                chat_id=1,
                user_id=2,
                username="@bob",
                full_name="Bob",
                text="Еще я собрал длинное описание проблемы, чтобы символов было больше, чем у остальных участников.",
                sent_at=datetime(2026, 4, 10, 11, 5, 0),
            ),
        ]

        summary = build_summary(
            messages,
            chat_title="Test Chat",
            start=datetime(2026, 4, 10, 0, 0, 0),
            end=datetime(2026, 4, 10, 21, 0, 0),
        )

        self.assertTrue(summary.startswith("самое главное за сегодня в сводке новостей в 21:00:"))
        self.assertIn("- самое главное", summary.replace("- главное", "- самое главное"))
        self.assertIn("- самые обсуждаемые темы: релиз, оплата, баги", summary)
        self.assertIn("- самый активный участник сегодня - @bob", summary)
        self.assertLessEqual(len(summary), 1000)

    @patch("summarizer.request_summary_json")
    def test_build_summary_falls_back_without_llm_and_stays_short(self, mock_request_summary_json) -> None:
        mock_request_summary_json.return_value = None
        messages = [
            StoredMessage(
                telegram_message_id=1,
                chat_id=1,
                user_id=1,
                username="@alice",
                full_name="Alice",
                text="Очень длинное сообщение про инфраструктуру " * 20,
                sent_at=datetime(2026, 4, 10, 15, 0, 0),
            )
        ]

        summary = build_summary(
            messages,
            chat_title="Test Chat",
            start=datetime(2026, 4, 10, 0, 0, 0),
            end=datetime(2026, 4, 10, 21, 0, 0),
        )

        self.assertIn("@alice", summary)
        self.assertLessEqual(len(summary), 1000)


if __name__ == "__main__":
    unittest.main()
