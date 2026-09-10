from __future__ import annotations

import difflib

MAX_PREVIEW_LINES = 12
MAX_DIFF_CONTEXT = 3


def build_change_preview(
    file_path: str,
    before: str | None,
    after: str,
    *,
    action: str,
) -> str:
    lines: list[str] = [f"[Preview] {action}: {file_path}"]

    before_lines = before.splitlines() if before is not None else []
    after_lines = after.splitlines()

    if before is None:
        for line in after_lines[:MAX_PREVIEW_LINES]:
            lines.append(f"+ {line}")
        remaining = len(after_lines) - MAX_PREVIEW_LINES
        if remaining > 0:
            lines.append(f"… ({remaining} more lines)")
        return "\n".join(lines)

    diff = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="before",
            tofile="after",
            lineterm="",
            n=MAX_DIFF_CONTEXT,
        )
    )
    if len(diff) <= 2:
        lines.append("[Preview] no content change")
        return "\n".join(lines)

    visible = diff[:MAX_PREVIEW_LINES]
    lines.extend(visible)
    remaining = len(diff) - len(visible)
    if remaining > 0:
        lines.append(f"… ({remaining} more lines)")
    return "\n".join(lines)
