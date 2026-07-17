from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def test_pytest_collects_only_new_application_suite() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert config["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]


def test_project_supports_python_310_with_tomli_fallback() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert config["project"]["requires-python"] == ">=3.10"
    assert "tomli==2.2.1; python_version < '3.11'" in config["project"][
        "dependencies"
    ]
    assert config["tool"]["ruff"]["target-version"] == "py310"
