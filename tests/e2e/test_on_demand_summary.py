from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.content import BotCopy
from app.domain.models import GeneratedSummary, SummaryStatus
from app.services.on_demand import OnDemandSummaryService


NOW = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
COPY = BotCopy.load(Path("content/bot_copy.ru.toml"))


class FakeTelegram:
    def __init__(self) -> None:
        self.sent = []
        self.recipients = []

    def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, *, receiver_user_id=None):
        self.sent.append((chat_id, text, reply_markup, parse_mode))
        self.recipients.append(receiver_user_id)
        return len(self.sent)


class FakeSummaries:
    def __init__(self, *, outcome="generated") -> None:
        self.outcome = outcome

    def generate(self, chat_id, kind, requester_user_id, now):
        return GeneratedSummary(
            chat_id=chat_id,
            summary_date="2026-07-16",
            kind=kind,
            source_hash="hash",
            text="<b>готовая сводка</b>",
            status=SummaryStatus.PREPARED,
            outcome=self.outcome,
        )


def make_service(outcome="generated"):
    telegram = FakeTelegram()
    service = OnDemandSummaryService(
        summaries=FakeSummaries(outcome=outcome),
        telegram=telegram,
        copy=COPY,
        now=lambda: NOW,
    )
    return service, telegram


def test_request_acknowledges_then_posts_html_result() -> None:
    service, telegram = make_service()

    service.request(chat_id=-1001, requester_user_id=42, destination_chat_id=-1001)

    assert telegram.sent == [
        (-1001, COPY.value("messages", "summary_accepted"), None, None),
        (-1001, "<b>готовая сводка</b>", None, "HTML"),
    ]
    assert "до 2 минут" in telegram.sent[0][1]


def test_acceptance_can_remove_picker_keyboard() -> None:
    service, telegram = make_service()

    service.request(
        chat_id=-1001,
        requester_user_id=42,
        destination_chat_id=42,
        ack_reply_markup={"remove_keyboard": True},
    )

    assert telegram.sent[0] == (
        42,
        COPY.value("messages", "summary_accepted"),
        {"remove_keyboard": True},
        None,
    )
    assert telegram.sent[1][2] is None


def test_request_without_ack_posts_only_html_result() -> None:
    service, telegram = make_service()

    service.request(
        chat_id=-1001,
        requester_user_id=42,
        destination_chat_id=42,
        send_ack=False,
    )

    assert telegram.sent == [(42, "<b>готовая сводка</b>", None, "HTML")]


@pytest.mark.parametrize(
    ("outcome", "copy_key"),
    [
        ("preparing", "summary_in_progress"),
        ("insufficient_messages", "not_enough_messages"),
    ],
)
def test_non_generated_outcomes_use_toml_copy(outcome, copy_key) -> None:
    service, telegram = make_service(outcome)

    service.request(chat_id=-1001, requester_user_id=42, destination_chat_id=42)

    assert telegram.sent[-1] == (
        42,
        COPY.value("messages", copy_key),
        None,
        None,
    )


@pytest.mark.parametrize("outcome", ["generated", "preparing", "insufficient_messages"])
def test_group_responses_are_ephemeral(outcome):
    service, telegram = make_service(outcome)
    service.request(chat_id=-1001, requester_user_id=42, destination_chat_id=-1001)
    assert telegram.recipients == [42, 42]


def test_group_delivery_failure_never_retries_publicly():
    service, telegram = make_service()
    def fail(chat_id, text, **kwargs):
        telegram.recipients.append(kwargs.get("receiver_user_id"))
        raise RuntimeError("rejected")
    telegram.send_message = fail
    with pytest.raises(RuntimeError, match="rejected"):
        service.request(chat_id=-1001, requester_user_id=42, destination_chat_id=-1001)
    assert telegram.recipients == [42]
