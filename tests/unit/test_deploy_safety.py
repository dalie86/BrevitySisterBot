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


def test_public_wsgi_entry_adds_project_root_without_secrets() -> None:
    entry = Path("public_html/passenger_wsgi.py").read_text(encoding="utf-8")

    assert "PROJECT_ROOT" in entry
    assert "app.web.production" in entry
    assert "TELEGRAM_BOT_TOKEN" not in entry
