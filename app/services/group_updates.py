from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.domain.models import StoredMessage
from app.domain.ports import (
    ChatRepository,
    MemberRepository,
    MessageRepository,
    TelegramGateway,
)
from app.integrations.telegram import IncomingUpdate
from app.services.access import AccessDenied, AccessService

NowFactory = Callable[[], datetime]
SummaryRequest = Callable[[int, int], None]


class GroupUpdateHandler:
    def __init__(
        self,
        *,
        chats: ChatRepository,
        members: MemberRepository,
        messages: MessageRepository,
        telegram: TelegramGateway,
        summary_request: SummaryRequest,
        now: NowFactory,
    ) -> None:
        self.chats = chats
        self.members = members
        self.messages = messages
        self.telegram = telegram
        self.summary_request = summary_request
        self.now = now
        self.access = AccessService(chats, telegram)

    def __call__(self, update: IncomingUpdate) -> None:
        message = update.message
        if message is None or message.chat_type not in {"group", "supergroup"}:
            return
        if message.user_id is None or message.sender_is_bot:
            return

        current = self.now()
        command = _command_name(message.text)
        if command == "/start":
            self.telegram.send_message(
                message.chat_id,
                "Команды: /enable_daily, /disable_daily, /summary",
            )
            return
        if command == "/enable_daily":
            self._change_group_setting(
                lambda: self.access.enable_daily(
                    message.chat_id,
                    message.user_id,
                    message.chat_title,
                    current,
                ),
                message.chat_id,
                "Ежедневная сводка включена. Публикация — в 21:00 по Москве.",
            )
            return
        if command == "/disable_daily":
            self._change_group_setting(
                lambda: self.access.disable_daily(
                    message.chat_id,
                    message.user_id,
                    current,
                ),
                message.chat_id,
                "Ежедневная публикация выключена. Буферизация сообщений продолжается.",
            )
            return
        if command == "/summary":
            if not self.chats.is_tracked(message.chat_id):
                self.telegram.send_message(
                    message.chat_id,
                    "Сначала администратор должен подключить группу через /enable_daily.",
                )
                return
            self.members.observe(
                message.chat_id,
                message.user_id,
                message.username,
                message.full_name,
                current,
            )
            self.members.confirm_summary_link(message.chat_id, message.user_id, current)
            self.summary_request(message.chat_id, message.user_id)
            return
        if command is not None:
            return
        if not self.chats.is_tracked(message.chat_id) or not message.text:
            return

        self.members.observe(
            message.chat_id,
            message.user_id,
            message.username,
            message.full_name,
            current,
        )
        self.messages.add(
            StoredMessage(
                telegram_message_id=message.message_id,
                chat_id=message.chat_id,
                user_id=message.user_id,
                username=message.username,
                full_name=message.full_name,
                text=message.text,
                sent_at=message.sent_at,
            )
        )

    def _change_group_setting(
        self,
        operation: Callable[[], None],
        chat_id: int,
        success_text: str,
    ) -> None:
        try:
            operation()
        except AccessDenied as exc:
            self.telegram.send_message(chat_id, str(exc))
            return
        self.telegram.send_message(chat_id, success_text)


def _command_name(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    return stripped.split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()
