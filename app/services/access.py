from __future__ import annotations

from datetime import datetime

from app.domain.ports import ChatRepository, TelegramGateway

ADMIN_STATUSES = frozenset({"creator", "administrator"})
MEMBER_STATUSES = frozenset({"creator", "administrator", "member", "restricted"})


class AccessDenied(RuntimeError):
    pass


class AccessService:
    def __init__(self, chats: ChatRepository, telegram: TelegramGateway) -> None:
        self.chats = chats
        self.telegram = telegram

    def enable_daily(
        self,
        chat_id: int,
        actor_id: int,
        title: str,
        now: datetime | None,
    ) -> None:
        self._require_admin(chat_id, actor_id)
        self.chats.enable(chat_id, title, now)

    def disable_daily(
        self,
        chat_id: int,
        actor_id: int,
        now: datetime | None,
    ) -> None:
        self._require_admin(chat_id, actor_id)
        self.chats.disable(chat_id, now)

    def verify_member(self, chat_id: int, user_id: int) -> bool:
        return self.telegram.get_chat_member(chat_id, user_id) in MEMBER_STATUSES

    def _require_admin(self, chat_id: int, user_id: int) -> None:
        if self.telegram.get_chat_member(chat_id, user_id) not in ADMIN_STATUSES:
            raise AccessDenied("Только администратор группы может менять настройки сводки.")
