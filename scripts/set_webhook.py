from __future__ import annotations

import json
import urllib.request

from dotenv import load_dotenv

from app.config import load_settings


def main() -> int:
    load_dotenv()
    settings = load_settings()
    payload = {
        "url": settings.webhook_url,
        "secret_token": settings.webhook_secret_token,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": False,
    }
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(str(result.get("description", "setWebhook failed")))
    print("Webhook configured:", settings.webhook_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
