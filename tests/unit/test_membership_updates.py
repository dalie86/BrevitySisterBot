from datetime import datetime, timezone

from app.domain.models import TelegramMembership
from app.integrations.telegram import IncomingChatMember, IncomingUpdate
from app.services.membership_updates import MembershipUpdateHandler


NOW = datetime(2026, 7, 16, 18, 15, tzinfo=timezone.utc)


class FakeMembers:
    def __init__(self) -> None:
        self.recorded = []

    def record_membership(self, chat_id, user_id, status, is_member, now) -> None:
        self.recorded.append((chat_id, user_id, status, is_member, now))


def test_departure_event_revokes_known_private_access() -> None:
    members = FakeMembers()
    handler = MembershipUpdateHandler(members=members, now=lambda: NOW)

    handler(
        IncomingUpdate(
            update_id=30,
            chat_member=IncomingChatMember(
                chat_id=-1001,
                user_id=42,
                username="@pavel",
                full_name="Pavel",
                new_membership=TelegramMembership("left", False),
            ),
        )
    )

    assert members.recorded == [(-1001, 42, "left", False, NOW)]


def test_non_membership_update_is_ignored() -> None:
    members = FakeMembers()
    handler = MembershipUpdateHandler(members=members, now=lambda: NOW)

    handler(IncomingUpdate(update_id=31))

    assert members.recorded == []
