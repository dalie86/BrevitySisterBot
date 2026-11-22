"""Configure Telegram menus after deploying ephemeral summary support."""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from pathlib import Path

from dotenv import load_dotenv

from app.config import load_settings
from app.integrations.telegram import TelegramClient


def configure_commands(client: TelegramClient) -> None:
    copy_path = Path(__file__).resolve().parents[1] / "content" / "bot_copy.ru.toml"
    with copy_path.open("rb") as source:
        descriptions = tomllib.load(source)["commands"]
    group_commands = [
        {"command": "summary", "description": descriptions["summary"], "is_ephemeral": True},
        {"command": "enable_daily", "description": descriptions["enable_daily"]},
        {"command": "disable_daily", "description": descriptions["disable_daily"]},
    ]
    private_commands = [{"command": "summary", "description": descriptions["summary"]}]
    for scope in ("default", "all_group_chats", "all_chat_administrators", "all_private_chats"):
        commands = private_commands if scope == "all_private_chats" else group_commands
        for language_code in ("", "ru"):
            client.set_my_commands(commands, scope={"type": scope}, language_code=language_code)


def main() -> int:
    load_dotenv()
    settings = load_settings()
    configure_commands(TelegramClient(settings.telegram_bot_token))
    print("Command menus configured: group summaries are ephemeral.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
