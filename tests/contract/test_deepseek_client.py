import json
from datetime import datetime, timezone
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from app.integrations import deepseek as deepseek_module
from app.integrations.deepseek import DeepSeekApiError, DeepSeekClient


class FakeResponse:
    def __init__(self, payload: dict | bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


def test_generate_summary_uses_exact_request_contract_and_returns_usage(monkeypatch) -> None:
    captured = {}
    clock_calls = []

    def fixed_utc_now():
        clock_calls.append(True)
        return datetime(2026, 7, 20, 2, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(deepseek_module, "_utc_now", fixed_utc_now)

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "model": "deepseek-v4-flash-0731",
                "choices": [
                    {"message": {"content": '{"headline":"Итог","topics":[]}'}}
                ],
                "usage": {
                    "prompt_tokens": 120,
                    "prompt_cache_hit_tokens": 20,
                    "prompt_cache_miss_tokens": 100,
                    "completion_tokens": 30,
                },
            }
        )

    monkeypatch.setattr("app.integrations.deepseek.urllib.request.urlopen", fake_urlopen)

    result = DeepSeekClient("secret").generate_summary("system JSON", "user")

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["body"] == {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "system JSON"},
            {"role": "user", "content": "user"},
        ],
        "thinking": {"type": "disabled"},
        "temperature": 0.2,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    assert captured["timeout"] == 90
    assert result.content == '{"headline":"Итог","topics":[]}'
    assert result.input_tokens == 120
    assert result.output_tokens == 30
    assert result.cache_hit_input_tokens == 20
    assert result.cache_miss_input_tokens == 100
    assert result.estimated_cost_microusd == 84
    assert clock_calls == [True]
    assert result.provider == "deepseek"
    assert result.model == "deepseek-v4-flash-0731"
    assert "secret" not in repr(captured["body"])


def test_missing_cache_miss_usage_is_derived_from_prompt_and_hit_tokens(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.deepseek.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {
                    "prompt_tokens": 120,
                    "prompt_cache_hit_tokens": 30,
                    "completion_tokens": 10,
                },
            }
        ),
    )

    result = DeepSeekClient("secret").generate_summary("system", "user")

    assert result.cache_hit_input_tokens == 30
    assert result.cache_miss_input_tokens == 90


@pytest.mark.parametrize("status_code", [400, 401, 402, 422, 429, 500, 503])
def test_http_errors_preserve_status_and_retry_after_without_api_key(
    monkeypatch, status_code
) -> None:
    error = HTTPError(
        url="https://api.deepseek.com/chat/completions",
        code=status_code,
        msg="HTTP error",
        hdrs={"Retry-After": "9"},
        fp=BytesIO(b'{"error":{"message":"super-secret was rejected"}}'),
    )
    monkeypatch.setattr(
        "app.integrations.deepseek.urllib.request.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(error),
    )

    with pytest.raises(DeepSeekApiError) as raised:
        DeepSeekClient("super-secret").generate_summary("system", "user")

    assert raised.value.status_code == status_code
    assert raised.value.retry_after_seconds == 9
    assert "super-secret" not in str(raised.value)


def test_response_body_may_begin_with_blank_lines(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.deepseek.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            b'\n\n  {"model":"actual","choices":[{"message":{"content":"{}"}}]}'
        ),
    )

    result = DeepSeekClient("secret").generate_summary("system", "user")

    assert result.content == "{}"
    assert result.model == "actual"


def test_missing_response_model_falls_back_to_configured_model(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.deepseek.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            {"choices": [{"message": {"content": "{}"}}]}
        ),
    )

    result = DeepSeekClient("secret", model="configured").generate_summary(
        "system", "user"
    )

    assert result.model == "configured"


def test_network_error_is_normalized_without_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.deepseek.urllib.request.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(URLError("offline")),
    )

    with pytest.raises(DeepSeekApiError, match="request failed") as raised:
        DeepSeekClient("super-secret").generate_summary("system", "user")

    assert "super-secret" not in str(raised.value)


def test_newline_api_key_is_rejected_before_opening_request(monkeypatch) -> None:
    opened = False

    def fake_urlopen(request, timeout):
        nonlocal opened
        opened = True
        return FakeResponse({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(
        "app.integrations.deepseek.urllib.request.urlopen",
        fake_urlopen,
    )

    with pytest.raises(DeepSeekApiError, match="invalid API key") as raised:
        DeepSeekClient("line-one\nline-two").generate_summary("system", "user")

    assert opened is False
    assert "line-one" not in str(raised.value)
    assert "line-two" not in str(raised.value)


def test_open_time_value_error_is_normalized_without_api_key(monkeypatch) -> None:
    api_key = "open-super-secret"
    monkeypatch.setattr(
        "app.integrations.deepseek.urllib.request.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(
            ValueError(f"Invalid header value containing Bearer {api_key}")
        ),
    )

    with pytest.raises(DeepSeekApiError, match="request failed") as raised:
        DeepSeekClient(api_key).generate_summary("system", "user")

    assert api_key not in str(raised.value)


def test_read_time_error_is_normalized_without_api_key(monkeypatch) -> None:
    api_key = "read-super-secret"

    class FailingResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self) -> bytes:
            raise OSError(f"socket read failed for Bearer {api_key}")

    monkeypatch.setattr(
        "app.integrations.deepseek.urllib.request.urlopen",
        lambda request, timeout: FailingResponse(),
    )

    with pytest.raises(DeepSeekApiError, match="request failed") as raised:
        DeepSeekClient(api_key).generate_summary("system", "user")

    assert api_key not in str(raised.value)


def test_http_error_read_failure_is_normalized_without_api_key(monkeypatch) -> None:
    api_key = "http-read-super-secret"

    class FailingBody:
        def read(self, size=-1) -> bytes:
            raise ValueError(f"failed to read error for Bearer {api_key}")

        def close(self) -> None:
            return None

    error = HTTPError(
        url="https://api.deepseek.com/chat/completions",
        code=503,
        msg="Service Unavailable",
        hdrs={"Retry-After": "4"},
        fp=FailingBody(),
    )
    monkeypatch.setattr(
        "app.integrations.deepseek.urllib.request.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(error),
    )

    with pytest.raises(DeepSeekApiError, match="DeepSeek HTTP error 503") as raised:
        DeepSeekClient(api_key).generate_summary("system", "user")

    assert raised.value.status_code == 503
    assert raised.value.retry_after_seconds == 4
    assert api_key not in str(raised.value)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not-json", "malformed JSON"),
        (b"{}", "no message content"),
        (
            b'{"choices":[{"message":{"content":"  "}}]}',
            "empty message content",
        ),
    ],
)
def test_invalid_responses_are_normalized(monkeypatch, payload, message) -> None:
    monkeypatch.setattr(
        "app.integrations.deepseek.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(payload),
    )

    with pytest.raises(DeepSeekApiError, match=message):
        DeepSeekClient("secret").generate_summary("system", "user")
