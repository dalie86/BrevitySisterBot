"""Explicit display-name preferences for summary participants."""

from __future__ import annotations

import re

# Stable Telegram identity: changing profile names must not change this preference.
DISPLAY_NAMES = {205580587: "Павел"}
_RUSSIAN_FULL_NAME = re.compile(
    r"(?<!\w)(Пав(?:ел|ла|лу|лом|ле))\s+[ШЩ]ечков(?:а|у|ым|е)?(?!\w)",
    re.IGNORECASE,
)
_LATIN_FULL_NAME = re.compile(
    r"(?<!\w)(?:Pavel\s+(?:Shechkov|Shchechkov)|(?:Shechkov|Shchechkov)\s+Pavel)(?!\w)",
    re.IGNORECASE,
)


def participant_name(user_id: int | None, profile_name: str) -> str:
    return DISPLAY_NAMES.get(user_id, profile_name)


def normalize_summary_names(text: str) -> str:
    """Remove known full-name variants without changing names of other people.

    Keep Russian case (e.g. 'Планы Павла', 'с Павлом'). A surname on its own
    is intentionally not enough to identify this participant.
    """
    text = _RUSSIAN_FULL_NAME.sub(lambda match: match.group(1).capitalize(), text)
    return _LATIN_FULL_NAME.sub("Павел", text)
