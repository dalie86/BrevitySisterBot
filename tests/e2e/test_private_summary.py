from datetime import datetime, timezone

from app.domain.models import GeneratedSummary, SummaryKind, SummaryStatus
from app.integrations.telegram import IncomingCallback, IncomingMessage, IncomingUpdate
from app.services.on_demand import OnDemandSummaryHandler


NOW = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)


class FakeMembers:
    def __init__(self, groups) -> None:
        self.groups = groups

    def linked_groups(self, user_id):
        return list(self.groups)


class FakeSelections:
    def __init__(self) -> None:
        self.items = {}
        self.counter = 0

    def create(self, user_id, chat_id, now):
        self.counter += 1
        token = f"opaque-{self.counter}"
        self.items[(token, user_id)] = chat_id
        return token

    def resolve(self, token, user_id, now):
        return self.items.get((token, user_id))


class FakeTelegram:
    def __init__(self) -> None:
        self.sent = []
        self.membership = {}

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return len(self.sent)

    def get_chat_member(self, chat_id, user_id):
        return self.membership.get((chat_id, user_id), "left")


class FakeSummaryService:
    def __init__(self) -> None:
        self.calls = []

    def generate(self, chat_id, kind, requester_user_id, now):
        self.calls.append((chat_id, kind, requester_user_id, now))
        return GeneratedSummary(
            chat_id=chat_id,
            summary_date="2026-07-16",
            kind=kind,
            source_hash="hash",
            text="📝 Сводка за сегодня\n\nГлавное",
            status=SummaryStatus.PREPARED,
        )


def private_command(user_id=42):
    return IncomingUpdate(
        update_id=1,
        message=IncomingMessage(
            message_id=1,
            chat_id=user_id,
            chat_type="private",
            chat_title="Private",
            user_id=user_id,
            username="@pavel",
            full_name="Pavel",
            sender_is_bot=False,
            text="/summary",
            sent_at=NOW,
        ),
    )


def make_handler(groups):
    telegram = FakeTelegram()
    summaries = FakeSummaryService()
    selections = FakeSelections()
    handler = OnDemandSummaryHandler(
        members=FakeMembers(groups),
        selections=selections,
        telegram=telegram,
        summaries=summaries,
        now=lambda: NOW,
    )
    return handler, telegram, summaries, selections


def test_private_summary_for_one_group_is_private() -> None:
    handler, telegram, summaries, _ = make_handler([(-1001, "Group 1")])
    telegram.membership[-1001, 42] = "member"

    handler(private_command())

    assert telegram.sent[0][0] == 42
    assert "1–2 минут" in telegram.sent[0][1]
    assert telegram.sent[-1][0] == 42
    assert "сводк" in telegram.sent[-1][1].lower()
    assert summaries.calls[0][0:3] == (-1001, SummaryKind.ON_DEMAND, 42)


def test_private_summary_with_multiple_groups_shows_opaque_buttons() -> None:
    handler, telegram, _, selections = make_handler([(-1001, "Group 1"), (-1002, "Group 2")])

    handler(private_command())

    markup = telegram.sent[-1][2]
    buttons = [row[0] for row in markup["inline_keyboard"]]
    assert [button["text"] for button in buttons] == ["Group 1", "Group 2"]
    assert all(button["callback_data"].startswith("summary_group:opaque-") for button in buttons)
    assert all("-100" not in button["callback_data"] for button in buttons)
    assert selections.counter == 2


def test_callback_rechecks_membership_before_private_delivery() -> None:
    handler, telegram, summaries, selections = make_handler([(-1001, "Group 1")])
    token = selections.create(42, -1001, NOW)
    telegram.membership[-1001, 42] = "left"

    handler(
        IncomingUpdate(
            update_id=2,
            callback=IncomingCallback(
                callback_id="callback",
                user_id=42,
                chat_id=42,
                data=f"summary_group:{token}",
            ),
        )
    )

    assert summaries.calls == []
    assert "не состоите" in telegram.sent[-1][1]
