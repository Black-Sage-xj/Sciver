"""Shared deterministic runtime helpers for TheoryMaster.

This module deliberately has no dependencies on workflow, backend, or scientific
model code.  Keeping file persistence and prompt bounding here prevents the
domain modules from importing the compatibility facade in :mod:`core`.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTEXT_VALUE_MAX_CHARS = 2000
CONTEXT_MAX_SEQUENCE = 24
CONTEXT_MAX_MAPPING = 32
CONTEXT_MAX_DEPTH = 8
CLIPPED_MARK = "…[clipped]"
PROTECTED_CONTEXT_KEYS = frozenset({"goal", "research_contract", "final_gate", "required_context", "investigation_result"})


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def compact_ts(iso: str | None = None) -> str:
    """Operator-facing stamp: ``09-20T22:07:35``."""
    text = now() if iso is None else str(iso)
    if not text:
        return ""
    if "T" not in text:
        return text
    date, _, rest = text.partition("T")
    mmdd = date[5:10] if len(date) >= 10 else date
    return f"{mmdd}T{rest[:8]}"


def canvas_ts(iso: str | None = None) -> str:
    """Canvas semantic timestamp: ``MM-DD-HH:MM`` in local time."""
    text = now() if iso is None else str(iso)
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        value = datetime.now(timezone.utc).astimezone()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc).astimezone()
    else:
        value = value.astimezone()
    return value.strftime("%m-%d-%H:%M")


def atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value: Any) -> None:
    atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def bound_json_value(
    value: Any,
    *,
    max_chars: int = CONTEXT_VALUE_MAX_CHARS,
    max_sequence: int = CONTEXT_MAX_SEQUENCE,
    max_mapping: int = CONTEXT_MAX_MAPPING,
    max_depth: int = CONTEXT_MAX_DEPTH,
    depth: int = 0,
) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        return [
            bound_json_value(item, max_chars=max_chars, max_sequence=max_sequence, max_mapping=max_mapping,
                             max_depth=max_depth, depth=depth + 1)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            str(key): bound_json_value(value[key], max_chars=max_chars, max_sequence=max_sequence,
                                       max_mapping=max_mapping, max_depth=max_depth, depth=depth + 1)
            for key in value
        }
    return str(value)


def bound_prompt_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value if key in PROTECTED_CONTEXT_KEYS else bound_json_value(value)
        for key, value in context.items()
    }
