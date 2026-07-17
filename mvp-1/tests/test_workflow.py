import unittest
from datetime import datetime

from workflow import BOT_TIMEZONE, format_collect_window, should_run_regular_collect


class WorkflowTests(unittest.TestCase):
    def test_regular_collect_window(self) -> None:
        self.assertFalse(
            should_run_regular_collect(datetime(2026, 4, 19, 9, 59, tzinfo=BOT_TIMEZONE))
        )
        self.assertTrue(
            should_run_regular_collect(datetime(2026, 4, 19, 10, 0, tzinfo=BOT_TIMEZONE))
        )
        self.assertTrue(
            should_run_regular_collect(datetime(2026, 4, 19, 20, 40, tzinfo=BOT_TIMEZONE))
        )
        self.assertTrue(
            should_run_regular_collect(datetime(2026, 4, 19, 20, 59, tzinfo=BOT_TIMEZONE))
        )
        self.assertFalse(
            should_run_regular_collect(datetime(2026, 4, 19, 21, 0, tzinfo=BOT_TIMEZONE))
        )

    def test_collect_window_format(self) -> None:
        self.assertEqual(format_collect_window(), "10:00-20:59")


if __name__ == "__main__":
    unittest.main()
