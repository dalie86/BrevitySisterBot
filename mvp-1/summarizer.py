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

SUMMARY_LIMIT = 1200
TOPIC_LIMIT = 4
TRANSCRIPT_CHAR_LIMIT = 12000
MIN_TOPIC_MESSAGE_COUNT = 2
MIN_TOPIC_PARTICIPANT_COUNT = 2
GENERIC_TOPIC_TITLES = {
    "будет",
    "были",
    "важно",
    "вопрос",
    "вопросы",
    "дело",
    "жизнь",
    "завтра",
    "люди",
    "меня",
    "можно",
    "нужно",
    "помощь",
    "потом",
    "сегодня",
    "слова",
    "тема",
    "тут",
    "человек",
    "любой",
}

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
            topics=[
                {
                    "title": "Без заметных тем",
                    "details": "В переписке не набралось крупных обсуждений или повторяющихся линий.",
                }
            ],
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
        "Ты редактор вечерней сводки для телеграм-группы. Пиши живо, но строго по фактам. "
        "Не пересказывай весь чат. Выбери только самые плотные и важные линии обсуждения. "
        "Верни строго JSON без markdown и пояснений. Формат ответа: "
        '{"headline":"короткая выжимка главного","topics":[{"title":"тема 1","details":"тезис 1; тезис 2; тезис 3; тезис 4"},{"title":"тема 2","details":"тезис 1; тезис 2; тезис 3; тезис 4"}]}. '
        "headline: до 180 символов, одна емкая выжимка главного напряжения, решения или незакрытого вопроса дня. "
        "topics: 4 темы, если в переписке действительно хватает материала; не выдумывай темы из слабых одноразовых реплик. "
        "title: до 50 символов, конкретный заголовок без канцелярита. "
        "details: до 250 символов, в одну строку, ровно 4 коротких тезиса через ';'. "
        "Для каждого details: тезис 1 - что произошло; тезис 2 - в чем была проблема, спор или важная деталь; тезис 3 - реакция, позиция или важный контекст; тезис 4 - к чему пришли или что осталось открытым. "
        "Запрещено: общие фразы вроде 'тема активно обсуждалась', 'участники делились мнениями', 'обсуждение продолжилось'; повторять headline внутри topics; добавлять темы, которые мелькнули один раз."
    )
    user_prompt = (
        "Сделай вечернюю сводку по переписке за день. "
        "Нужны самые обсуждаемые темы и одна короткая главная выжимка. "
        "Каждую тему верни отдельным пунктом: короткий заголовок и ровно 4 конкретных тезиса по сути. "
        "Старайся вернуть именно 4 темы, но не выдумывай лишнее, если в переписке нет материала.\n\n"
        f"Переписка:\n{_build_transcript(messages)}"
    )

    raw_response = request_summary_json(system_prompt, user_prompt)
    summary = _parse_summary_payload(raw_response)
    if _is_summary_usable(summary):
        return summary

    if raw_response:
        logger.warning("Could not parse primary LLM JSON: %s", raw_response[:300])
        repaired_response = _repair_llm_summary(raw_response)
        repaired_summary = _parse_summary_payload(repaired_response)
        if _is_summary_usable(repaired_summary):
            logger.info("LLM summary repaired successfully after invalid primary response.")
            return repaired_summary

    return None


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


def _parse_summary_payload(raw_response: Optional[str]) -> Optional[dict[str, object]]:
    if not raw_response:
        return None

    parsed = _extract_json(raw_response)
    if not parsed:
        return None

    normalized = _normalize_summary_payload(parsed)
    headline = _sanitize_text(str(normalized.get("headline", "")), limit=220)
    raw_topics = normalized.get("topics", [])
    topics = _parse_topics(raw_topics)
    if not headline and not topics:
        return None
    return {"headline": headline, "topics": topics[:TOPIC_LIMIT]}


def _normalize_summary_payload(parsed: dict[str, object]) -> dict[str, object]:
    if "headline" in parsed or "topics" in parsed:
        return parsed

    data = parsed.get("data")
    if not isinstance(data, dict):
        return parsed

    headline = (
        data.get("headline")
        or data.get("summary")
        or data.get("main_point")
        or _headline_from_key_points(data.get("key_points"))
    )
    topics = _topics_from_nested_payload(data)
    return {"headline": headline or "", "topics": topics}


def _headline_from_key_points(key_points: object) -> str:
    if not isinstance(key_points, list):
        return ""
    cleaned_points = [
        _sanitize_text(str(item), limit=120)
        for item in key_points
        if str(item).strip()
    ]
    return _sanitize_text(". ".join(cleaned_points[:2]), limit=180)


def _topics_from_nested_payload(data: dict[str, object]) -> list[dict[str, str]]:
    topic_details = data.get("topic_details")
    if isinstance(topic_details, list):
        return _parse_topics(topic_details)

    raw_topics = data.get("topics")
    raw_key_points = data.get("key_points")
    if not isinstance(raw_topics, list):
        return []

    key_points = [
        _sanitize_topic_details(str(item))
        for item in raw_key_points
        if str(item).strip()
    ] if isinstance(raw_key_points, list) else []
    details_fallback = "; ".join(point for point in key_points[:4] if point)

    topics: list[dict[str, str]] = []
    for item in raw_topics:
        title = _sanitize_topic_title(str(item))
        if not title:
            continue
        topics.append(
            {
                "title": title,
                "details": details_fallback or "Тема была одной из заметных линий дня; в ней остались открытые вопросы.",
            }
        )
    return topics


def _repair_llm_summary(raw_response: str) -> Optional[str]:
    repair_system_prompt = (
        "Ты исправляешь ответ другой модели. "
        "Верни строго JSON без markdown и пояснений в формате "
        '{"headline":"короткая выжимка главного","topics":[{"title":"тема 1","details":"тезис 1; тезис 2; тезис 3; тезис 4"}]}. '
        "Старайся вернуть именно 4 темы, если в исходном ответе для этого хватает материала, но не выдумывай лишнее. "
        "title до 50 символов. details до 250 символов. "
        "В каждом details должно быть ровно 4 коротких тезиса через ';'. "
        "Если в исходном ответе тема есть, но детали размыты, собери детали из фактов исходного ответа. "
        "Не добавляй новых фактов от себя."
    )
    repair_user_prompt = (
        "Приведи этот ответ к целевому JSON-формату саммари. "
        "Сохрани только факты из исходного ответа.\n\n"
        f"Исходный ответ модели:\n{raw_response[:8000]}"
    )
    return request_summary_json(repair_system_prompt, repair_user_prompt)


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


def _heuristic_topics(messages: Sequence[StoredMessage]) -> list[dict[str, str]]:
    token_counts: Counter[str] = Counter()
    token_messages: dict[str, int] = {}
    token_participants: dict[str, set[str]] = {}
    for message in messages:
        tokens = set(_extract_keywords(message.text.lower()))
        token_counts.update(tokens)
        for token in tokens:
            token_messages[token] = token_messages.get(token, 0) + 1
            token_participants.setdefault(token, set()).add(_display_name(message))

    topics = []
    for token, _count in token_counts.most_common(12):
        title = _sanitize_topic_title(token)
        participant_count = len(token_participants.get(token, set()))
        if (
            title
            and title not in {topic["title"] for topic in topics}
            and not _is_low_quality_topic_title(title)
            and token_messages.get(token, 0) >= MIN_TOPIC_MESSAGE_COUNT
            and participant_count >= MIN_TOPIC_PARTICIPANT_COUNT
        ):
            topics.append(
                {
                    "title": title,
                    "details": _sanitize_topic_details(
                        f"Тема всплывала в нескольких сообщениях; в обсуждение включались разные участники; у темы был заметный контекст дня; по ней не всё осталось окончательно ясно."
                    ),
                }
            )
        if len(topics) == TOPIC_LIMIT:
            break
    return topics or [
        {
            "title": "Без выраженной темы",
            "details": "Обсуждение было разрозненным, без одной явно доминирующей линии.",
        }
    ]


def _heuristic_headline(messages: Sequence[StoredMessage], topics: Sequence[dict[str, str]]) -> str:
    participant_count = len({_display_name(message) for message in messages})
    top_topics = ", ".join(topic["title"] for topic in topics[:3])
    return _sanitize_text(
        (
            f"За день участники активно обсуждали {top_topics}. "
            f"Всего сообщений: {len(messages)}, участников: {participant_count}."
        ),
        limit=220,
    )


def _extract_keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", text)
    return [
        word.lower()
        for word in words
        if word.lower() not in STOPWORDS and word.lower() not in GENERIC_TOPIC_TITLES
    ]


def _display_name(message: StoredMessage) -> str:
    return message.username or message.full_name or "участник"


def _sanitize_topic_title(text: str) -> str:
    cleaned = _sanitize_text(text, limit=50)
    cleaned = cleaned.strip(" -.,;:!?\n\t")
    return cleaned


def _sanitize_topic_details(text: str, *, limit: int = 250) -> str:
    cleaned = _sanitize_text(text, limit=limit)
    return cleaned.strip(" -.,;:!?\n\t")


def _is_summary_usable(summary: Optional[dict[str, object]]) -> bool:
    if not isinstance(summary, dict):
        return False

    headline = str(summary.get("headline", "")).strip()
    topics = summary.get("topics")
    if len(headline) < 12 or not isinstance(topics, list) or len(topics) < 2:
        return False

    good_topics = 0
    seen_titles: set[str] = set()
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        title = _sanitize_topic_title(str(topic.get("title", "")))
        details = _sanitize_topic_details(str(topic.get("details", "")))
        if (
            title
            and details
            and title.lower() not in seen_titles
            and not _is_low_quality_topic_title(title)
            and (
                not _is_low_quality_topic_details(details)
                or details.startswith("Тема заметно обсуждалась в течение дня")
            )
        ):
            good_topics += 1
            seen_titles.add(title.lower())

    return good_topics >= 2


def _is_low_quality_topic_title(title: str) -> bool:
    compact = title.strip().lower()
    if len(compact) < 4:
        return True
    if compact in STOPWORDS or compact in GENERIC_TOPIC_TITLES:
        return True
    return compact.isdigit()


def _is_low_quality_topic_details(details: str) -> bool:
    compact = details.strip().lower()
    generic_fragments = (
        "тему обсуждали несколько раз",
        "к ней возвращались по ходу дня",
        "заметно влияла на общую повестку",
        "тема заметно обсуждалась в течение дня",
    )
    if any(fragment in compact for fragment in generic_fragments):
        return True
    if len(compact) < 40:
        return True
    return False


def _sanitize_text(text: str, *, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    shortened = compact[: max(0, limit - 1)].rstrip(" ,;:-")
    return shortened + "…"


def _parse_topics(raw_topics: object) -> list[dict[str, str]]:
    topics = []
    if not isinstance(raw_topics, list):
        return topics

    for item in raw_topics:
        if isinstance(item, dict):
            title = _sanitize_topic_title(str(item.get("title", "")))
            details = _sanitize_topic_details(str(item.get("details", "")))
            if title:
                topics.append(
                    {
                        "title": title,
                        "details": details or "Тема заметно обсуждалась в течение дня.",
                    }
                )
            continue

        if isinstance(item, str):
            title = _sanitize_topic_title(item)
            if title:
                topics.append(
                    {
                        "title": title,
                        "details": "Тема заметно обсуждалась в течение дня.",
                    }
                )

    return topics


def _compose_message(headline: str, topics: Sequence[dict[str, str]], champion_name: str) -> str:
    intro = "самое главное за сегодня в сводке новостей:"
    champion_line = (
        f"- самый активный участник сегодня - {champion_name}, поздравляем. "
        "Ты больше всех активничал! Возьми поминальный набор с полочки, там два, крайний не бери!"
    )
    normalized_topics = [
        {
            "title": _sanitize_topic_title(topic.get("title", "")),
            "details": _sanitize_topic_details(topic.get("details", "")),
        }
        for topic in topics
        if topic.get("title")
    ] or [
        {
            "title": "Без заметных тем",
            "details": "В переписке не выделилось устойчивых обсуждений.",
        }
    ]

    for headline_limit, details_limit, topic_limit in (
        (180, 230, 4),
        (165, 200, 4),
        (150, 170, 3),
        (135, 150, 2),
    ):
        message = _render_message(
            intro=intro,
            headline=_sanitize_text(headline, limit=headline_limit),
            topics=normalized_topics[:topic_limit],
            details_limit=details_limit,
            champion_line=champion_line,
        )
        if len(message) <= SUMMARY_LIMIT:
            return message

    overflow = len(message) - SUMMARY_LIMIT
    reduced_champion = _sanitize_text(
        champion_name,
        limit=max(8, len(champion_name) - overflow - 10),
    )
    final_champion_line = (
        f"- самый активный участник сегодня - {reduced_champion}, поздравляем. "
        "Ты больше всех активничал! Возьми поминальный набор с полочки, там два, крайний не бери!"
    )
    final_message = _render_message(
        intro=intro,
        headline=_sanitize_text(headline, limit=110),
        topics=normalized_topics[:2],
        details_limit=130,
        champion_line=final_champion_line,
    )
    return final_message[:SUMMARY_LIMIT].rstrip()


def _render_message(
    *,
    intro: str,
    headline: str,
    topics: Sequence[dict[str, str]],
    details_limit: int,
    champion_line: str,
) -> str:
    topic_lines = []
    for topic in topics:
        title = _sanitize_topic_title(topic.get("title", ""))
        details = _sanitize_topic_details(topic.get("details", ""), limit=details_limit)
        if title:
            topic_lines.append(f"- {title}: {details or 'Тема заметно обсуждалась в течение дня.'}")

    topic_block = "\n".join(topic_lines) or "- Без заметных тем: В переписке не выделилось устойчивых обсуждений."
    return (
        f"{intro}\n"
        f"- главное: {headline}\n"
        f"{topic_block}\n"
        f"{champion_line}"
    )
