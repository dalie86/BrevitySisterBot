from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
REQUIRED: dict[tuple[str, str], frozenset[str]] = {
    ("summary", "document"): frozenset({"headline", "topics_block"}),
    ("summary", "topic"): frozenset({"topic_title", "topic_details"}),
    ("fallback", "document"): frozenset(
        {"message_count", "participants", "recent_messages"}
    ),
    ("fallback", "message_item"): frozenset({"message_text"}),
    ("prompts", "user"): frozenset({"transcript"}),
    ("prompts", "transcript_line"): frozenset(
        {"iso_datetime", "participant_name", "message_text"}
    ),
    ("prompts", "repair"): frozenset({"invalid_model_response"}),
}
STATIC_KEYS = {
    ("messages", key)
    for key in {
        "group_commands",
        "daily_enabled",
        "daily_disabled",
        "group_not_connected",
        "admin_required",
        "summary_accepted",
        "summary_in_progress",
        "not_enough_messages",
        "choose_group_native",
        "choose_group_known",
        "group_choice_expired",
        "membership_not_confirmed",
        "bot_admin_required",
        "private_link_invalid",
        "selected_group_not_connected",
        "group_access_unverifiable",
    }
} | {
    ("buttons", "get_private_summary"),
    ("buttons", "choose_group"),
    ("summary", "topics_separator"),
    ("fallback", "participants_unknown"),
    ("fallback", "messages_separator"),
    ("prompts", "system"),
}


class CopyConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BotCopy:
    sections: Mapping[str, Mapping[str, str]]

    @classmethod
    def load(cls, path: Path) -> BotCopy:
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise CopyConfigurationError(f"Cannot load bot copy: {exc}") from exc
        sections = {
            name: MappingProxyType(dict(values))
            for name, values in raw.items()
            if isinstance(values, dict)
        }
        if raw.get("schema_version") != 1 or raw.get("locale") != "ru":
            raise CopyConfigurationError("Expected schema_version=1 and locale=ru")
        catalog = cls(MappingProxyType(sections))
        for section, key in sorted(STATIC_KEYS | set(REQUIRED)):
            text = catalog.value(section, key)
            actual = frozenset(PLACEHOLDER.findall(text))
            expected = REQUIRED.get((section, key), frozenset())
            if actual != expected:
                raise CopyConfigurationError(
                    f"{section}.{key} placeholders: expected {sorted(expected)}, "
                    f"got {sorted(actual)}"
                )
        for key, label in catalog.sections["buttons"].items():
            if not 1 <= len(label) <= 64:
                raise CopyConfigurationError(
                    f"buttons.{key} must contain 1..64 characters"
                )
        return catalog

    def value(self, section: str, key: str) -> str:
        try:
            value = self.sections[section][key]
        except KeyError as exc:
            raise CopyConfigurationError(f"Missing copy key: {section}.{key}") from exc
        if not isinstance(value, str) or not value:
            raise CopyConfigurationError(
                f"Copy key must be non-empty: {section}.{key}"
            )
        return value

    def render(self, section: str, key: str, **values: object) -> str:
        template = self.value(section, key)
        required = frozenset(PLACEHOLDER.findall(template))
        supplied = frozenset(values)
        if required != supplied:
            raise CopyConfigurationError(
                f"{section}.{key} values: missing {sorted(required - supplied)}, "
                f"unexpected {sorted(supplied - required)}"
            )
        return PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), template)
