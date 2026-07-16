from __future__ import annotations

import logging
from datetime import datetime, timedelta

from archive_storage import ArchiveStorage, ArchivedSummary
from storage import Storage
from workflow import BOT_TIMEZONE, day_start, now_msk, summary_date

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
    archive_storage = ArchiveStorage()
    archived_at = datetime.now(BOT_TIMEZONE)

    total_messages = 0
    archived_summaries = 0
    for chat in storage.get_enabled_chats():
        chat_id = int(chat["chat_id"])
        messages = storage.get_messages_between(chat_id=chat_id, start=start, end=end)
        inserted = archive_storage.archive_messages(
            messages,
            archived_date=today,
            archived_at=archived_at,
        )
        total_messages += inserted

        summary_row = storage.get_prepared_summary_record(
            chat_id=chat_id,
            summary_date=today,
        )
        if summary_row:
            archive_storage.archive_summary(
                ArchivedSummary(
                    chat_id=chat_id,
                    summary_date=today,
                    summary_text=str(summary_row["summary_text"]),
                    prepared_at=datetime.fromisoformat(str(summary_row["prepared_at"])),
                    archived_at=archived_at,
                )
            )
            archived_summaries += 1

    logger.info(
        "Archived %s messages and %s summaries for %s",
        total_messages,
        archived_summaries,
        today,
    )


if __name__ == "__main__":
    main()
