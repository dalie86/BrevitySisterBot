from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)
ollama_logger = logging.getLogger("ollama_client")


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
    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    if provider in {"ollama", "local", "qwen"}:
        return _request_ollama(system_prompt, user_prompt)

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
            "temperature": 0.31,
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


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: int,
) -> Optional[dict[str, Any]]:
    return _do_request(
        url,
        json.dumps(payload).encode("utf-8"),
        headers,
        timeout=timeout,
    )


def _do_request(
    url: str,
    data: bytes,
    headers: dict[str, str],
    *,
    timeout: int,
) -> Optional[dict[str, Any]]:
    request = urllib.request.Request(url=url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
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
