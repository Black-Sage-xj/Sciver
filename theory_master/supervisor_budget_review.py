"""One independent Supervisor investment decision after the third Investigator tranche.

This is not a scientific Judge verdict. The SDK is the sole locked grant
writer, and the Supervisor does not gain permission to change frozen science.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .investigator_budget import (
    apply_supervisor_budget_decision, budget_status, REVIEWER_TRANCHES,
)
from .runtime import now, write_json
from .supervisor_intervention import supervisor_enabled, supervisor_model


def review_supervisor_extension(*, backend: Any, cycle_dir: Path,
                                state: dict[str, Any], goal: str,
                                research_contract: str, final_gate: str) -> dict[str, Any]:
    """One read-only model decision, followed by a guarded SDK ledger mutation."""
    cycle_dir = Path(cycle_dir).resolve()
    if not supervisor_enabled(cycle_dir.parent):
        raise RuntimeError("Supervisor is disabled; keep the third-tranche checkpoint")
    budget = budget_status(cycle_dir, cycle_dir.parent, state)
    if (budget["granted_tranches"] != REVIEWER_TRANCHES
            or budget["rejected"] or not budget["exhausted"]
            or budget["supervisor_authorized"]):
        raise ValueError("Supervisor review is not at the third-tranche boundary")
    from .budget_review_transcript import dialogue_source
    dialogue = dialogue_source(cycle_dir)
    if not dialogue["exists"] or not dialogue["event_count"]:
        raise RuntimeError("Missing original Investigator research dialogue; refuse to spend")
    lock = state.get("hypothesis_lock") or {}
    previous = cycle_dir / "budget" / "investigator_reviews.jsonl"
    recorded = [
        json.loads(line) for line in previous.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if previous.is_file() else []
    if len([row for row in recorded if row.get("decision") == "approve"]) != 2:
        raise ValueError("Third-tranche handoff requires exactly two Reviewer approvals")
    context = {
        "goal": str(goal)[:1800],
        "research_contract": str(research_contract)[:1800],
        "final_gate": str(final_gate)[:1200],
        "frozen_hypothesis": str(lock.get("hypothesis") or "")[:1800],
        "frozen_decisive_test": str(lock.get("decisive_test") or "")[:1800],
        "budget": {k: budget[k] for k in (
            "tranche_tokens", "observed_tokens", "allowance_tokens", "granted_tranches")},
        "reviewer_decisions": [
            {"reason": str(row.get("reason") or "")[:700],
             "next_test": str(row.get("next_bounded_test") or "")[:500]}
            for row in recorded if row.get("decision") == "approve"
        ],
        "original_dialogue": dialogue,
        "workspace": str((cycle_dir / "workspace").resolve()),
    }
    prompt = (
        "You are the independent, read-only TheoryMaster SUPERVISOR, NOT "
        "Budget Reviewer, Investigator, Research Lead, or scientific Judge. "
        "The Investigator has consumed three authorized resource tranches. "
        "Decide ONCE whether existing evidence and a credible, bounded route "
        "justify opening at most three further equal resource tranches. "
        "Use read-only tools to inspect the original chronological Investigator "
        "dialogue and verify relevant workspace artifacts; check novelty, "
        "repeated failed strategies, and whether an actionable discriminating "
        "test remains inside the FROZEN hypothesis, scope and decisive test. "
        "No new networks, scope changes, protocol exceptions, or provider quota "
        "workarounds are authorized by this decision. Missing/unreadable inputs "
        "are an operational error, not a justified scientific decline; state "
        "the missing source rather than assuming no progress. "
        "You have no direct file, resource, Worker or scientific authority. "
        "Return exactly JSON with decision: 'authorize' or 'decline', "
        "reason: nonempty >=30 characters, next_step: concrete >=20 characters. "
        "The SDK enforces grants and checkpoint safety. Context:\n"
        + json.dumps(context, ensure_ascii=False)
    )
    model = supervisor_model(cycle_dir.parent)
    invoke = getattr(backend, "invoke_runtime_review", None)
    if not callable(invoke):
        raise RuntimeError("Read-only Supervisor model interface unavailable")
    thread = None
    visible: dict[str, Any] = {"status": "unavailable"}
    try:
        thread, raw = invoke(prompt, model, "CodexHTTPS")
    finally:
        if thread:
            try:
                capture = getattr(backend, "snapshot_budget_review_thread", None)
                if callable(capture):
                    visible = capture(thread, cycle_dir / "budget" /
                                      "supervisor_extension_visible_thread.jsonl")
            except Exception as exc:
                visible = {"status": "unavailable", "error_type": type(exc).__name__}
            finally:
                drop = getattr(backend, "drop_thread", None)
                if callable(drop):
                    try:
                        drop(thread)
                    except Exception:
                        pass  # archive/cleanup failure cannot modify authority
    try:
        response = json.loads(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Supervisor did not return valid JSON") from exc
    if (not isinstance(response, dict)
            or response.get("decision") not in {"authorize", "decline"}
            or not isinstance(response.get("reason"), str)
            or len(response["reason"].strip()) < 30
            or not isinstance(response.get("next_step"), str)
            or len(response["next_step"].strip()) < 20):
        raise ValueError("Invalid Supervisor 3+3 resource decision contract")
    record = apply_supervisor_budget_decision(
        cycle_dir, authorize=response["decision"] == "authorize",
        reason=response["reason"], next_step=response["next_step"],
    )
    # This receipt is supplementary: the append-only ledger above is authority.
    try:
        write_json(cycle_dir / "budget" / "supervisor_extension_decision.json", {
            "schema": "theory-master/supervisor-budget-extension/v1",
            "at": now(), "model": model, "decision": response["decision"],
            "reason": response["reason"], "next_step": response["next_step"],
            "ledger_decision": record["decision"],
            "read_only_prompt": prompt, "model_output": str(raw),
            "visible_thread": visible,
        })
    except (OSError, ValueError):
        pass
    return record
