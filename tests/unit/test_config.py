import pytest

from app.config import load_settings


def test_load_settings_uses_production_defaults() -> None:
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "bot",
            "TELEGRAM_BOT_USERNAME": "@BrevitySisterBot",
            "WEBHOOK_SECRET_TOKEN": "secret",
            "WEBHOOK_URL": "https://brevity.prosto-kosmos.ru/telegram/webhook",
            "DB_HOST": "localhost",
            "DB_NAME": "brevity",
            "DB_USER": "brevity",
            "DB_PASSWORD": "password",
            "DEEPSEEK_API_KEY": "repr-super-secret",
            "DEEPSEEK_MODEL": "deepseek-v4-flash",
        }
    )

    assert settings.deepseek_model == "deepseek-v4-flash"
    assert settings.telegram_bot_username == "BrevitySisterBot"
    assert settings.webhook_url == "https://brevity.prosto-kosmos.ru/telegram/webhook"
    assert settings.timezone == "Europe/Moscow"
    assert settings.owner_chat_id is None
    assert settings.automatic_min_messages == 15
    assert settings.on_demand_min_messages == 5
    assert settings.archive_retention_days == 30
    assert "repr-super-secret" not in repr(settings)


def test_load_settings_reports_all_missing_required_values() -> None:
    with pytest.raises(RuntimeError) as error:
        load_settings({})

    assert "TELEGRAM_BOT_TOKEN" in str(error.value)
    assert "TELEGRAM_BOT_USERNAME" in str(error.value)
    assert "DB_NAME" in str(error.value)
    assert "DEEPSEEK_API_KEY" in str(error.value)
    assert "GROQ" + "_API_KEY" not in str(error.value)


def test_load_settings_parses_owner_chat_id() -> None:
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "bot",
            "TELEGRAM_BOT_USERNAME": "BrevitySisterBot",
            "WEBHOOK_SECRET_TOKEN": "secret",
            "WEBHOOK_URL": "https://brevity.prosto-kosmos.ru/telegram/webhook",
            "DB_HOST": "localhost",
            "DB_NAME": "brevity",
            "DB_USER": "brevity",
            "DB_PASSWORD": "password",
            "DEEPSEEK_API_KEY": "deepseek",
            "DEEPSEEK_MODEL": "deepseek-v4-flash",
            "OWNER_CHAT_ID": "-100123",
        }
    )

    assert settings.owner_chat_id == -100123


def test_load_settings_rejects_empty_bot_username_after_optional_at_sign() -> None:
    environment = {
        "TELEGRAM_BOT_TOKEN": "bot",
        "TELEGRAM_BOT_USERNAME": " @ ",
        "WEBHOOK_SECRET_TOKEN": "secret",
        "WEBHOOK_URL": "https://brevity.prosto-kosmos.ru/telegram/webhook",
        "DB_HOST": "localhost",
        "DB_NAME": "brevity",
        "DB_USER": "brevity",
        "DB_PASSWORD": "password",
        "DEEPSEEK_API_KEY": "deepseek",
        "DEEPSEEK_MODEL": "deepseek-v4-flash",
    }

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_USERNAME"):
        load_settings(environment)
