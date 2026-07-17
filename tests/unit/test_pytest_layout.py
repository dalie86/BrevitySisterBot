import tomllib
from pathlib import Path


def test_pytest_collects_only_new_application_suite() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert config["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]
