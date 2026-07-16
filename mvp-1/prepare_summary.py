from __future__ import annotations

import logging
import os
from datetime import datetime

from storage import Storage
from workflow import (
    BOT_TIMEZONE,
    MIN_MESSAGES_FOR_SUMMARY,
    build_summary_for_chat,
    get_summary_messages_for_chat,
    now_msk,
    summary_date,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def _llm_label() -> str:
    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower() or "ollama"

    if provider in {"ollama", "local", "qwen"}:
        model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b-instruct").strip()
        return f"ollama/{model}" if model else "ollama"
    return "ollama"


def main() -> None:
    storage = Storage()
    now = now_msk()
    today = summary_date(now)
    llm_label = _llm_label()

    for chat in storage.get_enabled_chats():
        chat_id = int(chat["chat_id"])
        chat_title = str(chat["title"])
        messages = get_summary_messages_for_chat(storage, chat_id=chat_id, now=now)
        if len(messages) < MIN_MESSAGES_FOR_SUMMARY:
            storage.delete_prepared_summary(chat_id=chat_id, summary_date=today)
            logger.info(
                "Skipping summary for chat %s: only %s messages collected, need at least %s.",
                chat_id,
                len(messages),
                MIN_MESSAGES_FOR_SUMMARY,
            )
            continue
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
        logger.info("Prepared summary for chat %s via %s", chat_id, llm_label)


if __name__ == "__main__":
    main()
