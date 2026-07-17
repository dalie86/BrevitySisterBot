from __future__ import annotations

from html import escape


def format_summary_for_telegram(summary: str) -> str:
    lines = summary.splitlines()
    if not lines:
        return ""

    first_line = lines[0].strip()
    rest = lines[1:]

    formatted_lines = [f"<b>{escape(first_line)}</b>"]
    formatted_lines.extend(escape(line) for line in rest)
    return "\n".join(formatted_lines)
