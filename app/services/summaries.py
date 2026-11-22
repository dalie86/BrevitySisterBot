from __future__ import annotations

import html
import json
import re
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from app.content import BotCopy
from app.domain.models import (
    GeneratedSummary,
    LlmResult,
    StoredMessage,
    SummaryKind,
    SummaryStatus,
    build_source_hash,
)
from app.domain.ports import LlmGateway, MessageRepository, SummaryRepository

from app.services.participant_names import normalize_summary_names, participant_name

TRANSCRIPT_CHARACTER_LIMIT = 18_000
TELEGRAM_SUMMARY_LIMIT = 3_900


class SummaryService:
    def __init__(
        self,
        *,
        messages: MessageRepository,
        summaries: SummaryRepository,
        llm: LlmGateway,
        copy: BotCopy,
        timezone_name: str,
        automatic_min_messages: int,
        on_demand_min_messages: int,
    ) -> None:
        self.messages = messages
        self.summaries = summaries
        self.llm = llm
        self.copy = copy
        self.timezone = ZoneInfo(timezone_name)
        self.automatic_min_messages = automatic_min_messages
        self.on_demand_min_messages = on_demand_min_messages

    def generate(
        self,
        chat_id: int,
        kind: SummaryKind,
        requester_user_id: int | None,
        now: datetime,
    ) -> GeneratedSummary:
        local_now = now.astimezone(self.timezone)
        local_start = datetime.combine(local_now.date(), time.min, self.timezone)
        local_end = local_start + timedelta(days=1)
        messages = list(
            self.messages.list_between(
                chat_id,
                local_start.astimezone(timezone.utc),
                local_end.astimezone(timezone.utc),
            )
        )
        source_hash = build_source_hash(
            (item.telegram_message_id, item.text, item.sent_at) for item in messages
        )
        minimum = (
            self.automatic_min_messages
            if kind is SummaryKind.SCHEDULED
            else self.on_demand_min_messages
        )
        if len(messages) < minimum:
            return GeneratedSummary(
                chat_id=chat_id,
                summary_date=local_now.date().isoformat(),
                kind=kind,
                source_hash=source_hash,
                text="",
                status=SummaryStatus.FAILED,
                outcome="insufficient_messages",
                message_count=len(messages),
            )

        cached = self.summaries.find_by_source_hash(chat_id, source_hash, kind)
        if cached:
            return GeneratedSummary(
                chat_id=chat_id,
                summary_date=local_now.date().isoformat(),
                kind=kind,
                source_hash=source_hash,
                text=normalize_summary_names(cached),
                status=SummaryStatus.PREPARED,
                outcome="cached",
                message_count=len(messages),
            )

        acquired = self.summaries.begin_generation(
            chat_id,
            local_now.date().isoformat(),
            kind,
            source_hash,
            requester_user_id,
            now,
        )
        if not acquired:
            return GeneratedSummary(
                chat_id=chat_id,
                summary_date=local_now.date().isoformat(),
                kind=kind,
                source_hash=source_hash,
                text="",
                status=SummaryStatus.PREPARING,
                outcome="preparing",
                message_count=len(messages),
            )

        provider = None
        model = None
        fallback_type = None
        llm_results: list[LlmResult] = []
        user_prompt = _build_user_prompt(messages, self.copy)
        try:
            system_prompt = self.copy.value("prompts", "system")
            llm_result = self.llm.generate_summary(
                system_prompt,
                user_prompt,
            )
            llm_results.append(llm_result)
            payload = _parse_payload(llm_result.content)
            if payload is None:
                repair = self.llm.generate_summary(
                    system_prompt,
                    self.copy.render(
                        "prompts",
                        "repair",
                        invalid_model_response=llm_result.content[:6000],
                    ),
                )
                llm_results.append(repair)
                payload = _parse_payload(repair.content)
            if payload is None:
                raise ValueError("unusable summary JSON")
            text = _render_payload(payload, self.copy)
        except Exception:
            text = _heuristic_summary(messages, self.copy)
            fallback_type = "heuristic"

        if llm_results:
            provider = llm_results[-1].provider
            model = llm_results[-1].model

        summary = GeneratedSummary(
            chat_id=chat_id,
            summary_date=local_now.date().isoformat(),
            kind=kind,
            source_hash=source_hash,
            text=normalize_summary_names(text),
            status=SummaryStatus.PREPARED,
            provider=provider,
            model=model,
            fallback_type=fallback_type,
            message_count=len(messages),
            latency_ms=sum(result.duration_ms for result in llm_results),
            input_tokens=sum(result.input_tokens or 0 for result in llm_results),
            output_tokens=sum(result.output_tokens or 0 for result in llm_results),
            input_characters=len(user_prompt),
            cache_hit_input_tokens=sum(
                result.cache_hit_input_tokens or 0 for result in llm_results
            ),
            cache_miss_input_tokens=sum(
                result.cache_miss_input_tokens or 0 for result in llm_results
            ),
            llm_call_count=len(llm_results),
            estimated_cost_microusd=sum(
                result.estimated_cost_microusd for result in llm_results
            ),
        )
        self.summaries.save(summary, requester_user_id, now)
        return summary


def _build_user_prompt(messages: Sequence[StoredMessage], copy: BotCopy) -> str:
    lines = [
        copy.render(
            "prompts",
            "transcript_line",
            iso_datetime=message.sent_at.isoformat(),
            participant_name=participant_name(message.user_id, message.full_name),
            message_text=normalize_summary_names(message.text),
        )
        for message in messages
    ]
    transcript = "\n".join(lines)
    if len(transcript) > TRANSCRIPT_CHARACTER_LIMIT:
        transcript = transcript[-TRANSCRIPT_CHARACTER_LIMIT:]
    return copy.render("prompts", "user", transcript=transcript)


def _parse_payload(raw: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if match is None:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    headline = str(payload.get("headline", "")).strip()
    topics = payload.get("topics", [])
    if not headline or not isinstance(topics, list):
        return None
    normalized_topics = []
    for topic in topics[:4]:
        if not isinstance(topic, dict):
            continue
        title = str(topic.get("title", "")).strip()
        details = str(topic.get("details", "")).strip()
        if title and details:
            normalized_topics.append({"title": title, "details": details})
    return {"headline": headline[:500], "topics": normalized_topics}


def _escaped(value: object) -> str:
    return html.escape(str(value), quote=False)


def _render_payload(payload: dict[str, Any], copy: BotCopy) -> str:
    topics = [
        copy.render(
            "summary",
            "topic",
            topic_title=_escaped(topic["title"]),
            topic_details=_escaped(topic["details"]),
        )
        for topic in payload["topics"]
    ]
    text = copy.render(
        "summary",
        "document",
        headline=_escaped(payload["headline"]),
        topics_block=copy.value("summary", "topics_separator").join(topics),
    ).rstrip()
    return _telegram_safe_html(text)


def _heuristic_summary(messages: Sequence[StoredMessage], copy: BotCopy) -> str:
    participants = Counter(participant_name(message.user_id, message.full_name) for message in messages)
    top_people = ", ".join(_escaped(name) for name, _ in participants.most_common(3))
    recent = [message.text.strip() for message in messages[-3:] if message.text.strip()]
    recent_messages = [
        copy.render("fallback", "message_item", message_text=_escaped(text[:240]))
        for text in recent
    ]
    text = copy.render(
        "fallback",
        "document",
        message_count=_escaped(len(messages)),
        participants=top_people or copy.value("fallback", "participants_unknown"),
        recent_messages=copy.value("fallback", "messages_separator").join(recent_messages),
    ).rstrip()
    return _telegram_safe_html(text)


def _telegram_safe_html(text: str) -> str:
    if len(text) <= TELEGRAM_SUMMARY_LIMIT:
        return text
    cut = text[: TELEGRAM_SUMMARY_LIMIT - 1].rstrip()
    if cut.rfind("&") > cut.rfind(";"):
        cut = cut[: cut.rfind("&")].rstrip()
    return cut + "…"
