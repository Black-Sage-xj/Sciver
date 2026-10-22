"""Canonical, read-only Web navigation context (never an execution authority).

A task identifier is an Observer ID, not a Master ID/Cycle or user path.
The incident source is resolved server-side; untrusted return URLs are not used.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

_TASK_ID = re.compile(r"[0-9a-f]{16}\Z")


def task_console_url(observer_id: str) -> str | None:
    ident = str(observer_id or "")
    return f"/run/{ident}/" if _TASK_ID.fullmatch(ident) else None


def task_explorer_url(observer_id: str) -> str | None:
    if task_console_url(observer_id) is None:
        return None
    return "/research-explorer/?" + urlencode({"observer_id": observer_id})


def task_supervisor_url(observer_id: str) -> str | None:
    base = task_console_url(observer_id)
    return base + "supervisor" if base else None


def incident_owner_id(runs: list[dict[str, Any]], research_root: Path) -> str | None:
    """Find the verified task owning an incident from its server-resolved root."""
    target = research_root.resolve()
    for run in runs:
        ident = str(run.get("observer_id") or "")
        raw = run.get("research_root") or run.get("task_root")
        if task_console_url(ident) and raw and Path(str(raw)).resolve() == target:
            return ident
    return None
