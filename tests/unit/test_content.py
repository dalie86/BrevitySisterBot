import subprocess
import sys
import textwrap
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
    assert copy.render(
        "usage_report",
        "balance",
        total_balance="49.00",
        currency="CNY",
    ) == "Баланс: 49.00 CNY"
    assert "недостаточно средств" in copy.value(
        "usage_report", "insufficient_balance"
    ).lower()


def test_content_import_falls_back_to_tomli_when_tomllib_is_unavailable() -> None:
    script = textwrap.dedent(
        """
        import importlib.abc
        import sys
        from pathlib import Path

        try:
            import tomllib as compatible_tomli
        except ModuleNotFoundError:
            import tomli as compatible_tomli

        class BlockTomllib(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "tomllib":
                    raise ModuleNotFoundError("tomllib blocked for Python 3.10 simulation")
                return None

        sys.modules.pop("tomllib", None)
        sys.modules["tomli"] = compatible_tomli
        sys.meta_path.insert(0, BlockTomllib())

        from app.content import BotCopy

        catalog = BotCopy.load(Path("content/bot_copy.ru.toml"))
        print(catalog.value("buttons", "get_private_summary"))
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "Получить сводку лично"


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


def test_catalog_rejects_missing_file_and_malformed_toml(tmp_path: Path) -> None:
    with pytest.raises(CopyConfigurationError, match="Cannot load bot copy"):
        BotCopy.load(tmp_path / "missing.toml")
    malformed = tmp_path / "malformed.toml"
    malformed.write_text("[messages\nbroken", encoding="utf-8")
    with pytest.raises(CopyConfigurationError, match="Cannot load bot copy"):
        BotCopy.load(malformed)


@pytest.mark.parametrize(
    ("old", "new"),
    [("schema_version = 1", "schema_version = 2"), ('locale = "ru"', 'locale = "en"')],
)
def test_catalog_rejects_wrong_schema_or_locale(
    tmp_path: Path, old: str, new: str
) -> None:
    source = Path("content/bot_copy.ru.toml").read_text(encoding="utf-8")
    broken = tmp_path / "copy.toml"
    broken.write_text(source.replace(old, new, 1), encoding="utf-8")

    with pytest.raises(CopyConfigurationError, match="schema_version=1 and locale=ru"):
        BotCopy.load(broken)


@pytest.mark.parametrize("value", ['""', '[]'])
def test_catalog_rejects_empty_or_non_string_value(tmp_path: Path, value: str) -> None:
    source = Path("content/bot_copy.ru.toml").read_text(encoding="utf-8")
    broken = tmp_path / "copy.toml"
    broken.write_text(
        source.replace('choose_group = "Выбрать группу"', f"choose_group = {value}", 1),
        encoding="utf-8",
    )

    with pytest.raises(CopyConfigurationError, match="buttons.choose_group"):
        BotCopy.load(broken)


@pytest.mark.parametrize("length", [0, 65])
def test_catalog_validates_button_length(tmp_path: Path, length: int) -> None:
    source = Path("content/bot_copy.ru.toml").read_text(encoding="utf-8")
    broken = tmp_path / "copy.toml"
    replacement = 'choose_group = "' + ("x" * length) + '"'
    broken.write_text(
        source.replace('choose_group = "Выбрать группу"', replacement, 1),
        encoding="utf-8",
    )

    with pytest.raises(CopyConfigurationError, match="buttons.choose_group"):
        BotCopy.load(broken)
