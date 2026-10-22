"""Human-facing workflow text shared by runtime and Canvas projection."""

from __future__ import annotations

import re
from typing import Any


_RUNTIME_CURRENT_STATE = re.compile(
    r"(?is)^(?:initialized|cycle\s+\S+\s+started|wait for\s+\S+|"
    r"(?:director|challenger|investigator|literature|judge|reporter|defense)"
    r"(?:\s+is\s+running|\s+completed))$"
)


def is_runtime_current_state(text: Any) -> bool:
    value = " ".join(str(text or "").split())
    if not value:
        return True
    if _RUNTIME_CURRENT_STATE.match(value):
        return True
    low = value.lower()
    return "codex process" in low or "stderr_tail" in low or "traceback" in low


def direction_text(title: Any = "", hypothesis: Any = "", *, limit: int | None = None) -> str:
    heading = " ".join(str(title or "").split())
    body = " ".join(str(hypothesis or "").split())
    if body and limit is not None and len(body) > limit:
        body = body[: limit - 1] + "…"
    if heading and body and body != heading:
        return f"{heading}\n\n{body}"
    return heading or body


def sticky_direction(prior: dict[str, Any] | None) -> str:
    prior = prior or {}
    current = str(prior.get("current_state") or "").strip()
    if current and not is_runtime_current_state(current) and not re.match(
            r"(?i)^(supported|rejected|milestone_advanced|open)\s*:", current):
        return current
    branches = prior.get("branches") or []
    if branches:
        last = branches[-1] if isinstance(branches[-1], dict) else {}
        text = direction_text(last.get("title", ""), last.get("hypothesis", ""))
        if text:
            return text
    return ""
