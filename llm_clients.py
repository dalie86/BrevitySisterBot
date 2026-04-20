from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)
ollama_logger = logging.getLogger("ollama_client")

_GIGACHAT_TOKEN: Optional[str] = None
_GIGACHAT_TOKEN_EXPIRES_AT = 0.0


def _configure_ollama_logger() -> None:
    if ollama_logger.handlers:
        return

    ollama_logger.setLevel(logging.INFO)
    ollama_logger.propagate = False
    log_dir = Path(__file__).resolve().parent / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / "ollama.log", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        )
        ollama_logger.addHandler(handler)
    except Exception as exc:  # pragma: no cover - logging fallback
        logger.warning("Could not initialize ollama.log file handler: %s", exc)


_configure_ollama_logger()


def request_summary_json(system_prompt: str, user_prompt: str) -> Optional[str]:
    provider = os.getenv("LLM_PROVIDER", "yandex").strip().lower()
    if provider in {"ollama", "local", "qwen"}:
        return _request_ollama(system_prompt, user_prompt)
    if provider in {"yandex", "yandexgpt"}:
        return _request_yandex(system_prompt, user_prompt)
    if provider == "gigachat":
        return _request_gigachat(system_prompt, user_prompt)

    logger.warning("Unsupported LLM_PROVIDER=%s", provider)
    return None


def _request_ollama(system_prompt: str, user_prompt: str) -> Optional[str]:
    api_url = os.getenv(
        "OLLAMA_API_URL",
        "http://127.0.0.1:11434/api/chat",
    ).strip()
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b-instruct").strip()
    keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "10m").strip()

    if not model:
        logger.info("OLLAMA_MODEL is not configured.")
        ollama_logger.warning("OLLAMA_MODEL is not configured.")
        return None

    ollama_logger.info(
        "Sending summary request to Ollama model=%s api_url=%s prompt_chars=%s",
        model,
        api_url,
        len(system_prompt) + len(user_prompt),
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": "json",
        "keep_alive": keep_alive,
        "options": {
            "temperature": 0.2,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    response_json = _post_json(api_url, payload, headers=headers, timeout=180)
    if not response_json:
        ollama_logger.error(
            "Ollama returned no response for model=%s api_url=%s",
            model,
            api_url,
        )
        return None

    message = response_json.get("message", {})
    content = str(message.get("content", "")).strip()
    if not content:
        ollama_logger.error(
            "Ollama response for model=%s did not contain message.content. Raw keys=%s",
            model,
            sorted(response_json.keys()),
        )
        return None

    ollama_logger.info(
        "Received Ollama response model=%s chars=%s preview=%s",
        model,
        len(content),
        content[:300].replace("\n", " "),
    )
    return content


def _request_yandex(system_prompt: str, user_prompt: str) -> Optional[str]:
    api_key = os.getenv("YANDEX_API_KEY", "").strip()
    iam_token = os.getenv("YANDEX_IAM_TOKEN", "").strip()
    folder_id = os.getenv("YANDEX_FOLDER_ID", "").strip()
    model_uri = os.getenv("YANDEX_MODEL_URI", "").strip() or (
        f"gpt://{folder_id}/yandexgpt-lite/latest" if folder_id else ""
    )
    api_url = os.getenv(
        "YANDEX_API_URL",
        "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
    ).strip()

    if not model_uri:
        logger.info("Yandex model URI is not configured.")
        return None
    if not api_key and not iam_token:
        logger.info("Neither YANDEX_API_KEY nor YANDEX_IAM_TOKEN is configured.")
        return None

    authorization = f"Api-Key {api_key}" if api_key else f"Bearer {iam_token}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": authorization,
        "x-client-request-id": str(uuid4()),
    }
    if folder_id:
        headers["x-folder-id"] = folder_id

    payload = {
        "modelUri": model_uri,
        "completionOptions": {
            "stream": False,
            "temperature": 0.2,
            "maxTokens": "220",
        },
        "messages": [
            {"role": "system", "text": system_prompt},
            {"role": "user", "text": user_prompt},
        ],
    }
    response_json = _post_json(api_url, payload, headers=headers, timeout=60)
    if not response_json:
        return None

    result = response_json.get("result", response_json)
    alternatives = result.get("alternatives", [])
    if not alternatives:
        return None
    message = alternatives[0].get("message", {})
    return str(message.get("text", "")).strip() or None


def _request_gigachat(system_prompt: str, user_prompt: str) -> Optional[str]:
    access_token = _get_gigachat_access_token()
    if not access_token:
        return None

    base_url = os.getenv(
        "GIGACHAT_BASE_URL",
        "https://gigachat.devices.sberbank.ru/api/v1",
    ).rstrip("/")
    model = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Lite").strip()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    response_json = _post_json(
        f"{base_url}/chat/completions",
        payload,
        headers=headers,
        timeout=60,
        ssl_context=_build_ssl_context("GIGACHAT"),
    )
    if not response_json:
        return None

    choices = response_json.get("choices", [])
    if not choices:
        return None
    message = choices[0].get("message", {})
    return str(message.get("content", "")).strip() or None


def _get_gigachat_access_token() -> Optional[str]:
    global _GIGACHAT_TOKEN
    global _GIGACHAT_TOKEN_EXPIRES_AT

    direct_token = os.getenv("GIGACHAT_ACCESS_TOKEN", "").strip()
    if direct_token:
        return direct_token

    now = time.time()
    if _GIGACHAT_TOKEN and now < (_GIGACHAT_TOKEN_EXPIRES_AT - 60):
        return _GIGACHAT_TOKEN

    auth_key = os.getenv("GIGACHAT_AUTH_KEY", "").strip()
    if not auth_key:
        logger.info("GIGACHAT_AUTH_KEY is not configured.")
        return None

    auth_url = os.getenv(
        "GIGACHAT_AUTH_URL",
        "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
    ).strip()
    scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid4()),
        "Authorization": f"Basic {auth_key}",
    }
    response_json = _post_form(
        auth_url,
        {"scope": scope},
        headers=headers,
        timeout=30,
        ssl_context=_build_ssl_context("GIGACHAT"),
    )
    if not response_json:
        return None

    token = str(response_json.get("access_token", "")).strip()
    expires_at = response_json.get("expires_at")
    if not token:
        return None

    _GIGACHAT_TOKEN = token
    _GIGACHAT_TOKEN_EXPIRES_AT = _normalize_expiry(expires_at)
    return token


def _normalize_expiry(expires_at: Any) -> float:
    try:
        value = float(expires_at)
    except (TypeError, ValueError):
        return time.time() + 25 * 60
    if value > 10**12:
        return value / 1000.0
    if value > 10**10:
        return value / 1000.0
    if value > 10**8:
        return value
    return time.time() + value


def _build_ssl_context(prefix: str) -> ssl.SSLContext:
    verify_ssl = os.getenv(f"{prefix}_VERIFY_SSL_CERTS", "true").strip().lower() != "false"
    ca_bundle = os.getenv(f"{prefix}_CA_BUNDLE_FILE", "").strip()

    if not verify_ssl:
        return ssl._create_unverified_context()
    if ca_bundle:
        return ssl.create_default_context(cafile=ca_bundle)
    return ssl.create_default_context()


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: int,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> Optional[dict[str, Any]]:
    return _do_request(
        url,
        json.dumps(payload).encode("utf-8"),
        headers,
        timeout=timeout,
        ssl_context=ssl_context,
    )


def _post_form(
    url: str,
    payload: dict[str, str],
    *,
    headers: dict[str, str],
    timeout: int,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> Optional[dict[str, Any]]:
    return _do_request(
        url,
        urllib.parse.urlencode(payload).encode("utf-8"),
        headers,
        timeout=timeout,
        ssl_context=ssl_context,
    )


def _do_request(
    url: str,
    data: bytes,
    headers: dict[str, str],
    *,
    timeout: int,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> Optional[dict[str, Any]]:
    request = urllib.request.Request(url=url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=ssl_context,
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        logger.warning("LLM HTTP error %s from %s: %s", exc.code, url, body[:500])
        if "11434" in url:
            ollama_logger.error(
                "Ollama HTTP error %s from %s: %s",
                exc.code,
                url,
                body[:1000],
            )
    except Exception as exc:
        logger.warning("LLM request to %s failed: %s", url, exc)
        if "11434" in url:
            ollama_logger.error("Ollama request to %s failed: %s", url, exc)
    return None
