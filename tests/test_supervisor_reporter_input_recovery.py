import hashlib
import json
from pathlib import Path

import pytest

from theory_master.invocations import InvocationLedger
from theory_master.resume_preparation import consume_resume_repairs
from theory_master.runtime import write_json
from theory_master.runtime_supervisor_review import resume_blocker, review_runtime_failure
from theory_master.supervisor_repair_receipts import inspect_repair_receipt
from theory_master.supervisor_reporter_input_recovery import VALIDATOR, validate_reporter_input_reentry

_ENGINE = Path(__file__).resolve().parents[1] / "theory_master"


@pytest.fixture
def overflow(tmp_path):
    cycle = tmp_path / "research" / "1"
    checkpoint = cycle / "checkpoints" / "000002-lead.json"
    artifact = cycle / "invocation_artifacts" / "investigation.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"records": []}')
    context = {"judge_decision": {"verdict": "open"}, "report_materials": [{
        "manifest_path": str(artifact), "manifest_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "assets": [{"kind": "document", "status": "available", "reference": f"{i:04d}-summary.md",
                    "source": "x" * 1200} for i in range(1000)],
    }]}
    prompt = "Reporter task\nContext:\n" + json.dumps(context)
    assert len(prompt) > 1048576
    error = "JSON-RPC error -32602: Input exceeds the maximum length of 1048576 characters."
    invocation, thread = "inv-1-reporter", "original-thread"
    ledger = InvocationLedger(cycle / "invocations.jsonl")
    ledger.prepare(invocation_id=invocation, kind="agent", input_payload=context, role="reporter", stage="report")
    ledger.transition(invocation, "running", thread_id=thread, role="reporter")
    ledger.transition(invocation, "failed", role="reporter", stage="report", commit_state="not_committed",
                      failure_phase="invoke", error_type="InvalidParamsError", error=error)
    rows = [
        {"event": "AGENT_STARTED", "role": "reporter", "stage": "report", "invocation_id": invocation,
         "thread_id": thread, "prompt_chars": len(prompt)},
        {"event": "AGENT_INPUT", "role": "reporter", "stage": "report", "input_kind": "task", "content": prompt},
    ]
    (cycle / "runtime_events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    committed = {"cycle_id": "1", "hypothesis_lock": {"status": "locked", "id": "H-0001"},
                 "scientific_verdict": "open", "assessment_state": "terminal", "scientific_phase": "committed",
                 "role_outputs": {role: {"committed": role}
                 for role in ("investigator", "judge", "research_lead", "frontier_maintenance")}}
    for filename, role in (("cycle_synthesis_out.json", "research_lead"),
                           ("frontier_state_out.json", "frontier_maintenance"),
                           ("scientific_model_out.json", None), ("scientific_model_update.json", None)):
        write_json(cycle / filename, committed["role_outputs"].get(role, {}))
    write_json(checkpoint, committed)
    state = {**committed, "status": "failed", "phase": "failed", "resumable": True, "protocol_health": "valid",
             "last_safe_checkpoint": str(checkpoint), "last_invocation_id": invocation,
             "threads": {"reporter": thread}, "recovery": {"action": "error", "kind": "InvalidParamsError",
             "category": "runtime", "message": error}, "last_invocation_failure": {
             "invocation_id": invocation, "role": "reporter", "stage": "report",
             "failure_phase": "invoke", "error_type": "InvalidParamsError"}}
    write_json(cycle / "research_state.json", state)
    return cycle, state, artifact


def test_reporter_overflow_is_replayed_once_at_the_committed_boundary(overflow):
    cycle, state, artifact = overflow
    assert "verified compact-prompt repair" in resume_blocker(state)
    original_checkpoint = Path(state["last_safe_checkpoint"]).read_bytes()
    result = review_runtime_failure(backend=None, research_root=cycle.parent, cycle_dir=cycle,
        state=state, state_path=cycle / "research_state.json", incident_id="reporter-overflow")
    assert result["action"] == "resume"
    receipt = inspect_repair_receipt(cycle, state)
    assert receipt["validator"] == VALIDATOR
    result = json.loads(receipt["tests"])
    assert result["projected_bytes"] < 65_536
    assert str(artifact) in receipt["extra_inputs"]
    assert Path(state["last_safe_checkpoint"]).read_bytes() == original_checkpoint
    assert "reporter" not in state["role_outputs"]
    assert consume_resume_repairs(cycle, state) is None
    write_json(cycle / "recovery_control.json", {"action": "pause"})
    with pytest.raises(ValueError, match="Pending operator control"):
        consume_resume_repairs(cycle, state)
    assert "verified compact-prompt repair" in resume_blocker(state, contract_projection_repair=receipt)


def test_unrelated_log_append_does_not_stale_repair_but_tampered_prompt_does(overflow):
    cycle, state, _ = overflow
    receipt = validate_reporter_input_reentry(cycle, state, engine=_ENGINE)
    with pytest.raises(ValueError, match="authorized Supervisor repair"):
        consume_resume_repairs(cycle, state)
    events = cycle / "runtime_events.jsonl"
    with events.open("a") as stream:
        stream.write('{"event": "OBSERVER_STARTED"}\n')
    assert inspect_repair_receipt(cycle, state) == receipt
    events.write_text(events.read_text().replace("0001-summary", "0999-summary", 1))
    with pytest.raises(ValueError, match="invocation input hash"):
        inspect_repair_receipt(cycle, state)


@pytest.mark.parametrize("damage", ["checks", "manifest", "commit", "synthesis"])
def test_reporter_overflow_repair_rejects_changed_science_or_failed_attempt(overflow, damage):
    cycle, state, artifact = overflow
    if damage == "checks":
        state["role_outputs"]["judge"]["committed"] = "changed"
    elif damage == "manifest":
        artifact.write_text("{}")
    elif damage == "synthesis":
        write_json(cycle / "cycle_synthesis_out.json", {})
    else:
        ledger = InvocationLedger(cycle / "invocations.jsonl")
        ledger.transition(state["last_invocation_id"], "committed", commit_state="committed")
    with pytest.raises(ValueError):
        validate_reporter_input_reentry(cycle, state, engine=_ENGINE)
