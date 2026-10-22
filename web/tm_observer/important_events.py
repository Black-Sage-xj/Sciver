"""Read-only bounded projection of Task-level and per-Cycle important events.

The registry's JSONL files are the authority. This adapter never writes them,
nor imports the scientific runtime into the Web server.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .run_monitor import CYCLE_NAME, _safe_cycle, redact

FILENAMES = ("cycle_anomalies.jsonl", "important_events.jsonl")
MAX_FILE_BYTES = 2_000_000
MAX_ROWS = 1500
MAX_LINE_CHARS = 16000
MAX_CYCLE_DIRS = 512


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "[nested data]"
    if isinstance(value, str):
        return redact(value[:5000])
    if isinstance(value, dict):
        return {str(k)[:100]: ("[redacted]" if re.search(
                    r"(api[_-]?key|password|secret|credential|authorization|access_token)",
                    str(k), re.I) else _scrub(v, depth + 1))
                for k, v in list(value.items())[:75]}
    if isinstance(value, list):
        return [_scrub(v, depth + 1) for v in value[:75]]
    return value if isinstance(value, (int, float, bool)) or value is None else str(value)[:150]


def _clock(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _read_file(path: Path, *, cycle: str | None) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        return []
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    result: list[dict[str, Any]] = []
    for raw in lines:
        if not raw.strip() or len(raw) > MAX_LINE_CHARS:
            continue
        try:
            row = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict) or not row.get("at"):
            continue
        event = _scrub(row)
        event["_display_cycle"] = cycle or "任务级"
        event["_cycle_name"] = path.parent.name if cycle is not None else ""
        result.append(event)
    return result


def read_important_events(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Chronological lifecycle rows, including legacy and Task root events.

    Validates against the known Observer-discovered research root. Skips symlinks
    and oversized files; never follows a path given by ledger contents.
    """
    active_cycle = _safe_cycle(run)
    if active_cycle is None:
        return []
    root = active_cycle.parent
    try:
        cycles = [path for path in root.iterdir()
                  if path.is_dir() and not path.is_symlink()
                  and CYCLE_NAME.fullmatch(path.name)]
    except OSError:
        return []
    cycles.sort(key=lambda path: int(path.name.removeprefix("cycle-")))
    sources: list[tuple[Path, str | None]] = [(root, None)]
    sources.extend((cycle, cycle.name.removeprefix("cycle-"))
                   for cycle in cycles[-MAX_CYCLE_DIRS:])
    events: list[dict[str, Any]] = []
    for directory, cycle_id in sources:
        for name in FILENAMES:
            events.extend(_read_file(directory / name, cycle=cycle_id))
    # Python's stable sorting preserves the order of events with the same
    # timestamp. Never deduplicate lifecycle events or invent resolution.
    events.sort(key=lambda row: _clock(row.get("at")))
    return events[-MAX_ROWS:]

# One immutable ledger, three different read-models. Scientific milestones
# belong in Important Events; operational failures belong in the incident
# archive; only present actionable stops become notifications.
def is_milestone(row: dict[str, Any]) -> bool:
    return str(row.get("severity") or "").lower() == "important" and str(
        row.get("kind") or ""
    ) not in {"system_failure", "needs_attention"}


def incident_cases(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group lifecycle rows for display, never alter or discard source rows.

    Incident id is scoped by Cycle. Legacy rows without an id are grouped by
    Cycle/kind/code/summary, not by timestamp or lifecycle; the raw history is
    always accessible from the resulting case.
    """
    cases: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in events:
        if not isinstance(row, dict) or is_milestone(row):
            continue
        cycle = str(row.get("_display_cycle") or "任务级")
        incident_id = str(row.get("incident_id") or "")
        key = ((cycle, "id", incident_id) if incident_id else
               (cycle, "legacy", str(row.get("kind") or ""),
                str(row.get("code") or ""), str(row.get("summary") or "")))
        if key not in cases:
            cases[key] = {
                "_display_cycle": cycle,
                "_cycle_name": str(row.get("_cycle_name") or ""),
                "incident_id": incident_id,
                "kind": str(row.get("kind") or ""),
                "code": str(row.get("code") or ""),
                "severity": str(row.get("severity") or "minor"),
                "summary": str(row.get("summary") or ""),
                "first_at": str(row.get("at") or ""),
                "last_at": str(row.get("at") or ""),
                "lifecycle": str(row.get("lifecycle") or "registered"),
                "records": [],
            }
        case = cases[key]
        case["records"].append(row)
        # Registration may be duplicated across old/new ledgers; retain both
        # verbatim for audit, but do not count a resume as a new failure.
        case["last_at"] = str(row.get("at") or "")
        case["lifecycle"] = str(row.get("lifecycle") or "registered")
    return sorted(cases.values(), key=lambda case: _clock(case["last_at"]))
