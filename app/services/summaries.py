from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from app.domain.models import (
    GeneratedSummary,
    StoredMessage,
    SummaryKind,
    SummaryStatus,
    build_source_hash,
)
from app.domain.ports import LlmGateway, MessageRepository, SummaryRepository

TRANSCRIPT_CHARACTER_LIMIT = 18_000
TELEGRAM_SUMMARY_LIMIT = 3_900
SYSTEM_PROMPT = (
    "Ты редактор краткой сводки Telegram-группы. Пиши по-русски, строго по фактам "
    "из переписки, без домыслов и персональных оценок. Верни только JSON: "
    '{"headline":"главный итог дня","topics":[{"title":"тема","details":"конкретные итоги"}]}. '
    "Не более четырех тем."
)


class SummaryService:
    def __init__(
        self,
        *,
        messages: MessageRepository,
        summaries: SummaryRepository,
        llm: LlmGateway,
        timezone_name: str,
        automatic_min_messages: int,
        on_demand_min_messages: int,
    ) -> None:
        self.messages = messages
        self.summaries = summaries
        self.llm = llm
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
                text=cached,
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
        try:
            llm_result = self.llm.generate_summary(
                SYSTEM_PROMPT,
                _build_user_prompt(messages),
            )
            payload = _parse_payload(llm_result.content)
            if payload is None:
                repair = self.llm.generate_summary(
                    SYSTEM_PROMPT,
                    "Исправь ответ в требуемый JSON без добавления фактов:\n"
                    + llm_result.content[:6000],
                )
                payload = _parse_payload(repair.content)
                llm_result = repair
            if payload is None:
                raise ValueError("unusable summary JSON")
            text = _render_payload(payload)
            provider = llm_result.provider
            model = llm_result.model
        except Exception:
            text = _heuristic_summary(messages)
            fallback_type = "heuristic"

        summary = GeneratedSummary(
            chat_id=chat_id,
            summary_date=local_now.date().isoformat(),
            kind=kind,
            source_hash=source_hash,
            text=text,
            status=SummaryStatus.PREPARED,
            provider=provider,
            model=model,
            fallback_type=fallback_type,
            message_count=len(messages),
        )
        self.summaries.save(summary, requester_user_id, now)
        return summary


def _build_user_prompt(messages: Sequence[StoredMessage]) -> str:
    lines = [
        f"[{message.sent_at.isoformat()}] {message.full_name}: {message.text}"
        for message in messages
    ]
    transcript = "\n".join(lines)
    if len(transcript) > TRANSCRIPT_CHARACTER_LIMIT:
        transcript = transcript[-TRANSCRIPT_CHARACTER_LIMIT:]
    return "Подготовь сводку сегодняшней переписки:\n\n" + transcript


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


def _render_payload(payload: dict[str, Any]) -> str:
    lines = ["📝 Сводка за сегодня", "", str(payload["headline"])]
    for topic in payload["topics"]:
        lines.extend(("", f"• {topic['title']}", topic["details"]))
    return _telegram_safe_text("\n".join(lines))


def _heuristic_summary(messages: Sequence[StoredMessage]) -> str:
    participants = Counter(message.full_name for message in messages)
    top_people = ", ".join(name for name, _ in participants.most_common(3))
    recent = [message.text.strip() for message in messages[-3:] if message.text.strip()]
    lines = [
        "📝 Сводка за сегодня",
        "",
        f"Сообщений за сегодня: {len(messages)}.",
        f"Активные участники: {top_people or 'не определены'}.",
    ]
    if recent:
        lines.append("Последние обсуждавшиеся вопросы:")
        lines.extend(f"• {text[:240]}" for text in recent)
    return _telegram_safe_text("\n".join(lines))


def _telegram_safe_text(text: str) -> str:
    if len(text) <= TELEGRAM_SUMMARY_LIMIT:
        return text
    return text[: TELEGRAM_SUMMARY_LIMIT - 1].rstrip() + "…"
