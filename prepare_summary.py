from __future__ import annotations

import logging
from datetime import datetime

from storage import Storage
from workflow import BOT_TIMEZONE, build_summary_for_chat, now_msk, summary_date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    storage = Storage()
    now = now_msk()
    today = summary_date(now)

    for chat in storage.get_enabled_chats():
        chat_id = int(chat["chat_id"])
        chat_title = str(chat["title"])
        summary = build_summary_for_chat(
            storage,
            chat_id=chat_id,
            chat_title=chat_title,
            now=now,
        )
        storage.save_prepared_summary(
            chat_id=chat_id,
            summary_date=today,
            summary_text=summary,
            prepared_at=datetime.now(BOT_TIMEZONE),
        )
        logger.info("Prepared summary for chat %s", chat_id)


if __name__ == "__main__":
    main()
