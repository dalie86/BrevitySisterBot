from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts import render_copy_deck


def test_copy_deck_is_current() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/render_copy_deck.py", "--check"],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr


def test_check_reports_drift_without_overwriting(
    tmp_path: Path, monkeypatch
) -> None:
    deck = tmp_path / "BOT_COPY_RU.md"
    deck.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(render_copy_deck, "DECK_PATH", deck)

    assert render_copy_deck.main(["--check"]) == 1
    assert deck.read_text(encoding="utf-8") == "stale\n"


def test_check_compares_bytes_even_when_the_deck_is_not_utf8(
    tmp_path: Path, monkeypatch
) -> None:
    deck = tmp_path / "BOT_COPY_RU.md"
    deck.write_bytes(b"\xff")
    monkeypatch.setattr(render_copy_deck, "DECK_PATH", deck)

    assert render_copy_deck.main(["--check"]) == 1
    assert deck.read_bytes() == b"\xff"


def test_normal_mode_writes_the_catalog_deterministically(
    tmp_path: Path, monkeypatch
) -> None:
    deck = tmp_path / "BOT_COPY_RU.md"
    monkeypatch.setattr(render_copy_deck, "DECK_PATH", deck)

    assert render_copy_deck.main([]) == 0
    first_render = deck.read_bytes()
    assert render_copy_deck.main([]) == 0

    assert deck.read_bytes() == first_render
    assert render_copy_deck.main(["--check"]) == 0
