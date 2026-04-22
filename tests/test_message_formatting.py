import unittest

from message_formatting import format_summary_for_telegram


class MessageFormattingTests(unittest.TestCase):
    def test_format_summary_for_telegram_bolds_first_line_only(self) -> None:
        summary = (
            "самое главное за сегодня в сводке новостей:\n"
            "- тема: текст <важный>\n"
            "- итог: всё спокойно"
        )

        formatted = format_summary_for_telegram(summary)

        self.assertTrue(
            formatted.startswith("<b>самое главное за сегодня в сводке новостей:</b>")
        )
        self.assertIn("- тема: текст &lt;важный&gt;", formatted)
        self.assertIn("- итог: всё спокойно", formatted)


if __name__ == "__main__":
    unittest.main()
