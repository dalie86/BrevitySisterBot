import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from app.integrations.groq import GroqApiError, GroqClient


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_generate_summary_uses_json_mode_and_returns_usage(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "choices": [{"message": {"content": '{"headline":"Итог","topics":[]}'}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            }
        )

    monkeypatch.setattr("app.integrations.groq.urllib.request.urlopen", fake_urlopen)

    result = GroqClient("secret", "qwen/qwen3.6-27b", timeout_seconds=12).generate_summary(
        "system",
        "user",
    )

    assert result.content == '{"headline":"Итог","topics":[]}'
    assert result.input_tokens == 120
    assert result.output_tokens == 30
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["model"] == "qwen/qwen3.6-27b"
    assert captured["timeout"] == 12
    assert "secret" not in repr(captured["body"])


def test_http_429_is_normalized_without_api_key(monkeypatch) -> None:
    error = HTTPError(
        url="https://api.groq.com/openai/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs={"Retry-After": "9"},
        fp=BytesIO(b'{"error":{"message":"rate limited"}}'),
    )
    monkeypatch.setattr(
        "app.integrations.groq.urllib.request.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(error),
    )

    with pytest.raises(GroqApiError) as raised:
        GroqClient("super-secret", "model").generate_summary("system", "user")

    assert raised.value.status_code == 429
    assert raised.value.retry_after_seconds == 9
    assert "super-secret" not in str(raised.value)
