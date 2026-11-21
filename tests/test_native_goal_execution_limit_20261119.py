"""Invocation-bound input limitations close execution without scientific adjudication.

All source files/tasks are disposable fixtures; no provider or scientific task runs.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from openai_codex._goal import _GoalOperationState
from openai_codex.generated.v2_all import (
    ThreadGoal, ThreadGoalStatus, ThreadGoalUpdatedNotification, Turn,
    TurnCompletedNotification, TurnStartedNotification, TurnStatus,
)
from openai_codex.models import Notification, UnknownNotification

from theory_master.native_goal import (
    EXECUTION_LIMIT_PROTOCOL_MARKER, EXECUTION_LIMIT_SCHEMA,
    NativeGoalDeliveryError, NativeGoalExecutionLimit, NativeGoalIncomplete, NativeGoalResult,
    NativeGoalBudgetExceeded, execution_limit_setup_binding, investigator_goal,
    read_execution_limit_handoff, run_native_goal,
)
from theory_master.scientific_objective import frozen_science_digest
from theory_master.supervisor_incident import NativeGoalRuntimeFailure
from theory_master.workflow import Workflow
import test_native_goal_quota_auto_adjust_20261010 as quota_fixture
from test_provider_capacity_reconnect_20261012 import backend_fixture


SCIENCE = {"hypothesis": "Night-only parameters are identifiable", "scope": "The two supplied files",
           "decisive_test": "Apply exact-zero irradiance and fit eight parameters with valid support."}


def _delivery(workspace: Path, invocation="inv-current", science=None, *, kind="input_support_unavailable",
              authorization_clause="", deliverable_clause=""):
    workspace.mkdir(parents=True, exist_ok=True)
    evidence = workspace / "gate.json"
    evidence.write_text('{"eligible_observations":[1,0],"source_recovery":"unavailable"}\n')
    handoff = workspace / ".tm-handoff" / (invocation + ".json")
    handoff.parent.mkdir(exist_ok=True)
    digest = frozen_science_digest(science or SCIENCE)
    limit = {
        "kind": kind, "invocation_id": invocation,
        "frozen_objective_sha256": digest,
        "reason": "The exact frozen gate leaves one/zero eligible observations for eight parameters.",
        "checked_alternatives": ["Verified both manifest hashes and independently counted eligibility.",
                                 "The authorized source archive contains only the same two files."],
        "unexecuted_test": "Parameter fits and uncertainty estimates could not execute.",
        "required_dependency": "Authorized source input with sufficient qualifying observations.",
        "evidence": [{"path": "gate.json", "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}],
    }
    if kind == "authorization_pending":
        limit.update(authorization_clause=authorization_clause,
                     deliverable_clause=deliverable_clause)
    handoff.write_text(json.dumps({"execution_limit": limit}))
    binding = {"schema": EXECUTION_LIMIT_SCHEMA, "invocation_id": invocation,
               "frozen_objective_sha256": digest, "workspace_path": str(workspace),
               "handoff_path": str(handoff)}
    return handoff, limit, binding


def _read(path, binding, status="blocked", *, fixture_inputs=None):
    return read_execution_limit_handoff(
        NativeGoalResult(status, "scientific-thread", 3, 4), path,
        invocation_id=binding["invocation_id"],
        frozen_objective_sha256=binding["frozen_objective_sha256"],
        workspace_path=Path(binding["workspace_path"]),
        fixture_inputs=fixture_inputs,
    )


def _started(thread="scientific-thread", turn="physical-1"):
    return Notification("turn/started", TurnStartedNotification(
        thread_id=thread, turn=Turn(id=turn, items=[], status=TurnStatus.in_progress)))


def _completed(thread="scientific-thread", turn="physical-1", error=None):
    return Notification("turn/completed", TurnCompletedNotification(
        thread_id=thread, turn=Turn(id=turn, items=[], status=TurnStatus.completed, error=error)))


def _goal_update(status):
    return Notification("thread/goal/updated", ThreadGoalUpdatedNotification(
        thread_id="scientific-thread", goal=ThreadGoal(
            thread_id="scientific-thread", objective="frozen goal", status=status,
            created_at=1, updated_at=2, time_used_seconds=0, tokens_used=0)))


def _official_stream(thread, events):
    """Use the installed SDK cursor/stream/state; replace only its server I/O."""
    state = _GoalOperationState(thread.id, logical_turn_id="logical-operation", status=ThreadGoalStatus.active)
    incoming = iter(events)
    calls, consumed = [], []
    server_goal = ThreadGoal(thread_id=thread.id, objective="frozen goal", status=ThreadGoalStatus.active,
                             created_at=1, updated_at=2, tokens_used=0, time_used_seconds=0)
    def next_event(_state):
        event = next(incoming)
        if isinstance(event.payload, ThreadGoalUpdatedNotification):
            state.status = event.payload.goal.status  # Normally updated by the SDK router.
            server_goal.status = event.payload.goal.status
        consumed.append(event)
        return event
    thread._client.start_goal_operation = lambda tid, objective: (state, "logical-operation")
    thread._client.next_goal_notification = next_event
    def cancel(_state):
        calls.append("official_cancel")
        server_goal.status = ThreadGoalStatus.paused
    thread._client.cancel_goal_operation = cancel
    thread._client.unregister_goal_operation = lambda _state: calls.append("official_unregister")
    thread._client.request = lambda method, params, **kwargs: SimpleNamespace(goal=server_goal)
    thread._client.thread_read = lambda tid, **kwargs: SimpleNamespace(thread=SimpleNamespace(
        id=tid, status=SimpleNamespace(root=SimpleNamespace(type="idle")), turns=[]))
    thread.run = MagicMock()
    return state, calls, consumed


@pytest.mark.parametrize("status", ["complete", "blocked"])
def test_exact_science_and_existing_source_bytes_are_required(tmp_path, status):
    handoff, limit, binding = _delivery(tmp_path / "workspace")
    before = handoff.read_bytes()
    assert _read(handoff, binding, status) == limit
    assert handoff.read_bytes() == before
    assert execution_limit_setup_binding(json.dumps({"frozen_objective": SCIENCE,
        "sdk_execution_limit_handoff": binding}), handoff) == binding


@pytest.mark.parametrize("mutation", ["invocation", "science", "kind", "missing_audit", "missing_evidence",
    "changed_bytes", "missing_file", "empty_file", "outside", "noncanonical", "file_symlink",
    "directory_symlink", "workspace_symlink", "handoff_symlink", "mixed_findings", "oversize", "directory"])
def test_invalid_or_unsafe_execution_limit_is_not_an_exit(tmp_path, mutation):
    workspace = tmp_path / "workspace"
    handoff, limit, binding = _delivery(workspace)
    payload = {"execution_limit": limit}
    source = workspace / "gate.json"
    if mutation == "invocation":
        limit["invocation_id"] = "previous-invocation"
    elif mutation == "science":
        limit["frozen_objective_sha256"] = "0" * 64
    elif mutation == "kind":
        limit["kind"] = "method_too_expensive"
    elif mutation == "missing_audit":
        limit["checked_alternatives"] = []
    elif mutation == "missing_evidence":
        limit["evidence"] = []
    elif mutation == "changed_bytes":
        source.write_text("different source")
    elif mutation == "missing_file":
        source.unlink()
    elif mutation == "empty_file":
        source.write_bytes(b"")
    elif mutation == "outside":
        limit["evidence"][0]["path"] = "../outside.json"
        (tmp_path / "outside.json").write_bytes(source.read_bytes())
    elif mutation == "noncanonical":
        limit["evidence"][0]["path"] = "./gate.json"
    elif mutation == "file_symlink":
        (workspace / "real.json").write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(workspace / "real.json")
    elif mutation == "directory_symlink":
        (workspace / "linked").symlink_to(workspace, target_is_directory=True)
        limit["evidence"][0]["path"] = "linked/gate.json"
    elif mutation == "workspace_symlink":
        alias = tmp_path / "workspace_alias"
        alias.symlink_to(workspace, target_is_directory=True)
        binding["workspace_path"] = str(alias)
        handoff = alias / ".tm-handoff" / handoff.name
    elif mutation == "handoff_symlink":
        (workspace / "real_handoff.json").write_text(json.dumps(payload))
        handoff.unlink()
        handoff.symlink_to(workspace / "real_handoff.json")
    elif mutation == "mixed_findings":
        payload["findings"] = [{"statement": "must not adjudicate an unperformed test"}]
    elif mutation == "oversize":
        with source.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
    elif mutation == "directory":
        source.unlink()
        source.mkdir()
    if mutation != "handoff_symlink":
        handoff.write_text(json.dumps(payload))
    with pytest.raises(NativeGoalDeliveryError):
        _read(handoff, binding)


def test_other_native_stops_and_unstructured_reports_cannot_authorize_limit(tmp_path):
    handoff, _, binding = _delivery(tmp_path / "workspace")
    for status in ("usageLimited", "budgetLimited", "paused", "active", "unknown"):
        with pytest.raises(NativeGoalIncomplete):
            _read(handoff, binding, status)
    handoff.write_text('{"findings":[{"statement":"ordinary completed science"}]}')
    assert _read(handoff, binding) is None
    handoff.unlink()
    assert _read(handoff, binding) is None


def test_authorization_hold_requires_exact_reviewed_plan_clauses(tmp_path):
    contract = "The numerical test requires separate operator authorization."
    final_gate = "Deliver a reviewable method plan before that authorization."
    handoff, limit, binding = _delivery(
        tmp_path / "workspace", kind="authorization_pending",
        authorization_clause=contract, deliverable_clause=final_gate,
    )
    assert _read(handoff, binding) == limit
    receiver = SimpleNamespace(
        spec=SimpleNamespace(contract="This is an unrelated contract.", final_gate=final_gate),
        cycle_workspace=handoff.parent.parent,
    )
    state = {
        "native_goal": {"status": "blocked"}, "hypothesis_lock": SCIENCE,
        "execution_limit_handoff": {
            "thread_id": "thread", "handoff_path": str(handoff),
            "invocation_id": binding["invocation_id"], "execution_limit": limit,
        },
    }
    with pytest.raises(ValueError, match="reviewed Research Contract"):
        Workflow._close_delivered_execution_limit(receiver, state, {}, {})
    payload = json.loads(handoff.read_text())
    del payload["execution_limit"]["deliverable_clause"]
    handoff.write_text(json.dumps(payload))
    with pytest.raises(NativeGoalDeliveryError):
        _read(handoff, binding)


@pytest.mark.parametrize("status", ["complete", "blocked"])
def test_backend_consumes_formal_limit_without_findings_or_extra_model_turn(tmp_path, status):
    handoff, limit, binding = _delivery(tmp_path / "workspace")
    backend, thread, cleared = backend_fixture()
    thread.run = MagicMock()
    with patch("theory_master.native_goal.run_native_goal",
               return_value=NativeGoalResult(status, thread.id, 3, 5)) as goal, \
         patch("theory_master.native_goal.read_goal_findings") as findings:
        with pytest.raises(NativeGoalExecutionLimit) as caught:
            backend.invoke_goal("investigator", goal_objective="frozen goal",
                context_prompt=json.dumps({"sdk_execution_limit_handoff": binding}),
                handoff_path=str(handoff), thread_id=thread.id, model="Fake", provider="Fake")
    assert caught.value.execution_limit == limit
    assert caught.value.thread_id == thread.id
    assert cleared == [thread.id]
    goal.assert_called_once()
    findings.assert_not_called()


def test_normal_completed_findings_keep_their_original_route_with_new_setup(tmp_path):
    handoff, _, binding = _delivery(tmp_path / "workspace")
    handoff.write_text('{"findings":[{"claim":"Completed comparison under the frozen scope."}]}')
    backend, thread, cleared = backend_fixture()
    thread.run = MagicMock()
    with patch("theory_master.native_goal.run_native_goal",
               return_value=NativeGoalResult("complete", thread.id, 2, 3)):
        identifier, raw = backend.invoke_goal(
            "investigator", goal_objective="frozen goal",
            context_prompt=json.dumps({"sdk_execution_limit_handoff": binding}),
            handoff_path=str(handoff), thread_id=thread.id, model="Fake", provider="Fake")
    assert identifier == thread.id and cleared == [thread.id]
    assert json.loads(raw) == {"findings": [{"statement": "Completed comparison under the frozen scope."}]}
    assert "execution_limit_kind" not in backend.last_native_goal


@pytest.mark.parametrize("failure", ["terminal_provider", "capacity", "invalid_handoff", "no_binding", "missing_handoff"])
def test_blocked_runtime_errors_do_not_take_execution_limit_exit(tmp_path, failure):
    handoff, limit, binding = _delivery(tmp_path / "workspace")
    backend, thread, cleared = backend_fixture()
    thread.run = MagicMock()
    if failure == "invalid_handoff":
        limit["invocation_id"] = "old"
        handoff.write_text(json.dumps({"execution_limit": limit}))
    if failure == "missing_handoff":
        handoff.unlink()
    def blocked(_thread, _objective, *, on_event, on_turn_boundary=None, **kwargs):
        if failure in {"terminal_provider", "capacity"}:
            on_event(SimpleNamespace(method="error", payload=None))
        return NativeGoalResult("blocked", thread.id, 3, 5, "Input is missing; this prose grants no authority")
    error = "Selected model is at capacity." if failure == "capacity" else "stream disconnected before completion"
    with patch("theory_master.native_goal.run_native_goal", side_effect=blocked) as goal, \
         patch("theory_master.observability.codex_events.map_notification",
               return_value={"event": "LLM_FAILED", "error": error}), \
         patch("theory_master.native_goal.read_goal_findings") as findings:
        with pytest.raises((NativeGoalRuntimeFailure, NativeGoalIncomplete)):
            backend.invoke_goal("investigator", goal_objective="frozen goal",
                context_prompt="" if failure == "no_binding" else json.dumps({"sdk_execution_limit_handoff": binding}),
                handoff_path=str(handoff), thread_id=thread.id, model="Fake", provider="Fake")
    goal.assert_called_once()
    findings.assert_not_called()
    assert cleared == []
    assert "execution_limit_kind" not in backend.last_native_goal


@pytest.mark.parametrize("sdk_status", ["blocked", "execution_limited"])
@pytest.mark.parametrize("kind", ["input_support_unavailable", "authorization_pending"])
def test_actual_invocation_and_closure_keep_model_and_judge_unmodified(tmp_path, sdk_status, kind):
    authorization_clause = "The numerical test requires separate operator authorization."
    deliverable_clause = "Deliver a reviewable method plan before that authorization."
    class LimitBackend(quota_fixture.GoalQuotaBackend):
        def invoke_goal(self, role, *, context_prompt, handoff_path, thread_id, **kwargs):
            self.started.append(thread_id)
            context, _ = json.JSONDecoder().raw_decode(context_prompt)
            binding = context["sdk_execution_limit_handoff"]
            assert EXECUTION_LIMIT_PROTOCOL_MARKER in kwargs["goal_objective"]
            assert "Method failure, scientific difficulty, cost alone, or unfinished authorized work" in context_prompt
            path, limit, _ = _delivery(Path(binding["workspace_path"]), binding["invocation_id"],
                                       context["frozen_objective"], kind=kind,
                                       authorization_clause=authorization_clause,
                                       deliverable_clause=deliverable_clause)
            assert str(path) == handoff_path
            verified = _read(path, binding)
            self.last_native_goal = {"status": sdk_status, "thread_id": thread_id,
                                     "execution_limit_kind": verified["kind"]}
            if sdk_status == "execution_limited":
                self.last_native_goal.update(source="sdk_turn_boundary_handoff",
                                             native_goal_cancel_requested=True)
            raise NativeGoalExecutionLimit(thread_id, verified, path)

        def invoke(self, role, prompt, thread_id, model, provider):
            assert role not in {"investigator", "judge", "reporter"}
            return super().invoke(role, prompt, thread_id, model, provider)

    spec = replace(quota_fixture.NativeGoalQuotaTests().make_spec(tmp_path), max_cycles=1)
    if kind == "authorization_pending":
        spec = replace(spec, contract=authorization_clause,
                       final_gate=deliverable_clause)
    config = json.loads(spec.config_path.read_text())
    config["max_cycles"] = 1
    spec.config_path.write_text(json.dumps(config))
    backend = LimitBackend("blocked")
    workflow = Workflow(spec, backend)
    state = workflow.run()
    assert len(backend.started) == 1
    assert state["status"] == "over" and not state["resumable"]
    assert state["scientific_verdict"] is None
    assert state["assessment_state"] == "not_adjudicated"
    assert not state["role_outputs"].get("investigator")
    assert not state["role_outputs"].get("judge")
    assert state["scientific_model_version_in"] == state["scientific_model_version_out"]
    assert state["scientific_model_patch_status"] == "kept"
    closure = json.loads((workflow.cycle_dir / "operational_closure.json").read_text())
    assert closure["kind"] == kind
    assert state["resource_disposition"] == kind
    assert closure["execution_limit"] == state["execution_limit_handoff"]["execution_limit"]
    assert not (workflow.cycle_dir / "evidence/registry.jsonl").exists()
    ledger = [json.loads(line) for line in (workflow.cycle_dir / "invocations.jsonl").read_text().splitlines()]
    limited = [row for row in ledger if row.get("execution_limit")]
    assert limited[-1]["state"] == "interrupted"
    assert limited[-1]["execution_limit"] == closure["execution_limit"]
    unchanged = copy.deepcopy(state["hypothesis_lock"])
    attempt = state["investigation_attempts"][f"attempt-{state['investigation_attempt']:04d}"]
    result, _ = workflow._close_delivered_execution_limit(state, {}, attempt)
    assert result["verdict"] is None and state["hypothesis_lock"] == unchanged


def test_physical_turn_boundary_consumes_limit_before_goal_auto_continues(tmp_path):
    handoff, limit, binding = _delivery(tmp_path / "workspace")
    backend, thread, cleared = backend_fixture()
    events = [_started(), _goal_update(ThreadGoalStatus.active), _completed(), _started(turn="physical-2")]
    state, calls, consumed = _official_stream(thread, events)
    with pytest.raises(NativeGoalExecutionLimit) as caught:
        backend.invoke_goal("investigator", goal_objective="frozen goal",
            context_prompt=json.dumps({"sdk_execution_limit_handoff": binding}),
            handoff_path=str(handoff), thread_id=thread.id, model="Fake", provider="Fake")
    assert caught.value.execution_limit == limit
    assert consumed == events[:3]  # No second physical turn or three-turn blocked audit.
    assert calls == ["official_cancel", "official_unregister"]
    assert state.is_finished() and state.status == ThreadGoalStatus.active
    assert cleared == []
    assert backend.last_native_goal["status"] == "execution_limited"
    assert backend.last_native_goal["native_goal_status"] == "paused"
    assert backend.last_native_goal["native_goal_stop_confirmed"]
    assert backend.last_native_goal["native_goal_cancel_requested"]
    assert backend.last_native_goal["boundary_turn_id"] == "physical-1"
    observed = [call.args[0]["event"] for call in backend.collector.on_codex.call_args_list
                if call.args[0].get("turn_id") != "setup-turn"]
    assert "LLM_TURN_COMPLETED" not in observed  # Do not alter logical Collector semantics.



def _registered_fixture(path):
    path.parent.mkdir(parents=True)
    data = b"time,state\n0,1\n"
    path.write_bytes(data)
    return {"reference": "benchmark/inputs/observed.csv", "path": str(path),
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "status": "available"}


def test_file_audit_rereads_the_pinned_source_and_separates_scientific_eligibility(tmp_path):
    handoff, limit, binding = _delivery(tmp_path / "workspace")
    fixture = tmp_path / "fixture" / "inputs" / "observed.csv"
    row = _registered_fixture(fixture)
    with pytest.raises(NativeGoalDeliveryError, match="input_support_audit"):
        _read(handoff, binding, fixture_inputs=[row])
    audit = {"mode": "missing_fixture_files", "fixture_references": [row["reference"]]}
    limit["input_support_audit"] = audit
    handoff.write_text(json.dumps({"execution_limit": limit}))
    with pytest.raises(NativeGoalDeliveryError, match="available at its pinned path"):
        _read(handoff, binding, fixture_inputs=[row])

    fixture.unlink()
    assert _read(handoff, binding, fixture_inputs=[row]) == limit
    audit["mode"] = "insufficient_eligible_support"
    handoff.write_text(json.dumps({"execution_limit": limit}))
    with pytest.raises(NativeGoalDeliveryError, match="not available for the eligibility audit"):
        _read(handoff, binding, fixture_inputs=[row])
    fixture.write_bytes(b"time,state\n0,1\n")
    assert _read(handoff, binding, fixture_inputs=[row]) == limit

    audit["fixture_references"] = ["benchmark/inputs/unregistered.csv"]
    handoff.write_text(json.dumps({"execution_limit": limit}))
    with pytest.raises(NativeGoalDeliveryError, match="outside the SDK fixture inventory"):
        _read(handoff, binding, fixture_inputs=[row])


@pytest.mark.parametrize("terminal", [ThreadGoalStatus.blocked, ThreadGoalStatus.complete])
def test_ignored_but_available_fixture_cannot_cancel_native_goal(tmp_path, terminal):
    handoff, limit, binding = _delivery(tmp_path / "workspace")
    row = _registered_fixture(tmp_path / "ignored_benchmark" / "inputs" / "observed.csv")
    (tmp_path / ".gitignore").write_text("*\n")
    limit["input_support_audit"] = {"mode": "missing_fixture_files", "fixture_references": [row["reference"]]}
    handoff.write_text(json.dumps({"execution_limit": limit}))
    backend, thread, cleared = backend_fixture()
    events = [_started(), _goal_update(ThreadGoalStatus.active), _completed(), _goal_update(terminal)]
    _, calls, consumed = _official_stream(thread, events)
    setup = {"sdk_execution_limit_handoff": binding, "registered_fixture_inputs": [row]}
    with pytest.raises(NativeGoalRuntimeFailure):
        backend.invoke_goal("investigator", goal_objective="frozen goal", context_prompt=json.dumps(setup),
                            handoff_path=str(handoff), thread_id=thread.id, model="Fake", provider="Fake")
    assert consumed == events
    assert calls == ["official_unregister"] and not cleared
    assert backend.last_native_goal["status"] == terminal.value
    assert backend.last_native_goal["runtime_failure_kind"] == "native_goal_fixture_availability_conflict"
    assert backend.last_native_goal["fixture_availability_conflict"] == {
        "invocation_id": binding["invocation_id"], "references": [row["reference"]],
        "handoff_sha256": hashlib.sha256(handoff.read_bytes()).hexdigest(),
    }
    assert "available at its pinned path" in backend.last_native_goal["execution_limit_delivery_error"]


def test_restored_fixture_remains_a_new_input_during_archive_publication(tmp_path, monkeypatch):
    from theory_master.supervisor_input_support import capture_input_support_baseline

    handoff, limit, binding = _delivery(tmp_path / "workspace")
    fixture = tmp_path / "inputs" / "observed.csv"
    row = _registered_fixture(fixture)
    contents = fixture.read_bytes()
    fixture.unlink()
    limit["input_support_audit"] = {"mode": "missing_fixture_files", "fixture_references": [row["reference"]]}
    handoff.write_text(json.dumps({"execution_limit": limit}))
    backend, thread, _ = backend_fixture()
    events = [_started(), _goal_update(ThreadGoalStatus.active), _completed()]
    _official_stream(thread, events)
    setup = {"sdk_execution_limit_handoff": binding, "registered_fixture_inputs": [row]}
    with pytest.raises(NativeGoalExecutionLimit) as caught:
        backend.invoke_goal("investigator", goal_objective="frozen goal", context_prompt=json.dumps(setup),
                            handoff_path=str(handoff), thread_id=thread.id, model="Fake", provider="Fake")
    fixture.write_bytes(contents)
    monkeypatch.setattr("theory_master.research_inputs.registered_fixture_inputs", lambda spec: [row])
    expected = [{"kind": "fixture_input", "sha256": row["sha256"]},
                {"kind": "supplemental_input", "sha256": "b" * 64}]
    monkeypatch.setattr("theory_master.research_inputs.verified_research_inputs", lambda spec: expected)
    receiver = SimpleNamespace(spec=SimpleNamespace(task_mode=True), cycle_workspace=handoff.parent.parent,
                               _mark_non_scientific_closure=MagicMock())
    state = {"cycle_id": "3", "native_goal": {"status": "execution_limited"}, "hypothesis_lock": SCIENCE,
             "execution_limit_handoff": {"thread_id": thread.id, "handoff_path": str(handoff),
                 "invocation_id": binding["invocation_id"], "execution_limit": limit,
                 "fixture_audit_pins": caught.value.fixture_audit_pins}}
    Workflow._close_delivered_execution_limit(receiver, state, {}, {})
    kwargs = receiver._mark_non_scientific_closure.call_args.kwargs
    assert kwargs["execution_limit"] == limit
    state["operational_only_closure"] = {"kind": limit["kind"], "execution_limit": limit}
    capture_input_support_baseline(receiver.spec, state)
    assert state["input_support_baseline"]["verified_research_input_ids"] == ["b" * 64]

def test_safe_boundary_requires_official_current_thread_and_started_physical_turn(tmp_path):
    handoff, limit, _ = _delivery(tmp_path / "workspace")
    _, thread, _ = backend_fixture()
    unknown = Notification("turn/completed", UnknownNotification({
        "threadId": thread.id, "turn": {"id": "physical-1", "status": "completed", "items": []}}))
    events = [_completed(turn="never-started"), _started(), _goal_update(ThreadGoalStatus.active),
              unknown, _completed(thread="foreign-thread"), _completed(turn="old-turn"), _completed()]
    # The first official completion precedes activation and is rejected by the
    # SDK itself, so inject it through a bound stream after active status exists.
    events = [_goal_update(ThreadGoalStatus.active), *events]
    state, calls, consumed = _official_stream(thread, events)
    boundaries = []
    def boundary(event):
        boundaries.append(event.payload.turn.id)
        raise NativeGoalExecutionLimit(thread.id, limit, handoff)
    with pytest.raises(NativeGoalExecutionLimit):
        run_native_goal(thread, "frozen goal", on_turn_boundary=boundary)
    assert boundaries == ["physical-1"]
    assert consumed == events
    assert calls == ["official_cancel", "official_unregister"] and state.is_finished()


def test_keyboard_interrupt_at_boundary_cancels_the_official_goal_once():
    _, thread, _ = backend_fixture()
    events = [_started(), _goal_update(ThreadGoalStatus.active), _completed()]
    state, calls, _ = _official_stream(thread, events)
    def interrupted(_event):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        run_native_goal(thread, "frozen goal", on_turn_boundary=interrupted)
    assert calls == ["official_cancel", "official_unregister"] and state.is_finished()


@pytest.mark.parametrize("status", [ThreadGoalStatus.usage_limited, ThreadGoalStatus.budget_limited,
                                   ThreadGoalStatus.paused])
def test_native_usage_and_operator_stops_precede_boundary_handoff(tmp_path, status):
    handoff, _, binding = _delivery(tmp_path / "workspace")
    backend, thread, _ = backend_fixture()
    events = [_started(), _goal_update(ThreadGoalStatus.active), _goal_update(status), _completed()]
    _, calls, consumed = _official_stream(thread, events)
    with pytest.raises(NativeGoalIncomplete) as caught:
        backend.invoke_goal("investigator", goal_objective="frozen goal",
            context_prompt=json.dumps({"sdk_execution_limit_handoff": binding}),
            handoff_path=str(handoff), thread_id=thread.id, model="Fake", provider="Fake")
    assert caught.value.status == status.value
    assert consumed == events and calls == ["official_unregister"]
    assert "execution_limit_kind" not in backend.last_native_goal


@pytest.mark.parametrize("case", ["missing", "invalid", "ordinary_findings", "provider_error", "completion_error"])
def test_physical_boundary_never_interrupts_without_safe_valid_input_limit(tmp_path, case):
    handoff, limit, binding = _delivery(tmp_path / "workspace")
    if case == "missing":
        handoff.unlink()
    elif case == "invalid":
        limit["invocation_id"] = "stale"
        handoff.write_text(json.dumps({"execution_limit": limit}))
    elif case == "ordinary_findings":
        handoff.write_text('{"findings":[{"statement":"ordinary scientific handoff"}]}')
    backend, thread, _ = backend_fixture()
    events = [_started(), _goal_update(ThreadGoalStatus.active)]
    if case == "provider_error":
        events.append(Notification("error", UnknownNotification({"error": {"message": "terminal provider failure"}})))
    events.extend([_completed(error={"message": "terminal provider failure"} if case == "completion_error" else None),
                   _goal_update(ThreadGoalStatus.blocked)])
    _, calls, consumed = _official_stream(thread, events)
    with pytest.raises(NativeGoalRuntimeFailure):
        backend.invoke_goal("investigator", goal_objective="frozen goal",
            context_prompt=json.dumps({"sdk_execution_limit_handoff": binding}),
            handoff_path=str(handoff), thread_id=thread.id, model="Fake", provider="Fake")
    assert calls == ["official_unregister"]  # Goal reached its own terminal status.
    assert consumed == events
    assert backend.last_native_goal["status"] == "blocked"
    assert "native_goal_cancel_requested" not in backend.last_native_goal


@pytest.mark.parametrize("tokens_before", [80, 100])
def test_token_guard_precedes_physical_boundary_limit_delivery(tmp_path, tokens_before):
    handoff, _, binding = _delivery(tmp_path / "workspace")
    backend, thread, _ = backend_fixture()
    usage = Notification("thread/tokenUsage/updated", UnknownNotification({"threadId": thread.id}))
    events = [_started(), _goal_update(ThreadGoalStatus.active)]
    if tokens_before == 80:
        events.append(usage)
    events.append(_completed())
    _, calls, _ = _official_stream(thread, events)
    from theory_master.observability.codex_events import map_notification
    def mapped(event, **kwargs):
        if event.method == "thread/tokenUsage/updated":
            return {"event": "TOKEN_USAGE", "raw_usage": {"input_tokens": 20, "output_tokens": 5},
                    "last_request_usage": {"input_tokens": 20, "output_tokens": 5}}
        return map_notification(event, **kwargs)
    with patch("theory_master.observability.codex_events.map_notification", side_effect=mapped), \
         patch("theory_master.native_goal.read_execution_limit_handoff", wraps=read_execution_limit_handoff) as read_limit:
        with pytest.raises(NativeGoalBudgetExceeded):
            backend.invoke_goal("investigator", goal_objective="frozen goal",
                context_prompt=json.dumps({"sdk_execution_limit_handoff": binding}),
                handoff_path=str(handoff), thread_id=thread.id, model="Fake", provider="Fake",
                token_allowance=100, tokens_before_goal=tokens_before)
    assert calls == (["official_cancel", "official_unregister"] if tokens_before == 80 else [])
    assert backend.last_native_goal["status"] == "budgetLimited"
    read_limit.assert_not_called()


def test_all_goal_layouts_and_setup_reference_keep_the_narrow_exit():
    for stage in ("investigate", "judge_rework", "integrate_literature"):
        context = {"frozen_objective": SCIENCE,
                   "judge_objections": [{"requested_resolution": "Complete the frozen missing comparison"}]}
        for layout in ("v1", "v2"):
            for reference in (False, True):
                source = copy.deepcopy(context)
                if reference:
                    source["frozen_objective"]["decisive_test"] *= 100
                goal = investigator_goal(stage, source, layout=layout, allow_setup_reference=reference)
                assert EXECUTION_LIMIT_PROTOCOL_MARKER in goal
                assert "bounded source-recovery audit" in goal
                assert "unfinished authorized work do not qualify" in goal
                assert "Neither completes science" in goal
                assert len(goal) <= 3800


@pytest.mark.parametrize("terminal", ["blocked", "complete"])
def test_supervisor_fixture_conflict_resumes_same_frozen_attempt_once(tmp_path, terminal, monkeypatch):
    from theory_master.native_goal import NativeGoalFixtureAvailabilityConflict
    from theory_master.observability.collector import Collector
    from theory_master.runtime_supervisor_review import review_runtime_failure, resume_blocker
    from theory_master.specification import TaskSpec
    from theory_master.supervisor_fixture_conflict_recovery import VALIDATOR
    from theory_master.supervisor_repair_receipts import inspect_repair_receipt

    class FixtureConflictBackend(quota_fixture.GoalQuotaBackend):
        def __init__(self):
            super().__init__("usageLimited")
            self.original = None
            self.original_handoff = None
            self.corrected = None

        def invoke_goal(self, role, *, context_prompt, handoff_path, thread_id, goal_objective,
                        goal_identity_observer=None, **other):
            context, _ = json.JSONDecoder().raw_decode(context_prompt)
            binding = context["sdk_execution_limit_handoff"]
            if self.original is not None:
                self.corrected = (thread_id, binding, context, other["tokens_before_goal"])
                assert thread_id != self.original.thread_id
                return super().invoke_goal(role, context_prompt=context_prompt, handoff_path=handoff_path,
                    thread_id=thread_id, goal_objective=goal_objective,
                    goal_identity_observer=goal_identity_observer, **other)
            self.started.append(thread_id)
            self.original = SimpleNamespace(
                thread_id=thread_id, objective=goal_objective, created_at=123456,
                status=terminal, tokens_used=420, time_used_seconds=3, token_budget=None,
            )
            from theory_master.native_goal_recovery import goal_identity
            goal_identity_observer({**goal_identity(self.original), "status": "active", "tokens_used": 0,
                                    "time_used_seconds": 0})
            path, limit, _ = _delivery(Path(binding["workspace_path"]), binding["invocation_id"],
                                      context["frozen_objective"])
            limit["input_support_audit"] = {"mode": "missing_fixture_files", "fixture_references": [ref]}
            path.write_text(json.dumps({"execution_limit": limit}))
            self.original_handoff = path
            workflow.collector.record_native_goal_usage(
                "investigator", observed_tokens=420, allowance_tokens=other["token_allowance"],
            )
            try:
                _read(path, binding, terminal, fixture_inputs=context["registered_fixture_inputs"])
            except NativeGoalFixtureAvailabilityConflict as exc:
                self.last_native_goal = {
                    "status": terminal, "thread_id": thread_id,
                    "runtime_failure_kind": "native_goal_fixture_availability_conflict",
                    "fixture_availability_conflict": {"invocation_id": binding["invocation_id"],
                         "references": exc.references, "handoff_sha256": exc.handoff_sha256},
                }
                raise NativeGoalRuntimeFailure(thread_id=thread_id, error=exc.detail) from exc
            raise AssertionError("available fixture was not proved")

    seed = quota_fixture.NativeGoalQuotaTests().make_spec(tmp_path)
    config = json.loads(seed.config_path.read_text())
    config["max_cycles"] = 1
    seed.config_path.write_text(json.dumps(config))
    root = Path(config["benchmark_root"])
    fixture = _registered_fixture(root / "inputs" / "observed.csv")
    ref = fixture["reference"]
    (root / "fixture_manifest.json").write_text(json.dumps({
        "schema": "sciver/private-scientific-fixture-manifest/v1",
        "files": [{"path": ref, "size": fixture["size"], "sha256": fixture["sha256"]}],
    }))
    seed.document.write_text(seed.document.read_text().replace(
        "- fixture\n", f"- benchmark/fixture_manifest.json\n- {ref}\n", 1))
    spec = TaskSpec.from_config(seed.config_path)
    backend = FixtureConflictBackend()
    workflow = Workflow(spec, backend, collector=Collector(spec))

    with pytest.raises(NativeGoalRuntimeFailure, match="available at its pinned path"):
        workflow.run()
    cycle = workflow.cycle_dir
    state_path = cycle / "research_state.json"
    original_state = json.loads(state_path.read_text())
    assert original_state["status"] == "failed"
    assert original_state["scientific_verdict"] is None
    initial_lock = copy.deepcopy(original_state["hypothesis_lock"])
    initial_handoff = backend.original_handoff.read_bytes()
    assert "requires a verified one-use" in resume_blocker(original_state)

    def terminal_read(cycle_dir, identity, *, require_idle):
        assert cycle_dir == cycle and require_idle
        assert identity["thread_id"] == backend.original.thread_id
        return backend.original

    monkeypatch.setattr("theory_master.supervisor_native_goal_recovery.read_existing_goal", terminal_read)
    review = review_runtime_failure(backend=None, research_root=spec.research_root, cycle_dir=cycle,
        state=original_state, state_path=state_path, incident_id="fixture-conflict", scheduler_locked=True)
    assert review["action"] == "resume"
    receipt = inspect_repair_receipt(cycle, original_state)
    assert receipt["validator"] == VALIDATOR
    write_control = cycle / "recovery_control.json"
    write_control.write_text('{"action":"pause"}')
    with pytest.raises(ValueError, match="untouched terminal fixture"):
        inspect_repair_receipt(cycle, original_state)
    write_control.unlink()
    backend.original_handoff.write_bytes(initial_handoff + b"\n")
    with pytest.raises(ValueError, match="contradictory report or pinned files changed"):
        inspect_repair_receipt(cycle, original_state)
    backend.original_handoff.write_bytes(initial_handoff)

    backend.original.status = "active"
    with pytest.raises(ValueError, match="no longer terminal"):
        Workflow(spec, backend).resume(spec.master_id, supervisor_takeover=True)
    backend.original.status = terminal
    assert len(backend.started) == 1

    resumed = Workflow(spec, backend, collector=Collector(spec))
    reentered = resumed.resume(spec.master_id, supervisor_takeover=True)
    assert reentered["status"] == "paused"
    assert len(backend.started) == 2
    assert resumed.cycle_dir == cycle
    assert reentered["hypothesis_lock"] == initial_lock
    assert reentered["investigation_attempt"] == original_state["investigation_attempt"]
    assert reentered["scientific_verdict"] is None
    assert backend.original_handoff.read_bytes() == initial_handoff
    assert len(reentered["supervisor_fixture_conflict_operations"]) == 1
    _, retry_binding, retry_context, tokens_before_retry = backend.corrected
    assert tokens_before_retry == 420
    assert retry_binding["frozen_objective_sha256"] == frozen_science_digest(initial_lock)
    assert retry_binding["handoff_path"] != str(backend.original_handoff)
    assert retry_context["sdk_fixture_availability_correction"] == [
        {key: value for key, value in fixture.items() if key != "status"}
    ]
