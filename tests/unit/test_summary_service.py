from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.content import BotCopy
from app.domain.models import LlmResult, StoredMessage, SummaryKind
from app.services import summaries as summaries_module
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
        self.active = set()

    def find_by_source_hash(self, chat_id, source_hash, kind):
        return self.items.get((chat_id, kind, source_hash))

    def begin_generation(self, chat_id, summary_date, kind, source_hash, requester_user_id, now):
        key = (chat_id, kind, source_hash)
        if key in self.active:
            return False
        self.active.add(key)
        return True

    def save(self, summary, requester_user_id, now):
        self.saved.append((summary, requester_user_id, now))
        self.items[(summary.chat_id, summary.kind, summary.source_hash)] = summary.text


class FakeLlm:
    def __init__(self, content: str = "") -> None:
        self.content = content
        self.call_count = 0
        self.error = None
        self.system_prompts = []
        self.user_prompts = []

    def generate_summary(self, system_prompt, user_prompt):
        self.call_count += 1
        self.system_prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        if self.error is not None:
            raise self.error
        return LlmResult(
            content=self.content,
            provider="deepseek",
            model="deepseek-v4-flash",
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
            copy=BotCopy.load(Path("content/bot_copy.ru.toml")),
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


def test_on_demand_uses_fallback_after_deepseek_error() -> None:
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
            return LlmResult(content, "deepseek", "model", 10)

    llm = RepairingLlm()
    service, _, _ = make_service(5, llm)

    result = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert result.fallback_type is None
    assert "Исправлено" in result.text
    assert llm.call_count == 2


def test_initial_and_repair_usage_is_aggregated_before_saving() -> None:
    class MeteredRepairingLlm(FakeLlm):
        def generate_summary(self, system_prompt, user_prompt):
            self.call_count += 1
            self.system_prompts.append(system_prompt)
            self.user_prompts.append(user_prompt)
            if self.call_count == 1:
                return LlmResult(
                    content="not-json",
                    provider="deepseek",
                    model="initial-model",
                    duration_ms=11,
                    input_tokens=101,
                    output_tokens=17,
                    cache_hit_input_tokens=31,
                    cache_miss_input_tokens=70,
                    estimated_cost_microusd=23,
                )
            return LlmResult(
                content='{"headline":"Исправлено","topics":[]}',
                provider="deepseek",
                model="repair-model",
                duration_ms=29,
                input_tokens=211,
                output_tokens=19,
                cache_hit_input_tokens=41,
                cache_miss_input_tokens=170,
                estimated_cost_microusd=47,
            )

    llm = MeteredRepairingLlm()
    service, _, summaries = make_service(5, llm)

    result = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    saved = summaries.saved[0][0]
    assert result == saved
    assert saved.provider == "deepseek"
    assert saved.model == "repair-model"
    assert saved.latency_ms == 40
    assert saved.input_tokens == 312
    assert saved.output_tokens == 36
    assert saved.cache_hit_input_tokens == 72
    assert saved.cache_miss_input_tokens == 240
    assert saved.llm_call_count == 2
    assert saved.input_characters == len(llm.user_prompts[0])
    assert saved.estimated_cost_microusd == 70


def test_successful_invalid_call_metrics_survive_heuristic_fallback() -> None:
    class InvalidThenErrorLlm(FakeLlm):
        def generate_summary(self, system_prompt, user_prompt):
            self.call_count += 1
            self.user_prompts.append(user_prompt)
            if self.call_count == 2:
                raise TimeoutError("repair failed")
            return LlmResult(
                content="not-json",
                provider="deepseek",
                model="last-successful-model",
                duration_ms=13,
                input_tokens=50,
                output_tokens=7,
                cache_hit_input_tokens=10,
                cache_miss_input_tokens=40,
                estimated_cost_microusd=9,
            )

    llm = InvalidThenErrorLlm()
    service, _, _ = make_service(5, llm)

    result = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert result.fallback_type == "heuristic"
    assert result.provider == "deepseek"
    assert result.model == "last-successful-model"
    assert result.latency_ms == 13
    assert result.input_tokens == 50
    assert result.output_tokens == 7
    assert result.cache_hit_input_tokens == 10
    assert result.cache_miss_input_tokens == 40
    assert result.llm_call_count == 1
    assert result.estimated_cost_microusd == 9


def test_active_generation_is_not_sent_to_llm_twice() -> None:
    service, llm, summaries = make_service(5)
    messages = service.messages.list_between(
        -1001,
        datetime(2026, 7, 15, tzinfo=timezone.utc),
        datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    from app.domain.models import build_source_hash

    source_hash = build_source_hash(
        (item.telegram_message_id, item.text, item.sent_at) for item in messages
    )
    summaries.active.add((-1001, SummaryKind.ON_DEMAND, source_hash))

    result = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert result.outcome == "preparing"
    assert llm.call_count == 0


def test_on_demand_cache_does_not_replace_scheduled_summary_record() -> None:
    service, llm, summaries = make_service(15)

    on_demand = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)
    scheduled = service.generate(-1001, SummaryKind.SCHEDULED, None, NOW)

    assert on_demand.kind is SummaryKind.ON_DEMAND
    assert scheduled.kind is SummaryKind.SCHEDULED
    assert scheduled.outcome == "generated"
    assert llm.call_count == 2
    assert len(summaries.saved) == 2


def test_generated_summary_fits_single_telegram_message() -> None:
    huge = "x" * 6000
    llm = FakeLlm('{"headline":"Главное","topics":[{"title":"Тема","details":"' + huge + '"}]}')
    service, _, _ = make_service(5, llm)

    result = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert len(result.text) <= 3900
    assert result.text.endswith("…")


def test_model_summary_uses_copy_template_and_escapes_dynamic_html() -> None:
    llm = FakeLlm(
        '{"headline":"A < B & C","topics":[{"title":"<релиз>","details":"готово & принято"}]}'
    )
    service, _, _ = make_service(5, llm)

    result = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert result.text == (
        "<b>📝 Краткая сводка за сегодня</b>\n\n"
        "A &lt; B &amp; C\n\n"
        "• &lt;релиз&gt;\nготово &amp; принято"
    )
    assert llm.system_prompts[0] == service.copy.value("prompts", "system")


def test_fallback_escapes_names_and_message_fragments() -> None:
    llm = FakeLlm()
    llm.error = TimeoutError("timeout")
    service, _, _ = make_service(5, llm)
    service.messages.items[1] = replace(
        service.messages.items[1],
        full_name="Pavel <admin>",
    )
    service.messages.items[-1] = replace(
        service.messages.items[-1],
        full_name="Pavel <admin>",
        text="2 < 3 & это важно",
    )

    result = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert result.text.startswith("<b>📝 Краткая сводка за сегодня</b>")
    assert "Pavel &lt;admin&gt;" in result.text
    assert "2 &lt; 3 &amp; это важно" in result.text


def test_html_truncation_does_not_cut_entity() -> None:
    text = "<b>title</b>\n\n" + ("&amp;" * 1000)

    result = summaries_module._telegram_safe_html(text)

    assert len(result) <= 3900
    assert not result.removesuffix("…").endswith(("&", "&a", "&am", "&amp"))


def test_prompt_uses_exact_catalog_transcript_line_and_user_templates() -> None:
    service, llm, _ = make_service(5)
    expected_lines = [
        service.copy.render(
            "prompts",
            "transcript_line",
            iso_datetime=message.sent_at.isoformat(),
            participant_name=message.full_name,
            message_text=message.text,
        )
        for message in service.messages.items
    ]

    service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert llm.user_prompts == [
        service.copy.render("prompts", "user", transcript="\n".join(expected_lines))
    ]


def test_repair_uses_catalog_template_and_caps_invalid_response_at_6000() -> None:
    invalid = "x" * 7000

    class RepairLlm(FakeLlm):
        def generate_summary(self, system_prompt, user_prompt):
            self.system_prompts.append(system_prompt)
            self.user_prompts.append(user_prompt)
            if len(self.user_prompts) == 1:
                return LlmResult(invalid, "deepseek", "model", 10)
            return LlmResult('{"headline":"fixed","topics":[]}', "deepseek", "model", 10)

    llm = RepairLlm()
    service, _, _ = make_service(5, llm)

    service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert llm.user_prompts[1] == service.copy.render(
        "prompts", "repair", invalid_model_response=invalid[:6000]
    )


def test_pavel_display_name_is_bound_to_telegram_id_before_llm():
    service, llm, _ = make_service(5)
    service.messages.items[0] = replace(
        service.messages.items[0],
        user_id=205580587,
        full_name="Pavel Shechkov",
    )
    service.messages.items[1] = replace(
        service.messages.items[1],
        user_id=205580587,
        full_name="Новое имя профиля",
    )
    service.generate(-1001, SummaryKind.ON_DEMAND, 205580587, NOW)
    assert "Павел:" in llm.user_prompts[0]
    assert "Pavel Shechkov" not in llm.user_prompts[0]
    assert "Новое имя профиля" not in llm.user_prompts[0]
    assert "User 2" in llm.user_prompts[0]


def test_generated_and_cached_summary_remove_known_full_name_variants():
    llm = FakeLlm(
        '{"headline":"Планы Павла Щечкова","topics":[{"title":"Встреча с Павлом Шечковым","details":"Павел Щечков предложил встретиться. Алексей Шечков ответил."}]}'
    )
    service, _, store = make_service(5, llm)
    first = service.generate(-1001, SummaryKind.ON_DEMAND, 205580587, NOW)
    assert "Планы Павла" in first.text
    assert "Встреча с Павлом" in first.text
    assert "Павел предложил" in first.text
    assert "Щечков" not in first.text
    assert "Алексей Шечков ответил" in first.text
    key = next(iter(store.items))
    store.items[key] = "<b>Планы Павла Щечкова</b>\nPavel Shechkov сообщил о поездке."
    cached = service.generate(-1001, SummaryKind.ON_DEMAND, 205580587, NOW)
    assert cached.text == "<b>Планы Павла</b>\nПавел сообщил о поездке."
    assert cached.outcome == "cached"
    assert llm.call_count == 1


def test_fallback_uses_canonical_name_and_normalizes_message_mentions():
    llm = FakeLlm()
    llm.error = TimeoutError("offline")
    service, _, _ = make_service(5, llm)
    service.messages.items = [
        replace(
            m, user_id=205580587, full_name="Pavel Shechkov", text="Встретимся с Павлом Щечковым"
        )
        for m in service.messages.items
    ]
    result = service.generate(-1001, SummaryKind.ON_DEMAND, 205580587, NOW)
    assert "Активные участники: Павел." in result.text
    assert "Встретимся с Павлом" in result.text
    assert "Щечков" not in result.text
    assert "Shechkov" not in result.text
