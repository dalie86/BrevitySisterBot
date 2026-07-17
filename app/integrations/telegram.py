from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.domain.models import TelegramMembership


@dataclass(frozen=True)
class IncomingMessage:
    message_id: int
    chat_id: int
    chat_type: str
    chat_title: str
    user_id: int | None
    username: str
    full_name: str
    sender_is_bot: bool
    text: str
    sent_at: datetime


@dataclass(frozen=True)
class IncomingCallback:
    callback_id: str
    user_id: int
    chat_id: int
    data: str


@dataclass(frozen=True)
class IncomingChatShared:
    request_id: int
    shared_chat_id: int
    title: str
    user_id: int
    private_chat_id: int


@dataclass(frozen=True)
class IncomingChatMember:
    chat_id: int
    user_id: int
    username: str
    full_name: str
    new_membership: TelegramMembership


@dataclass(frozen=True)
class IncomingUpdate:
    update_id: int
    message: IncomingMessage | None = None
    callback: IncomingCallback | None = None
    chat_shared: IncomingChatShared | None = None
    chat_member: IncomingChatMember | None = None


class TelegramApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class TelegramClient:
    def __init__(self, token: str, timeout_seconds: float = 10.0) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.timeout_seconds = timeout_seconds

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ) -> int:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        result = self._call("sendMessage", payload)
        if not isinstance(result, dict):
            raise TelegramApiError("Telegram sendMessage response is invalid")
        return int(result["message_id"])

    def get_chat_member(self, chat_id: int, user_id: int) -> TelegramMembership:
        result = self._call(
            "getChatMember",
            {"chat_id": chat_id, "user_id": user_id},
        )
        if not isinstance(result, dict):
            raise TelegramApiError("Telegram getChatMember response is invalid")
        status = str(result["status"])
        return TelegramMembership(
            status=status,
            is_member=bool(
                result.get(
                    "is_member",
                    status in {"creator", "administrator", "member"},
                )
            ),
        )

    def answer_callback_query(self, callback_id: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id})

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        request = urllib.request.Request(
            url=f"{self.base_url}/{method}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            retry_after = _retry_after_seconds(exc.headers)
            description = _http_error_description(exc)
            raise TelegramApiError(
                description,
                status_code=exc.code,
                retry_after_seconds=retry_after,
            ) from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TelegramApiError(f"Telegram request failed: {type(exc).__name__}") from None

        if not body.get("ok"):
            raise TelegramApiError(str(body.get("description", "Telegram API error")))
        result = body.get("result")
        if result is None:
            raise TelegramApiError("Telegram API response has no result")
        return result


def parse_update(payload: dict[str, Any]) -> IncomingUpdate:
    update_id = int(payload["update_id"])
    raw_message = payload.get("message")
    if isinstance(raw_message, dict):
        raw_chat_shared = raw_message.get("chat_shared")
        if isinstance(raw_chat_shared, dict):
            raw_chat = raw_message.get("chat") or {}
            raw_user = raw_message.get("from") or {}
            return IncomingUpdate(
                update_id=update_id,
                chat_shared=IncomingChatShared(
                    request_id=int(raw_chat_shared["request_id"]),
                    shared_chat_id=int(raw_chat_shared["chat_id"]),
                    title=str(
                        raw_chat_shared.get("title") or raw_chat_shared["chat_id"]
                    ),
                    user_id=int(raw_user["id"]),
                    private_chat_id=int(raw_chat["id"]),
                ),
            )
        return IncomingUpdate(update_id=update_id, message=_parse_message(raw_message))

    raw_chat_member = payload.get("chat_member")
    if isinstance(raw_chat_member, dict):
        raw_chat = raw_chat_member.get("chat") or {}
        new_chat_member = raw_chat_member.get("new_chat_member") or {}
        raw_user = new_chat_member.get("user") or {}
        status = str(new_chat_member.get("status", ""))
        username, full_name = _parse_user_identity(raw_user)
        return IncomingUpdate(
            update_id=update_id,
            chat_member=IncomingChatMember(
                chat_id=int(raw_chat["id"]),
                user_id=int(raw_user["id"]),
                username=username,
                full_name=full_name,
                new_membership=TelegramMembership(
                    status=status,
                    is_member=bool(
                        new_chat_member.get(
                            "is_member",
                            status in {"creator", "administrator", "member"},
                        )
                    ),
                ),
            ),
        )

    raw_callback = payload.get("callback_query")
    if isinstance(raw_callback, dict):
        callback_message = raw_callback.get("message") or {}
        callback_from = raw_callback.get("from") or {}
        callback_chat = callback_message.get("chat") or {}
        return IncomingUpdate(
            update_id=update_id,
            callback=IncomingCallback(
                callback_id=str(raw_callback.get("id", "")),
                user_id=int(callback_from["id"]),
                chat_id=int(callback_chat["id"]),
                data=str(raw_callback.get("data", "")),
            ),
        )

    return IncomingUpdate(update_id=update_id)


def _parse_message(raw_message: dict[str, Any]) -> IncomingMessage:
    raw_chat = raw_message.get("chat") or {}
    raw_user = raw_message.get("from") or {}
    username, full_name = _parse_user_identity(raw_user)
    text = str(raw_message.get("text") or raw_message.get("caption") or "").strip()
    return IncomingMessage(
        message_id=int(raw_message["message_id"]),
        chat_id=int(raw_chat["id"]),
        chat_type=str(raw_chat.get("type", "")),
        chat_title=str(raw_chat.get("title") or raw_chat.get("username") or raw_chat["id"]),
        user_id=int(raw_user["id"]) if raw_user.get("id") is not None else None,
        username=username,
        full_name=full_name,
        sender_is_bot=bool(raw_user.get("is_bot", False)),
        text=text,
        sent_at=datetime.fromtimestamp(int(raw_message["date"]), tz=timezone.utc),
    )


def _parse_user_identity(raw_user: dict[str, Any]) -> tuple[str, str]:
    first_name = str(raw_user.get("first_name", "")).strip()
    last_name = str(raw_user.get("last_name", "")).strip()
    full_name = " ".join(part for part in (first_name, last_name) if part)
    username_value = str(raw_user.get("username", "")).strip()
    return (
        f"@{username_value}" if username_value else "",
        full_name or username_value or "Unknown User",
    )


def _retry_after_seconds(headers: Any) -> int | None:
    if headers is None:
        return None
    raw_value = headers.get("Retry-After")
    if raw_value is None:
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _http_error_description(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}
    return str(body.get("description") or f"Telegram HTTP error {error.code}")
