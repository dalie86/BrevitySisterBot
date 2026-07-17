from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.domain.ports import MemberRepository
from app.integrations.telegram import IncomingUpdate

NowFactory = Callable[[], datetime]


class MembershipUpdateHandler:
    def __init__(self, *, members: MemberRepository, now: NowFactory) -> None:
        self.members = members
        self.now = now

    def __call__(self, update: IncomingUpdate) -> None:
        event = update.chat_member
        if event is None:
            return
        self.members.record_membership(
            event.chat_id,
            event.user_id,
            event.new_membership.status,
            event.new_membership.is_member,
            self.now(),
        )
