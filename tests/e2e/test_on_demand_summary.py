from datetime import datetime, timezone

from app.domain.models import GeneratedSummary, SummaryStatus
from app.services.on_demand import OnDemandSummaryService


NOW = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)


class FakeTelegram:
    def __init__(self) -> None:
        self.sent = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))
        return len(self.sent)


class FakeSummaries:
    def generate(self, chat_id, kind, requester_user_id, now):
        return GeneratedSummary(
            chat_id=chat_id,
            summary_date="2026-07-16",
            kind=kind,
            source_hash="hash",
            text="готовая сводка",
            status=SummaryStatus.PREPARED,
        )


def test_group_request_acknowledges_then_posts_result_to_group() -> None:
    telegram = FakeTelegram()
    service = OnDemandSummaryService(
        summaries=FakeSummaries(),
        telegram=telegram,
        now=lambda: NOW,
    )

    service.request(chat_id=-1001, requester_user_id=42, destination_chat_id=-1001)

    assert telegram.sent == [
        (
            -1001,
            "Запрос принят. Подготовка сводки может занять до 1–2 минут. "
            "Пожалуйста, не отправляйте команду повторно.",
        ),
        (-1001, "готовая сводка"),
    ]
