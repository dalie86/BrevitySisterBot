import json
from types import SimpleNamespace

import pytest

from scripts import check_deepseek


def test_check_uses_synthetic_prompt_and_prints_only_model_metadata(
    monkeypatch, capsys
) -> None:
    captured = {}
    settings = SimpleNamespace(
        deepseek_api_key="super-secret",
        deepseek_model="deepseek-v4-flash",
    )
    monkeypatch.setattr(check_deepseek, "load_settings", lambda: settings)

    class FakeClient:
        def generate_summary(self, system_prompt, user_prompt):
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return SimpleNamespace(
                content='{"ok":true}',
                provider="deepseek",
                model="deepseek-v4-flash-0731",
                input_tokens=12,
                output_tokens=3,
            )

    monkeypatch.setattr(
        check_deepseek,
        "DeepSeekClient",
        lambda api_key, model: FakeClient(),
    )

    assert check_deepseek.main() == 0

    assert captured == {
        "system_prompt": 'Return only a JSON object matching {"ok": true}.',
        "user_prompt": "Return the requested JSON object now.",
    }
    assert json.loads(capsys.readouterr().out) == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash-0731",
        "input_tokens": 12,
        "output_tokens": 3,
    }


def test_check_rejects_a_non_object_response(monkeypatch, capsys) -> None:
    settings = SimpleNamespace(
        deepseek_api_key="super-secret",
        deepseek_model="deepseek-v4-flash",
    )
    monkeypatch.setattr(check_deepseek, "load_settings", lambda: settings)

    class FakeClient:
        def generate_summary(self, system_prompt, user_prompt):
            return SimpleNamespace(content="[]")

    monkeypatch.setattr(
        check_deepseek,
        "DeepSeekClient",
        lambda api_key, model: FakeClient(),
    )

    assert check_deepseek.main() == 1
    assert capsys.readouterr().err == "DeepSeek check returned invalid JSON object\n"


def test_check_rejects_an_object_without_the_requested_ok_signal(monkeypatch, capsys) -> None:
    settings = SimpleNamespace(
        deepseek_api_key="super-secret",
        deepseek_model="deepseek-v4-flash",
    )
    monkeypatch.setattr(check_deepseek, "load_settings", lambda: settings)

    class FakeClient:
        def generate_summary(self, system_prompt, user_prompt):
            return SimpleNamespace(
                content='{"ok": false}',
                provider="deepseek",
                model="deepseek-v4-flash",
                input_tokens=1,
                output_tokens=1,
            )

    monkeypatch.setattr(
        check_deepseek,
        "DeepSeekClient",
        lambda api_key, model: FakeClient(),
    )

    assert check_deepseek.main() == 1
    assert capsys.readouterr().err == "DeepSeek check returned invalid JSON object\n"


@pytest.mark.parametrize(
    ("input_tokens", "output_tokens"),
    [
        (None, 3),
        (12, None),
        ("12", 3),
        (12, 3.5),
    ],
)
def test_check_rejects_missing_or_non_numeric_usage_telemetry(
    monkeypatch, capsys, input_tokens, output_tokens
) -> None:
    settings = SimpleNamespace(
        deepseek_api_key="fake-api-key",
        deepseek_model="deepseek-v4-flash",
    )
    monkeypatch.setattr(check_deepseek, "load_settings", lambda: settings)

    class FakeClient:
        def generate_summary(self, system_prompt, user_prompt):
            return SimpleNamespace(
                content='{"ok": true}',
                provider="deepseek",
                model="deepseek-v4-flash-0731",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

    monkeypatch.setattr(
        check_deepseek,
        "DeepSeekClient",
        lambda api_key, model: FakeClient(),
    )

    assert check_deepseek.main() == 1
    assert capsys.readouterr().err == (
        "DeepSeek check returned invalid usage telemetry\n"
    )


def test_check_rejects_response_from_unexpected_model(monkeypatch, capsys) -> None:
    settings = SimpleNamespace(
        deepseek_api_key="fake-api-key",
        deepseek_model="deepseek-v4-flash",
    )
    monkeypatch.setattr(check_deepseek, "load_settings", lambda: settings)

    class FakeClient:
        def generate_summary(self, system_prompt, user_prompt):
            return SimpleNamespace(
                content='{"ok": true}',
                provider="deepseek",
                model="deepseek-reasoner",
                input_tokens=12,
                output_tokens=3,
            )

    monkeypatch.setattr(
        check_deepseek,
        "DeepSeekClient",
        lambda api_key, model: FakeClient(),
    )

    assert check_deepseek.main() == 1
    assert capsys.readouterr().err == "DeepSeek check returned unexpected model\n"
