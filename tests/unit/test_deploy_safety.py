import re
from pathlib import Path


def test_deploy_excludes_secrets_runtime_data_and_mvp_databases() -> None:
    script = Path("scripts/deploy_beget.exp").read_text(encoding="utf-8")

    for excluded in (
        "--exclude .env",
        "--exclude .env.hosting",
        "--exclude .venv",
        "--exclude .venv-*",
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


def test_deploy_runs_deepseek_smoke_before_passenger_restart() -> None:
    script = Path("scripts/deploy_beget.exp").read_text(encoding="utf-8")

    command_order = (
        "scripts/backup_db.sh",
        "-m pip install",
        "scripts/migrate.py",
        "scripts/check_deepseek.py",
        "touch tmp/restart.txt",
    )

    positions = [script.index(command) for command in command_order]
    assert positions == sorted(positions)


def test_readme_beget_preparation_steps_are_consecutive() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    preparation = readme.split("Подготовка:\n", 1)[1].split(
        "Deploy не переносит", 1
    )[0]

    assert re.findall(r"^(\d+)\.", preparation, flags=re.MULTILINE) == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
    ]


def test_beget_defaults_to_python_310() -> None:
    example = Path("hosting.env.example").read_text(encoding="utf-8")
    script = Path("scripts/deploy_beget.exp").read_text(encoding="utf-8")

    assert "BEGET_PYTHON=python3.10" in example
    assert 'set config(BEGET_PYTHON) "python3.10"' in script


def test_deploy_rebuilds_invalid_venv_with_beget_virtualenv() -> None:
    script = Path("scripts/deploy_beget.exp").read_text(encoding="utf-8")

    assert (
        "if [ ! -x .venv/bin/python ] || [ -L .venv/bin/python ] || "
        "! .venv/bin/python -m pip --version >/dev/null 2>&1; then"
        in script
    )
    assert "/usr/bin/virtualenv --copies -p %s --clear .venv" in script
    assert "} $config(BEGET_REMOTE_DIR) $config(BEGET_PYTHON)]" in script
    assert "%s -m venv .venv" not in script


def test_deploy_ships_passenger_python_configuration() -> None:
    config_path = Path("public_html/.htaccess")
    script = Path("scripts/deploy_beget.exp").read_text(encoding="utf-8")

    assert config_path.is_file()
    assert config_path.read_text(encoding="utf-8").splitlines() == [
        "PassengerEnabled On",
        (
            "PassengerPython "
            "/home/p/pshech/brevity.prosto-kosmos.ru/.venv/bin/python"
        ),
    ]
    assert "--exclude .htaccess" not in script
    assert "--exclude public_html/.htaccess" not in script
    assert "./ $target" in script


def test_public_wsgi_entry_adds_project_root_without_secrets() -> None:
    entry = Path("public_html/passenger_wsgi.py").read_text(encoding="utf-8")

    assert "PROJECT_ROOT" in entry
    assert "app.web.production" in entry
    assert "TELEGRAM_BOT_TOKEN" not in entry


def test_beget_crontab_uses_date_without_percent_escaping() -> None:
    example = Path("crontab.beget.example").read_text(encoding="utf-8")
    command_lines = [line for line in example.splitlines() if line and not line.startswith("#")]
    rotate_line = next(line for line in command_lines if "rotate_logs.sh" in line)
    dated_log_lines = [line for line in command_lines if "rotate_logs.sh" not in line]

    assert re.search(r"\$\(\s*date\b[^)]*%", example) is None
    assert len(dated_log_lines) == 9
    assert example.count("$(date -I)") == 9
    assert all(line.count("$(date -I)") == 1 for line in dated_log_lines)
    assert re.search(r"\$\(\s*date\b", rotate_line) is None
