import re
from pathlib import Path


def test_deploy_excludes_secrets_runtime_data_and_mvp_databases() -> None:
    script = Path("scripts/deploy_beget.exp").read_text(encoding="utf-8")

    for excluded in (
        "--exclude .env",
        "--exclude .env.hosting",
        "--exclude bot.db",
        "--exclude archive.db",
        "--exclude backups",
        "--exclude logs",
        "--exclude .git",
    ):
        assert excluded in script


def test_deploy_script_does_not_exclude_runtime_content() -> None:
    script = Path("scripts/deploy_beget.exp").read_text(encoding="utf-8")

    exclude_arguments = re.findall(
        r"--exclude(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s\\]+))", script
    )
    for groups in exclude_arguments:
        value = next(group for group in groups if group)
        normalized = value.removeprefix("./").removeprefix("/").rstrip("/")
        assert normalized != "content"
        assert not normalized.startswith("content/")
    assert "./ $target" in script


def test_deploy_backs_up_before_running_migrations() -> None:
    script = Path("scripts/deploy_beget.exp").read_text(encoding="utf-8")

    assert script.index("scripts/backup_db.sh") < script.index("scripts/migrate.py")


def test_beget_defaults_to_python_310() -> None:
    example = Path("hosting.env.example").read_text(encoding="utf-8")
    script = Path("scripts/deploy_beget.exp").read_text(encoding="utf-8")

    assert "BEGET_PYTHON=python3.10" in example
    assert 'set config(BEGET_PYTHON) "python3.10"' in script


def test_public_wsgi_entry_adds_project_root_without_secrets() -> None:
    entry = Path("public_html/passenger_wsgi.py").read_text(encoding="utf-8")

    assert "PROJECT_ROOT" in entry
    assert "app.web.production" in entry
    assert "TELEGRAM_BOT_TOKEN" not in entry
