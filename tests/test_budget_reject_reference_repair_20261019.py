"""Offline regression for immutable budget decision, ID-only repair, and science hold."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from theory_master.internal_budget_review import review_budget_in_sdk
from theory_master.investigator_budget import apply_review, budget_status, LEDGER_REL
from theory_master.workflow_investigation import InvestigationMixin
from theory_master.workflow_support import RecoveryStop


def fixture(root):
    research = root / "research"
    cycle = research / "155"
    cycle.mkdir(parents=True)
    state = {
        "research_root": str(research), "run_id": "155", "cycle_id": "155",
        "status": "needs_attention", "phase": "investigator:budget_review",
        "resumable": True, "execution_active": False,
        "attention": {"code": "investigator_budget_review_required"},
        "agents": {"investigator": {"tokens_total": 50_055_391}},
        "investigation_attempt": 2, "hypothesis_lock": {"id": "H-0155"},
        "last_safe_checkpoint": str(cycle / "checkpoints" / "000018.json"),
        "investigation_attempts": {
            "attempt-0001": {
                "judge_decision": {"assessment_state": "rework_requested",
                                   "missing_evidence": "Structural contrast was absent."},
                "outputs": [{"executor_role": "investigator", "round_completed": True,
                             "output": {"findings": [{"statement": "Earlier work"}]}}],
            },
            "attempt-0002": {"outputs": [], "readiness": {}},
        },
    }
    for name in ("run_state.json", "research_state.json"):
        (cycle / name).write_text(json.dumps(state))
    evidence = cycle / "evidence"
    evidence.mkdir()
    (evidence / "registry.jsonl").write_text(
        json.dumps({"evidence_id": "E-1", "summary": "Existing registered result"}) + "\n")
    return research, cycle, state


def response(decision, evidence, progress, reason=None):
    return json.dumps({
        "decision": decision,
        "reason": reason or "The investigated data do not support another identical broad scan.",
        "next_test": "Only if a new input source is identified, prescreen it topology-first.",
        "evidence_ids": evidence, "progress_refs": progress,
    })


@pytest.mark.parametrize("repair_mode", ["correct", "tries_to_flip_decision", "malformed"])
def test_reference_fix_keeps_reject_terminal_and_auditable(tmp_path, repair_mode):
    research, cycle, state = fixture(tmp_path)
    original = response("reject", ["E-1"], ["unoffered-sha"])
    if repair_mode == "correct":
        correction = response("reject", ["E-1"], [])
    elif repair_mode == "tries_to_flip_decision":
        correction = response("approve", ["E-1"], [])
    else:
        correction = "INVALID"
    backend = SimpleNamespace(
        invoke_budget_review=MagicMock(side_effect=[
            ("original-review", original), ("repair-review", correction),
        ]),
        drop_thread=MagicMock(),
    )
    result = review_budget_in_sdk(
        backend=backend, cycle_dir=cycle, state=state,
        goal="Goal", research_contract="Contract", final_gate="Final Gate",
    )
    assert result["decision"] == "reject"
    assert result["new_allowance"] == 50_000_000
    assert result["progress_refs"] == []
    assert budget_status(cycle, research, state)["rejected"]
    assert backend.invoke_budget_review.call_count == 2
    assert backend.drop_thread.call_count == 2
    trace = next((cycle / "budget").glob("reference_repair_*.json"))
    saved = json.loads(trace.read_text())
    assert saved["original_decision"] == saved["validated_decision"] == "reject"
    assert saved["method"] == (
        "one_reviewer_reference_retry" if repair_mode == "correct"
        else "safe_reject_reference_prune")


def test_approval_reference_repair_exact_match_and_does_not_overgrant(tmp_path):
    research, cycle, state = fixture(tmp_path)
    first = response("approve", ["E-1"], ["hallucination"])
    second = response("approve", ["E-1"], [])
    backend = SimpleNamespace(
        invoke_budget_review=MagicMock(side_effect=[("initial", first), ("repair", second)]),
        drop_thread=MagicMock(),
    )
    result = review_budget_in_sdk(
        backend=backend, cycle_dir=cycle, state=state,
        goal="Goal", research_contract="Contract", final_gate="Final Gate",
    )
    assert result["decision"] == "approve"
    assert result["new_allowance"] == 100_000_000
    assert len((cycle / LEDGER_REL).read_text().splitlines()) == 1


def test_malformed_approval_repair_fails_closed_without_ledger(tmp_path):
    _, cycle, state = fixture(tmp_path)
    backend = SimpleNamespace(
        invoke_budget_review=MagicMock(side_effect=[
            ("initial", response("approve", ["E-1"], ["unoffered"])),
            ("repair", response("reject", ["E-1"], [])),
        ]),
        drop_thread=MagicMock(),
    )
    with pytest.raises(ValueError, match="approval reference repair failed closed"):
        review_budget_in_sdk(backend=backend, cycle_dir=cycle, state=state,
                             goal="Goal", research_contract="Contract", final_gate="Gate")
    assert not (cycle / LEDGER_REL).exists()
    saved = json.loads(next((cycle / "budget").glob("reference_repair_*.json")).read_text())
    assert saved["status"] == "failed_closed"


def test_budget_rejection_preserves_unfinished_judge_objection_not_open(tmp_path):
    research, cycle, state = fixture(tmp_path)
    record = apply_review(
        cycle, decision="reject",
        reason="Further identical scans have no plausible topology contrast source.",
        next_test="Only inspect a named independently justified topology source.",
        evidence_ids=["E-1"], progress_refs=[],
    )
    workflow = InvestigationMixin()
    workflow.cycle_dir = cycle
    workflow.spec = SimpleNamespace(research_root=research)
    workflow._emit = MagicMock()
    workflow._save_attempt = MagicMock()
    workflow._evidence_context = lambda: {}
    workflow._persist = lambda value, **kw: [
        (cycle / name).write_text(json.dumps(value)) for name in
        ("run_state.json", "research_state.json")
    ]
    with pytest.raises(RecoveryStop, match="scientific verdict not inferred"):
        workflow._hold_budget_rejected(state, record["reason"])
    assert state["attention"]["code"] == "investigator_budget_review_rejected"
    assert state["phase"] == "judge:rework_blocked"
    assert state["assessment_state"] == "rework_requested"
    assert state["scientific_verdict"] is None
    assert state["last_safe_checkpoint"].endswith("000018.json")
    assert state["investigation_attempts"]["attempt-0001"]["judge_decision"]["missing_evidence"] == "Structural contrast was absent."
    assert state["investigation_attempts"]["attempt-0002"]["readiness"]["ready"] is False
    assert state["investigator_budget"]["rejected"] is True
    handoff = json.loads((cycle / "budget/denied_budget_science_handoff.json").read_text())
    assert handoff["ready_for_judge"] is False
    assert handoff["formal_new_investigator_submissions"] == 0
