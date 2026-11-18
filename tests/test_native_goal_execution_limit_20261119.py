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


def _delivery(workspace: Path, invocation="inv-current", science=None):
    workspace.mkdir(parents=True, exist_ok=True)
    evidence = workspace / "gate.json"
    evidence.write_text('{"eligible_observations":[1,0],"source_recovery":"unavailable"}\n')
    handoff = workspace / ".tm-handoff" / (invocation + ".json")
    handoff.parent.mkdir(exist_ok=True)
    digest = frozen_science_digest(science or SCIENCE)
    limit = {
        "kind": "input_support_unavailable", "invocation_id": invocation,
        "frozen_objective_sha256": digest,
        "reason": "The exact frozen gate leaves one/zero eligible observations for eight parameters.",
        "checked_alternatives": ["Verified both manifest hashes and independently counted eligibility.",
                                 "The authorized source archive contains only the same two files."],
        "unexecuted_test": "Parameter fits and uncertainty estimates could not execute.",
        "required_dependency": "Authorized source input with sufficient qualifying observations.",
        "evidence": [{"path": "gate.json", "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}],
    }
    handoff.write_text(json.dumps({"execution_limit": limit}))
    binding = {"schema": EXECUTION_LIMIT_SCHEMA, "invocation_id": invocation,
               "frozen_objective_sha256": digest, "workspace_path": str(workspace),
               "handoff_path": str(handoff)}
    return handoff, limit, binding


def _read(path, binding, status="blocked"):
    return read_execution_limit_handoff(
        NativeGoalResult(status, "scientific-thread", 3, 4), path,
        invocation_id=binding["invocation_id"],
        frozen_objective_sha256=binding["frozen_objective_sha256"],
        workspace_path=Path(binding["workspace_path"]),
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
    def next_event(_state):
        event = next(incoming)
        if isinstance(event.payload, ThreadGoalUpdatedNotification):
            state.status = event.payload.goal.status  # Normally updated by the SDK router.
        consumed.append(event)
        return event
    thread._client.start_goal_operation = lambda tid, objective: (state, "logical-operation")
    thread._client.next_goal_notification = next_event
    thread._client.cancel_goal_operation = lambda _state: calls.append("official_cancel")
    thread._client.unregister_goal_operation = lambda _state: calls.append("official_unregister")
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
    def blocked(_thread, _objective, *, on_event, on_turn_boundary=None):
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
def test_actual_invocation_and_closure_keep_model_and_judge_unmodified(tmp_path, sdk_status):
    class LimitBackend(quota_fixture.GoalQuotaBackend):
        def invoke_goal(self, role, *, context_prompt, handoff_path, thread_id, **kwargs):
            self.started.append(thread_id)
            context, _ = json.JSONDecoder().raw_decode(context_prompt)
            binding = context["sdk_execution_limit_handoff"]
            assert EXECUTION_LIMIT_PROTOCOL_MARKER in kwargs["goal_objective"]
            assert "Method failure, scientific difficulty or cost alone do not qualify" in context_prompt
            path, limit, _ = _delivery(Path(binding["workspace_path"]), binding["invocation_id"],
                                       context["frozen_objective"])
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
    assert closure["kind"] == "input_support_unavailable"
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
    assert backend.last_native_goal["native_goal_status"] == "administrative_cancel_requested"
    assert backend.last_native_goal["native_goal_cancel_requested"]
    assert backend.last_native_goal["boundary_turn_id"] == "physical-1"
    observed = [call.args[0]["event"] for call in backend.collector.on_codex.call_args_list]
    assert "LLM_TURN_COMPLETED" not in observed  # Do not alter logical Collector semantics.


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
    assert calls == ["official_cancel", "official_unregister"]
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
                assert "Method failure or cost alone does not" in goal
                assert "without a completed test, Findings or scientific verdict" in goal
                assert len(goal) <= 3800
