"""Bounded, read-only cycle provenance for the Web recent-progress timeline.

The CLI snapshot carries action_history and agent_timeline forward between Cycles
without a per-entry Cycle ID. Scientific events are nevertheless stored in
per-Cycle runtime_events.jsonl. Only unambiguous exact event identities from
those original files are attached to Web presentation; otherwise the renderer
falls back to its historical textual grouping.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def cycle_provenance(research_root: Path, current_cycle: Any) -> dict[tuple[str, str, str], dict[str, str]]:
    """Return trusted event identities (timestamp, event kind, role) with source Cycle.

    Limit IO to 12 recent existing Cycle directories and the last 2 MiB of each
    event stream; never create files or mutate any scientific observation.
    """
    root = Path(research_root)
    if not root.is_dir():
        return {}
    dirs = []
    try:
        for child in root.iterdir():
            if child.is_dir() and not child.is_symlink() and re.fullmatch(r"\d{1,8}", child.name):
                dirs.append(child)
    except OSError:
        return {}
    # Read recent histories, plus current cycle even when task directory order
    # differs; earlier incomplete logs simply have no verified provenance.
    latest = sorted(dirs, key=lambda x: int(x.name))[-12:]
    indexed: dict[tuple[str, str, str], dict[str, str] | None] = {}
    for folder in latest:
        path = folder / "runtime_events.jsonl"
        if not path.is_file() or path.is_symlink():
            continue
        starts: dict[str, tuple[str, str, str]] = {}
        completions: dict[str, str] = {}
        raw: list[dict[str, Any]] = []
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                total = handle.tell()
                handle.seek(max(0, total - 2_097_152))
                payload = handle.read(2_097_152)
            lines = payload.decode("utf-8", errors="replace").splitlines()
            if total > 2_097_152:
                lines = lines[1:]
            for line in lines:
                try:
                    event = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                kind = str(event.get("event") or "")
                if kind not in {"CYCLE_STARTED", "SKILL_LOADED", "JUDGE_VERDICT",
                                "JUDGE_REWORK_REQUESTED", "AGENT_STARTED",
                                "AGENT_COMPLETED", "RUN_FAILED", "RUN_PAUSED",
                                "RUN_INTERRUPTED", "WORKFLOW_STOPPED"}:
                    continue
                at = str(event.get("ts") or "")
                role = str(event.get("role") or event.get("agent") or "").lower()
                if not at:
                    continue
                raw.append(event)
                invocation = str(event.get("invocation_id") or "")
                if invocation and kind == "AGENT_STARTED":
                    starts[invocation] = (at, kind, role)
                if invocation and kind == "AGENT_COMPLETED":
                    completions[invocation] = at
        except (OSError, UnicodeError):
            continue
        for event in raw:
            kind = str(event["event"])
            if kind == "AGENT_COMPLETED":
                continue
            at = str(event["ts"])
            role = str(event.get("role") or event.get("agent") or "").lower()
            key = (at, kind, role)
            detail = {"cycle_id": str(int(folder.name))}
            if kind == "AGENT_STARTED":
                stage = event.get("stage")
                if isinstance(stage, str):
                    detail["stage"] = stage[:90]
                invocation = str(event.get("invocation_id") or "")
                if invocation in completions:
                    detail["completed_at"] = completions[invocation]
            if key in indexed:
                # Multiple original events with an identical public signature
                # must never be attributed to a guessed Cycle or Agent.
                indexed[key] = None
            else:
                indexed[key] = detail
    return {key: value for key, value in indexed.items() if value is not None}


def enrich_recent_events(recent: list[dict[str, str]], provenance: dict[
                         tuple[str, str, str], dict[str, str]]) -> list[dict[str, str]]:
    """Attach only verified Cycle/stage/end-time metadata; keep links unchanged."""
    enriched = []
    for entry in recent:
        key = (str(entry.get("at") or ""), str(entry.get("kind") or ""),
               str(entry.get("role") or "").lower())
        matched = provenance.get(key)
        enriched.append({**entry, **matched} if matched else entry)
    return enriched
