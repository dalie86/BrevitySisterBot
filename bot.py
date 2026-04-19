from __future__ import annotations

import sys

from collect_updates import main as collect_main
from prepare_summary import main as prepare_main
from send_summary import main as send_main


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "collect":
        collect_main()
        return
    if command == "prepare":
        prepare_main()
        return
    if command == "send":
        send_main()
        return

    raise SystemExit(
        "Usage:\n"
        "  python3 bot.py collect\n"
        "  python3 bot.py prepare\n"
        "  python3 bot.py send"
    )


if __name__ == "__main__":
    main()
