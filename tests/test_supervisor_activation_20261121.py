from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master.backends import FakeBackend
from theory_master.operator_control import dispatch
from theory_master.runtime import write_json
from theory_master.runtime_manager import load_registry, update_registry
from theory_master.specification import TaskSpec
from theory_master.supervisor import main as supervisor_main
from theory_master.supervisor_activation import stopped_recovery
from theory_master.supervisor_intervention import KEY, supervisor_enabled, write_supervisor_setting
from theory_master.task import bootstrap
from theory_master.workflow import Workflow


@pytest.fixture
def stopped_task(tmp_path):
    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal\ng\n\n# Research Contract\nc\n\n# Final Gate\nf\n\n# Required Context\n- r\n")
    binding = bootstrap(plan)
    spec = TaskSpec.from_config(binding.config_path)
    workflow = Workflow(spec, FakeBackend(reworks=0))
    workflow._ensure_cycle()
    cycle = workflow.cycle_dir
    state = {
        **workflow._new_state(),
        "cycle_id": cycle.name,
        "status": "needs_attention",
        "phase": "research_lead:frontier_proposal:contract_repair_failed",
        "execution_active": False,
        "owner_pid": None,
        "attention": {"code": "contract_repair_failed", "reason": "persisted contract failure"},
    }
    write_json(cycle / "research_state.json", state)
    write_json(cycle / "run_state.json", {"status": "NEEDS_ATTENTION"})
    return binding, spec, cycle, state


@pytest.mark.parametrize("was_enabled", [False, True])
def test_operator_enable_dispatches_stopped_contract_once(stopped_task, was_enabled):
    binding, spec, cycle, _ = stopped_task
    write_supervisor_setting(spec.research_root, was_enabled)
    with patch("theory_master.runtime_manager.launch_managed_runtime", return_value={
        "status": "started", "launch_id": "takeover-1",
    }) as launch:
        result = dispatch({
            "action": "supervisor", "value": True, "task_root": str(binding.task_root),
            "research_root": str(spec.research_root), "run_id": spec.master_id,
        })
    assert supervisor_enabled(spec.research_root)
    assert result == {
        "enabled": True, "effective_at": "sdk:takeover:started",
        "takeover": {"status": "started", "launch_id": "takeover-1",
                     "cycle": cycle.name, "route": "runtime"},
    }
    assert launch.call_args.kwargs == {
        "operation": "takeover", "identifier": str(spec.master_id), "takeover_cycle": cycle.name,
    }


def test_late_operator_control_prevents_queued_managed_takeover(stopped_task):
    _, spec, cycle, _ = stopped_task
    root = spec.research_root
    write_supervisor_setting(root, True)
    update_registry(root, state="LAUNCHING", launch_id="stale-takeover",
                    takeover_cycle=cycle.name, supervisor_attempt=0)
    write_json(cycle / "recovery_control.json", {"action": "pause"})
    with patch("theory_master.supervisor._recover_failed_state") as recovery, \
            patch("theory_master.supervisor.subprocess.Popen") as runner:
        assert supervisor_main([
            "--task-root", str(root), "--operation", "takeover", "--launch-id", "stale-takeover",
        ]) == 0
    recovery.assert_not_called()
    runner.assert_not_called()
    assert load_registry(root)["state"] == "STOPPED"
    assert json.loads((cycle / "recovery_control.json").read_text()) == {"action": "pause"}


def test_no_new_cycle_for_paused_abandoned_or_goal_complete_state(stopped_task):
    _, _, cycle, state = stopped_task
    assert stopped_recovery(cycle, state) == "runtime"
    assert stopped_recovery(cycle, {**state, "recovery": {"action": "pause"}}) is None
    assert stopped_recovery(cycle, {**state, "abandoned_cycle": True}) is None
    assert stopped_recovery(cycle, {**state, "goal_completion": {"verdict": "passed"}}) is None
    assert stopped_recovery(cycle, {**state, "status": "over"}) is None


def test_input_support_wait_only_wakes_for_ready_exact_new_input(tmp_path, monkeypatch):
    from tests.test_task_progression_continuation_20261119 import _canonical_input_limit
    from theory_master.supervisor_activation import input_support_target, takeover_runner_operation
    from theory_master.supervisor_input_support import capture_input_support_baseline

    cycle = tmp_path / "3"
    cycle.mkdir()
    state = {**_canonical_input_limit(), "cycle_id": cycle.name}
    write_json(cycle / "operational_closure.json", state["operational_only_closure"])
    source_ids = []
    spec = SimpleNamespace(task_mode=True)
    monkeypatch.setattr(TaskSpec, "from_task_root", lambda _: spec)
    monkeypatch.setattr("theory_master.supervisor_input_support._available_input_ids", lambda _: list(source_ids))
    capture_input_support_baseline(spec, state)
    assert stopped_recovery(cycle, state, include_wait=True) == "input_support_wait"
    assert stopped_recovery(cycle, state) is None

    source_ids.extend(["a" * 64, "b" * 64])
    ready = input_support_target(cycle, state)
    assert ready["route"] == "input_support"
    assert takeover_runner_operation(ready["route"]) == "run"
    attempt = {"stopped_cycle": cycle.name, "launch_id": "old", "status": "launching",
               "dependency_identity": ready["dependency_identity"], "wake_identity": ready["wake_identity"],
               "attempted_input_ids": ["a" * 64, "b" * 64]}
    monkeypatch.setattr("theory_master.runtime_manager.process_identity_alive", lambda *_: True)
    update_registry(tmp_path, requested_operation="takeover", takeover_cycle=cycle.name, launch_id="old",
                    input_support_attempt=attempt)
    assert stopped_recovery(cycle, state) == "input_support"  # The exact managed child is admitted.
    source_ids.pop(0)
    assert stopped_recovery(cycle, state, include_wait=True) == "input_support_wait"
    update_registry(tmp_path, launch_id="new")
    assert stopped_recovery(cycle, state, include_wait=True) == "input_support_wait"
    source_ids.append("c" * 64)
    assert stopped_recovery(cycle, state) == "input_support"

    assert stopped_recovery(cycle, {**state, "worker_id": "worker-1"}, include_wait=True) is None
    assert stopped_recovery(cycle, {**state, "recovery": {"action": "pause"}}, include_wait=True) is None
    assert stopped_recovery(cycle, {**state, "cycle_budget": {"exhausted": True}}, include_wait=True) is None
    write_json(cycle / "recovery_control.json", {"action": "pause"})
    assert input_support_target(cycle, state) is None


@pytest.mark.parametrize("pause", [False, True])
def test_input_support_wait_projects_exact_dependency_and_obeys_control(tmp_path, monkeypatch, pause):
    from theory_master import supervisor_activation

    cycle = tmp_path / "3"
    cycle.mkdir()
    dependency = {"route": "input_support_wait", "reason": "Matched archived reason",
                  "wake_condition": "The declared measurements", "dependency_identity": "proof",
                  "invocation_id": "inv-input"}
    targets = iter(((cycle, {}, "input_support_wait"), (cycle, {}, "input_support")))
    ticks = iter((0, 31))
    monkeypatch.setattr(supervisor_activation, "latest_recovery", lambda *_, **__: next(targets))
    monkeypatch.setattr(supervisor_activation, "input_support_target", lambda *_: dependency)
    monkeypatch.setattr(supervisor_activation.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr("theory_master.supervisor_intervention.supervisor_enabled", lambda _: True)
    monkeypatch.setattr("theory_master.worker_store._live_cycle_claims", lambda _: [])
    monkeypatch.setattr("theory_master.worker_store._master_complete", lambda _: False)

    class Stop:
        def is_set(self):
            return False

        def wait(self, _):
            health = load_registry(tmp_path)
            assert health["state"] == "RECOVERY_WAIT"
            projected = health["qualification_wait"]
            assert projected["kind"] == "input_support_unavailable"
            for key in ("reason", "wake_condition", "dependency_identity", "invocation_id"):
                assert projected[key] == dependency[key]
            if pause:
                write_json(cycle / "recovery_control.json", {"action": "pause"})

    assert supervisor_activation.wait_for_qualification_change(tmp_path, cycle, "launch", Stop()) is not pause
    assert load_registry(tmp_path)["qualification_wait"] is None


@pytest.mark.parametrize("waiting", [True, False])
def test_delivery_history_blocks_revisited_revision_without_granting_frontier_retry(tmp_path, monkeypatch, waiting):
    from theory_master.supervisor_goal_reassessment import reassessment_launch_identity

    cycle = tmp_path / "3"
    cycle.mkdir()
    state = {
        "cycle_id": cycle.name, "status": "no_qualified_candidate", "phase": "qualification",
        "qualification_status": "no_qualified_candidate", "protocol_health": "valid",
        "attention": {"code": "no_qualified_candidate"}, "hypothesis_lock": {"status": "unlocked"},
        "frontier_review": {"status": "unqualified"}, "qualification_attempts": [{"round": 3}],
    }
    if waiting:
        state["supervisor_qualification"] = {"status": "waiting", "session_id": "SUP-original"}
    plan = {"stopped_cycle": cycle.name, "anchor_cycle": "1", "reassessment_of": "first-goal",
            "delivery_revision": "a" * 64}
    identity = reassessment_launch_identity(plan, "original-launch")
    monkeypatch.setattr(TaskSpec, "from_task_root", lambda _: object())
    monkeypatch.setattr("theory_master.worker_direction.research_direction_fingerprint", lambda _: {})
    monkeypatch.setattr("theory_master.supervisor_goal_reassessment.delivery_reassessment_plan", lambda _: plan)
    monkeypatch.setattr("theory_master.runtime_manager.process_identity_alive", lambda *_: True)

    update_registry(tmp_path, launch_id="original-launch", requested_operation="takeover", takeover_cycle=cycle.name,
                    delivery_reassessment_attempt={
        **identity, "status": "launching",
    })
    # The in-flight exact child must still see its admitted route.
    assert stopped_recovery(cycle, state) == "goal_reassessment"
    update_registry(tmp_path, launch_id="replacement-launch")
    hold = "qualification_wait"
    assert stopped_recovery(cycle, state, include_wait=True) == hold
    assert stopped_recovery(cycle, state) is None

    finished = {**identity, "status": "unconfirmed"}
    second = {**identity, "launch_id": "replacement-launch", "delivery_revision": "b" * 64, "status": "failed"}
    update_registry(tmp_path, delivery_reassessment_attempt=second, delivery_reassessment_history=[finished, second])
    plan["reassessment_of"] = "later-goal"
    assert stopped_recovery(cycle, state, include_wait=True) == hold
    plan["delivery_revision"] = "c" * 64
    assert stopped_recovery(cycle, state) == "goal_reassessment"

    monkeypatch.setattr("theory_master.supervisor_goal_reassessment.delivery_reassessment_plan", lambda _: None)
    monkeypatch.setattr("theory_master.worker_direction.direction_changed", lambda *_: True)
    assert stopped_recovery(cycle, state, include_wait=True) == ("qualification" if waiting else "qualification_wait")
    write_json(cycle / "recovery_control.json", {"action": "pause"})
    assert stopped_recovery(cycle, state, include_wait=True) is None


def test_delivery_only_wait_reports_failed_revision_without_inventing_qualification(tmp_path, monkeypatch):
    from theory_master import supervisor_activation

    cycle = tmp_path / "3"
    update_registry(tmp_path, launch_id="delivery-launch", delivery_reassessment_attempt={
        "stopped_cycle": cycle.name, "anchor_cycle": "1", "reassessment_of": "goal",
        "delivery_revision": "a" * 64, "status": "failed",
    })
    targets = iter(((cycle, {}, "qualification_wait"), (cycle, {}, "goal_reassessment")))
    ticks = iter((0, 31))
    monkeypatch.setattr(supervisor_activation, "latest_recovery", lambda *_, **__: next(targets))
    monkeypatch.setattr(supervisor_activation.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr("theory_master.supervisor_intervention.supervisor_enabled", lambda _: True)
    monkeypatch.setattr("theory_master.worker_store._live_cycle_claims", lambda _: [])
    monkeypatch.setattr("theory_master.worker_store._master_complete", lambda _: False)

    class Stop:
        def is_set(self):
            return False

        def wait(self, _):
            registry = load_registry(tmp_path)
            assert registry["state"] == "RECOVERY_WAIT"
            receipt = registry["qualification_wait"]
            assert receipt["launch_id"] == "delivery-launch" and receipt["cycle"] == cycle.name
            assert receipt["delivery_revision"] == "a" * 64 and receipt["session_id"] is None
            assert "failed" in receipt["reason"] and "new verified public submission" in receipt["wake_condition"]

    assert supervisor_activation.wait_for_qualification_change(tmp_path, cycle, "delivery-launch", Stop())
    assert load_registry(tmp_path)["qualification_wait"] is None


@pytest.mark.parametrize("suffix", ["}", "} extra", "{}"])
def test_stopped_spv_reply_reentry_requires_exact_logged_failure(stopped_task, suffix):
    from pathlib import Path
    from theory_master import runtime_supervisor_review
    from theory_master.resume_preparation import consume_resume_repairs
    from theory_master.supervisor_intervention import SupervisorSession
    from theory_master.supervisor_qualification_reentry import validate_qualification_reply_reentry
    from theory_master.supervisor_repair_receipts import inspect_repair_receipt

    _, spec, cycle, state = stopped_task
    reply = json.dumps({"action": "select_candidate", "reason": "Evidence supports another qualified test",
                        "candidate": {"lineage_other_node_ids": "H-0001"}}) + suffix
    with pytest.raises(json.JSONDecodeError) as legacy:
        json.loads(reply)
    reason = "Supervisor qualification review output was invalid: " + str(legacy.value)
    checkpoint = cycle / "checkpoints" / "000001-research_lead.json"
    checkpoint.parent.mkdir(exist_ok=True)
    state.update(phase="supervisor_qualification_review", resumable=True, protocol_health="valid",
                 hypothesis_lock={"status": "unlocked"}, last_safe_checkpoint=str(checkpoint),
                 attention={"code": "supervisor_qualification_review_failed", "reason": reason},
                 role_outputs={"research_lead_frontier": {"candidates": [], "blocking_reason": "Known gap"}})
    write_json(checkpoint, {"cycle_id": cycle.name, "hypothesis_lock": state["hypothesis_lock"]})
    state_path = cycle / "research_state.json"
    write_json(state_path, state)
    session = SupervisorSession(cycle, profile="attention", incident_id="qualification-test",
                                code="no_qualified_candidate", summary="Whole-task qualification recovery")
    session.append("prompt", "Return one object", role="attention_reviewer")
    session.append("model_output", reply, thread_id="thread-1")
    session.append("prompt", "Return the corrected object", role="attention_reviewer", input_kind="contract_repair")
    session.append("model_output", reply, thread_id="thread-2")
    session.close("hold", action="hold", reason=reason)
    engine = Path(runtime_supervisor_review.__file__).parent
    assert "verified repair receipt" in runtime_supervisor_review.resume_blocker(state)
    if suffix != "}":
        with pytest.raises(ValueError):
            validate_qualification_reply_reentry(cycle, state, engine=engine)
        return
    review = runtime_supervisor_review.review_runtime_failure(
        backend=None, research_root=spec.research_root, cycle_dir=cycle, state=state,
        state_path=state_path, incident_id="repair-test", scheduler_locked=True,
    )
    assert review["action"] == "resume"
    assert json.loads(inspect_repair_receipt(cycle, state)["tests"])["thread_id"] == "thread-2"

    control = cycle / "recovery_control.json"
    write_json(control, {"action": "pause"})
    with pytest.raises(ValueError, match="response evidence changed"):
        consume_resume_repairs(cycle, state)
    control.unlink()
    consume_resume_repairs(cycle, state)
    assert state["supervisor_qualification_reentries"] == [session.session_id]
    assert state["supervisor_qualification_reentry_audit"][0]["original_attention"]["reason"] == reason
    assert state["hypothesis_lock"] == {"status": "unlocked"}
    assert state["role_outputs"]["research_lead_frontier"]["candidates"] == []
    assert "attention" not in state


def test_loaded_reply_validator_wakes_an_older_dependency_wait(stopped_task, monkeypatch):
    from theory_master import supervisor_qualification_reentry
    from theory_master.supervisor_recovery import due, ensure_job, inputs, save_job

    _, spec, cycle, state = stopped_task
    state.update(phase="supervisor_qualification_review", attention={
        "code": "supervisor_qualification_review_failed",
        "reason": "Supervisor qualification review output was invalid: Extra data: line 1 column 2 (char 1)",
    })
    job = ensure_job(cycle, state)
    with monkeypatch.context() as old_owner:
        old_owner.setattr(supervisor_qualification_reentry, "is_reply_failure", lambda _: False)
        job.update(status="waiting", dependency_wait=True, input_digest=inputs(spec.research_root, state))
        save_job(cycle, job)
        assert not due(cycle, state)
    assert due(cycle, state)
    job.update(input_digest=inputs(spec.research_root, state))
    save_job(cycle, job)
    assert not due(cycle, state)


def test_live_worker_inherited_switch_does_not_mask_task_toggle(stopped_task, monkeypatch):
    _, spec, _, _ = stopped_task
    monkeypatch.setenv(KEY, "1")
    write_supervisor_setting(spec.research_root, False)
    assert not supervisor_enabled(spec.research_root)
    monkeypatch.setenv(KEY, "0")
    write_supervisor_setting(spec.research_root, True)
    assert supervisor_enabled(spec.research_root)


def test_worker_only_recovery_start_is_deduplicated(stopped_task):
    from theory_master.supervisor_activation import _launch_worker_recovery

    _, spec, _, _ = stopped_task
    with patch("theory_master.worker_launch._launch_detached_service") as launch:
        assert _launch_worker_recovery(spec.research_root)["status"] == "worker_recovery_requested"
        assert _launch_worker_recovery(spec.research_root)["status"] == "worker_recovery_owned"
    launch.assert_called_once()
    assert "-m" in launch.call_args.args[0]
    assert "theory_master.supervisor_activation" in launch.call_args.args[0]


@pytest.mark.parametrize(
    ("route", "operation", "operational_review"),
    [("qualification", "run", False), ("contract_repair_failed", "resume", True),
     ("public_submission_incomplete", "resume", False),
     ("budget_preserve", "preserve", False)],
)
def test_managed_takeover_launches_exact_route(stopped_task, monkeypatch, route, operation, operational_review):
    from unittest.mock import MagicMock
    from theory_master import supervisor

    _, spec, cycle, state = stopped_task
    if route == "qualification":
        state.update(
            status="no_qualified_candidate", phase="qualification",
            qualification_status="no_qualified_candidate", protocol_health="valid",
            attention={"code": "no_qualified_candidate"}, hypothesis_lock={"status": "unlocked"},
            frontier_review={"status": "unqualified"}, qualification_attempts=[{"round": 3}],
        )
        write_json(cycle / "research_state.json", state)
    elif route == "public_submission_incomplete":
        state.update(attention={"code": route}, phase="goal_submission:delivery_pending")
        write_json(cycle / "research_state.json", state)
        monkeypatch.setattr("theory_master.submission_rework.submission_takeover_preflight",
                            lambda *_: {"status": "ready_for_goal"})
    elif route == "budget_preserve":
        state.update(attention={"code": "investigator_budget_supervisor_declined"},
                     phase="investigator:budget_denied", budget_auto_preserve={"policy": "three_plus_three_v1"})
        write_json(cycle / "research_state.json", state)
        monkeypatch.setattr("theory_master.budget_auto_preservation.single_budget_preservation_preflight",
                            lambda *_: {"status": "ready"})
    root = spec.research_root
    write_supervisor_setting(root, True)
    update_registry(root, state="LAUNCHING", launch_id="exact-takeover",
                    takeover_cycle=cycle.name, supervisor_attempt=0)
    child = MagicMock(pid=99_999_999)
    child.wait.return_value = 0
    calls = []
    with patch.object(supervisor, "_recover_failed_state", side_effect=lambda *a, **k: calls.append("review") or True), \
            patch.object(supervisor, "_notify_supervisor_terminal"), \
            patch.object(supervisor, "_exit_disposition", return_value="terminal"), \
            patch.object(supervisor.subprocess, "Popen", side_effect=lambda *a, **k: calls.append("runner") or child) as runner:
        assert supervisor_main([
            "--task-root", str(root), "--operation", "takeover",
            "--identifier", str(spec.master_id), "--launch-id", "exact-takeover",
        ]) == 0
    assert calls == (["review", "runner"] if operational_review else ["runner"])
    argv = runner.call_args.args[0]
    assert argv[argv.index("--operation") + 1] == operation
    assert argv[argv.index("--supervisor-takeover-cycle") + 1] == cycle.name


def test_managed_supervisor_dispatches_preservation_after_investigator_budget_denial(stopped_task, monkeypatch):
    from unittest.mock import MagicMock
    from theory_master import supervisor

    _, spec, cycle, state = stopped_task
    root = spec.research_root
    write_supervisor_setting(root, True)
    update_registry(root, state="LAUNCHING", launch_id="run-budget-owner", supervisor_attempt=0)
    path = cycle / "research_state.json"
    write_json(path, {**state, "status": "active", "phase": "investigator:running"})
    monkeypatch.setattr("theory_master.budget_auto_preservation.single_budget_preservation_preflight",
                        lambda *_: {"status": "ready"})
    seen = []

    def launch(argv, **_):
        operation = argv[argv.index("--operation") + 1]
        seen.append(operation)
        child = MagicMock(pid=99_999_999)
        def finish(**_):
            if operation == "run":
                write_json(path, {**state, "phase": "investigator:budget_denied",
                                  "attention": {"code": "investigator_budget_supervisor_declined"},
                                  "budget_auto_preserve": {"policy": "three_plus_three_v1"}})
            else:
                assert operation == "preserve"
                assert argv[argv.index("--supervisor-takeover-cycle") + 1] == cycle.name
                write_json(path, {**state, "status": "over", "phase": "investigator:preserved"})
            return 0
        child.wait.side_effect = finish
        return child

    with patch.object(supervisor, "_notify_supervisor_terminal"), \
            patch.object(supervisor, "_exit_disposition", return_value="terminal"), \
            patch.object(supervisor.subprocess, "Popen", side_effect=launch):
        assert supervisor_main([
            "--task-root", str(root), "--operation", "run",
            "--identifier", str(spec.master_id), "--launch-id", "run-budget-owner",
        ]) == 0
    assert seen == ["run", "preserve"]


def test_managed_runner_passes_exact_budget_preservation_cycle_and_launch(stopped_task):
    from theory_master.runner import main as runner_main

    _, spec, cycle, state = stopped_task
    root = spec.research_root
    write_supervisor_setting(root, True)
    update_registry(root, state="STARTING", current_cycle=cycle.name,
                    requested_operation="takeover", launch_id="preservation-owner")
    with patch("theory_master.supervisor_activation.latest_recovery",
               return_value=(cycle, state, "budget_preserve")), \
            patch("theory_master.budget_auto_preservation.run_single_budget_preservation",
                  return_value={"status": "over"}) as archive, \
            patch("theory_master.codex_backend.CodexBackend.preflight_runtime") as preflight:
        assert runner_main([
            "--task-root", str(root), "--operation", "preserve", "--identifier", str(spec.master_id),
            "--managed-child", "--supervisor-takeover-cycle", cycle.name,
        ]) == 0
    archive.assert_called_once()
    assert archive.call_args.args[1] == cycle
    assert archive.call_args.kwargs == {"launch_id": "preservation-owner"}
    preflight.assert_not_called()


def _wait_state(cycle, state):
    state.update(
        status="no_qualified_candidate", phase="qualification",
        qualification_status="no_qualified_candidate", protocol_health="valid",
        attention={"code": "no_qualified_candidate"}, hypothesis_lock={"status": "unlocked"},
        frontier_review={"status": "unqualified"}, qualification_attempts=[{"round": 3}],
        supervisor_qualification={"status": "waiting", "session_id": "SUP-wait",
                                  "wake_condition": "independent observations admitted",
                                  "direction_fingerprint": {"revision": "old"}},
    )
    write_json(cycle / "research_state.json", state)


@pytest.mark.parametrize("blocker", [None, "launch", "cycle", "session", "root", "released", "cycle_control", "disabled"])
def test_restarted_managed_run_stays_bound_to_its_qualification_wait(stopped_task, blocker, monkeypatch):
    from theory_master import supervisor, supervisor_activation
    from theory_master.runtime_manager import registry_path

    _, spec, cycle, state = stopped_task
    root = spec.research_root
    _wait_state(cycle, state)
    write_supervisor_setting(root, blocker != "disabled")
    launch_id = "original-managed-run"
    receipt = {"launch_id": launch_id, "cycle": cycle.name, "session_id": "SUP-wait"}
    if blocker == "launch":
        receipt["launch_id"] = "old-managed-run"
    elif blocker == "cycle":
        receipt["cycle"] = "0"
    elif blocker == "session":
        receipt["session_id"] = "SUP-distinct"
    registry = update_registry(root, state="RECOVERY_WAIT", launch_id=launch_id, supervisor_attempt=1,
                               requested_operation="takeover", current_cycle=cycle.name, takeover_cycle=cycle.name,
                               qualification_wait=receipt)
    if blocker == "root":
        write_json(registry_path(root), {**registry, "research_root": str(root.parent)})
    elif blocker == "released":
        control = supervisor_activation._qualification_wait_control(root)
        write_json(control, {"launch_id": launch_id, "action": "run"})
        monkeypatch.setattr("theory_master.worker_direction.direction_changed", lambda *_: True)
    elif blocker == "cycle_control":
        write_json(cycle / "recovery_control.json", {"action": "pause"})

    with (patch.object(supervisor_activation, "wait_for_qualification_change", return_value=False) as wait,
          patch.object(supervisor, "_recover_failed_state") as recovery,
          patch.object(supervisor.subprocess, "Popen") as runner):
        assert supervisor_main(["--task-root", str(root), "--operation", "run", "--launch-id", launch_id]) == 0
    if blocker is None:
        wait.assert_called_once()
        assert wait.call_args.args[:3] == (root, cycle, launch_id)
    else:
        wait.assert_not_called()
    recovery.assert_not_called()
    runner.assert_not_called()
    registry = load_registry(root)
    assert registry["state"] == "STOPPED"
    assert registry["qualification_wait"] is None
    assert registry["takeover_cycle"] is None


def test_restarted_managed_run_uses_bound_takeover_when_new_input_arrives(stopped_task):
    import hashlib
    from theory_master import supervisor, supervisor_activation
    from theory_master.worker_direction import research_direction_fingerprint

    _, spec, cycle, state = stopped_task
    root = spec.research_root
    _wait_state(cycle, state)
    state["supervisor_qualification"]["direction_fingerprint"] = research_direction_fingerprint(spec)
    path = cycle / "research_state.json"
    write_json(path, state)
    write_supervisor_setting(root, True)
    launch_id = "original-managed-run"
    update_registry(root, state="RECOVERY_WAIT", launch_id=launch_id, supervisor_attempt=1,
                    requested_operation="takeover", current_cycle=cycle.name, takeover_cycle=cycle.name,
                    qualification_wait={"launch_id": launch_id, "cycle": cycle.name, "session_id": "SUP-wait"})
    data = b"x\n1\n"
    artifact = spec.task_home / "measurements.csv"
    artifact.write_bytes(data)
    write_json(spec.task_home / "input_manifest.json", {
        "schema": "theorymaster/public-research-input-manifest/v1",
        "data_files": [{"path": artifact.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
    })

    def finish_child(timeout):
        write_json(path, {**state, "status": "over", "attention": None, "supervisor_qualification": None})
        return 0

    child = SimpleNamespace(pid=99999998, wait=finish_child)
    with (patch.object(supervisor_activation, "wait_for_qualification_change") as wait,
          patch.object(supervisor, "runtime_python", return_value="offline-sdk-python"),
          patch.object(supervisor.subprocess, "Popen", return_value=child) as runner):
        assert supervisor_main(["--task-root", str(root), "--operation", "run", "--launch-id", launch_id]) == 0
    wait.assert_not_called()
    runner.assert_called_once()
    argv = runner.call_args.args[0]
    assert argv[argv.index("--operation") + 1] == "run"
    assert argv[argv.index("--supervisor-takeover-cycle") + 1] == cycle.name
    assert load_registry(root)["qualification_wait"] is None


def test_supervisor_wait_wakes_only_after_new_direction(stopped_task, monkeypatch):
    from threading import Event
    from theory_master.supervisor_activation import latest_recovery, wait_for_qualification_change

    _, spec, cycle, state = stopped_task
    root = spec.research_root
    _wait_state(cycle, state)
    write_supervisor_setting(root, True)
    monkeypatch.setattr("theory_master.worker_direction.research_direction_fingerprint", lambda _spec: {})
    changed = False
    monkeypatch.setattr("theory_master.worker_direction.direction_changed", lambda *_: changed)
    monkeypatch.setattr("theory_master.supervisor_activation.QUALIFICATION_WAIT_CHECK_SECONDS", 0)
    assert latest_recovery(root) is None
    assert latest_recovery(root, include_wait=True)[2] == "qualification_wait"
    stop = Event()

    def evidence_arrives(_seconds):
        nonlocal changed
        registry = load_registry(root)
        assert registry["state"] == "RECOVERY_WAIT"
        assert registry["qualification_wait"]["wake_condition"] == "independent observations admitted"
        changed = True

    monkeypatch.setattr(stop, "wait", evidence_arrives)
    assert wait_for_qualification_change(root, cycle, "old-launch", stop)
    assert load_registry(root)["qualification_wait"] is None
    assert latest_recovery(root)[2] == "qualification"


def test_historical_wait_baseline_wakes_for_restored_verified_input(stopped_task, monkeypatch):
    import hashlib
    from pathlib import Path
    from threading import Event
    from theory_master.research_inputs import verified_research_inputs
    from theory_master.supervisor_activation import latest_recovery, wait_for_qualification_change
    from theory_master.worker_direction import research_direction_fingerprint

    _, spec, cycle, state = stopped_task
    root = spec.research_root
    _wait_state(cycle, state)
    write_supervisor_setting(root, True)
    home = Path(spec.task_home)
    expected = b"x\n1\n"
    manifest = home / "input_manifest.json"
    file = home / "measurements.csv"
    write_json(manifest, {
        "schema": "theorymaster/public-research-input-manifest/v1",
        "data_files": [{"path": file.name, "bytes": len(expected),
                        "sha256": hashlib.sha256(expected).hexdigest()}],
    })
    before = research_direction_fingerprint(spec)
    before.pop("verified_research_input_ids")
    state["supervisor_qualification"]["direction_fingerprint"] = before
    saved = cycle / "research_state.json"
    write_json(saved, state)
    science = saved.read_bytes()
    stop = Event()
    monkeypatch.setattr("theory_master.supervisor_activation.QUALIFICATION_WAIT_CHECK_SECONDS", 0)
    waits = []

    def restore_file(_seconds):
        waits.append(load_registry(root)["qualification_wait"]["cycle"])
        if len(waits) == 1:
            manifest.write_text("\n" + manifest.read_text())
        else:
            assert len(waits) == 2
            file.write_bytes(expected)

    monkeypatch.setattr(stop, "wait", restore_file)
    assert wait_for_qualification_change(root, cycle, "exact-wait", stop)
    assert waits == [cycle.name, cycle.name]
    assert saved.read_bytes() == science
    assert latest_recovery(root)[2] == "qualification"
    assert [row["path"] for row in verified_research_inputs(spec)] == [str(file)]


def test_wait_releases_only_for_explicit_run_in_current_launch(stopped_task, monkeypatch):
    from threading import Event
    from theory_master.supervisor_activation import (
        _qualification_wait_control, qualification_wait_owned, release_qualification_wait_for_run,
        wait_for_qualification_change,
    )

    _, spec, cycle, state = stopped_task
    root = spec.research_root
    _wait_state(cycle, state)
    write_supervisor_setting(root, True)
    monkeypatch.setattr("theory_master.worker_direction.direction_changed", lambda *_: False)
    stop = Event()
    control = _qualification_wait_control(root)
    write_json(control, {"launch_id": "past-launch", "action": "run"})
    calls = 0

    def operator_runs(_seconds):
        nonlocal calls
        calls += 1
        assert calls == 1
        registry = load_registry(root)
        registry.update(launch_id="current-launch", requested_operation="takeover", supervisor_pid=123)
        health = {"state": "RECOVERY_WAIT", "supervisor_alive": True, "runner_alive": False, "registry": registry}
        assert qualification_wait_owned(health)
        assert not qualification_wait_owned({**health, "runner_alive": True})
        with patch("theory_master.runtime_manager.runtime_health", return_value=health), \
                patch("theory_master.runtime_manager.process_identity_alive", return_value=False):
            release_qualification_wait_for_run(root)

    monkeypatch.setattr(stop, "wait", operator_runs)
    assert not wait_for_qualification_change(root, cycle, "current-launch", stop)
    assert calls == 1
    assert json.loads(control.read_text()) == {"launch_id": "current-launch", "action": "run"}


def test_dormant_worker_supervisor_queues_exact_direction_without_a_legacy_runner(stopped_task):
    from theory_master.supervisor_activation import request_stopped_takeover
    from theory_master.worker_direction import _pending_retry_direction
    from theory_master.worker_store import save_worker

    _, spec, cycle, state = stopped_task
    root = spec.research_root
    _wait_state(cycle, state)
    state.pop("supervisor_qualification")
    state["worker_id"] = "worker-1"
    write_json(cycle / "research_state.json", state)
    save_worker(root, "worker-1", status="WAITING_RESEARCH_DIRECTION", current_cycle=cycle.name)
    write_supervisor_setting(root, True)
    with patch("theory_master.supervisor_activation._launch_worker_recovery",
               return_value={"status": "worker_recovery_requested"}) as launch, \
            patch("theory_master.runtime_manager.launch_managed_runtime") as legacy:
        result = request_stopped_takeover(spec)
    legacy.assert_not_called()
    launch.assert_called_once_with(root)
    assert result == {"status": "worker_recovery_requested"}
    request = _pending_retry_direction(root, "worker-1")
    assert request["source"] == "supervisor"
    assert request["waiting_cycle"] == cycle.name
    write_supervisor_setting(root, False)
    assert _pending_retry_direction(root, "worker-1") is None


def _worker_dependency_wait(stopped_task, *, legacy=False):
    import hashlib

    from theory_master.worker_direction import research_direction_fingerprint
    from theory_master.worker_store import save_worker

    _, spec, cycle, state = stopped_task
    root = spec.research_root
    data = b"x\n1\n"
    artifact = spec.task_home / "measurements.csv"
    write_json(spec.task_home / "input_manifest.json", {
        "schema": "theorymaster/public-research-input-manifest/v1",
        "data_files": [{"path": artifact.name, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()}],
    })
    fingerprint = research_direction_fingerprint(spec)
    if legacy:
        fingerprint.pop("verified_research_input_ids")
    _wait_state(cycle, state)
    state["worker_id"] = "worker-1"
    state["wake_fingerprint"] = fingerprint
    state["supervisor_qualification"]["direction_fingerprint"] = fingerprint
    write_json(cycle / "research_state.json", state)
    save_worker(root, "worker-1", status="WAITING_RESEARCH_DIRECTION", current_cycle=cycle.name,
                wake_fingerprint=fingerprint, waiting_since="stopped-worker-wait")
    write_supervisor_setting(root, True)
    return spec, cycle, artifact, data


@pytest.mark.parametrize("legacy", [False, True])
def test_detached_worker_supervisor_watches_until_verified_input_arrives(stopped_task, monkeypatch, legacy):
    from theory_master import supervisor_activation
    from theory_master.runtime_manager import _file_lock
    from theory_master.worker_direction import _retry_direction_path
    from theory_master.worker_store import read_worker

    spec, cycle, artifact, data = _worker_dependency_wait(stopped_task, legacy=legacy)
    root = spec.research_root
    scheduler = root / ".theory-master" / "worker_scheduler.json"
    write_json(scheduler, {"status": "STOPPED", "quota_mode": "per_worker", "cycles_started": 1,
                           "cycles_started_by_worker": {"worker-1": 1},
                           "max_cycles_per_worker": spec.max_cycles, "resume_batch": True})
    checkpoint = (cycle / "research_state.json").read_bytes()
    with patch.object(supervisor_activation, "_launch_worker_recovery",
                      return_value={"status": "worker_recovery_requested"}) as detached, \
            patch("theory_master.runtime_manager.launch_managed_runtime") as legacy_runner:
        assert supervisor_activation.request_stopped_takeover(spec) == {"status": "worker_recovery_requested"}
    detached.assert_called_once_with(root)
    legacy_runner.assert_not_called()
    assert not _retry_direction_path(root, "worker-1").exists()

    launches = []
    ticks = 0
    monkeypatch.setattr("theory_master.worker_routes._route_capacity", lambda *_: (1, [None]))
    monkeypatch.setattr("theory_master.worker_launch._launch_detached_service",
                        lambda args, **_: launches.append(args))

    def advance(_seconds):
        nonlocal ticks
        ticks += 1
        with _file_lock(root / ".theory-master" / "scheduler_owner.lock", nonblocking=True):
            pass  # Explicit normal Scheduler remains able to take ownership between ticks.
        if ticks == 1:
            assert not launches
            assert not _retry_direction_path(root, "worker-1").exists()
            artifact.write_bytes(b"not the declared bytes")
        elif ticks == 2:
            assert not launches
            artifact.write_bytes(data)
        elif ticks == 3:
            assert len(launches) == 1
            assert read_worker(root, "worker-1")["claim_id"]
            assert json.loads(scheduler.read_text())["cycles_started_by_worker"] == {"worker-1": 2}
            write_supervisor_setting(root, False)
        else:
            pytest.fail("Worker Supervisor did not leave after SPV OFF")

    monkeypatch.setattr(supervisor_activation.time, "sleep", advance)
    assert supervisor_activation._run_worker_recovery(root) == 0
    assert ticks == 3
    assert (cycle / "research_state.json").read_bytes() == checkpoint
    receipt = json.loads(_retry_direction_path(root, "worker-1").read_text())
    assert receipt["waiting_cycle"] == cycle.name
    assert receipt["status"] == "consumed"
    assert receipt["consumed_by_claim_id"] == read_worker(root, "worker-1")["claim_id"]
    assert "run-one" in launches[0] and "worker-1" in launches[0]


@pytest.mark.parametrize("blocked_by", ["pause", "quota"])
def test_detached_worker_wait_does_not_bypass_stops(stopped_task, blocked_by):
    from theory_master import supervisor_activation

    spec, cycle, artifact, data = _worker_dependency_wait(stopped_task)
    root = spec.research_root
    if blocked_by == "pause":
        write_json(cycle / "recovery_control.json", {"action": "pause"})
    else:
        write_json(root / ".theory-master" / "worker_scheduler.json", {
            "status": "STOPPED", "quota_mode": "per_worker",
            "cycles_started_by_worker": {"worker-1": spec.max_cycles},
        })
    artifact.write_bytes(data)
    with patch.object(supervisor_activation, "_launch_worker_recovery") as detached, \
            patch("theory_master.worker_scheduler.schedule_once") as allocate:
        result = supervisor_activation.request_stopped_takeover(spec)
        if blocked_by == "pause":
            assert result["status"] == "policy_hold"
        else:
            assert result is None
        assert supervisor_activation._run_worker_recovery(root) == 0
    detached.assert_not_called()
    allocate.assert_not_called()


def test_detached_worker_wait_yields_to_normal_scheduler(stopped_task, monkeypatch):
    from contextlib import ExitStack

    from theory_master import supervisor_activation
    from theory_master.runtime_manager import _file_lock

    spec, _, _, _ = _worker_dependency_wait(stopped_task)
    root = spec.research_root
    with ExitStack() as normal_scheduler, patch("theory_master.worker_scheduler.schedule_once") as allocate:
        def take_ownership(_seconds):
            normal_scheduler.enter_context(_file_lock(root / ".theory-master" / "scheduler_owner.lock"))

        monkeypatch.setattr(supervisor_activation.time, "sleep", take_ownership)
        assert supervisor_activation._run_worker_recovery(root) == 0
    allocate.assert_not_called()
    assert json.loads((root / ".theory-master" / "worker_supervisor.json").read_text()) == {}


def test_normal_scheduler_outwaits_an_active_worker_supervisor_tick(stopped_task, monkeypatch):
    import fcntl
    import os

    from theory_master import worker_scheduler
    from theory_master.runtime_manager import process_start_ticks

    _, spec, _, _ = stopped_task
    root = spec.research_root
    owner_path = root / ".theory-master" / "scheduler_owner.lock"
    marker = root / ".theory-master" / "worker_supervisor.json"
    with owner_path.open("a+") as supervisor_owner, owner_path.open("a+") as normal_owner:
        fcntl.flock(supervisor_owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not worker_scheduler._acquire_scheduler_owner(root, normal_owner)
        write_json(marker, {"pid": os.getpid(), "start_ticks": process_start_ticks(os.getpid())})
        ticks = []

        def finish_supervisor_tick(seconds):
            ticks.append(seconds)
            fcntl.flock(supervisor_owner.fileno(), fcntl.LOCK_UN)

        monkeypatch.setattr(worker_scheduler.time, "sleep", finish_supervisor_tick)
        assert worker_scheduler._acquire_scheduler_owner(root, normal_owner)
        assert len(ticks) == 1


@pytest.mark.parametrize("policy", ["budget", "pause", "submission", "maintenance"])
def test_policy_activation_records_actual_hold_without_changing_science(stopped_task, monkeypatch, policy):
    binding, spec, cycle, state = stopped_task
    if policy == "budget":
        state.update(phase="investigator:budget_denied",
                     attention={"code": "investigator_budget_cap_reached", "reason": "Six tranches exhausted"})
    elif policy == "pause":
        state.update(status="paused", phase="paused", recovery={"action": "pause"})
    elif policy == "maintenance":
        from theory_master.supervisor_recovery import ensure_job, path_for, save_job

        state.update(status="failed", phase="failed", attention={},
                     recovery={"action": "error", "message": "Historical recoverable failure"})
        job = ensure_job(cycle, state)
        save_job(cycle, {**job, "status": "stopped", "current_action": "maintenance_stop",
                         "next_wakeup_epoch": None, "blocking_dependency": "Maintenance stopped this incident"})
        maintenance_receipt = path_for(cycle).read_bytes()
    else:
        state.update(phase="goal_submission:delivery_pending",
                     attention={"code": "public_submission_incomplete"})
        monkeypatch.setattr("theory_master.submission_rework.submission_takeover_preflight",
                            lambda *_: {"status": "hold", "code": "submission_corrections_exhausted",
                                        "reason": "The original correction allowance is exhausted."})
    path = cycle / "research_state.json"
    write_json(path, state)
    science_before = path.read_bytes()
    with patch("theory_master.runtime_manager.launch_managed_runtime") as launch:
        result = dispatch({
            "action": "supervisor", "value": True, "task_root": str(binding.task_root),
            "research_root": str(spec.research_root), "run_id": spec.master_id,
        })
    launch.assert_not_called()
    assert path.read_bytes() == science_before
    assert result["effective_at"] == "sdk:takeover:policy_hold"
    assert result["takeover"]["status"] == "policy_hold"
    receipt = json.loads((cycle / "supervisor" / "activation.json").read_text())
    projection = json.loads((cycle / "run_state.json").read_text())
    assert receipt["owner"] == "sdk:supervisor"
    assert receipt["reason"] == result["takeover"]["reason"]
    assert projection["attention_takeover_handoff"]["status"] == "policy_hold"
    assert projection["attention_takeover_handoff"]["reason"] == receipt["reason"]
    if policy == "maintenance":
        assert result["takeover"]["code"] == "maintenance_stop"
        assert receipt["reason"] == "Maintenance stopped this incident"
        assert path_for(cycle).read_bytes() == maintenance_receipt


def _seal_before_later_stop(stopped_task, *, source_in_task_home=False, invalid_submission=False):
    import hashlib
    from theory_master.science_graph_store import ScienceGraphStore

    binding, spec, prior, state = stopped_task
    graph = spec.graph_database
    if source_in_task_home or invalid_submission:
        config = dict(binding.config)
        if source_in_task_home:
            graph = binding.task_home / "bound-goal.sqlite3"
            config["graph_database"] = str(graph)
        if invalid_submission:
            config["submission_contract"] = "benchmark/missing-contract.json"
        write_json(binding.config_path, config)
    with ScienceGraphStore(graph) as store:
        store.add_hypothesis("H-0001")
        store.register_cycle_hypothesis(cycle_id=prior.name, hypothesis_id="H-0001")
        store.append_outcome(outcome_id="cycle-outcome-1", hypothesis_id="H-0001",
                             cycle_id=prior.name, verdict="supported")
        store.seal_goal(goal_id=spec.goal_id, source_cycle=prior.name,
                        research_plan_sha256=hashlib.sha256(spec.document.read_bytes()).hexdigest(),
                        judge_receipt_ref=None, detail={"rationale": "Completed Goal"})
    later = spec.research_root / "2"
    later.mkdir()
    (later / ".theory-master-cycle").touch()
    stale = {**state, "cycle_id": later.name}
    write_json(later / "research_state.json", stale)
    return spec.research_root, later, stale


@pytest.mark.parametrize("source_in_task_home,invalid_submission", [(False, False), (True, True)])
def test_later_local_stop_cannot_override_canonical_goal(
        stopped_task, source_in_task_home, invalid_submission):
    from theory_master.goal_terminal import recovery_goal_blocker
    from theory_master.supervisor_activation import latest_recovery

    root, later, state = _seal_before_later_stop(
        stopped_task, source_in_task_home=source_in_task_home, invalid_submission=invalid_submission)
    assert recovery_goal_blocker(root) == "goal_completed"
    assert stopped_recovery(later, state) is None
    assert latest_recovery(root, include_wait=True) is None
    _wait_state(later, state)
    assert stopped_recovery(later, state, include_wait=True) is None


@pytest.mark.parametrize("operation", ["run", "resume", "takeover"])
def test_restarted_owner_exits_without_diagnosis_or_runner_on_sealed_goal(stopped_task, operation):
    from theory_master import supervisor

    root, later, _ = _seal_before_later_stop(stopped_task, source_in_task_home=True, invalid_submission=True)
    write_supervisor_setting(root, True)
    update_registry(root, state="RECOVERY_WAIT", launch_id="retained-launch",
                    takeover_cycle=later.name, supervisor_attempt=1)
    with (patch.object(supervisor, "_recover_failed_state") as recovery,
          patch.object(supervisor.subprocess, "Popen") as runner,
          patch.object(supervisor, "_notify_supervisor_terminal") as notify):
        assert supervisor_main(["--task-root", str(root), "--operation", operation,
                                "--launch-id", "retained-launch"]) == 0
    recovery.assert_not_called()
    runner.assert_not_called()
    notify.assert_not_called()
    assert supervisor._exit_disposition(root) == "terminal"
    registry = load_registry(root)
    assert registry["state"] == "STOPPED"
    assert registry["goal_recovery_blocker"] == "goal_completed"
    assert not registry["supervisor_pid"]


def test_unbound_diagnostic_root_and_malformed_canonical_binding_are_distinct(stopped_task):
    from theory_master.goal_terminal import recovery_goal_blocker

    _, spec, cycle, state = stopped_task
    unbound = spec.research_root / "unregistered"
    assert recovery_goal_blocker(unbound) is None
    write_json(spec.research_root / ".theory-master/task.json", [])
    assert recovery_goal_blocker(spec.research_root) == "goal_authority_unavailable"
    assert stopped_recovery(cycle, state) is None


@pytest.mark.parametrize("repair_enabled", [False, True])
def test_persisted_recovery_does_not_consume_receipts_after_goal_pass(stopped_task, repair_enabled):
    from threading import Event
    from theory_master import supervisor

    root, _, _ = _seal_before_later_stop(stopped_task)
    write_supervisor_setting(root, True)
    with (patch.object(supervisor, "_handle_failed_state") as repair,
          patch("theory_master.runtime_supervisor_review.review_runtime_failure") as review):
        assert not supervisor._recover_failed_state(root, repair_enabled=repair_enabled, stop=Event())
    repair.assert_not_called()
    review.assert_not_called()


@pytest.fixture
def retained_previous_boot_wait(stopped_task, monkeypatch):
    from theory_master import runtime_manager
    from theory_master.worker_direction import research_direction_fingerprint

    _, spec, cycle, state = stopped_task
    root = spec.research_root
    _wait_state(cycle, state)
    fingerprint = research_direction_fingerprint(spec)
    state["wake_fingerprint"] = fingerprint
    state["supervisor_qualification"]["direction_fingerprint"] = fingerprint
    write_json(cycle / "research_state.json", state)
    write_supervisor_setting(root, True)

    old_boot = "79c6021f-34c9-495c-993b-bb8f35f52830"
    boot = "916c58c7-0165-4cb6-8c97-b4fd8e374d8d"
    monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: boot)
    registry = update_registry(
        root, state="RECOVERY_WAIT", launch_id="previous-boot-wait",
        requested_operation="takeover", takeover_cycle=cycle.name, current_cycle=cycle.name,
        systemd_unit=runtime_manager.unit_name(root, spec.master_id), launcher="systemd-user",
        master_id=spec.master_id, runner_pid=None, runner_start_ticks=None,
        supervisor_pid=99999998, supervisor_start_ticks=87654321,
        qualification_wait={"launch_id": "previous-boot-wait", "cycle": cycle.name,
                            "session_id": "SUP-wait", "direction_fingerprint": fingerprint},
    )
    # Reproduce the exact disk state left by a cold boot. No fake process is started.
    registry["boot_id"] = old_boot
    write_json(runtime_manager.registry_path(root), registry)
    return spec, cycle, state, boot, registry


def test_boot_recovery_reestablishes_the_exact_previous_wait(retained_previous_boot_wait):
    from theory_master import runtime_manager
    from theory_master.supervisor_activation import _qualification_wait_control
    from theory_master.supervisor_boot_recovery import eligible_boot_wait, recover_task_wait

    spec, cycle, _, _, _ = retained_previous_boot_wait
    root = spec.research_root
    control = _qualification_wait_control(root)
    write_json(control, {"launch_id": "still-an-older-wait", "action": "run"})
    registry_path = runtime_manager.registry_path(root)
    before = (cycle / "research_state.json").read_bytes(), registry_path.read_bytes(), control.read_bytes()

    snapshot = eligible_boot_wait(root)
    assert snapshot is not None
    with patch.object(runtime_manager, "launch_managed_runtime", return_value={"status": "started"}) as launch:
        assert recover_task_wait(spec)["status"] == "started"
    launch.assert_called_once_with(
        spec, operation="takeover", identifier=str(spec.master_id), takeover_cycle=cycle.name,
        boot_wait=snapshot,
    )
    assert ((cycle / "research_state.json").read_bytes(), registry_path.read_bytes(), control.read_bytes()) == before


@pytest.mark.parametrize("reason", [
    "current_boot", "no_proven_boot", "disable", "pause", "abandon", "explicit_run",
    "live_owner", "different_session", "different_cycle", "different_launch", "worker",
])
def test_boot_recovery_honors_exact_ownership_and_control(retained_previous_boot_wait, reason):
    import os
    from theory_master import runtime_manager
    from theory_master.supervisor_activation import _qualification_wait_control
    from theory_master.supervisor_boot_recovery import eligible_boot_wait, recover_task_wait

    spec, cycle, state, boot, registry = retained_previous_boot_wait
    root = spec.research_root
    if reason == "current_boot":
        registry["boot_id"] = boot
    elif reason == "no_proven_boot":
        registry.pop("boot_id")
    elif reason == "disable":
        write_supervisor_setting(root, False)
    elif reason == "pause":
        write_json(cycle / "recovery_control.json", {"action": "pause"})
    elif reason == "abandon":
        state["abandoned_cycle"] = True
    elif reason == "explicit_run":
        write_json(_qualification_wait_control(root), {"launch_id": registry["launch_id"], "action": "run"})
    elif reason == "live_owner":
        from theory_master.invocations import stable_hash

        plan = eligible_boot_wait(root)
        owner = "already-starting-on-this-boot"
        registry.update(
            state="STARTING", boot_id=boot, launch_id=owner,
            boot_wait=plan["origin"], boot_recovery={
                "boot_id": boot, "launch_id": owner, "origin_sha256": stable_hash(plan["origin"]), "attempt": 1,
            },
            supervisor_pid=os.getpid(), supervisor_start_ticks=runtime_manager.process_start_ticks(os.getpid()),
        )
    elif reason == "different_session":
        registry["qualification_wait"]["session_id"] = "SUP-not-the-original-session"
    elif reason == "different_cycle":
        registry["qualification_wait"]["cycle"] = "0"
    elif reason == "different_launch":
        registry["qualification_wait"]["launch_id"] = "superseded-episode"
    elif reason == "worker":
        state["worker_id"] = "worker-1"

    write_json(runtime_manager.registry_path(root), registry)
    write_json(cycle / "research_state.json", state)
    assert eligible_boot_wait(root) is None
    with patch.object(runtime_manager, "launch_managed_runtime") as launch:
        assert recover_task_wait(spec)["status"] == "skipped"
    launch.assert_not_called()


@pytest.mark.parametrize("dependency", ["qualification", "delivery", "input_support"])
def test_boot_wait_survives_when_the_live_projection_is_cleared(retained_previous_boot_wait, dependency):
    from theory_master import runtime_manager
    from theory_master.supervisor_activation import input_support_target, latest_recovery
    from theory_master.supervisor_boot_recovery import eligible_boot_wait
    from theory_master.supervisor_input_support import capture_input_support_baseline
    from tests.test_task_progression_continuation_20261119 import _canonical_input_limit

    spec, cycle, state, _, registry = retained_previous_boot_wait
    root = spec.research_root
    wait = registry["qualification_wait"]
    expected_route = "qualification_wait"
    if dependency == "delivery":
        state.pop("supervisor_qualification")
        revision = "d" * 64
        receipt = {"stopped_cycle": cycle.name, "anchor_cycle": "1", "reassessment_of": "goal-first",
                   "delivery_revision": revision, "status": "finished"}
        registry["delivery_reassessment_history"] = [receipt]
        wait = {**wait, "session_id": None, "delivery_revision": revision}
    elif dependency == "input_support":
        state = {**_canonical_input_limit(), "cycle_id": cycle.name}
        capture_input_support_baseline(spec, state)
        write_json(cycle / "operational_closure.json", state["operational_only_closure"])
        target = input_support_target(cycle, state)["dependency_identity"]
        invocation = state["operational_only_closure"]["execution_limit"]["invocation_id"]
        wait = {**wait, "kind": "input_support_unavailable", "session_id": None,
                "dependency_identity": target, "invocation_id": invocation}
        expected_route = "input_support_wait"

    registry.update(
        qualification_wait=None,
        boot_wait={"schema": "theory-master/supervisor-boot-wait/v1", "boot_id": registry["boot_id"],
                   "launch_id": registry["launch_id"], "research_root": str(root),
                   "master_id": spec.master_id, "cycle": cycle.name, "qualification_wait": wait},
    )
    write_json(runtime_manager.registry_path(root), registry)
    write_json(cycle / "research_state.json", state)
    assert latest_recovery(root, include_wait=True)[2] == expected_route
    assert eligible_boot_wait(root) is not None

    if dependency == "input_support":
        wait["invocation_id"] = "inv-other"
    elif dependency == "delivery":
        wait["delivery_revision"] = "e" * 64
    else:
        wait["session_id"] = "SUP-other"
    write_json(runtime_manager.registry_path(root), registry)
    assert eligible_boot_wait(root) is None


def test_boot_recovery_requires_the_managed_systemd_owner_and_never_falls_back(retained_previous_boot_wait):
    from theory_master import runtime_manager
    from theory_master.supervisor_boot_recovery import eligible_boot_wait, recover_task_wait

    spec, cycle, _, _, registry = retained_previous_boot_wait
    root = spec.research_root
    science = (cycle / "research_state.json").read_bytes()
    initial_plan = eligible_boot_wait(root)
    assert initial_plan and initial_plan["attempt"] == 1
    unavailable = SimpleNamespace(returncode=1, stdout="", stderr="test user manager unavailable")
    with (
        patch.object(runtime_manager.shutil, "which", return_value="/usr/bin/offline-test-systemd"),
        patch.object(runtime_manager, "runtime_python", return_value="/usr/bin/offline-test-python"),
        patch.object(runtime_manager.subprocess, "run", return_value=unavailable) as systemd_run,
        patch.object(runtime_manager.subprocess, "Popen") as detached,
    ):
        for expected_attempt in (1, 2, 3):
            assert eligible_boot_wait(root)["attempt"] == expected_attempt
            assert recover_task_wait(spec)["status"] == "systemd_launch_failed"
            retained = load_registry(root)
            assert retained["boot_recovery"]["attempt"] == expected_attempt
            assert retained["boot_wait"] == initial_plan["origin"]
            assert retained["boot_wait"]["qualification_wait"] == registry["qualification_wait"]
            assert eligible_boot_wait(root) is None  # A just-issued start still owns the admission grace.
            update_registry(root, launch_requested_at="2000-01-01T00:00:00+00:00")
        assert recover_task_wait(spec)["status"] == "skipped"
    assert systemd_run.call_count == 3
    detached.assert_not_called()
    assert (cycle / "research_state.json").read_bytes() == science



def test_boot_recovery_launch_does_not_overwrite_an_unprojected_live_owner(retained_previous_boot_wait):
    import fcntl
    from theory_master import runtime_manager
    from theory_master.supervisor_boot_recovery import eligible_boot_wait, recover_task_wait

    spec, cycle, _, _, _ = retained_previous_boot_wait
    root = spec.research_root
    before = runtime_manager.registry_path(root).read_bytes()
    assert eligible_boot_wait(root)
    with runtime_manager.owner_lock_path(root).open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch.object(runtime_manager.subprocess, "run") as child, \
                pytest.raises(ValueError, match="runner.lock is held"):
            recover_task_wait(spec)
        child.assert_not_called()
    assert runtime_manager.registry_path(root).read_bytes() == before


def test_previous_boot_wait_is_ready_only_after_current_verified_input(retained_previous_boot_wait):
    import hashlib
    from theory_master.supervisor_boot_recovery import eligible_boot_wait

    spec, cycle, _, _, _ = retained_previous_boot_wait
    root = spec.research_root
    assert eligible_boot_wait(root)["route"] == "qualification_wait"
    data = b"time,observed\n0,1\n"
    path = spec.task_home / "approved_measurement.csv"
    write_json(spec.task_home / "input_manifest.json", {
        "schema": "theorymaster/public-research-input-manifest/v1",
        "data_files": [{"path": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
    })
    assert eligible_boot_wait(root)["route"] == "qualification_wait"
    path.write_bytes(data)
    recovery = eligible_boot_wait(root)
    assert recovery["route"] == "qualification"
    assert recovery["cycle"] == cycle.name
    assert recovery["origin"]["qualification_wait"]["session_id"] == "SUP-wait"
