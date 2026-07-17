from __future__ import annotations

import asyncio
import logging

from telegram import Bot, Update
from telegram.constants import ChatType
from telegram.error import TelegramError

from message_formatting import format_summary_for_telegram
from storage import Storage, StoredMessage
from workflow import (
    BOT_TOKEN,
    BOT_TIMEZONE,
    STATE_LAST_UPDATE_ID,
    build_summary_for_chat,
    format_collect_window,
    now_msk,
    should_run_regular_collect,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def _display_name(user) -> str:
    if user and user.full_name:
        return user.full_name
    if user and user.username:
        return f"@{user.username}"
    return "Unknown User"


async def _reply(bot: Bot, chat_id: int, text: str, *, parse_mode: str | None = None) -> None:
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    except TelegramError as exc:
        logger.warning("Could not send reply to chat %s: %s", chat_id, exc)


async def _handle_command(bot: Bot, storage: Storage, update: Update) -> bool:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat:
        return False

    text = (message.text or "").strip()
    if not text.startswith("/"):
        return False

    command = text.split()[0].split("@")[0].lower()
    if command == "/start":
        await _reply(
            bot,
            chat.id,
            "Бот работает в cron-режиме. Команды обрабатываются при ближайшем запуске сборщика.\n\n"
            "/enable_daily - включить ежедневную сводку\n"
            "/disable_daily - выключить ежедневную сводку\n"
            "/summary - прислать текущую сводку дня",
        )
        return True

    if chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await _reply(bot, chat.id, "Эта команда работает только в группе.")
        return True

    if command == "/enable_daily":
        storage.enable_chat(chat.id, chat.title or str(chat.id))
        await _reply(
            bot,
            chat.id,
            "Ежедневная сводка включена. Сообщения буферизуются по cron, подготовка саммари в 20:45, публикация в 21:00 по Москве.",
        )
        return True

    if command == "/disable_daily":
        storage.disable_chat(chat.id)
        await _reply(
            bot,
            chat.id,
            "Ежедневная сводка выключена для этого чата. Буферизация сообщений для уже подключенного чата продолжается.",
        )
        return True

    if command == "/summary":
        summary = build_summary_for_chat(
            storage,
            chat_id=chat.id,
            chat_title=chat.title or str(chat.id),
        )
        await _reply(
            bot,
            chat.id,
            format_summary_for_telegram(summary),
            parse_mode="HTML",
        )
        return True

    return False


async def _process_update(bot: Bot, storage: Storage, update: Update) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat:
        return

    if await _handle_command(bot, storage, update):
        return

    if chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    if not storage.is_chat_tracked(chat.id):
        return
    if user and user.is_bot:
        return

    text = (message.text or message.caption or "").strip()
    if not text:
        return

    storage.add_message(
        StoredMessage(
            telegram_message_id=message.message_id,
            chat_id=chat.id,
            user_id=user.id if user else None,
            username=f"@{user.username}" if user and user.username else "",
            full_name=_display_name(user),
            text=text,
            sent_at=message.date.astimezone(BOT_TIMEZONE),
        )
    )


async def run() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")

    current_time = now_msk()
    if not should_run_regular_collect(current_time):
        logger.info(
            "Skipping collect at %s MSK: outside allowed window %s.",
            current_time.strftime("%H:%M"),
            format_collect_window(),
        )
        return

    storage = Storage()
    bot = Bot(BOT_TOKEN)

    offset_value = storage.get_state(STATE_LAST_UPDATE_ID)
    offset = int(offset_value) if offset_value is not None else None
    logger.info(
        "Starting collect at %s MSK with offset=%s. Allowed window: %s.",
        current_time.strftime("%H:%M"),
        offset,
        format_collect_window(),
    )

    while True:
        updates = await bot.get_updates(offset=offset, limit=100, timeout=0, allowed_updates=["message"])
        if not updates:
            break

        for update in updates:
            try:
                await _process_update(bot, storage, update)
            except Exception:
                logger.exception("Failed to process update %s", update.update_id)
            finally:
                offset = update.update_id + 1
                storage.set_state(STATE_LAST_UPDATE_ID, str(offset))

        if len(updates) < 100:
            break

    logger.info("Collection finished successfully.")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
