from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from app.services.updates import UpdateService

StartResponse = Callable[[str, list[tuple[str, str]]], Any]
HealthCheck = Callable[[], bool]


def create_application(
    *,
    webhook_secret: str,
    update_service: UpdateService,
    health_check: HealthCheck,
) -> Callable[[dict, StartResponse], Iterable[bytes]]:
    def application(environ: dict, start_response: StartResponse) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/"))

        if path == "/health":
            if method != "GET":
                return _json_response(start_response, "405 Method Not Allowed", {"ok": False})
            healthy = bool(health_check())
            status = "200 OK" if healthy else "503 Service Unavailable"
            return _json_response(start_response, status, {"ok": healthy})

        if path != "/telegram/webhook":
            return _json_response(start_response, "404 Not Found", {"ok": False})
        if method != "POST":
            return _json_response(start_response, "405 Method Not Allowed", {"ok": False})

        provided_secret = str(
            environ.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", "")
        )
        if provided_secret != webhook_secret:
            return _json_response(start_response, "403 Forbidden", {"ok": False})

        try:
            payload = _read_json_body(environ)
            if "update_id" not in payload:
                raise ValueError("update_id is missing")
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return _json_response(start_response, "400 Bad Request", {"ok": False})

        update_service.accept(payload, datetime.now(timezone.utc))
        return _json_response(start_response, "200 OK", {"ok": True})

    return application


def _read_json_body(environ: dict) -> dict:
    length = int(environ.get("CONTENT_LENGTH") or "0")
    body = environ["wsgi.input"].read(length)
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def _json_response(
    start_response: StartResponse,
    status: str,
    payload: dict,
) -> list[bytes]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    start_response(
        status,
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]
