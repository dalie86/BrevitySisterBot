from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime
from typing import Optional, Sequence

from llm_clients import request_summary_json
from storage import StoredMessage

logger = logging.getLogger(__name__)

SUMMARY_LIMIT = 1000
TOPIC_LIMIT = 4
TRANSCRIPT_CHAR_LIMIT = 12000

STOPWORDS = {
    "а",
    "без",
    "более",
    "бы",
    "был",
    "была",
    "были",
    "было",
    "быть",
    "вам",
    "вас",
    "весь",
    "вот",
    "все",
    "всё",
    "вы",
    "где",
    "да",
    "даже",
    "для",
    "его",
    "ее",
    "её",
    "если",
    "есть",
    "еще",
    "ещё",
    "же",
    "здесь",
    "или",
    "им",
    "их",
    "как",
    "когда",
    "кто",
    "ли",
    "либо",
    "мне",
    "может",
    "мы",
    "надо",
    "наш",
    "него",
    "нее",
    "неё",
    "нет",
    "них",
    "она",
    "они",
    "оно",
    "очень",
    "под",
    "при",
    "про",
    "раз",
    "самое",
    "сегодня",
    "так",
    "также",
    "там",
    "тем",
    "того",
    "тоже",
    "только",
    "том",
    "уже",
    "хорошо",
    "чего",
    "чем",
    "что",
    "чтобы",
    "этот",
    "эту",
    "это",
}


def build_summary(
    messages: Sequence[StoredMessage],
    *,
    chat_title: str,
    start: datetime,
    end: datetime,
) -> str:
    del chat_title
    del start
    del end

    if not messages:
        return _compose_message(
            headline="Сегодня в чате было тихо, важных обсуждений не набралось.",
            topics=["без заметных тем"],
            champion_name="пока никто",
        )

    champion_name = _find_top_participant(messages)
    topics = _heuristic_topics(messages)
    headline = _heuristic_headline(messages, topics)

    llm_result = _build_llm_summary(messages)
    if llm_result:
        headline = llm_result.get("headline") or headline
        llm_topics = llm_result.get("topics") or []
        if llm_topics:
            topics = llm_topics[:TOPIC_LIMIT]

    return _compose_message(
        headline=headline,
        topics=topics,
        champion_name=champion_name,
    )


def _build_llm_summary(messages: Sequence[StoredMessage]) -> Optional[dict[str, object]]:
    system_prompt = (
        "Ты редактор вечерней сводки для телеграм-группы. "
        "Верни строго JSON без markdown и пояснений. Формат ответа: "
        '{"headline":"короткая выжимка главного","topics":["тема 1","тема 2","тема 3"]}. '
        "headline: до 220 символов, по-русски, только суть дня. "
        "topics: от 2 до 4 коротких тем, каждая до 32 символов."
    )
    user_prompt = (
        "Сделай вечернюю сводку по переписке за день. "
        "Нужны самые обсуждаемые темы и одна короткая главная выжимка.\n\n"
        f"Переписка:\n{_build_transcript(messages)}"
    )

    raw_response = request_summary_json(system_prompt, user_prompt)
    if not raw_response:
        return None

    parsed = _extract_json(raw_response)
    if not parsed:
        logger.warning("Could not parse LLM JSON: %s", raw_response[:300])
        return None

    headline = _sanitize_text(str(parsed.get("headline", "")), limit=220)
    raw_topics = parsed.get("topics", [])
    topics = []
    if isinstance(raw_topics, list):
        for item in raw_topics:
            topic = _sanitize_topic(str(item))
            if topic:
                topics.append(topic)

    if not headline and not topics:
        return None
    return {"headline": headline, "topics": topics[:TOPIC_LIMIT]}


def _extract_json(raw_response: str) -> Optional[dict[str, object]]:
    try:
        data = json.loads(raw_response)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw_response, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def _build_transcript(messages: Sequence[StoredMessage]) -> str:
    parts = []
    total = 0
    for message in reversed(messages):
        line = f"[{message.sent_at:%H:%M}] {_display_name(message)}: {message.text.strip()}\n"
        if total + len(line) > TRANSCRIPT_CHAR_LIMIT:
            break
        parts.append(line)
        total += len(line)
    return "".join(reversed(parts))


def _find_top_participant(messages: Sequence[StoredMessage]) -> str:
    char_counts: Counter[str] = Counter()
    for message in messages:
        char_counts[_display_name(message)] += len(message.text)

    if not char_counts:
        return "пока никто"

    winner, _chars = max(
        char_counts.items(),
        key=lambda item: (item[1], item[0].lower()),
    )
    return winner


def _heuristic_topics(messages: Sequence[StoredMessage]) -> list[str]:
    token_counts: Counter[str] = Counter()
    for message in messages:
        token_counts.update(_extract_keywords(message.text.lower()))

    topics = []
    for token, _count in token_counts.most_common(12):
        topic = _sanitize_topic(token)
        if topic and topic not in topics:
            topics.append(topic)
        if len(topics) == TOPIC_LIMIT:
            break
    return topics or ["без выраженной темы"]


def _heuristic_headline(messages: Sequence[StoredMessage], topics: Sequence[str]) -> str:
    participant_count = len({_display_name(message) for message in messages})
    top_topics = ", ".join(topics[:3])
    return _sanitize_text(
        (
            f"За день участники активно обсуждали {top_topics}. "
            f"Всего сообщений: {len(messages)}, участников: {participant_count}."
        ),
        limit=220,
    )


def _extract_keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", text)
    return [word.lower() for word in words if word.lower() not in STOPWORDS]


def _display_name(message: StoredMessage) -> str:
    return message.username or message.full_name or "участник"


def _sanitize_topic(text: str) -> str:
    cleaned = _sanitize_text(text, limit=32)
    cleaned = cleaned.strip(" -.,;:!?\n\t")
    return cleaned


def _sanitize_text(text: str, *, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    shortened = compact[: max(0, limit - 1)].rstrip(" ,;:-")
    return shortened + "…"


def _compose_message(headline: str, topics: Sequence[str], champion_name: str) -> str:
    intro = "самое главное за сегодня в сводке новостей в 21:00:"
    champion_line = (
        f"- самый активный участник сегодня - {champion_name}, поздравляем. "
        "Ты больше всех активничал! Возьми пирожок с полочки, там два, крайний не бери!"
    )

    headline = _sanitize_text(headline, limit=260)
    topics_text = ", ".join(_sanitize_topic(topic) for topic in topics if topic)
    topics_text = _sanitize_text(topics_text or "без заметных тем", limit=220)

    message = (
        f"{intro}\n"
        f"- главное: {headline}\n"
        f"- самые обсуждаемые темы: {topics_text}\n"
        f"{champion_line}"
    )
    if len(message) <= SUMMARY_LIMIT:
        return message

    headline = _sanitize_text(headline, limit=180)
    topics_text = _sanitize_text(topics_text, limit=140)
    message = (
        f"{intro}\n"
        f"- главное: {headline}\n"
        f"- самые обсуждаемые темы: {topics_text}\n"
        f"{champion_line}"
    )
    if len(message) <= SUMMARY_LIMIT:
        return message

    overflow = len(message) - SUMMARY_LIMIT
    reduced_champion = _sanitize_text(champion_name, limit=max(8, len(champion_name) - overflow - 10))
    final_message = (
        f"{intro}\n"
        f"- главное: {headline}\n"
        f"- самые обсуждаемые темы: {topics_text}\n"
        f"- самый активный участник сегодня - {reduced_champion}, поздравляем. "
        "Ты больше всех активничал! Возьми пирожок с полочки, там два, крайний не бери!"
    )
    return final_message[:SUMMARY_LIMIT].rstrip()
