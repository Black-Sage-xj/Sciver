"""Read-only scientific semantic projection of Canvas text node headers.

Never infers scientific authority from layout, color, markdown references or status
of a descendant. Missing/legacy fields remain unknown, not silently reconstructed.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

SCIENTIFIC_TYPES = frozenset({"Hypothesis", "Decision", "Finding", "StageSummary", "External"})
FRONTIER_STATES = frozenset({"active", "parked", "closed"})
HEADER_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9_]*):\s*(.*?)\s*$")
CYCLE_DECISION_ID = re.compile(r"^(?:historical-c)?(\d+)(?:-decision)(?:$|-)", re.I)
LEGACY_HYPOTHESIS_CYCLE_ID = re.compile(r"^([1-9][0-9]*)-hypothesis$", re.I)
AUDIT_HYPOTHESIS_CYCLE_ID = re.compile(r"^audit-hyp-c0*([1-9][0-9]*)(?:-|$)", re.I)


def _header(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    # Metadata lives in a small plain-text preamble, not arbitrary body prose.
    for line in text.splitlines()[:32]:
        if not line.strip():
            if fields:
                break
            continue
        found = HEADER_FIELD.fullmatch(line.strip())
        if not found:
            break
        key, value = found.groups()
        if key not in fields:
            fields[key] = value
    return fields


def project_node(raw: dict[str, Any]) -> dict[str, Any]:
    text = str(raw.get("text") or "")
    header = _header(text)
    science_type = header.get("type") if header.get("type") in SCIENTIFIC_TYPES else "Unknown"
    frontier = header.get("frontier_status") if header.get("frontier_status") in FRONTIER_STATES else None
    # An External record is never an official Judge/Hypothesis branch.
    if science_type == "External":
        frontier = None
    status = header.get("scientific_status") if science_type == "Hypothesis" else None
    verdict = header.get("verdict") if science_type == "Decision" else None
    timestamp = header.get("timestamp")
    sortable_time = None
    if timestamp:
        try:
            parsed = datetime.fromisoformat(timestamp)
            if parsed.year >= 2000 and re.match(r"^\d{4}-\d{2}-\d{2}", timestamp):
                sortable_time = parsed.isoformat()
        except ValueError:
            pass
    identifier = str(raw.get("id") or "")
    # Prefer explicit Cycle metadata when present; never guess a Cycle from
    # arbitrary H/External IDs, timestamps, display order or body mentions.
    explicit_cycle = (header.get("created_cycle") if science_type == "Hypothesis" else None)
    explicit_cycle = explicit_cycle or header.get("cycle_id") or header.get("cycle")
    numeric_cycle = re.fullmatch(r"(?:cycle-)?([0-9]+)", explicit_cycle or "")
    match = CYCLE_DECISION_ID.match(identifier) if science_type == "Decision" else None
    legacy_h = LEGACY_HYPOTHESIS_CYCLE_ID.fullmatch(identifier) if science_type == "Hypothesis" else None
    audit_h = AUDIT_HYPOTHESIS_CYCLE_ID.match(identifier) if science_type == "Hypothesis" else None
    cycle = (int(numeric_cycle.group(1)) if numeric_cycle
             else int(match.group(1)) if match
             else int(legacy_h.group(1)) if legacy_h
             else int(audit_h.group(1)) if audit_h else None)
    # Stable display copy: no LLM paraphrasing or fictitious conclusions.
    heading = next(
        (line.lstrip("#").strip() for line in text.splitlines()
         if line.startswith("# ") and line[2:].strip()), None
    )
    label = heading or identifier
    return {
        "type": science_type, "scientific_status": status, "frontier_status": frontier,
        "verdict": verdict, "legacy_status": header.get("status"),
        "timestamp": timestamp, "sortable_time": sortable_time,
        "cycle": cycle, "title": label, "source": header.get("source"),
        "content_type": header.get("content_type"),
    }
