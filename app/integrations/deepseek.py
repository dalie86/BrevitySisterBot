from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.models import LlmBalance, LlmResult
from app.services.llm_usage import estimate_flash_cost_microusd


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeepSeekApiError(RuntimeError):
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


class DeepSeekClient:
    endpoint = "https://api.deepseek.com/chat/completions"
    balance_endpoint = "https://api.deepseek.com/user/balance"

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        timeout_seconds: float = 90.0,
    ) -> None:
        if not api_key or any(not 0x21 <= ord(character) <= 0x7E for character in api_key):
            raise DeepSeekApiError("DeepSeek invalid API key")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def get_balance(self) -> tuple[bool, tuple[LlmBalance, ...]]:
        try:
            request = urllib.request.Request(
                self.balance_endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/json",
                },
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8").strip())
        except urllib.error.HTTPError as exc:
            raise DeepSeekApiError(
                _http_error_message(exc, self.api_key),
                status_code=exc.code,
                retry_after_seconds=_retry_after_seconds(exc.headers),
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise DeepSeekApiError("DeepSeek returned malformed balance JSON") from None
        except (OSError, ValueError, http.client.HTTPException):
            raise DeepSeekApiError("DeepSeek balance request failed") from None

        if not isinstance(body, dict) or not isinstance(body.get("is_available"), bool):
            raise DeepSeekApiError("DeepSeek returned malformed balance body")
        raw_balances = body.get("balance_infos")
        if not isinstance(raw_balances, list):
            raise DeepSeekApiError("DeepSeek returned malformed balance body")
        try:
            balances = tuple(_parse_balance(item) for item in raw_balances)
        except (InvalidOperation, KeyError, TypeError, ValueError):
            raise DeepSeekApiError("DeepSeek returned malformed balance body") from None
        return body["is_available"], balances

    def generate_summary(self, system_prompt: str, user_prompt: str) -> LlmResult:
        requested_at = _utc_now()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0.2,
            "max_tokens": 1200,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        started = time.monotonic()
        try:
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
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8").strip()
                body = json.loads(raw_body)
        except urllib.error.HTTPError as exc:
            raise DeepSeekApiError(
                _http_error_message(exc, self.api_key),
                status_code=exc.code,
                retry_after_seconds=_retry_after_seconds(exc.headers),
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise DeepSeekApiError("DeepSeek returned malformed JSON") from None
        except (OSError, ValueError, http.client.HTTPException):
            raise DeepSeekApiError("DeepSeek request failed") from None

        content = _extract_content(body)
        usage = body.get("usage") if isinstance(body, dict) else {}
        if not isinstance(usage, dict):
            usage = {}
        input_tokens = _optional_int(usage.get("prompt_tokens"))
        output_tokens = _optional_int(usage.get("completion_tokens"))
        cache_hit_input_tokens = _optional_int(
            usage.get("prompt_cache_hit_tokens")
        )
        cache_miss_input_tokens = _optional_int(
            usage.get("prompt_cache_miss_tokens")
        )
        if cache_miss_input_tokens is None:
            cache_miss_input_tokens = max(
                (input_tokens or 0) - (cache_hit_input_tokens or 0), 0
            )
        return LlmResult(
            content=content,
            provider="deepseek",
            model=_response_model(body, self.model),
            duration_ms=round((time.monotonic() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit_input_tokens=cache_hit_input_tokens,
            cache_miss_input_tokens=cache_miss_input_tokens,
            estimated_cost_microusd=estimate_flash_cost_microusd(
                cache_hit_input_tokens or 0,
                cache_miss_input_tokens,
                output_tokens or 0,
                requested_at,
            ),
        )


def _extract_content(body: Any) -> str:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise DeepSeekApiError("DeepSeek response has no message content") from None
    if not isinstance(content, str) or not content.strip():
        raise DeepSeekApiError("DeepSeek response has empty message content")
    return content.strip()


def _response_model(body: Any, configured_model: str) -> str:
    if isinstance(body, dict):
        model = body.get("model")
        if isinstance(model, str) and model:
            return model
    return configured_model


def _retry_after_seconds(headers: Any) -> int | None:
    if headers is None:
        return None
    try:
        return int(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None


def _http_error_message(error: urllib.error.HTTPError, api_key: str) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8").strip())
        message = payload.get("error", {}).get("message")
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        AttributeError,
        OSError,
        ValueError,
        http.client.HTTPException,
    ):
        message = None
    safe_message = str(message or f"DeepSeek HTTP error {error.code}")
    return safe_message.replace(api_key, "[redacted]") if api_key else safe_message


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_balance(item: Any) -> LlmBalance:
    if not isinstance(item, dict):
        raise TypeError("balance entry must be an object")
    currency = item["currency"]
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError("balance currency is required")
    return LlmBalance(
        currency=currency.strip().upper(),
        total_balance=Decimal(item["total_balance"]),
        granted_balance=Decimal(item["granted_balance"]),
        topped_up_balance=Decimal(item["topped_up_balance"]),
    )
