"""Task-wide, read-only Cycle index and selected historical progress.

Only the chosen Cycle's snapshot is read on each page refresh. Directory
listing covers *all* task Cycles, not just the current invocation or the
bounded action_history in the live TUI projection.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


_CYCLE_DIR = re.compile(r"(?:cycle-)?(\d{1,8})\Z")
_CLOCK = re.compile(r"\d{4}-\d{2}-\d{2}[T ](\d{2}:\d{2}:\d{2})")
_ROLE_LABELS = {
    "research_lead": "Research Lead", "challenger": "Challenger",
    "investigator": "Investigator", "literature": "Literature",
    "judge": "Judge", "reporter": "Reporter",
}


def cycle_catalog(root: Path) -> list[dict[str, str]]:
    """Enumerate every persisted Cycle directory with stable numeric identity.

    Numeric paths take precedence over legacy cycle-0001 duplicates.
    No snapshot or event log is opened while listing.
    """
    found: dict[int, Path] = {}
    try:
        for child in Path(root).iterdir():
            match = _CYCLE_DIR.fullmatch(child.name)
            if not match or not child.is_dir() or child.is_symlink():
                continue
            number = int(match.group(1))
            if number not in found or (child.name.isdecimal() and not found[number].name.isdecimal()):
                found[number] = child
    except OSError:
        return []
    return [{"id": str(number), "dir": found[number].name} for number in sorted(found)]


def selected_cycle(catalog: list[dict[str, str]], requested: str = "") -> str:
    if not catalog:
        return ""
    chosen = str(int(requested)) if requested.isascii() and requested.isdecimal() and len(requested) <= 8 else ""
    return chosen if any(item["id"] == chosen for item in catalog) else catalog[-1]["id"]


def cycle_summary_page(root: Path, before: str, *, limit: int = 10) -> dict[str, Any]:
    """Read-only, bounded page of older Cycles from the task-owned catalog."""
    if not before.isascii() or not before.isdecimal() or not 0 < len(before) <= 8:
        raise ValueError("before must be a numeric Cycle identifier")
    catalog = cycle_catalog(root)
    earlier = [entry for entry in catalog if int(entry["id"]) < int(before)]
    page = earlier[-min(max(limit, 1), 10):]
    rows = [{"id": item["id"], **cycle_summary(root, catalog, item["id"])} for item in page]
    return {"cycles": rows, "has_more": len(earlier) > len(page), "total": len(catalog)}


@lru_cache(maxsize=512)
def _cached_cycle_summary(path_text: str, mtime_ns: int, size: int,
                          selected: str) -> tuple[str, str]:
    """Read only the compact persisted Cycle start and final Judge verdict."""
    path = Path(path_text)
    try:
        if path.is_symlink() or not path.is_file() or size > 4_000_000:
            return "", ""
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return "", ""
    if not isinstance(record, dict):
        return "", ""
    actions = record.get("action_history")
    actions = actions if isinstance(actions, list) else []
    starts: list[str] = []
    start_pattern = re.compile(
        r"Cycle started\s*[·:：-]\s*(?:cycle-)?0*" + re.escape(selected), re.I
    )
    for action in actions:
        if not isinstance(action, dict) or action.get("event") != "CYCLE_STARTED":
            continue
        at = str(action.get("at") or "")
        if _CLOCK.match(at) and start_pattern.fullmatch(str(action.get("text") or "")):
            starts.append(at)
    cutoff = min(starts) if starts else ""
    # A cumulative snapshot without a matching start cannot safely attribute a
    # verdict to this Cycle. Do not display a neighboring Cycle's decision.
    if not cutoff and any(isinstance(action, dict) and action.get("event") == "CYCLE_STARTED"
                          for action in actions):
        return "", ""
    disposition_rows: list[tuple[str, int, str]] = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict) or action.get("event") not in {"JUDGE_VERDICT", "JUDGE_REWORK_REQUESTED"}:
            continue
        at = str(action.get("at") or "")
        if not _CLOCK.match(at) or (cutoff and at < cutoff):
            continue
        explicit = action.get("cycle_id")
        if explicit is not None and str(explicit).removeprefix("cycle-").lstrip("0") != selected.lstrip("0"):
            continue
        if action.get("event") == "JUDGE_REWORK_REQUESTED":
            # A later rework request means an earlier verdict is no longer the
            # final disposition for this Cycle; wait for a later JUDGE_VERDICT.
            disposition_rows.append((at, index, ""))
            continue
        verdict = str(action.get("verdict") or action.get("scientific_verdict") or "").strip()
        if not verdict:
            text = str(action.get("text") or "")
            match = re.match(r"Judge verdict:\s*(.+)", text, re.I)
            verdict = match.group(1).strip() if match else ""
        if verdict:
            disposition_rows.append((at, index, verdict))
    latest = max(disposition_rows, key=lambda row: (row[0], row[1]), default=None)
    start_clock = _CLOCK.match(cutoff).group(1) if cutoff else ""
    return start_clock, latest[2] if latest and latest[2] else ""


def _completed_scientific_verdict(folder: Path, selected: str) -> str | None:
    """Only this Cycle's terminal scientific state can label its closed H."""
    path = folder / "research_state.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_000_000:
            return None
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(state, dict):
        return None
    cycle_id = str(state.get("cycle_id") or "")
    if not cycle_id.removeprefix("cycle-").isdecimal() or int(cycle_id.removeprefix("cycle-")) != int(selected):
        return None
    if str(state.get("status") or "").lower() not in {"over", "completed"}:
        return None
    verdict = str(state.get("scientific_verdict") or "").strip().lower()
    if state.get("assessment_state") == "terminal" and verdict in {"supported", "rejected", "open"}:
        return verdict
    # Operationally completed without a scientific Judge: never invent Open.
    return "unadjudicated"


def cycle_summary(root: Path, catalog: list[dict[str, str]], selected: str) -> dict[str, str]:
    """Return a trusted start clock and this Cycle's terminal scientific verdict."""
    item = next((entry for entry in catalog if entry["id"] == selected), None)
    if item is None:
        return {"time": "", "verdict": ""}
    path = Path(root) / item["dir"] / "run_state.json"
    try:
        if path.is_symlink() or not path.is_file():
            return {"time": "", "verdict": ""}
        stat = path.stat()
    except OSError:
        return {"time": "", "verdict": ""}
    if stat.st_size > 4_000_000:
        return {"time": "", "verdict": ""}
    start_clock, legacy_verdict = _cached_cycle_summary(str(path), stat.st_mtime_ns, stat.st_size, selected)
    scientific = _completed_scientific_verdict(path.parent, selected)
    return {"time": start_clock, "verdict": scientific if scientific is not None else legacy_verdict}


def _role_events(folder: Path, selected: str, cutoff: str) -> list[tuple[str, str, str, str]]:
    """Read only this Cycle's event ledger; keep role/stage/verdict scalars, not science prose."""
    path = folder / "events.jsonl"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_000_000:
            return []
        from theory_master.observability.collector_projection import CollectorProjectionMixin
        rows = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if len(line) > 2_000_000:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(item, dict) or item.get("type") not in {"role.started", "role.completed"}:
                    continue
                at = str(item.get("at") or "")
                payload = item.get("payload")
                if not isinstance(payload, dict) or not _CLOCK.match(at) or (cutoff and at < cutoff):
                    continue
                role = str(payload.get("role") or "")
                if role not in _ROLE_LABELS:
                    continue
                completed = item["type"] == "role.completed"
                event = {"event": "JUDGE_COMPLETED" if completed and role == "judge" else
                         "AGENT_COMPLETED" if completed else "AGENT_STARTED",
                         "agent": role, "stage": str(payload.get("stage") or ""),
                         "output": {"verdict": (payload.get("output") or {}).get("verdict"),
                                    "assessment_state": (payload.get("output") or {}).get("assessment_state")}
                         if isinstance(payload.get("output"), dict) else {}}
                message = CollectorProjectionMixin._action_text(event)
                if message:
                    rows.append((at, role, event["event"], message))
        return rows
    except (OSError, UnicodeError):
        return []


def cycle_events(root: Path, catalog: list[dict[str, str]], selected: str) -> tuple[str, list[dict[str, str]]]:
    """Load only one checked Cycle snapshot; never infer older events from live state."""
    item = next((entry for entry in catalog if entry["id"] == selected), None)
    if item is None:
        return "", []
    path = Path(root) / item["dir"] / "run_state.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_000_000:
            return "", []
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return "", []
    if not isinstance(record, dict):
        return "", []
    collected: list[tuple[str, int, str, dict[str, str]]] = []
    actions = record.get("action_history")
    actions = actions if isinstance(actions, list) else []
    starts = [str(row.get("at") or "") for row in actions
              if isinstance(row, dict) and row.get("event") == "CYCLE_STARTED"
              and re.fullmatch(r"Cycle started\s*[·:：-]\s*(?:cycle-)?0*" + re.escape(selected),
                               str(row.get("text") or ""), re.I)
              and _CLOCK.match(str(row.get("at") or ""))]
    cutoff = min(starts) if starts else ""
    if not cutoff and any(isinstance(row, dict) and row.get("event") == "CYCLE_STARTED"
                          for row in actions):
        return "", []
    role_rows = _role_events(path.parent, selected, cutoff)
    # When the native event ledger exists it owns true invocation start/end
    # timestamps; the timeline's 'done' at start is only a legacy projection.
    native_roles = {row[1] for row in role_rows}
    for action in actions:
        if not isinstance(action, dict):
            continue
        if (action.get("event") in {"AGENT_COMPLETED", "JUDGE_COMPLETED"}
                and str(action.get("role") or "") in native_roles):
            continue
        at = str(action.get("at") or "")
        match = _CLOCK.match(at)
        if not match or (cutoff and at < cutoff):
            continue
        # A per-Cycle snapshot is the source of attribution. Honor an explicit
        # differing Cycle ID rather than painting that row into this Cycle.
        explicit = action.get("cycle_id")
        if explicit is not None and str(explicit).removeprefix("cycle-").lstrip("0") != selected.lstrip("0"):
            continue
        message = str(action.get("text") or action.get("event") or "-").replace("\n", " ").strip()
        meta = {"at": at[:64], "kind": str(action.get("event") or "")[:96],
                "role": str(action.get("role") or "")[:48], "hint": message[:240],
                "cycle_id": selected}
        collected.append((at, len(collected), message, meta))
    timeline = record.get("agent_timeline")
    for agent in timeline if isinstance(timeline, list) else []:
        if not isinstance(agent, dict) or str(agent.get("role") or "") in native_roles:
            continue
        at = str(agent.get("started_at") or "")
        if not _CLOCK.match(at) or (cutoff and at < cutoff):
            continue
        role = str(agent.get("role") or "")
        label = _ROLE_LABELS.get(role, role or "Agent")
        message = f"{label} · {'running' if agent.get('active') else 'done'}"
        handoff = str(agent.get("handoff_to") or "")
        if handoff:
            message += " → " + _ROLE_LABELS.get(handoff, handoff)
        meta = {"at": at[:64], "kind": "AGENT_STARTED", "role": role[:48],
                "hint": message[:240], "cycle_id": selected}
        collected.append((at, len(collected), message, meta))
    for at, role, kind, message in role_rows:
        meta = {"at": at[:64], "kind": kind, "role": role[:48],
                "hint": message[:240], "cycle_id": selected}
        collected.append((at, len(collected), message, meta))
    collected.sort(key=lambda row: (row[0], row[1]))
    source = [row[3] for row in collected]
    lines = [f"{_CLOCK.match(row[0]).group(1)}  {row[2]}" for row in collected]
    return "\n".join(lines), source
