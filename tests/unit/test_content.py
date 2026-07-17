from pathlib import Path

import pytest

from app.content import BotCopy, CopyConfigurationError


def test_repository_copy_catalog_loads_and_renders() -> None:
    copy = BotCopy.load(Path("content/bot_copy.ru.toml"))

    assert copy.value("buttons", "get_private_summary") == "Получить сводку лично"
    assert copy.render(
        "summary",
        "topic",
        topic_title="Релиз",
        topic_details="Версия согласована.",
    ) == "• Релиз\nВерсия согласована."


def test_catalog_rejects_missing_required_placeholder(tmp_path: Path) -> None:
    source = Path("content/bot_copy.ru.toml").read_text(encoding="utf-8")
    broken = tmp_path / "copy.toml"
    broken.write_text(source.replace("{{headline}}", "итог", 1), encoding="utf-8")

    with pytest.raises(CopyConfigurationError, match="summary.document.*headline"):
        BotCopy.load(broken)


def test_render_rejects_unknown_or_missing_values() -> None:
    copy = BotCopy.load(Path("content/bot_copy.ru.toml"))

    with pytest.raises(CopyConfigurationError, match="topic_details"):
        copy.render("summary", "topic", topic_title="Релиз")
    with pytest.raises(CopyConfigurationError, match="unexpected"):
        copy.render(
            "summary",
            "topic",
            topic_title="Релиз",
            topic_details="Готово",
            unexpected="value",
        )
