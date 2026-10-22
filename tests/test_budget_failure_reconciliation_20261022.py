"""Offline regression: migration proof must never waive a real stop/violation."""
import copy
import json
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from theory_master.budget_reconciliation import (
    LEGACY_ERROR, RECEIPT_REL, inspect_legacy_failure, reconcile_legacy_failure,
)
from theory_master.failure_classification import record_failure
from theory_master.investigator_budget import (
    BudgetLedgerError, LEDGER_REL, LEGACY_BRIDGE_REL, _review_record_sha256,
    budget_status,
)
from theory_master.output_normalization import OutputNormalizationError
from theory_master.runtime import write_json
from theory_master.runtime_supervisor_review import resume_blocker, review_runtime_failure
from theory_master.supervisor_continuity import engineering_recheck_ready


@pytest.fixture
def stopped(tmp_path):
    cycle = tmp_path / "185"
    (cycle / "budget").mkdir(parents=True)
    (cycle / "checkpoints").mkdir()
    checkpoint = cycle / "checkpoints/000010-lock.json"
    state = {
        "cycle_id": "185", "status": "failed", "phase": "failed",
        "execution_active": False, "resumable": True, "protocol_health": "violation",
        "investigator_budget_policy": "three_plus_three_v1",
        "hypothesis_lock": {"status": "locked", "id": "H-185", "hypothesis": "frozen"},
        "recovery": {"kind": "ValueError", "action": "error", "message": LEGACY_ERROR},
        "last_safe_checkpoint": str(checkpoint), "supervisor_runtime_resume_count": 0,
        "agents": {"investigator": {"tokens_total": 55_000_000}},
    }
    write_json(checkpoint, {**state, "last_seq": 10})
    write_json(cycle / "checkpoints/000005-lock.json",
               {**state, "last_seq": 5, "protocol_health": "valid"})
    row = {"schema": "theory-master/investigator-budget-review/v1", "review_index": 1,
           "decision": "approve", "reason": "reviewed scientific progress",
           "tranche_tokens": 50_000_000, "prior_allowance": 50_000_000,
           "new_allowance": 100_000_000, "observed_tokens": 52_000_000,
           "allowance_basis": "prior_allowance"}
    (cycle / LEDGER_REL).write_text(json.dumps(row) + "\n")
    receipt = {key: row[key] for key in ("review_index", "decision", "reason", "new_allowance")}
    write_json(cycle / "budget/internal_review_0001.json", receipt)
    write_json(cycle / LEGACY_BRIDGE_REL, {
        "schema": "theory-master/three-plus-three-ledger-bridge/v1",
        "source": "operator_audited_run_state_policy_mismatch",
        "original_row_sha256": _review_record_sha256(row),
        "reviewer_receipt_sha256": _review_record_sha256(receipt),
    })
    (cycle / "events.jsonl").write_text(json.dumps({
        "seq": 8, "type": "run.failed", "payload": {"error": LEGACY_ERROR}}) + "\n")
    write_json(cycle / "research_state.json", state)
    write_json(cycle / "run_state.json", state)
    return cycle, state


def test_typed_budget_failure_and_generic_valueerror_do_not_invent_protocol_violation(stopped):
    cycle, state = stopped
    (cycle / LEGACY_BRIDGE_REL).unlink()
    with pytest.raises(BudgetLedgerError) as error:
        budget_status(cycle, cycle.parent, state)
    fresh = {"protocol_health": "valid"}
    event = record_failure(fresh, error.value)
    assert event["failure_category"] == "budget_validation"
    assert fresh["protocol_health"] == "valid"
    record_failure(fresh, ValueError("projection validation"))
    assert fresh["protocol_health"] == "valid"
    assert fresh["recovery"]["category"] == "runtime_validation"
    record_failure(fresh, OutputNormalizationError("invalid output", classification="invalid_json"))
    assert fresh["protocol_health"] == "violation"
    record_failure(fresh, ValueError("secondary failure"))
    assert fresh["protocol_health"] == "violation"


def test_controlled_reconciliation_preserves_science_pause_budget_and_checkpoint(stopped):
    cycle, state = stopped
    state.update(status="paused", phase="paused", recovery={"action": "pause", "reason": "operator"})
    write_json(cycle / "research_state.json", state)
    write_json(cycle / "run_state.json", state)
    checkpoint_bytes = Path(state["last_safe_checkpoint"]).read_bytes()
    ledger_bytes = (cycle / LEDGER_REL).read_bytes()
    original = copy.deepcopy(state)
    receipt = reconcile_legacy_failure(cycle, state=state)
    assert state["protocol_health"] == "valid"
    assert state["hypothesis_lock"] == original["hypothesis_lock"]
    assert state["recovery"] == original["recovery"]
    assert state["status"] == "paused"
    assert state["supervisor_runtime_resume_count"] == 0
    assert receipt["budget"]["remaining_tokens"] == 45_000_000
    assert receipt["token_grant"] is receipt["resume_performed"] is False
    assert Path(state["last_safe_checkpoint"]).read_bytes() == checkpoint_bytes
    assert (cycle / LEDGER_REL).read_bytes() == ledger_bytes
    assert "Operator pause" in resume_blocker(state)
    assert json.loads((cycle / "run_state.json").read_text())["protocol_health"] == "valid"
    assert '"status": "repair_validated"' in (cycle / "important_events.jsonl").read_text()
    assert (cycle / RECEIPT_REL).is_file()


@pytest.mark.parametrize("damage", ["bridge", "receipt", "ledger", "checkpoint", "wrong_cycle",
                                  "live_owner", "live_worker", "later_failure", "contract", "quarantine",
                                  "budget", "pending_control", "no_valid_before", "symlink"])
def test_revalidation_rejects_unsafe_or_unresolved_evidence(stopped, damage):
    cycle, state = stopped
    if damage in {"bridge", "receipt"}:
        path = cycle / (LEGACY_BRIDGE_REL if damage == "bridge" else "budget/internal_review_0001.json")
        write_json(path, {})
    elif damage == "ledger":
        (cycle / LEDGER_REL).write_text('{"schema":"wrong"}\n')
    elif damage in {"checkpoint", "wrong_cycle"}:
        ck = json.loads(Path(state["last_safe_checkpoint"]).read_text())
        ck["hypothesis_lock" if damage == "checkpoint" else "cycle_id"] = "different"
        write_json(Path(state["last_safe_checkpoint"]), ck)
    elif damage == "live_owner":
        state["owner_pid"] = os.getpid()
    elif damage == "later_failure":
        with (cycle / "events.jsonl").open("a") as f:
            f.write(json.dumps({"seq": 11, "type": "run.failed", "payload": {"error": "different failure"}}) + "\n")
    elif damage == "contract":
        write_json(cycle / "contract_repairs/contract.json", {"errors": ["invalid output"]})
    elif damage == "quarantine":
        with (cycle / "events.jsonl").open("a") as f:
            f.write(json.dumps({"seq": 11, "type": "invocation.quarantined"}) + "\n")
    elif damage == "budget":
        state["agents"]["investigator"]["tokens_total"] = 100_000_001
    elif damage == "pending_control":
        write_json(cycle / "recovery_control.json", {"action": "abandon_cycle"})
    elif damage == "no_valid_before":
        (cycle / "checkpoints/000005-lock.json").unlink()
    elif damage == "symlink":
        path = cycle / "budget/internal_review_0001.json"
        target = cycle.parent / "external.json"
        path.rename(target)
        path.symlink_to(target)
    write_json(cycle / "research_state.json", state)
    before = (cycle / "research_state.json").read_bytes()
    workers = [{"current_cycle": "185", "process_alive": True}] if damage == "live_worker" else []
    with patch("theory_master.worker_runtime.list_workers", return_value=workers):
        with pytest.raises(ValueError):
            reconcile_legacy_failure(cycle)
    assert (cycle / "research_state.json").read_bytes() == before
    assert not (cycle / RECEIPT_REL).exists()


def test_stale_caller_cannot_clear_current_state(stopped):
    cycle, state = stopped
    with pytest.raises(ValueError, match="state changed"):
        reconcile_legacy_failure(cycle, state={**state, "phase": "stale"})


def test_runtime_rechecks_bridge_before_protocol_hold_without_calling_model(stopped):
    cycle, state = stopped
    backend = MagicMock()
    result = review_runtime_failure(backend=backend, research_root=cycle.parent,
        cycle_dir=cycle, state=state, state_path=cycle / "research_state.json", incident_id="INC-test")
    assert result["action"] == "resume"
    assert state["protocol_health"] == "valid"
    assert state["supervisor_runtime_resume_count"] == 1
    backend.invoke.assert_not_called()
    backend.invoke_runtime_review.assert_not_called()


def test_new_typed_budget_failure_can_revalidate_after_bridge_repair(stopped):
    cycle, state = stopped
    state["protocol_health"] = "valid"
    record_failure(state, BudgetLedgerError(LEGACY_ERROR))
    write_json(cycle / "research_state.json", state)
    write_json(cycle / "run_state.json", state)
    result = review_runtime_failure(backend=None, research_root=cycle.parent,
        cycle_dir=cycle, state=state, state_path=cycle / "research_state.json", incident_id="INC-test")
    assert result["action"] == "resume"
    assert state["protocol_health"] == "valid"


def test_runtime_validation_can_enter_repair_but_never_bypass_resource_guard(stopped):
    _, state = stopped
    state["protocol_health"] = "valid"
    record_failure(state, ValueError("bad projection"))
    assert "objective revalidation" in resume_blocker(state)
    assert resume_blocker(state, for_repair=True) is None
    state["investigator_budget"] = {"exhausted": True}
    assert "budget guard" in resume_blocker(state, for_repair=True)


def test_old_receipt_reference_cannot_waive_a_new_budget_failure(stopped):
    _, state = stopped
    state["protocol_health"] = "valid"
    state["budget_failure_reconciliation"] = "old-receipt.json"
    record_failure(state, BudgetLedgerError(LEGACY_ERROR))
    assert "objective revalidation" in resume_blocker(state)


def test_invalid_bridge_is_actionable_hold_and_never_calls_model(stopped):
    cycle, state = stopped
    (cycle / LEGACY_BRIDGE_REL).unlink()
    backend = MagicMock()
    result = review_runtime_failure(backend=backend, research_root=cycle.parent,
        cycle_dir=cycle, state=state, state_path=cycle / "research_state.json", incident_id="INC-test")
    assert result["action"] == "hold"
    assert "budget_reconciliation" in result["repair_suggestion"]
    assert state["protocol_health"] == "violation"
    backend.invoke_runtime_review.assert_not_called()


def test_engineering_reply_needs_independent_validator(stopped):
    cycle, state = stopped
    assert engineering_recheck_ready(cycle, state)
    (cycle / LEGACY_BRIDGE_REL).unlink()
    assert not engineering_recheck_ready(cycle, state)


def test_assembled_engineering_prompt_delegates_resume_and_names_real_phases():
    from theory_master.identity import identity_block
    from theory_master.supervisor_incident_session import _history_prompt
    prompt = identity_block("incident_supervisor") + _history_prompt({}, [])
    assert "Do not invoke Worker controls" in prompt
    assert "scheduler.lock" in prompt
    assert "repair_applied" in prompt and "repair_validated" in prompt
    assert "ValueError alone does not prove" in prompt


@pytest.mark.parametrize("error, category", [
    (BudgetLedgerError(LEGACY_ERROR), "budget_validation"),
    (ValueError("runtime validator failed"), "runtime_validation"),
])
def test_workflow_failure_persists_category_without_protocol_poisoning(tmp_path, error, category):
    import test_core
    from theory_master.core import FakeBackend, Workflow

    class FailingBackend(FakeBackend):
        def invoke(self, role, prompt, thread_id, model, provider):
            if role == "investigator":
                raise error
            return super().invoke(role, prompt, thread_id, model, provider)

    helper = test_core.TheoryMasterTest()
    spec, _ = helper.make(tmp_path)
    with pytest.raises(type(error), match=re.escape(str(error))):
        Workflow(spec, FailingBackend(reworks=0)).run()
    cycle = helper.cycle(spec)
    state = json.loads((cycle / "research_state.json").read_text())
    assert state["status"] == "failed"
    assert state["protocol_health"] == "valid"
    assert state["recovery"]["category"] == category
    assert state["last_runtime_failure"]["message"] == str(error)
    assert "objective revalidation" in resume_blocker(state)


def test_worker_engineering_handoff_rechecks_fixed_bridge_before_resume(stopped):
    from contextlib import ExitStack
    from theory_master import worker_supervisor_dispatch as dispatch
    from theory_master.worker_runtime import save_worker, worker_dir

    cycle, state = stopped
    root = cycle.parent
    state["worker_id"] = "worker-1"
    write_json(cycle / "research_state.json", state)
    save_worker(root, "worker-1", status="FAILED", current_cycle="185", pid=None,
                claim_id="claim", epoch=1, target_h_id="H-185", entry_node_id="H-185")
    write_json(root / ".theory-master/cycle_claims.json", {
        "schema": "theory-master/cycle-claims/v1", "epoch": 1, "history": [],
        "claims": {"claim": {"claim_id": "claim", "fencing_epoch": 1, "worker_id": "worker-1",
                            "cycle_id": "185", "hypothesis_id": "H-185", "status": "claimed"}}})
    write_json(worker_dir(root, "worker-1") / "supervisor_dispatch.json",
               {"cycle": "185", "fingerprint": dispatch._fingerprint(state)})
    bridge_path = cycle / LEGACY_BRIDGE_REL
    bridge = bridge_path.read_bytes()
    bridge_path.unlink()

    def repair(*args):
        bridge_path.write_bytes(bridge)
        return {"status": "engineering_attempted", "repair_phase": "repair_applied"}

    with ExitStack() as stack:
        stack.enter_context(patch.object(dispatch, "supervisor_enabled", return_value=True))
        stack.enter_context(patch.object(dispatch, "runtime_health", return_value={"active": False}))
        stack.enter_context(patch("theory_master.supervisor._supervisor_backend", return_value=None))
        engine = stack.enter_context(patch("theory_master.supervisor_continuity.attempt_engineering_skill", side_effect=repair))
        stack.enter_context(patch("theory_master.specification.TaskSpec.from_task_root", return_value=object()))
        launch = stack.enter_context(patch.object(dispatch, "worker_action", return_value={"pid": 123}))
        result = dispatch.review_worker(root, "worker-1", "185", dispatch._fingerprint(state))
    engine.assert_called_once()
    launch.assert_called_once()
    assert result["status"] == "resume_launched"
    assert result["reason"] == "Awaiting real model/tool activity"
    assert json.loads((cycle / "research_state.json").read_text())["protocol_health"] == "valid"


def test_single_runner_uses_same_engineering_bridge_revalidation(stopped):
    from theory_master.supervisor import _handle_failed_state
    cycle, state = stopped
    (cycle / '.theory-master-cycle').write_text('theory-master/v2\ntest\n')
    bridge_path = cycle / LEGACY_BRIDGE_REL
    bridge = bridge_path.read_bytes()
    bridge_path.unlink()

    def repair(*args):
        bridge_path.write_bytes(bridge)
        return {'status': 'engineering_attempted', 'repair_phase': 'repair_applied'}

    with patch('theory_master.supervisor._supervisor_backend', return_value=None), \
         patch('theory_master.supervisor_continuity.attempt_engineering_skill', side_effect=repair) as engineering:
        assert _handle_failed_state(cycle.parent, repair_enabled=True)
    engineering.assert_called_once()
    saved = json.loads((cycle / 'research_state.json').read_text())
    assert saved['protocol_health'] == 'valid'
    assert saved['supervisor_runtime_resume_count'] == 1
    handoff = json.loads((cycle / 'run_state.json').read_text())['repair_handoff']
    assert handoff['status'] == 'supervisor_resume'
    assert handoff['engineering_skill']['repair_phase'] == 'repair_validated'
    # The launcher owns resume and may now acquire the same gate.
    import fcntl
    with (cycle.parent / '.theory-master/scheduler.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
