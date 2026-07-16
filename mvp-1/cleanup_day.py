from __future__ import annotations

import logging
from datetime import timedelta

from storage import Storage
from workflow import day_start, now_msk, summary_date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    now = now_msk()
    start = day_start(now)
    end = start + timedelta(days=1)
    today = summary_date(now)

    storage = Storage()
    deleted_summaries = 0
    for chat in storage.get_enabled_chats():
        chat_id = int(chat["chat_id"])
        if storage.get_prepared_summary(chat_id=chat_id, summary_date=today):
            storage.delete_prepared_summary(chat_id=chat_id, summary_date=today)
            deleted_summaries += 1

    storage.delete_messages_between(start, end)
    logger.info("Cleanup completed for %s. Deleted summaries=%s", today, deleted_summaries)


if __name__ == "__main__":
    main()
