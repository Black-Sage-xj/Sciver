"""Bounded, source-bound qualification feedback; never scientific Outcomes."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .invocations import stable_hash
from .scientific_objective import complete_science, frozen_science_digest, frozen_science_view, require_review_binding
from .specification import list_cycle_dirs

MAX_FILE_BYTES = 2_000_000
MAX_CYCLES = 12
MAX_INVOCATIONS = 96
MAX_FEEDBACK_CHARS = 48_000


def previous_qualification_stop(candidate: dict[str, Any], stops: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match exact scientific text independently of candidate or Cycle identity."""
    science = frozen_science_view(candidate)
    if not complete_science(science):
        return None
    digest = frozen_science_digest(science)
    return next((row for row in stops if row.get("frozen_objective_sha256") == digest), None)


def _read(path: Path, cycle: Path, *, lines: bool = False) -> Any:
    relative = path.relative_to(cycle)
    if (cycle.is_symlink() or any((cycle / Path(*relative.parts[:i])).is_symlink()
                                  for i in range(1, len(relative.parts) + 1))
            or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES):
        raise ValueError("unsafe or oversized qualification history")
    with path.open("rb") as handle:
        raw = handle.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("oversized qualification history")
    text = raw.decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()] if lines else json.loads(text)


def _closed_outputs(cycle: Path) -> list[tuple[dict[str, Any], str]]:
    records = _read(cycle / "invocations.jsonl", cycle, lines=True)
    snapshots: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        ident = str(record.get("invocation_id") or "")
        if not re.fullmatch(r"inv-[A-Za-z0-9_-]{1,160}", ident):
            continue
        snapshots.setdefault(ident, {}).update(record)
    result = []
    for ident, snapshot in list(snapshots.items())[-MAX_INVOCATIONS:]:
        if (snapshot.get("state") != "committed" or snapshot.get("commit_state") != "committed"
                or snapshot.get("stage") not in {"frontier_proposal", "candidate_review", "selection_repair"}):
            continue
        path = cycle / "semantic_closure" / f"{ident}.json"
        try:
            closure = _read(path, cycle)
            if (not isinstance(closure, dict) or stable_hash(closure) != snapshot.get("closure_hash")
                    or closure.get("role") != snapshot.get("role")
                    or closure.get("stage") != snapshot.get("stage")
                    or not isinstance(closure.get("closed_output"), dict)):
                continue
            result.append((closure, str(path.resolve())))
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
    return result


def _supervisor_proposal(state: dict[str, Any]) -> dict[str, Any] | None:
    receipt = state.get("supervisor_qualification")
    frontier = state.get("frontier_review")
    if (not isinstance(receipt, dict) or receipt.get("status") != "review_exhausted"
            or not re.fullmatch(r"SUP-[0-9a-f]{16}", str(receipt.get("session_id") or ""))
            or not receipt.get("thread_id") or not isinstance(frontier, dict)
            or frontier.get("status") != "unqualified" or frontier.get("selected_candidate_id") is not None):
        return None
    proposal = receipt.get("frontier")
    candidates = proposal.get("candidates") if isinstance(proposal, dict) else None
    if not isinstance(candidates, list) or len(candidates) != 1:
        return None
    candidate = candidates[0]
    if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str) or not candidate["id"]:
        return None
    # The archived Supervisor receipt and final Cycle view must agree on
    # identity. Their science is admitted only by the committed review below.
    generated = frontier.get("generated_candidates")
    if not isinstance(generated, list):
        return None
    mirrors = [row for row in generated if isinstance(row, dict) and row.get("id") == candidate["id"]]
    if len(mirrors) != 1 or frozen_science_view(mirrors[0]) != frozen_science_view(candidate):
        return None
    return candidate


def recent_qualification_stops(root: Path, *, current_cycle: str, limit: int = 6) -> list[dict[str, Any]]:
    """Recover rejected science with a committed, hash-bound Challenger review."""
    if limit <= 0:
        return []
    result: list[dict[str, Any]] = []
    remaining = MAX_FEEDBACK_CHARS
    for cycle in list_cycle_dirs(root)[:MAX_CYCLES]:
        if cycle.name == current_cycle or cycle.is_symlink():
            continue
        try:
            state = _read(cycle / "research_state.json", cycle)
            if (not isinstance(state, dict) or state.get("status") != "no_qualified_candidate"
                    or state.get("execution_active") or state.get("scientific_verdict") is not None
                    or (state.get("hypothesis_lock") or {}).get("status", "unlocked") != "unlocked"):
                continue
            candidates, reviews, repairs = {}, {}, {}
            empty_stops = []
            for closure, source in _closed_outputs(cycle):
                output = closure["closed_output"]
                if closure["role"] == "research_lead" and closure["stage"] == "frontier_proposal":
                    reason = output.get("blocking_reason")
                    if output.get("candidates") == [] and isinstance(reason, str) and reason.strip():
                        row = {"cycle_id": cycle.name, "blocking_reason": reason,
                               "source_paths": [source],
                               "evidence_status": "Lead preparation stop; no scientific verdict or proof of unavailable data"}
                        empty_stops.append(row)
                    for candidate in output.get("candidates") or []:
                        if isinstance(candidate, dict) and candidate.get("id"):
                            candidates[str(candidate["id"])] = (candidate, source)
                elif closure["role"] == "challenger" and closure["stage"] == "candidate_review":
                    for review in output.get("reviews") or []:
                        if isinstance(review, dict) and review.get("candidate_id"):
                            reviews[str(review["candidate_id"])] = (review, source)
                elif (closure["role"] == "research_lead" and closure["stage"] == "selection_repair"
                      and output.get("outcome") == "cannot_repair"):
                    repairs[str(output.get("selected_parent_candidate_id") or "")] = (output, source)
            supervisor = _supervisor_proposal(state)
            supervisor_id = None
            if supervisor is not None and supervisor["id"] not in candidates:
                supervisor_id = supervisor["id"]
                candidates[supervisor_id] = (supervisor, str((cycle / "research_state.json").resolve()))
            ids = [str(ident) for attempt in reversed(state.get("qualification_attempts") or [])
                   if isinstance(attempt, dict) for ident in attempt.get("candidate_ids") or []]
            candidate_stops = []
            for ident in dict.fromkeys(ids):
                if ident not in candidates or ident not in reviews:
                    continue
                candidate, proposal_source = candidates[ident]
                review, review_source = reviews[ident]
                science = frozen_science_view(candidate)
                if (not complete_science(science)
                        or len(json.dumps(science, ensure_ascii=False)) > 24_000
                        or review.get("verdict") not in {"reject", "revise"}
                        or (ident == supervisor_id and review.get("verdict") != "reject")):
                    continue
                try:
                    require_review_binding(ident, science, review.get("reviewed_science"))
                except (KeyError, ValueError):
                    continue
                repair, repair_source = repairs.get(ident, ({}, ""))
                row = {
                    "cycle_id": cycle.name, "candidate_id": ident,
                    "frozen_objective": science,
                    "frozen_objective_sha256": frozen_science_digest(science),
                    "review": {key: review[key] for key in ("verdict", "reason", "blocking_issues") if key in review},
                    "cannot_repair_reason": str(repair.get("reason") or repair.get("cannot_repair_reason") or ""),
                    "source_paths": list(dict.fromkeys(p for p in (proposal_source, review_source, repair_source) if p)),
                    "evidence_status": "Qualification preparation history; no scientific verdict",
                }
                candidate_stops.append(row)
            # Preserve the established Lead ordering, but do not let its
            # empty exit hide the independently rejected SPV proposal.
            if any(row["candidate_id"] == supervisor_id for row in candidate_stops):
                recent = candidate_stops + empty_stops
            else:
                recent = empty_stops + candidate_stops
            for row in recent:
                size = len(json.dumps(row, ensure_ascii=False))
                if size <= remaining:
                    result.append(row)
                    remaining -= size
                    if len(result) >= min(limit, 12):
                        return result
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
            continue
    return result
