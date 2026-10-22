"""Historical Frontier State codec retained only for read-only migration and audit.

Frontier is SDK workflow state, not Research Plan content. V2 is read only as a
bounded compatibility input; all normalized state and writes use v3.

Research Plan and Canvas are the only cross-Cycle scientific authorities. This
file may hold machine pointers and, while a Cycle is open, that Cycle's
candidate runtime for recovery. After the Cycle closes, the next Cycle must
not read scientific_synthesis, established, unresolved, boundary,
remaining_goal_gap, or a Hypothesis science catalog from this file.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .runtime import atomic

FRONTIER_SCHEMA = "theory-master/frontier-state/v3"
LEGACY_FRONTIER_SCHEMA = "theory-master/frontier-state/v2"
FRONTIER_FILENAME = "frontier_state.json"
POINTER_CONTINUATION_KEYS = ("source_cycle", "hypothesis_ref")
# Persisted continuation fields that would become a third scientific picture.
CROSS_CYCLE_SCIENCE_KEYS = frozenset({
    "scientific_synthesis",
    "established",
    "unresolved",
    "boundary",
    "remaining_goal_gap",
    "remaining_gap",
    "cycle_scientific_state",
    "judge_rationale",
    "judge_verdict",
    "scientific_model_version",
})


def state_path(root: Path) -> Path:
    return Path(root).expanduser().resolve() / FRONTIER_FILENAME


def empty_frontier(goal_id: str) -> dict[str, Any]:
    return {
        "schema": FRONTIER_SCHEMA,
        "goal_id": goal_id,
        "updated_from_cycle": None,
        "selected_candidate_id": None,
        "selected_candidate": None,
        "candidates": [],
        "next_start_node_id": None,
        "continuation_anchor_decision_id": None,
        "continuation_context": None,
        "hypotheses": [],
    }


def validate_frontier(frontier: dict[str, Any], *, goal_id: str) -> None:
    if not isinstance(frontier, dict):
        raise ValueError("Frontier State must be an object")
    if frontier.get("schema") != FRONTIER_SCHEMA:
        raise ValueError("invalid Frontier State schema; expected current v3 or migratable v2")
    if frontier.get("goal_id") != goal_id:
        raise ValueError("Frontier State goal_id does not match task config")
    if frontier.get("updated_from_cycle") is not None and not isinstance(frontier.get("updated_from_cycle"), str):
        raise ValueError("Frontier State updated_from_cycle must be a string or null")
    if frontier.get("selected_candidate_id") is not None and not isinstance(frontier.get("selected_candidate_id"), str):
        raise ValueError("Frontier State selected_candidate_id must be a string or null")
    if frontier.get("selected_candidate") is not None and not isinstance(frontier.get("selected_candidate"), dict):
        raise ValueError("Frontier State selected_candidate must be an object or null")
    if not isinstance(frontier.get("candidates"), list) or any(not isinstance(item, dict) for item in frontier["candidates"]):
        raise ValueError("Frontier State candidates must be a list of objects")
    if frontier.get("next_start_node_id") is not None and not isinstance(frontier.get("next_start_node_id"), str):
        raise ValueError("Frontier State next_start_node_id must be a string or null")
    if frontier.get("continuation_anchor_decision_id") is not None and not isinstance(frontier.get("continuation_anchor_decision_id"), str):
        raise ValueError("Frontier State continuation_anchor_decision_id must be a string or null")
    continuation = frontier.get("continuation_context")
    if continuation is not None and not isinstance(continuation, dict):
        raise ValueError("Frontier State continuation_context must be an object or null")
    if isinstance(continuation, dict):
        forbidden = CROSS_CYCLE_SCIENCE_KEYS & set(continuation)
        if forbidden:
            raise ValueError(
                "Frontier State continuation_context is not a cross-Cycle scientific authority: "
                + ", ".join(sorted(forbidden))
            )
    hypotheses = frontier.get("hypotheses") or []
    if not isinstance(hypotheses, list) or any(not isinstance(item, dict) for item in hypotheses):
        raise ValueError("Frontier State hypotheses must be a list of objects")
    seen: set[str] = set()
    for item in hypotheses:
        hypothesis_ref = str(item.get("id") or "")
        if not re.fullmatch(r"H-\d{4,}", hypothesis_ref):
            raise ValueError("Frontier State Hypothesis id must use H-xxxx identity")
        if hypothesis_ref in seen:
            raise ValueError(f"duplicate Frontier State Hypothesis id: {hypothesis_ref}")
        seen.add(hypothesis_ref)
        if set(item) != {"id", "hypothesis", "scope"}:
            raise ValueError(f"Frontier State {hypothesis_ref} must contain only id, hypothesis, and scope")
        if not isinstance(item.get("hypothesis"), str) or not item.get("hypothesis", "").strip():
            raise ValueError(f"Frontier State {hypothesis_ref} requires hypothesis text")
        if not isinstance(item.get("scope"), str):
            raise ValueError(f"Frontier State {hypothesis_ref} scope must be a string")
    selected_id = frontier.get("selected_candidate_id")
    selected = frontier.get("selected_candidate")
    if selected_id is not None and (selected is None or selected.get("id") != selected_id):
        raise ValueError("Frontier State selected candidate does not match selected_candidate_id")


def load(root: Path, *, goal_id: str) -> dict[str, Any]:
    path = state_path(root)
    if not path.is_file():
        raise ValueError(f"current Frontier State is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    value = _migrate_frontier(value)
    validate_frontier(value, goal_id=goal_id)
    return value


def _candidate_without_legacy_identity(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    candidate = dict(value)
    candidate.pop("hypothesis_id", None)
    candidate.pop("parent_hypothesis_ids", None)
    if candidate.get("evolution_operator") == "repair":
        candidate.pop("evolution_operator", None)
    return candidate


def _migrate_frontier(value: Any) -> dict[str, Any]:
    """Read v2 identity aliases and return the canonical v3 frontier shape."""
    if not isinstance(value, dict):
        raise ValueError("Frontier State must be an object")
    schema = value.get("schema")
    if schema not in {LEGACY_FRONTIER_SCHEMA, FRONTIER_SCHEMA}:
        raise ValueError("invalid Frontier State schema; expected current v3 or migratable v2")
    migrated = dict(value)
    migrated["schema"] = FRONTIER_SCHEMA
    rows = migrated.get("hypotheses") if isinstance(migrated.get("hypotheses"), list) else []
    migrated["hypotheses"] = [
        {
            "id": str(row.get("id") or row.get("hypothesis_id") or "").strip(),
            "hypothesis": str(row.get("hypothesis") or "").strip(),
            "scope": str(row.get("scope") or ""),
        }
        for row in rows if isinstance(row, dict)
    ]
    candidates = migrated.get("candidates") if isinstance(migrated.get("candidates"), list) else []
    migrated["candidates"] = [_candidate_without_legacy_identity(item) for item in candidates]
    selected = migrated.get("selected_candidate")
    if isinstance(selected, dict):
        migrated["selected_candidate"] = _candidate_without_legacy_identity(selected)
    continuation = migrated.get("continuation_context")
    if isinstance(continuation, dict):
        continuation = dict(continuation)
        if "hypothesis_ref" not in continuation and continuation.get("hypothesis_id"):
            continuation["hypothesis_ref"] = continuation.get("hypothesis_id")
        continuation.pop("hypothesis_id", None)
        for key in CROSS_CYCLE_SCIENCE_KEYS:
            continuation.pop(key, None)
        migrated["continuation_context"] = pointer_continuation(continuation)
    return migrated


def pointer_continuation(value: Any) -> dict[str, Any] | None:
    """Keep only Cycle/Canvas pointers. Drop scientific handoff fields."""
    if not isinstance(value, dict):
        return None
    kept = {
        key: value.get(key)
        for key in POINTER_CONTINUATION_KEYS
        if value.get(key) not in (None, "")
    }
    return kept or None


def cross_cycle_frontier(frontier: dict[str, Any]) -> dict[str, Any]:
    """Seal Frontier State down to machine pointers.

    Open-Cycle candidate runtime may exist earlier in the same Cycle. The next
    Cycle's scientific input is Research Plan plus Canvas, not this file.
    """
    source = frontier if isinstance(frontier, dict) else {}
    next_start = source.get("next_start_node_id")
    anchor = source.get("continuation_anchor_decision_id")
    updated = source.get("updated_from_cycle")
    return {
        "schema": FRONTIER_SCHEMA,
        "goal_id": source.get("goal_id"),
        "updated_from_cycle": updated if isinstance(updated, str) else None,
        "selected_candidate_id": None,
        "selected_candidate": None,
        "candidates": [],
        "next_start_node_id": next_start if isinstance(next_start, str) else None,
        "continuation_anchor_decision_id": anchor if isinstance(anchor, str) else None,
        "continuation_context": pointer_continuation(source.get("continuation_context")),
        "hypotheses": [],
    }


def save(root: Path, frontier: dict[str, Any], *, goal_id: str) -> None:
    frontier = dict(frontier)
    if isinstance(frontier.get("continuation_context"), dict):
        continuation = dict(frontier["continuation_context"])
        for key in CROSS_CYCLE_SCIENCE_KEYS:
            continuation.pop(key, None)
        frontier["continuation_context"] = pointer_continuation(continuation)
    validate_frontier(frontier, goal_id=goal_id)
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic(path, json.dumps(frontier, ensure_ascii=False, indent=2) + "\n")


def initialize(root: Path, *, goal_id: str) -> dict[str, Any]:
    path = state_path(root)
    if path.is_file():
        return load(root, goal_id=goal_id)
    frontier = empty_frontier(goal_id)
    save(root, frontier, goal_id=goal_id)
    return frontier
