from datetime import datetime, timedelta, timezone

from app.domain.models import LlmResult, StoredMessage, SummaryKind
from app.services.summaries import SummaryService


NOW = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)


class FakeMessages:
    def __init__(self, count: int) -> None:
        self.items = [
            StoredMessage(
                telegram_message_id=index + 1,
                chat_id=-1001,
                user_id=40 + index % 3,
                username=f"@user{index % 3}",
                full_name=f"User {index % 3}",
                text=f"Сообщение про релиз и важный вопрос номер {index + 1}",
                sent_at=NOW - timedelta(minutes=count - index),
            )
            for index in range(count)
        ]

    def list_between(self, chat_id, start, end):
        return [
            item for item in self.items if item.chat_id == chat_id and start <= item.sent_at < end
        ]


class FakeSummaries:
    def __init__(self) -> None:
        self.items = {}
        self.saved = []

    def find_by_source_hash(self, chat_id, source_hash):
        return self.items.get((chat_id, source_hash))

    def save(self, summary, requester_user_id, now):
        self.saved.append((summary, requester_user_id, now))
        self.items[(summary.chat_id, summary.source_hash)] = summary.text


class FakeLlm:
    def __init__(self, content: str = "") -> None:
        self.content = content
        self.call_count = 0
        self.error = None

    def generate_summary(self, system_prompt, user_prompt):
        self.call_count += 1
        if self.error is not None:
            raise self.error
        return LlmResult(
            content=self.content,
            provider="groq",
            model="qwen/qwen3.6-27b",
            duration_ms=25,
        )


def make_service(count: int, llm: FakeLlm | None = None):
    summaries = FakeSummaries()
    selected_llm = llm or FakeLlm(
        '{"headline":"Главное","topics":[{"title":"Релиз","details":"Договорились о запуске"}]}'
    )
    return (
        SummaryService(
            messages=FakeMessages(count),
            summaries=summaries,
            llm=selected_llm,
            timezone_name="Europe/Moscow",
            automatic_min_messages=15,
            on_demand_min_messages=5,
        ),
        selected_llm,
        summaries,
    )


def test_scheduled_summary_requires_15_messages() -> None:
    service, llm, _ = make_service(14)

    result = service.generate(-1001, SummaryKind.SCHEDULED, None, NOW)

    assert result.outcome == "insufficient_messages"
    assert llm.call_count == 0


def test_on_demand_uses_fallback_after_groq_error() -> None:
    llm = FakeLlm()
    llm.error = TimeoutError("timeout")
    service, _, summaries = make_service(5, llm)

    result = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert result.outcome == "generated"
    assert result.fallback_type == "heuristic"
    assert "Сообщений за сегодня: 5" in result.text
    assert summaries.saved


def test_valid_json_is_rendered_and_cached_by_source_hash() -> None:
    service, llm, _ = make_service(5)

    first = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)
    second = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert "Главное" in first.text
    assert second.text == first.text
    assert second.outcome == "cached"
    assert llm.call_count == 1


def test_invalid_json_gets_one_repair_attempt_then_fallback() -> None:
    class RepairingLlm(FakeLlm):
        def generate_summary(self, system_prompt, user_prompt):
            self.call_count += 1
            content = (
                "not-json" if self.call_count == 1 else '{"headline":"Исправлено","topics":[]}'
            )
            return LlmResult(content, "groq", "model", 10)

    llm = RepairingLlm()
    service, _, _ = make_service(5, llm)

    result = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert result.fallback_type is None
    assert "Исправлено" in result.text
    assert llm.call_count == 2
