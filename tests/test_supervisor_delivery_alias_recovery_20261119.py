"""A completed original Goal may recover only its exact polluted-Setup delivery."""
from __future__ import annotations

import copy
import hashlib
import json
import shlex
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from openai_codex.generated.v2_all import ThreadGoal, ThreadGoalStatus

from theory_master.invocations import InvocationLedger
from theory_master.native_goal import native_goal_setup_turn
from theory_master.native_goal_recovery import find_native_goal_origin, goal_identity
from theory_master.runtime import write_json
from theory_master.supervisor_delivery_alias_recovery import (
    _authored_python_write, _evidence, prepare_completed_delivery_alias,
    reprocess_completed_delivery_alias, VALIDATOR,
)


@pytest.fixture
def alias_case(tmp_path, monkeypatch):
    import test_native_goal_quota_auto_adjust_20261010 as fixture
    from theory_master.contract_stages import role_contract
    from theory_master.invocation_artifacts import InvocationArtifactTracker
    from theory_master.observability.collector import Collector
    from theory_master.workflow import Workflow
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("THEORY_MASTER_SUPERVISOR", "0")
    notes = tmp_path / "notes"
    notes.mkdir()
    spec = fixture.NativeGoalQuotaTests().make_spec(notes)
    backend = fixture.GoalQuotaBackend("paused")
    owner = Workflow(spec, backend, collector=Collector(spec))
    state = owner.run()
    cycle = owner.cycle_dir
    from theory_master import supervisor_delivery_alias_recovery as recovery
    production_scope = {key: getattr(recovery, key) for key in (
        "_AUDITED_CYCLE", "_AUDITED_TASK_DIRECTORY", "_AUDITED_SOURCE_GIT_SHA", "_OLD_INVESTIGATOR_SCHEMA")}
    monkeypatch.setattr(recovery, "_AUDITED_CYCLE", cycle.name)
    monkeypatch.setattr(recovery, "_AUDITED_TASK_DIRECTORY", cycle.parent.parent.name)
    monkeypatch.setattr(recovery, "_AUDITED_SOURCE_GIT_SHA", state["runtime_source_provenance"]["sdk_git_sha"])
    # This is a synthetic current-contract incident, not the private historical
    # record. Keep its recorded invocation contract and the proof policy aligned.
    monkeypatch.setattr(recovery, "_OLD_INVESTIGATOR_SCHEMA", role_contract("investigator", phase="investigate").schema_hash)
    ledger = InvocationLedger(cycle / "invocations.jsonl")
    original = [r for r in ledger.snapshots() if r.get("role") == "investigator"][-1]
    contract = role_contract("investigator", phase="investigate")
    state["recovery"] = {"action": "resume"}
    session = SimpleNamespace(state=state, owner=owner, invocation_ledger=ledger,
        invocation_id="inv-alias-fixture-probe", stage="investigate", logical_key=original["logical_key"],
        identifier=original["thread_id"], contract=contract,
        handoff_path=cycle / "workspace/.tm-handoff/inv-alias-fixture-probe.json")
    origin = find_native_goal_origin(session)
    starts = [json.loads(line) for line in (cycle / "events.jsonl").read_text().splitlines()]
    began = next(row["at"] for row in starts if row.get("type") == "role.started"
                 and row["payload"].get("invocation_id") == original["invocation_id"])
    time = datetime.fromisoformat(began)
    stamp = lambda delta: (time + timedelta(seconds=delta)).isoformat(timespec="seconds")
    wrong_id, monitor = "inv-1-investigator-polluted", "inv-1-investigator-monitor"
    path = cycle / "workspace/.tm-handoff" / (wrong_id + ".json")
    initial = ThreadGoal(thread_id=origin["thread_id"], objective=origin["objective"].strip(),
        status=ThreadGoalStatus.active, created_at=int(time.timestamp()), updated_at=int(time.timestamp()),
        tokens_used=100, time_used_seconds=10, token_budget=900000)
    for invocation in [wrong_id, monitor]:
        ledger.prepare(invocation_id=invocation, kind="agent", input_payload={},
            logical_key=original["logical_key"], contract_ref=contract.descriptor(), role="investigator", stage="investigate")
        ledger.transition(invocation, "running", thread_id=origin["thread_id"])
    ledger.transition(wrong_id, "failed", error_type="InvalidRequestError", failure_phase="invoke",
        error="JSON-RPC error -32600: thread must be idle before starting a goal: " + origin["thread_id"])
    ledger.transition(monitor, "running", native_goal_origin=origin, native_goal_identity=goal_identity(initial))
    tracker = InvocationArtifactTracker.start(cycle_dir=cycle, workspace=cycle / "workspace",
        invocation_id=monitor, role="investigator", stage="investigate")
    (cycle / "workspace/result.txt").write_text("Original computed artifact, unchanged by recovery.\n")
    authored = {"findings": [{"statement": "The original authored fixture claim.",
                              "method": "Read the saved local computation.", "result": {"value": 1}}]}
    path.write_text(json.dumps(authored, ensure_ascii=False, indent=2))
    manifest = tracker.finalize(status="interrupted")
    ledger.transition(monitor, "interrupted", failure_phase="invoke", reason="SDK handoff invalid: explicit handoff file does not exist or is a symlink",
        invocation_artifact_manifest=str(manifest))
    with (cycle / "events.jsonl").open("a") as stream:
        for invocation, delta in [(wrong_id, 10), (monitor, 20)]:
            stream.write(json.dumps({"type": "role.started", "at": stamp(delta), "payload": {
                "invocation_id": invocation, "thread_id": origin["thread_id"],
                "role": "investigator", "stage": "investigate"}}) + "\n")
    polluted = origin["setup_context"].replace(origin["invocation_id"], wrong_id)
    relative = path.relative_to(cycle / "workspace").as_posix()
    script = ("import json\nfrom pathlib import Path\np=Path(" + repr(relative) + ")\nobj=" + repr(authored)
        + "\np.write_text(json.dumps(obj,ensure_ascii=False,indent=2))\nprint(p)")
    command = "/task/python - <<'PY'\n" + script + "\nPY"
    detail = shlex.join(["/usr/bin/bash", "-lc", command])
    turn = "original-physical-turn"
    rows = [
        {"ts": stamp(11), "event": "AGENT_INPUT", "role": "investigator", "stage": "investigate",
         "input_kind": "native_goal_setup_turn", "content": native_goal_setup_turn(polluted, origin["objective"])},
        {"ts": stamp(11), "event": "AGENT_INPUT", "role": "investigator", "stage": "investigate",
         "input_kind": "native_goal_objective", "content": origin["objective"]},
        {"ts": stamp(25), "event": "TOOL_ACTIVITY", "role": "investigator", "tool": "command",
         "thread_id": origin["thread_id"], "turn_id": turn, "detail": detail},
        {"ts": stamp(30), "event": "LLM_TURN_COMPLETED", "role": "investigator",
         "thread_id": origin["thread_id"], "turn_id": turn, "error": None},
    ]
    with (cycle / "runtime_events.jsonl").open("a") as stream:
        stream.write("".join(json.dumps(row) + "\n" for row in rows))
    complete = initial.model_copy(update={"status": ThreadGoalStatus.complete, "tokens_used": 200,
                                           "time_used_seconds": 20})
    state.update(status="needs_attention", phase="investigator:handoff_invalid", execution_active=False,
        supervisor_runtime_resume_count=1,
        active_invocation_id=None, last_invocation_id=monitor, scientific_verdict=None,
        attention={"code": "investigator_handoff_invalid", "reason": "explicit handoff file does not exist or is a symlink",
                   "thread_id": origin["thread_id"]},
        last_invocation_failure={"invocation_id": monitor, "role": "investigator", "stage": "investigate",
                                 "failure_phase": "invoke", "error_type": "RecoveryStop"},
        native_goal={"status": "complete", "thread_id": origin["thread_id"],
                     "origin_binding": {k: v for k, v in origin.items() if k not in {"objective", "setup_context"}},
                     "persistent_goal": goal_identity(complete)})
    state.setdefault("threads", {})["investigator"] = origin["thread_id"]
    from theory_master.supervisor_native_goal_recovery import reentry_key
    state["recovery"] = {"action": "error", "kind": "InvalidRequestError",
                         "message": "JSON-RPC error -32600: thread must be idle before starting a goal: " + origin["thread_id"]}
    state["supervisor_native_goal_reentries"] = [reentry_key(state)]
    write_json(cycle / "research_state.json", state)
    monkeypatch.setattr("theory_master.supervisor_native_goal_recovery.read_existing_goal", lambda *_: complete)
    monkeypatch.setattr("theory_master.supervisor_repair_receipts.source_manifest", lambda _: {"alias-source": "frozen"})
    return SimpleNamespace(production_scope=production_scope, spec=spec, cycle=cycle, state=state, origin=origin, path=path, monitor=monitor,
        wrong_id=wrong_id, owner=owner, complete=complete, detail=detail, rows=rows, backend=backend)


def test_complete_alias_receipt_and_formal_offline_commit_preserve_authored_bytes(alias_case):
    c = alias_case
    before = c.path.read_bytes()
    frozen = copy.deepcopy(c.state["hypothesis_lock"])
    manifest = c.cycle / "invocation_artifacts" / (c.monitor + ".json")
    old_inventory = manifest.read_bytes()
    inventory = json.loads(old_inventory)
    scientific_bytes = {row["path"]: Path(row["path"]).read_bytes()
                        for row in inventory["created"] + inventory["modified"]}
    old_ledger = (c.cycle / "invocations.jsonl").read_bytes()
    receipt = prepare_completed_delivery_alias(c.spec.config_path)
    assert receipt["validator"] == VALIDATOR
    assert json.loads(receipt["tests"])["declared_handoff_path"] == c.origin["handoff_path"]
    result = reprocess_completed_delivery_alias(c.spec.config_path)
    assert result["extra_model_requests"] == 0
    assert c.path.read_bytes() == before and not Path(c.origin["handoff_path"]).exists()
    assert all(Path(path).read_bytes() == data for path, data in scientific_bytes.items())
    state = json.loads((c.cycle / "research_state.json").read_text())
    assert state["status"] == "paused" and state["phase"] == "engineering_delivery_reprocessed"
    assert state["hypothesis_lock"] == frozen and state["scientific_verdict"] is None
    assert not state.get("role_outputs", {}).get("judge")
    assert state["investigation_round_count"] == c.state["investigation_round_count"]
    audit = state["supervisor_delivery_reprocessing"]
    assert audit["delivery_alias"]["handoff_path"] == str(c.path)
    assert audit["delivery_alias"]["declared_handoff_path"] == c.origin["handoff_path"]
    assert Path(audit["original_failed_manifest"]).read_bytes() == old_inventory
    assert (c.cycle / "invocations.jsonl").read_bytes().startswith(old_ledger)
    assert InvocationLedger(c.cycle / "invocations.jsonl").snapshot(c.monitor)["state"] == "committed"
    with pytest.raises(ValueError):
        reprocess_completed_delivery_alias(c.spec.config_path)


@pytest.mark.parametrize("fault", ["blocked", "foreign_goal", "reset_goal", "changed_artifact", "wrong_write_thread",
    "wrong_write_turn", "no_write", "provider_error", "changed_setup", "wrong_contract", "canonical_exists",
    "symlink", "pause", "unknown_control", "budget", "protocol", "stale_receipt"])
def test_alias_proof_fails_closed_without_any_delivery_rewrite(alias_case, monkeypatch, fault):
    c = alias_case
    state_path = c.cycle / "research_state.json"
    if fault in {"blocked", "foreign_goal", "reset_goal"}:
        changes = {"blocked": {"status": ThreadGoalStatus.blocked}, "foreign_goal": {"objective": "foreign science"},
                   "reset_goal": {"created_at": c.complete.created_at + 1}}[fault]
        monkeypatch.setattr("theory_master.supervisor_native_goal_recovery.read_existing_goal", lambda *_: c.complete.model_copy(update=changes))
    elif fault == "changed_artifact": (c.cycle / "workspace/result.txt").write_text("changed")
    elif fault in {"wrong_write_thread", "wrong_write_turn", "no_write", "provider_error", "changed_setup"}:
        path = c.cycle / "runtime_events.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for row in rows:
            if row.get("event") == "TOOL_ACTIVITY" and row.get("detail") == c.detail:
                if fault == "wrong_write_thread": row["thread_id"] = "foreign"
                elif fault == "wrong_write_turn": row["turn_id"] = "foreign"
                elif fault == "no_write": row["detail"] = "file was written"
            if fault == "changed_setup" and row.get("input_kind") == "native_goal_setup_turn" and c.wrong_id in row.get("content", ""):
                row["content"] = row["content"].replace(c.wrong_id, "inv-foreign")
        if fault == "provider_error": rows.append({"ts": c.rows[-1]["ts"], "event": "LLM_FAILED", "error": "provider failure"})
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    elif fault == "wrong_contract":
        InvocationLedger(c.cycle / "invocations.jsonl").transition(c.monitor, "interrupted", contract_ref={})
    elif fault == "canonical_exists": Path(c.origin["handoff_path"]).write_text(c.path.read_text())
    elif fault == "symlink":
        raw = c.path.read_bytes(); c.path.unlink(); target = c.path.parent / "other.json";target.write_bytes(raw);c.path.symlink_to(target)
    elif fault == "pause": write_json(c.cycle / "recovery_control.json", {"action": "pause"})
    elif fault == "unknown_control": write_json(c.cycle / "recovery_control.json", {"action": "future-control"})
    elif fault in {"budget", "protocol"}:
        state = json.loads(state_path.read_text())
        if fault == "budget":state["investigator_budget"]["rejected"] = True
        else:state["protocol_health"] = "violation"
        write_json(state_path, state)
    elif fault == "stale_receipt":
        prepare_completed_delivery_alias(c.spec.config_path)
        c.path.write_text(c.path.read_text() + " ")
        with pytest.raises(ValueError):reprocess_completed_delivery_alias(c.spec.config_path)
        return
    before = state_path.read_bytes()
    with pytest.raises((ValueError, OSError, RuntimeError)):
        prepare_completed_delivery_alias(c.spec.config_path)
    assert state_path.read_bytes() == before
    assert InvocationLedger(c.cycle / "invocations.jsonl").snapshot(c.monitor)["commit_state"] == "not_committed"


def test_recorded_command_is_literal_evidence_never_an_executable_repair(alias_case):
    c = alias_case
    relative = c.path.relative_to(c.cycle / "workspace").as_posix()
    assert _authored_python_write(c.detail, relative, c.path.read_bytes())
    assert not _authored_python_write(c.detail.replace("obj=", "obj=__import__('os').system('false') or "), relative, c.path.read_bytes())
    assert not _authored_python_write(c.detail, ".tm-handoff/foreign.json", c.path.read_bytes())


def test_new_pause_after_receipt_blocks_commit_without_changing_authored_delivery(alias_case):
    c = alias_case
    prepare_completed_delivery_alias(c.spec.config_path)
    write_json(c.cycle / "recovery_control.json", {"action": "pause"})
    state_before = (c.cycle / "research_state.json").read_bytes()
    handoff_before = c.path.read_bytes()
    ledger_before = (c.cycle / "invocations.jsonl").read_bytes()
    with pytest.raises(ValueError, match="control"):
        reprocess_completed_delivery_alias(c.spec.config_path)
    assert (c.cycle / "research_state.json").read_bytes() == state_before
    assert (c.cycle / "invocations.jsonl").read_bytes() == ledger_before
    assert c.path.read_bytes() == handoff_before


def test_synthetic_incident_is_refused_by_the_production_scope(alias_case, monkeypatch):
    from theory_master import supervisor_delivery_alias_recovery as recovery
    case = alias_case
    for key, value in case.production_scope.items():
        monkeypatch.setattr(recovery, key, value)
    before = (case.cycle / "research_state.json").read_bytes()
    with pytest.raises(ValueError, match="Not an unclosed"):
        prepare_completed_delivery_alias(case.spec.config_path)
    assert (case.cycle / "research_state.json").read_bytes() == before
