from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from app.domain.models import LlmResult


class GroqApiError(RuntimeError):
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


class GroqClient:
    endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 45.0) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def generate_summary(self, system_prompt: str, user_prompt: str) -> LlmResult:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise GroqApiError(
                _http_error_message(exc),
                status_code=exc.code,
                retry_after_seconds=_retry_after_seconds(exc.headers),
            ) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GroqApiError(f"Groq request failed: {type(exc).__name__}") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise GroqApiError("Groq returned malformed JSON") from None

        content = _extract_content(body)
        usage = body.get("usage") if isinstance(body, dict) else {}
        if not isinstance(usage, dict):
            usage = {}
        return LlmResult(
            content=content,
            provider="groq",
            model=self.model,
            duration_ms=round((time.monotonic() - started) * 1000),
            input_tokens=_optional_int(usage.get("prompt_tokens")),
            output_tokens=_optional_int(usage.get("completion_tokens")),
        )


def _extract_content(body: Any) -> str:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise GroqApiError("Groq response has no message content") from None
    if not isinstance(content, str) or not content.strip():
        raise GroqApiError("Groq response has empty message content")
    return content.strip()


def _retry_after_seconds(headers: Any) -> int | None:
    if headers is None:
        return None
    try:
        return int(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None


def _http_error_message(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
        message = payload.get("error", {}).get("message")
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        message = None
    return str(message or f"Groq HTTP error {error.code}")


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
