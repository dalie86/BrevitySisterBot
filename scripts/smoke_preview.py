from __future__ import annotations

import argparse
import json
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default="https://brevity.prosto-kosmos.ru",
    )
    arguments = parser.parse_args()
    url = arguments.base_url.rstrip("/") + "/health"
    with urllib.request.urlopen(url, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
        status = response.status
    if status != 200 or payload != {"ok": True}:
        raise RuntimeError(f"Health check failed: status={status}, payload={payload!r}")
    print("Health check passed:", url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
