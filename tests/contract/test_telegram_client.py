import json
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from app.integrations.telegram import (
    TelegramApiError,
    TelegramClient,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_send_message_calls_bot_api_and_returns_message_id(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({"ok": True, "result": {"message_id": 55}})

    monkeypatch.setattr("app.integrations.telegram.urllib.request.urlopen", fake_urlopen)
    client = TelegramClient("token", timeout_seconds=7.0)

    message_id = client.send_message(
        -1001,
        "Готово",
        reply_markup={"inline_keyboard": []},
    )

    assert message_id == 55
    assert captured == {
        "url": "https://api.telegram.org/bottoken/sendMessage",
        "body": {
            "chat_id": -1001,
            "text": "Готово",
            "reply_markup": {"inline_keyboard": []},
        },
        "timeout": 7.0,
    }


def test_send_message_includes_html_parse_mode(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"ok": True, "result": {"message_id": 55}})

    monkeypatch.setattr("app.integrations.telegram.urllib.request.urlopen", fake_urlopen)

    TelegramClient("token").send_message(42, "<b>title</b>", parse_mode="HTML")

    assert captured["body"]["parse_mode"] == "HTML"


def test_get_chat_member_returns_structured_active_membership(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.telegram.urllib.request.urlopen",
        lambda request, timeout: FakeResponse({"ok": True, "result": {"status": "administrator"}}),
    )

    membership = TelegramClient("token").get_chat_member(-1001, 42)

    assert membership.status == "administrator"
    assert membership.is_member is True


@pytest.mark.parametrize(
    ("result", "expected_is_member"),
    [
        ({"status": "restricted", "is_member": True}, True),
        ({"status": "restricted", "is_member": False}, False),
        ({"status": "left"}, False),
        ({"status": "kicked"}, False),
    ],
)
def test_get_chat_member_preserves_membership_state(
    monkeypatch,
    result: dict,
    expected_is_member: bool,
) -> None:
    monkeypatch.setattr(
        "app.integrations.telegram.urllib.request.urlopen",
        lambda request, timeout: FakeResponse({"ok": True, "result": result}),
    )

    membership = TelegramClient("token").get_chat_member(-1001, 42)

    assert membership.status == result["status"]
    assert membership.is_member is expected_is_member


def test_answer_callback_query_accepts_boolean_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.telegram.urllib.request.urlopen",
        lambda request, timeout: FakeResponse({"ok": True, "result": True}),
    )

    TelegramClient("token").answer_callback_query("callback-1")


def test_http_error_is_normalized_without_token_leak(monkeypatch) -> None:
    error = HTTPError(
        url="https://api.telegram.org/bottoken/sendMessage",
        code=429,
        msg="Too Many Requests",
        hdrs={"Retry-After": "3"},
        fp=BytesIO(b'{"ok":false,"description":"Too Many Requests"}'),
    )
    monkeypatch.setattr(
        "app.integrations.telegram.urllib.request.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(error),
    )

    with pytest.raises(TelegramApiError) as raised:
        TelegramClient("secret-token").send_message(42, "hello")

    assert raised.value.status_code == 429
    assert raised.value.retry_after_seconds == 3
    assert "secret-token" not in str(raised.value)


def test_set_webhook_subscribes_to_membership_updates(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"ok": True, "result": True})

    monkeypatch.setattr(
        "scripts.set_webhook.load_settings",
        lambda: SimpleNamespace(
            webhook_url="https://example.test/telegram/webhook",
            webhook_secret_token="secret",
            telegram_bot_token="777:token",
        ),
    )
    monkeypatch.setattr("scripts.set_webhook.load_dotenv", lambda: None)
    monkeypatch.setattr("scripts.set_webhook.urllib.request.urlopen", fake_urlopen)

    from scripts.set_webhook import main

    assert main() == 0
    assert captured["body"]["allowed_updates"] == [
        "message",
        "callback_query",
        "chat_member",
    ]
    assert captured["body"]["drop_pending_updates"] is False


def test_ephemeral_send_targets_one_user_and_returns_ephemeral_id(monkeypatch) -> None:
    captured = []

    def fake_urlopen(request, timeout):
        captured.append(json.loads(request.data))
        return FakeResponse({"ok": True, "result": {
            "message_id": 0, "ephemeral_message_id": 987,
        }})

    monkeypatch.setattr("app.integrations.telegram.urllib.request.urlopen", fake_urlopen)
    result = TelegramClient("token").send_message(-1001, "Сводка", receiver_user_id=42)

    assert result == 987
    assert captured == [{
        "chat_id": -1001, "text": "Сводка",
        "ephemeral_message_parameters": {"receiver_user_id": 42},
    }]


def test_failed_ephemeral_send_never_retries_publicly(monkeypatch) -> None:
    captured = []

    def fake_urlopen(request, timeout):
        captured.append(json.loads(request.data))
        return FakeResponse({"ok": False, "description": "not an administrator"})

    monkeypatch.setattr("app.integrations.telegram.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(TelegramApiError):
        TelegramClient("token").send_message(-1001, "Сводка", receiver_user_id=42)

    assert len(captured) == 1
    assert captured[0]["ephemeral_message_parameters"] == {"receiver_user_id": 42}


def test_command_menus_hide_group_summary_but_keep_private_and_daily_commands(monkeypatch) -> None:
    from scripts.set_commands import configure_commands

    captured = []

    def fake_urlopen(request, timeout):
        assert request.full_url.endswith("/setMyCommands")
        captured.append(json.loads(request.data))
        return FakeResponse({"ok": True, "result": True})

    monkeypatch.setattr("app.integrations.telegram.urllib.request.urlopen", fake_urlopen)
    configure_commands(TelegramClient("token"))

    assert {(item["scope"]["type"], item["language_code"]) for item in captured} == {
        (scope, language)
        for scope in ("default", "all_group_chats", "all_chat_administrators", "all_private_chats")
        for language in ("", "ru")
    }
    for item in captured:
        commands = {command["command"]: command for command in item["commands"]}
        if item["scope"]["type"] == "all_private_chats":
            assert set(commands) == {"summary"}
            assert not commands["summary"].get("is_ephemeral", False)
        else:
            assert set(commands) == {"summary", "enable_daily", "disable_daily"}
            assert commands["summary"]["is_ephemeral"] is True
            assert not commands["enable_daily"].get("is_ephemeral", False)
            assert not commands["disable_daily"].get("is_ephemeral", False)


def test_command_registration_rejects_false_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.telegram.urllib.request.urlopen",
        lambda request, timeout: FakeResponse({"ok": True, "result": False}),
    )
    with pytest.raises(TelegramApiError):
        TelegramClient("token").set_my_commands([], scope={"type": "default"})
