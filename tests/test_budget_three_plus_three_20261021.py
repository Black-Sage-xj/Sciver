"""Deterministic 3+3 resource, failure and Worker preservation contracts.

All samples are synthetic; no Provider, model or live research is called.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from theory_master.investigator_budget import (
    BUDGET_ATTENTION_CODE, BUDGET_POLICY_3_PLUS_3, BUDGET_CAP_CODE,
    SUPERVISOR_DECISION_CODE, REVIEWER_TRANCHES, MAX_TRANCHES,
    apply_review, apply_supervisor_budget_decision, budget_status,
    grant_supervisor_tranche,
)
from theory_master.supervisor_budget_review import review_supervisor_extension
from theory_master.budget_auto_preservation import (
    budget_preservation_notice, dispatch_auto_budget_preservation,
)
from theory_master.research_preservation import preservation_blockage, PreservationGuard

BASE = 50_000_000


def _new_cycle(tmp_path):
    root = tmp_path / "research"
    cycle = root / "1"
    (cycle / "budget").mkdir(parents=True)
    (cycle / "evidence").mkdir()
    (cycle / "workspace").mkdir()
    state = {
        "cycle_id": "1", "research_root": str(root),
        "status": "needs_attention", "resumable": True,
        "execution_active": False, "phase": "investigator:budget_review",
        "attention": {"code": BUDGET_ATTENTION_CODE},
        "investigator_budget_policy": BUDGET_POLICY_3_PLUS_3,
        "hypothesis_lock": {"id": "H-0001", "status": "locked",
                            "hypothesis": "fixed test", "scope": "old inputs",
                            "decisive_test": "one criterion"},
        "agents": {"investigator": {"tokens_total_live": BASE}},
    }
    _save(cycle, state)
    return cycle, state


def _save(cycle, state):
    for name in ("research_state.json", "run_state.json"):
        (cycle / name).write_text(json.dumps(state), encoding="utf-8")


def _advance(cycle, state, *, tokens, code=BUDGET_ATTENTION_CODE):
    state["agents"]["investigator"]["tokens_total_live"] = tokens
    state["attention"]["code"] = code
    _save(cycle, state)
    return state


def _approve_first_three(cycle, state):
    root = cycle.parent
    evidence_file = cycle / "evidence" / "registry.jsonl"
    evidence_file.write_text(json.dumps({"evidence_id": "E-1", "summary": "progress-1"}) + "\n")
    a = apply_review(
        cycle, decision="approve", reason="First verified new scientific progress with executable next measurement",
        next_test="Execute a bounded decisive test on existing networks",
        evidence_ids=["E-1"])
    assert a["new_allowance"] == BASE * 2
    _advance(cycle, state, tokens=BASE * 2)
    with evidence_file.open("a") as sink:
        sink.write(json.dumps({"evidence_id": "E-2", "summary": "progress-2"}) + "\n")
    b = apply_review(
        cycle, decision="approve", reason="Second distinct validated scientific progress after prior checkpoint",
        next_test="Finish the frozen signed energy comparison on prior networks",
        evidence_ids=["E-2"])
    assert b["new_allowance"] == BASE * 3
    _advance(cycle, state, tokens=BASE * 3, code=SUPERVISOR_DECISION_CODE)
    status = budget_status(cycle, root, state)
    assert status["granted_tranches"] == REVIEWER_TRANCHES
    return status


def test_supervisor_once_then_sdk_grants_fifth_and_sixth_only(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    (cycle / "runtime_events.jsonl").write_text(
        json.dumps({"agent": "investigator", "event": "LLM_MESSAGE",
                    "content": "A concrete validated scientific derivation"}) + "\n")
    fake = SimpleNamespace(
        invoke_runtime_review=Mock(return_value=("supervisor-thread", json.dumps({
            "decision": "authorize",
            "reason": "Independent available inputs support a discriminating bounded next analysis",
            "next_step": "Use the exact frozen protocol on existing graph assets",
        }))),
        drop_thread=Mock(),
    )
    rec = review_supervisor_extension(
        backend=fake, cycle_dir=cycle, state=state,
        goal="Frozen test", research_contract="No new assets", final_gate="Judge only")
    assert rec["decision"] == "supervisor_authorize"
    assert rec["status"]["granted_tranches"] == 4
    assert rec["status"]["supervisor_authorized"]
    assert fake.invoke_runtime_review.call_count == 1
    original = (cycle / "budget/investigator_reviews.jsonl").read_bytes()
    with pytest.raises(ValueError):
        apply_supervisor_budget_decision(
            cycle, authorize=True,
            reason="This is a second and forbidden full Supervisor authorization",
            next_step="Repeat another scientific comparison")
    assert (cycle / "budget/investigator_reviews.jsonl").read_bytes() == original
    _advance(cycle, state, tokens=BASE * 4)
    x = grant_supervisor_tranche(cycle)
    assert x["grant_number"] == 5
    with pytest.raises(ValueError):
        grant_supervisor_tranche(cycle)  # cannot spend twice at same boundary
    _advance(cycle, state, tokens=BASE * 5)
    y = grant_supervisor_tranche(cycle)
    assert y["grant_number"] == 6
    _advance(cycle, state, tokens=BASE * 6)
    terminal = budget_status(cycle, cycle.parent, state)
    assert terminal["hard_cap_reached"] and terminal["granted_tranches"] == MAX_TRANCHES
    assert terminal["allowance_tokens"] == 300_000_000
    before = (cycle / "budget/investigator_reviews.jsonl").read_bytes()
    with pytest.raises(ValueError):
        grant_supervisor_tranche(cycle)  # never grant a seventh
    assert (cycle / "budget/investigator_reviews.jsonl").read_bytes() == before
    assert len((cycle / "budget/investigator_reviews.jsonl").read_text().splitlines()) == 6


def test_supervisor_denies_and_cannot_grant_later(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    rejected = apply_supervisor_budget_decision(
        cycle, authorize=False,
        reason="The candidate test cannot distinguish competing hypotheses using frozen inputs",
        next_step="Archive current evidence, then let Research Lead select a separate branch")
    status = budget_status(cycle, cycle.parent, state)
    assert rejected["decision"] == "supervisor_decline"
    assert status["supervisor_declined"] and status["rejected"]
    assert status["allowance_tokens"] == BASE * 3
    with pytest.raises(ValueError):
        grant_supervisor_tranche(cycle)
    with pytest.raises(ValueError):
        apply_supervisor_budget_decision(
            cycle, authorize=True,
            reason="Cannot override one immutable Supervisor decision after rejection",
            next_step="Spend more tokens contrary to the recorded rejection")


def test_supervisor_missing_dialogue_fails_closed_without_model_or_grant(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    fake = SimpleNamespace(invoke_runtime_review=Mock())
    before = (cycle / "budget/investigator_reviews.jsonl").read_bytes()
    with pytest.raises(RuntimeError, match="Missing original"):
        review_supervisor_extension(
            backend=fake, cycle_dir=cycle, state=state,
            goal="Research", research_contract="Contract", final_gate="Gate")
    fake.invoke_runtime_review.assert_not_called()
    assert (cycle / "budget/investigator_reviews.jsonl").read_bytes() == before


def test_reviewer_cannot_grant_fourth_tranche(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    _advance(cycle, state, tokens=BASE * 3, code=BUDGET_ATTENTION_CODE)
    with pytest.raises(ValueError, match="Budget Reviewer may authorize only"):
        apply_review(
            cycle, decision="approve",
            reason="This reviewer is incorrectly authorizing a fourth tranche beyond its authority",
            next_test="Attempt an extra statistical comparison without Supervisor approval",
            evidence_ids=[])
    assert budget_status(cycle, cycle.parent, state)["granted_tranches"] == 3


def test_terminal_cap_preservation_checks_both_ledgers(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    apply_supervisor_budget_decision(
        cycle, authorize=True,
        reason="Existing test has a concrete and independent discriminating route",
        next_step="Check a fixed trusted network before another costly simulation")
    _advance(cycle, state, tokens=BASE * 4)
    grant_supervisor_tranche(cycle)
    _advance(cycle, state, tokens=BASE * 5)
    grant_supervisor_tranche(cycle)
    _advance(cycle, state, tokens=BASE * 6, code=BUDGET_CAP_CODE)
    state["phase"] = "investigator:budget_denied"
    state["investigator_budget"] = budget_status(cycle, cycle.parent, state)
    _save(cycle, state)
    assert preservation_blockage(cycle, state, state)["code"] == BUDGET_CAP_CODE
    corrupt = dict(state, investigator_budget=dict(state["investigator_budget"]))
    corrupt["investigator_budget"]["hard_cap_reached"] = False
    with pytest.raises(PreservationGuard):
        preservation_blockage(cycle, corrupt, state)


def test_dispatch_is_explicit_new_policy_once_and_not_manual_pause(tmp_path):
    root = tmp_path / "research"
    wid = "worker-1"
    cycle = root / "1"
    cycle.mkdir(parents=True)
    worker_folder = root / ".theory-master/workers" / wid
    worker_folder.mkdir(parents=True)
    status = {"worker_id": wid, "status": "PAUSED",
              "current_cycle": "1", "process_alive": False}
    marker = {
        "policy": "three_plus_three_v1", "code": "investigator_budget_cap_reached",
        "source": "sdk_authorized_budget_terminal", "cycle": "1"}
    scientific = {"status": "needs_attention", "execution_active": False,
                  "investigator_budget_policy": "three_plus_three_v1",
                  "attention": {"code": marker["code"]}, "budget_auto_preserve": marker}
    (cycle / "research_state.json").write_text(json.dumps(scientific))
    spec = SimpleNamespace(research_root=root)
    with patch("theory_master.worker_runtime.list_workers", return_value=[status]), \
         patch("theory_master.research_preservation.blocked_checkpoint") as checkpoint, \
         patch("theory_master.worker_runtime.worker_action",
               return_value={"status": "PRESERVING"}) as worker_action:
        assert dispatch_auto_budget_preservation(spec) is False  # user pause means pause
        status["status"] = "NEEDS_ATTENTION"
        assert dispatch_auto_budget_preservation(spec) is True
        assert dispatch_auto_budget_preservation(spec) is False
        checkpoint.assert_called_once()
        worker_action.assert_called_once_with(spec, wid, "preserve_release")
    receipt = json.loads((worker_folder / "budget_auto_preserve_1.json").read_text())
    assert receipt["status"] == "launched"


def test_preservation_reminder_durable_and_idempotent(tmp_path):
    root = tmp_path / "research"
    (root / "1").mkdir(parents=True)
    first = budget_preservation_notice(root, "worker-2", "1", success=True,
                                       hypothesis_id="H-0001")
    second = budget_preservation_notice(root, "worker-2", "1", success=True)
    assert first["incident_id"] == second["incident_id"]
    assert first["severity"] == "important"
    assert first["persistent_reminder"] is True
    assert len((root / "1/important_events.jsonl").read_text().splitlines()) == 1


def test_preserve_failure_keeps_one_shot_receipt_and_notifies(tmp_path):
    root = tmp_path / "research"
    cycle = root / "2"
    cycle.mkdir(parents=True)
    worker_folder = root / ".theory-master/workers/worker-2"
    worker_folder.mkdir(parents=True)
    marker = {"policy": "three_plus_three_v1",
              "code": "investigator_budget_cap_reached",
              "source": "sdk_authorized_budget_terminal", "cycle": "2"}
    state = {"status": "needs_attention", "execution_active": False,
             "investigator_budget_policy": "three_plus_three_v1",
             "attention": {"code": marker["code"]}, "budget_auto_preserve": marker}
    (cycle / "research_state.json").write_text(json.dumps(state))
    worker = {"worker_id": "worker-2", "status": "NEEDS_ATTENTION",
              "current_cycle": "2", "process_alive": False}
    spec = SimpleNamespace(research_root=root)
    with patch("theory_master.worker_runtime.list_workers", return_value=[worker]), \
         patch("theory_master.research_preservation.blocked_checkpoint"), \
         patch("theory_master.worker_runtime.worker_action",
               side_effect=RuntimeError("synthetic launch error")) as action:
        assert dispatch_auto_budget_preservation(spec) is True
        assert dispatch_auto_budget_preservation(spec) is False
        action.assert_called_once()
    receipt = json.loads((worker_folder / "budget_auto_preserve_2.json").read_text())
    assert receipt["status"] == "failed_closed"
    events = [json.loads(x) for x in (cycle / "important_events.jsonl").read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["code"] == "budget_preservation_failed"
    assert events[0]["severity"] == "important"


def test_forged_marker_and_old_policy_cannot_auto_preserve(tmp_path):
    root = tmp_path / "research"
    cycle = root / "3"
    cycle.mkdir(parents=True)
    worker = {"worker_id": "worker-1", "status": "NEEDS_ATTENTION",
              "current_cycle": "3", "process_alive": False}
    original = {"status": "needs_attention", "execution_active": False,
                "investigator_budget_policy": "legacy",
                "attention": {"code": "investigator_budget_cap_reached"},
                "budget_auto_preserve": {"policy": "three_plus_three_v1",
                    "code": "investigator_budget_cap_reached",
                    "source": "sdk_authorized_budget_terminal", "cycle": "3"}}
    spec = SimpleNamespace(research_root=root)
    with patch("theory_master.worker_runtime.list_workers", return_value=[worker]), \
         patch("theory_master.worker_runtime.worker_action") as action:
        (cycle / "research_state.json").write_text(json.dumps(original))
        assert dispatch_auto_budget_preservation(spec) is False
        original["investigator_budget_policy"] = "three_plus_three_v1"
        original["budget_auto_preserve"]["code"] = "forged_different_code"
        (cycle / "research_state.json").write_text(json.dumps(original))
        assert dispatch_auto_budget_preservation(spec) is False
        original["budget_auto_preserve"]["code"] = original["attention"]["code"]
        original["status"] = "paused"
        (cycle / "research_state.json").write_text(json.dumps(original))
        assert dispatch_auto_budget_preservation(spec) is False
        action.assert_not_called()


def test_supervisor_ledger_refuses_tampering(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    apply_supervisor_budget_decision(
        cycle, authorize=True,
        reason="A verified new analytic approach can address the unchanged decisive test",
        next_step="Check predicted release on available independent roots only")
    journal = cycle / "budget/investigator_reviews.jsonl"
    good = journal.read_bytes()
    rows = [json.loads(line) for line in good.splitlines()]
    rows[-1]["grant_number"] = 5  # forged fourth grant
    journal.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(ValueError, match="invalid or excessive Supervisor tranche"):
        budget_status(cycle, cycle.parent, state)
    journal.write_bytes(good)
    assert budget_status(cycle, cycle.parent, state)["granted_tranches"] == 4


def test_new_three_plus_three_policy_does_not_migrate_old_cycle(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    state.pop("investigator_budget_policy")
    _save(cycle, state)
    assert budget_status(cycle, cycle.parent, state)["policy_version"] == "legacy"
    assert budget_status(cycle, cycle.parent, state)["granted_tranches"] == 1


def test_workflow_uses_exactly_one_supervisor_review_then_sdk_installments(tmp_path):
    from theory_master.workflow_investigation import InvestigationMixin
    from theory_master.workflow_support import RecoveryStop

    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    (cycle / "runtime_events.jsonl").write_text(
        json.dumps({"agent": "investigator", "event": "LLM_MESSAGE",
                    "content": "Useful bounded numerical research"}) + "\n")
    backend = SimpleNamespace(
        invoke_runtime_review=Mock(return_value=("supervisor-1", json.dumps({
            "decision": "authorize",
            "reason": "A useful independent derivation warrants one last fixed-scope comparison",
            "next_step": "Test the frozen relation against the known independent root set",
        }))),
        drop_thread=Mock(),
    )
    events = Mock()

    def _snapshot(current):
        current["investigator_budget"] = budget_status(cycle, cycle.parent, current)

    fake = SimpleNamespace(
        cycle_dir=cycle, spec=SimpleNamespace(research_root=cycle.parent,
        goal="Frozen research", contract="Only old networks",
        final_gate="Separate Judge"),
        backend=backend, _persist=lambda current, **kw: _save(cycle, current),
        _persist_investigator_budget_snapshot=_snapshot, _emit=events,
        _adjudicable_judge_records=lambda _: [],
        _evidence_context=lambda: {},
    )
    fake._hold_budget_terminal = lambda current, **kwargs: (
        InvestigationMixin._hold_budget_terminal(fake, current, **kwargs))
    with patch("theory_master.supervisor_budget_review.supervisor_enabled",
               return_value=True):
        # No reviewer approval is asked after the third budget ends.
        assert InvestigationMixin._resolve_3plus3_budget(
            fake, state, budget_status(cycle, cycle.parent, state))
    assert backend.invoke_runtime_review.call_count == 1
    assert budget_status(cycle, cycle.parent, state)["granted_tranches"] == 4
    for expected in (5, 6):
        _advance(cycle, state, tokens=(expected - 1) * BASE)
        assert InvestigationMixin._resolve_3plus3_budget(
            fake, state, budget_status(cycle, cycle.parent, state))
        assert budget_status(cycle, cycle.parent, state)["granted_tranches"] == expected
        assert backend.invoke_runtime_review.call_count == 1
    _advance(cycle, state, tokens=6 * BASE)
    with pytest.raises(RecoveryStop, match="resource phase ended"):
        InvestigationMixin._resolve_3plus3_budget(
            fake, state, budget_status(cycle, cycle.parent, state))
    assert state["budget_auto_preserve"]["code"] == BUDGET_CAP_CODE
    assert state["status"] == "needs_attention"
    assert state["scientific_verdict"] is None
    assert backend.invoke_runtime_review.call_count == 1
    assert budget_status(cycle, cycle.parent, state)["granted_tranches"] == 6
