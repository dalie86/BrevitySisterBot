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
    print(
        json.dumps(
            {
                "provider": result.provider,
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
