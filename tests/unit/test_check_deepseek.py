import json
from types import SimpleNamespace

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
