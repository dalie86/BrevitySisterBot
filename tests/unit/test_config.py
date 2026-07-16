import pytest

from app.config import load_settings


def test_load_settings_uses_production_defaults() -> None:
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "bot",
            "WEBHOOK_SECRET_TOKEN": "secret",
            "DB_HOST": "localhost",
            "DB_NAME": "brevity",
            "DB_USER": "brevity",
            "DB_PASSWORD": "password",
            "GROQ_API_KEY": "groq",
        }
    )

    assert settings.groq_model == "qwen/qwen3.6-27b"
    assert settings.timezone == "Europe/Moscow"
    assert settings.owner_chat_id is None
    assert settings.automatic_min_messages == 15
    assert settings.on_demand_min_messages == 5
    assert settings.archive_retention_days == 30


def test_load_settings_reports_all_missing_required_values() -> None:
    with pytest.raises(RuntimeError) as error:
        load_settings({})

    assert "TELEGRAM_BOT_TOKEN" in str(error.value)
    assert "DB_NAME" in str(error.value)
    assert "GROQ_API_KEY" in str(error.value)


def test_load_settings_parses_owner_chat_id() -> None:
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "bot",
            "WEBHOOK_SECRET_TOKEN": "secret",
            "DB_HOST": "localhost",
            "DB_NAME": "brevity",
            "DB_USER": "brevity",
            "DB_PASSWORD": "password",
            "GROQ_API_KEY": "groq",
            "OWNER_CHAT_ID": "-100123",
        }
    )

    assert settings.owner_chat_id == -100123
