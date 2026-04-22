from __future__ import annotations

import asyncio
import logging

from telegram import Bot

from message_formatting import format_summary_for_telegram
from storage import Storage
from workflow import BOT_TOKEN, cleanup_old_data, now_msk, summary_date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def run() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")

    storage = Storage()
    bot = Bot(BOT_TOKEN)
    now = now_msk()
    today = summary_date(now)

    for chat in storage.get_enabled_chats():
        chat_id = int(chat["chat_id"])
        summary = storage.get_prepared_summary(chat_id=chat_id, summary_date=today)
        if not summary:
            logger.info(
                "Skipping send for chat %s: no prepared summary for %s.",
                chat_id,
                today,
            )
            continue
        await bot.send_message(
            chat_id=chat_id,
            text=format_summary_for_telegram(summary),
            parse_mode="HTML",
        )
        logger.info("Sent summary to chat %s", chat_id)

    cleanup_old_data(storage, now)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
