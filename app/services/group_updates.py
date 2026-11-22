from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.content import BotCopy
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
        access: AccessService,
        copy: BotCopy,
        telegram_bot_username: str,
        summary_request: SummaryRequest,
        now: NowFactory,
    ) -> None:
        self.chats = chats
        self.members = members
        self.messages = messages
        self.telegram = telegram
        self.access = access
        self.copy = copy
        self.telegram_bot_username = telegram_bot_username
        self.summary_request = summary_request
        self.now = now

    def __call__(self, update: IncomingUpdate) -> None:
        message = update.message
        if message is None or message.chat_type not in {"group", "supergroup"}:
            return
        if message.user_id is None or message.sender_is_bot:
            return

        current = self.now()
        command = _command_name(message.text, self.telegram_bot_username)
        if message.ephemeral_message_id is not None and command != "/summary":
            # Never include private input in the shared summary buffer.
            return
        if command is None and message.text.strip().startswith("/"):
            return
        if command == "/start":
            self.telegram.send_message(
                message.chat_id,
                self.copy.value("messages", "group_commands"),
            )
            return
        if command == "/enable_daily":
            try:
                token = self.access.enable_daily(
                    message.chat_id,
                    message.user_id,
                    message.chat_title,
                    current,
                )
            except AccessDenied as exc:
                self.telegram.send_message(
                    message.chat_id,
                    self.copy.value("messages", exc.copy_key),
                )
                return
            self.members.remember_group(
                message.chat_id,
                message.user_id,
                message.username,
                message.full_name,
                current,
            )
            self.telegram.send_message(
                message.chat_id,
                self.copy.value("messages", "daily_enabled"),
                reply_markup={
                    "inline_keyboard": [
                        [
                            {
                                "text": self.copy.value(
                                    "buttons", "get_private_summary"
                                ),
                                "url": (
                                    f"https://t.me/{self.telegram_bot_username}"
                                    f"?start=summary_{token}"
                                ),
                            }
                        ]
                    ]
                },
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
                "daily_disabled",
            )
            return
        if command == "/summary":
            if not self.chats.is_tracked(message.chat_id):
                self.telegram.send_message(
                    message.chat_id,
                    self.copy.value("messages", "group_not_connected"),
                    receiver_user_id=message.user_id,
                )
                return
            self.members.remember_group(
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

        self.members.remember_group(
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
        success_copy_key: str,
    ) -> None:
        try:
            operation()
        except AccessDenied as exc:
            self.telegram.send_message(
                chat_id,
                self.copy.value("messages", exc.copy_key),
            )
            return
        self.telegram.send_message(
            chat_id,
            self.copy.value("messages", success_copy_key),
        )


def _command_name(text: str, telegram_bot_username: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    command_token = stripped.split(maxsplit=1)[0]
    command, separator, addressed_username = command_token.partition("@")
    if separator and addressed_username.casefold() != telegram_bot_username.lstrip(
        "@"
    ).casefold():
        return None
    return command.lower()
