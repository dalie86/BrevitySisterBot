from __future__ import annotations

import json
import sys

from dotenv import load_dotenv

from app.config import load_settings
from app.integrations.deepseek import DeepSeekClient


def main() -> int:
    load_dotenv()
    settings = load_settings()
    result = DeepSeekClient(
        settings.deepseek_api_key,
        settings.deepseek_model,
    ).generate_summary(
        'Return only a JSON object matching {"ok": true}.',
        "Return the requested JSON object now.",
    )
    try:
        payload = json.loads(result.content)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        print("DeepSeek check returned invalid JSON object", file=sys.stderr)
        return 1
    input_tokens = getattr(result, "input_tokens", None)
    output_tokens = getattr(result, "output_tokens", None)
    if not _valid_token_count(input_tokens) or not _valid_token_count(output_tokens):
        print("DeepSeek check returned invalid usage telemetry", file=sys.stderr)
        return 1
    if not _matches_requested_model(result.model, settings.deepseek_model):
        print("DeepSeek check returned unexpected model", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "provider": result.provider,
                "model": result.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            sort_keys=True,
        )
    )
    return 0


def _valid_token_count(value: object) -> bool:
    return type(value) is int and value >= 0


def _matches_requested_model(response_model: object, requested_model: str) -> bool:
    return isinstance(response_model, str) and (
        response_model == requested_model
        or response_model.startswith(f"{requested_model}-")
    )


if __name__ == "__main__":
    raise SystemExit(main())
