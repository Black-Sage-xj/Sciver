from __future__ import annotations

"""Read-only cross-Cycle adjudication projection for one stable Hypothesis."""

import json
from pathlib import Path
from typing import Any

from .specification import list_compatible_cycle_dirs


HISTORY_SCHEMA = "theory-master/hypothesis-adjudication-history/v2"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _scientific_synthesis_result(value: Any, fallback: str = "") -> list[str]:
    """Return only compact scientific conclusions, never workflow/frontier data."""
    if not isinstance(value, dict):
        return [fallback] if fallback else []
    result: list[str] = []
    keys = ("conclusions",) if isinstance(value.get("conclusions"), list) else (
        "learned", "strengthened", "invalidated", "resolved_questions",
    )
    for key in keys:
        rows = value.get(key)
        rows = rows if isinstance(rows, list) else []
        for row in rows:
            if isinstance(row, dict):
                text = str(
                    row.get("statement")
                    or row.get("summary")
                    or row.get("result")
                    or row.get("question")
                    or ""
                ).strip()
            else:
                text = str(row or "").strip()
            if text and text not in result:
                result.append(text)
    if not result and fallback:
        result.append(fallback)
    return result[:12]


def _compact_evidence(
    manifest: dict[str, Any],
    *,
    hypothesis_ref: str,
    cycle_id: str,
) -> list[dict[str, Any]]:
    """Project persisted adjudicable Evidence without raw/artifact bookkeeping."""
    records = manifest.get("records") if isinstance(manifest.get("records"), list) else []
    compact: list[dict[str, Any]] = []
    for raw in records:
        if not isinstance(raw, dict) or raw.get("adjudicable") is not True:
            continue
        # New records bind these IDs durably. For a pre-field compatibility row,
        # the containing Cycle's locked H/cycle are the deterministic fallback;
        # no prose or semantic matching is used.
        record_hypothesis_ref = str(raw.get("hypothesis_ref") or raw.get("hypothesis_id") or hypothesis_ref).strip()
        record_cycle_id = str(raw.get("cycle_id") or cycle_id).strip()
        if record_hypothesis_ref != hypothesis_ref or record_cycle_id != cycle_id:
            continue
        criterion_ids = [
            str(value).strip()
            for value in (raw.get("criterion_ids") or [])
            if str(value).strip()
        ]
        row = {
            key: value
            for key, value in {
                "evidence_id": raw.get("evidence_id"),
                "summary": raw.get("summary") or raw.get("scientific_insight"),
                "relation": raw.get("relation"),
                "scope": raw.get("scope"),
                "limitations": raw.get("limitations"),
                "criterion_ids": criterion_ids,
                "criterion_bindings": [
                    {
                        "hypothesis_ref": hypothesis_ref,
                        "cycle_id": cycle_id,
                        "criterion_id": criterion_id,
                    }
                    for criterion_id in criterion_ids
                ],
            }.items()
            if value not in (None, "", [], {})
        }
        if row:
            compact.append(row)
    return compact


def build_hypothesis_adjudication_history(
    research_root: Path,
    hypothesis_ref: str,
    *,
    current_cycle_id: str | None = None,
    task_mode: bool = False,
    legacy_research_root: Path | None = None,
) -> dict[str, Any]:
    """Deterministically derive prior adjudications from per-Cycle authorities.

    This function is intentionally read-only. It never registers Evidence, rebuilds
    an Evidence Registry, or writes a cross-Cycle store.
    """
    target = str(hypothesis_ref or "").strip()
    history: dict[str, Any] = {
        "schema": HISTORY_SCHEMA,
        "hypothesis_ref": target,
        "cycles": [],
    }
    if not target:
        return history

    # Compatible discovery is newest-first. Judge context reads chronologically.
    for cycle_dir in reversed(list_compatible_cycle_dirs(
        Path(research_root), legacy_research_root, task_mode=task_mode
    )):
        cycle_id = cycle_dir.name
        if current_cycle_id is not None and cycle_id == str(current_cycle_id):
            continue
        state = _read_json(cycle_dir / "research_state.json")
        lock = state.get("hypothesis_lock") if isinstance(state.get("hypothesis_lock"), dict) else {}
        lock_ref = str(lock.get("id") or lock.get("hypothesis_id") or "").strip()
        if lock_ref != target:
            continue
        judge = (state.get("role_outputs") or {}).get("judge")
        judge = judge if isinstance(judge, dict) else {}
        verdict = str(judge.get("verdict") or state.get("scientific_verdict") or "").strip()
        if not verdict:
            continue

        manifest = _read_json(cycle_dir / "workspace" / "evidence_manifest.json")
        evidence = _compact_evidence(
            manifest,
            hypothesis_ref=target,
            cycle_id=cycle_id,
        )
        synthesis = state.get("scientific_synthesis") if isinstance(state.get("scientific_synthesis"), dict) else {}
        rationale = str(judge.get("rationale") or "").strip()
        limitations = list(
            dict.fromkeys(
                str(item.get("limitations") or "").strip()
                for item in evidence
                if str(item.get("limitations") or "").strip()
            )
        )
        context_only = verdict == "open"
        history["cycles"].append(
            {
                "cycle_id": cycle_id,
                "hypothesis_ref": target,
                "decisive_test": lock.get("decisive_test") or lock.get("cycle_milestone"),
                "verdict": verdict,
                "adjudication_role": (
                    "context_only_unresolved"
                    if context_only
                    else "adjudicated_local_progress"
                    if verdict == "milestone_advanced"
                    else "terminal_hypothesis_adjudication"
                    if verdict in {"supported", "rejected"}
                    else "adjudicated_context"
                ),
                "established_result": _scientific_synthesis_result(synthesis, rationale),
                "evidence": evidence,
                "scope": lock.get("scope"),
                "limitations": limitations,
            }
        )
    return history
