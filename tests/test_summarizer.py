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
            '"topics":['
            '{"title":"Релиз","details":"Согласовали сроки; сверили чеклист; подтвердили готовность."},'
            '{"title":"Оплата","details":"Проверили баг; обсудили логи; определили следующий шаг."}'
            ']}'
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

        self.assertTrue(summary.startswith("самое главное за сегодня в сводке новостей:"))
        self.assertIn("- самое главное", summary.replace("- главное", "- самое главное"))
        self.assertIn("- Релиз: Согласовали сроки; сверили чеклист; подтвердили готовность", summary)
        self.assertIn("- Оплата: Проверили баг; обсудили логи; определили следующий шаг", summary)
        self.assertIn("- самый активный участник сегодня - @bob", summary)
        self.assertIn("Возьми поминальный набор с полочки", summary)
        self.assertLessEqual(len(summary), 1200)

    @patch("summarizer.request_summary_json")
    def test_build_summary_accepts_old_string_topic_format(self, mock_request_summary_json) -> None:
        mock_request_summary_json.return_value = (
            '{"headline":"Команда обсуждала релиз.",'
            '"topics":["релиз","дедлайны"]}'
        )
        messages = [
            StoredMessage(
                telegram_message_id=1,
                chat_id=1,
                user_id=1,
                username="@alice",
                full_name="Alice",
                text="Обсудим релиз и дедлайны на сегодня.",
                sent_at=datetime(2026, 4, 10, 10, 0, 0),
            )
        ]

        summary = build_summary(
            messages,
            chat_title="Test Chat",
            start=datetime(2026, 4, 10, 0, 0, 0),
            end=datetime(2026, 4, 10, 21, 0, 0),
        )

        self.assertIn("- релиз: Тема заметно обсуждалась в течение дня", summary)
        self.assertIn("- дедлайны: Тема заметно обсуждалась в течение дня", summary)
        self.assertLessEqual(len(summary), 1200)

    @patch("summarizer.request_summary_json")
    def test_build_summary_repairs_nested_llm_payload(self, mock_request_summary_json) -> None:
        mock_request_summary_json.side_effect = [
            (
                '{"type":"summary","data":{"topics":["будет","меня"],'
                '"key_points":["неясны даты и место","участники ждут конкретику","нужно больше практики","есть открытые вопросы"]}}'
            ),
            (
                '{"headline":"Есть неясность по планам и подготовке.",'
                '"topics":['
                '{"title":"Подготовка","details":"неясны даты и место; участники ждут конкретику; нужно больше практики; есть открытые вопросы"},'
                '{"title":"Логистика","details":"обсуждаются условия; участники ждут ясности; тема влияет на планы; решения пока нет"}'
                ']}'
            ),
        ]
        messages = [
            StoredMessage(
                telegram_message_id=1,
                chat_id=1,
                user_id=1,
                username="@alice",
                full_name="Alice",
                text="Нам нужны точные даты и больше ясности по плану.",
                sent_at=datetime(2026, 4, 10, 10, 0, 0),
            ),
            StoredMessage(
                telegram_message_id=2,
                chat_id=1,
                user_id=2,
                username="@bob",
                full_name="Bob",
                text="Без практики и понимания логистики идти рано.",
                sent_at=datetime(2026, 4, 10, 10, 5, 0),
            ),
        ]

        summary = build_summary(
            messages,
            chat_title="Test Chat",
            start=datetime(2026, 4, 10, 0, 0, 0),
            end=datetime(2026, 4, 10, 21, 0, 0),
        )

        self.assertIn("- Подготовка:", summary)
        self.assertIn("- Логистика:", summary)
        self.assertEqual(mock_request_summary_json.call_count, 2)
        self.assertLessEqual(len(summary), 1200)

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
        self.assertLessEqual(len(summary), 1200)


if __name__ == "__main__":
    unittest.main()
