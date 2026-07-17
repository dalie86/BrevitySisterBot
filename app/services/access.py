from __future__ import annotations

from datetime import datetime

from app.domain.ports import ChatRepository, TelegramGateway


class AccessDenied(RuntimeError):
    def __init__(self, copy_key: str) -> None:
        super().__init__(copy_key)
        self.copy_key = copy_key


class AccessService:
    def __init__(
        self,
        chats: ChatRepository,
        telegram: TelegramGateway,
        bot_user_id: int,
    ) -> None:
        self.chats = chats
        self.telegram = telegram
        self.bot_user_id = bot_user_id

    def enable_daily(
        self,
        chat_id: int,
        actor_id: int,
        title: str,
        now: datetime | None,
    ) -> str:
        self._require_admin(chat_id, actor_id)
        if not self.telegram.get_chat_member(chat_id, self.bot_user_id).is_admin:
            raise AccessDenied("bot_admin_required")
        return self.chats.enable(chat_id, title, now)

    def disable_daily(
        self,
        chat_id: int,
        actor_id: int,
        now: datetime | None,
    ) -> None:
        self._require_admin(chat_id, actor_id)
        self.chats.disable(chat_id, now)

    def verify_member(self, chat_id: int, user_id: int) -> bool:
        return self.telegram.get_chat_member(chat_id, user_id).can_read

    def _require_admin(self, chat_id: int, user_id: int) -> None:
        if not self.telegram.get_chat_member(chat_id, user_id).is_admin:
            raise AccessDenied("admin_required")
