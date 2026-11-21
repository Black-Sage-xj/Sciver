"""A fresh Supervisor cannot replay a persisted failure before recovery review."""
import copy
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from theory_master import supervisor


@pytest.mark.parametrize("operation", ["run", "resume"])
@pytest.mark.parametrize("failure", [
    {"status": "failed", "native_goal": {"runtime_failure_kind": "native_goal_dependency_blocked"}},
    {"status": "needs_attention", "protocol_health": "violation",
     "attention": {"code": "contract_repair_failed"}},
])
def test_startup_hold_never_spawns_runner(tmp_path, operation, failure):
    state = {**copy.deepcopy(failure), "resumable": True, "execution_active": False}
    original = copy.deepcopy(state)
    with ExitStack() as stack:
        stack.enter_context(patch.object(supervisor.threading, "Thread"))
        stack.enter_context(patch.object(supervisor, "load_registry", return_value={"launch_id": "offline"}))
        stack.enter_context(patch.object(supervisor, "_latest_scientific_state", return_value=("5", state, None)))
        stack.enter_context(patch.object(supervisor, "update_registry"))
        stack.enter_context(patch.object(supervisor, "_notify_supervisor_terminal"))
        recover = stack.enter_context(patch.object(supervisor, "_recover_failed_state", return_value=False))
        spawn = stack.enter_context(patch.object(supervisor.subprocess, "Popen"))
        assert supervisor.main(["--task-root", str(tmp_path), "--operation", operation,
                                "--launch-id", "offline", "--error-repair"]) == 0
    recover.assert_called_once()
    spawn.assert_not_called()
    assert state == original


@pytest.mark.parametrize("initial_status", ["failed", "active", "paused", "interrupted", "over"])
def test_startup_launches_after_recovery_only_when_needed(tmp_path, initial_status):
    state = {"status": initial_status, "resumable": True, "execution_active": False}
    calls = []
    child = MagicMock(pid=99999999)
    child.wait.return_value = 0

    def recover(*args, **kwargs):
        calls.append("review")
        return True

    def spawn(*args, **kwargs):
        calls.append("runner")
        assert calls == (["review", "runner"] if initial_status == "failed" else ["runner"])
        return child

    with ExitStack() as stack:
        stack.enter_context(patch.object(supervisor.threading, "Thread"))
        stack.enter_context(patch.object(supervisor, "load_registry", return_value={"launch_id": "offline"}))
        stack.enter_context(patch.object(supervisor, "_latest_scientific_state", return_value=("5", state, None)))
        stack.enter_context(patch.object(supervisor, "update_registry"))
        stack.enter_context(patch.object(supervisor, "_notify_supervisor_terminal"))
        stack.enter_context(patch.object(supervisor, "_recover_failed_state", side_effect=recover))
        stack.enter_context(patch.object(supervisor, "_runtime_supervisor_receipt", return_value=("5", "SUP-offline")))
        stack.enter_context(patch.object(supervisor, "_quota_pause_context", return_value=None))
        stack.enter_context(patch.object(supervisor, "_quota_resume_started"))
        stack.enter_context(patch.object(supervisor, "_runtime_env", return_value={}))
        command = stack.enter_context(patch.object(supervisor, "_runner_command", return_value=["offline-runner"]))
        stack.enter_context(patch.object(supervisor.subprocess, "Popen", side_effect=spawn))
        stack.enter_context(patch.object(supervisor, "_exit_disposition", return_value="terminal"))
        verification = stack.enter_context(patch.object(supervisor, "_record_runtime_verification"))
        assert supervisor.main(["--task-root", str(tmp_path), "--operation", "run",
                                "--launch-id", "offline", "--error-repair"]) == 0
    if initial_status == "failed":
        assert calls == ["review", "runner"]
        assert command.call_args.args[1] == "resume"
        verification.assert_called_once()
        assert verification.call_args.args[1] == ("5", "SUP-offline")
        assert verification.call_args.kwargs["verified"] is False
    else:
        assert calls == ["runner"]
        assert command.call_args.args[1] == "run"
        verification.assert_not_called()


def test_live_runner_is_adopted_before_any_failure_recovery(tmp_path):
    with ExitStack() as stack:
        stack.enter_context(patch.object(supervisor.threading, "Thread"))
        stack.enter_context(patch.object(supervisor, "load_registry", return_value={
            "launch_id": "offline", "runner_pid": 99999999, "runner_start_ticks": 123}))
        stack.enter_context(patch.object(supervisor, "_latest_scientific_state", return_value=(
            "5", {"status": "failed"}, None)))
        # Startup and adoption see the live identity; it then exits normally.
        identity = stack.enter_context(patch.object(supervisor, "process_identity_alive", side_effect=[True, True, False]))
        stack.enter_context(patch.object(supervisor, "update_registry"))
        stack.enter_context(patch.object(supervisor, "_notify_supervisor_terminal"))
        stack.enter_context(patch.object(supervisor, "_exit_disposition", return_value="terminal"))
        recover = stack.enter_context(patch.object(supervisor, "_recover_failed_state"))
        spawn = stack.enter_context(patch.object(supervisor.subprocess, "Popen"))
        assert supervisor.main(["--task-root", str(tmp_path), "--operation", "resume",
                                "--launch-id", "offline", "--error-repair"]) == 0
    recover.assert_not_called()
    spawn.assert_not_called()
    identity.assert_any_call(99999999, 123)


@pytest.mark.parametrize("repair", ["--error-repair", "--no-error-repair"])
def test_maintenance_stop_blocks_restarted_owner_and_unused_repair_credit(tmp_path, repair):
    from theory_master.runtime import write_json
    from theory_master.supervisor_intervention import write_supervisor_setting
    from theory_master.supervisor_recovery import ensure_job, path_for, save_job

    write_supervisor_setting(tmp_path, True)
    supervisor.update_registry(tmp_path, launch_id="offline", requested_operation="run", supervisor_attempt=1)
    cycle = tmp_path / "1"
    cycle.mkdir()
    (cycle / ".theory-master-cycle").write_text("theory-master/v2\ntest\n", encoding="utf-8")
    state = {"cycle_id": cycle.name, "status": "failed", "phase": "failed", "resumable": True,
             "recovery": {"action": "error", "message": "Archived runtime failure"}}
    state_path = cycle / "research_state.json"
    write_json(state_path, state)
    save_job(cycle, {**ensure_job(cycle, state), "status": "stopped", "current_action": "maintenance_stop",
                     "next_wakeup_epoch": None})
    before = path_for(cycle).read_bytes(), state_path.read_bytes()

    with ExitStack() as stack:
        stack.enter_context(patch.object(supervisor.threading, "Thread"))
        stack.enter_context(patch.object(supervisor, "_notify_supervisor_terminal"))
        pending = stack.enter_context(patch.object(supervisor, "_handle_failed_state",
                                                   side_effect=AssertionError("maintenance stop launched handler")))
        consume = stack.enter_context(patch("theory_master.runtime_supervisor_review.review_runtime_failure"))
        spawn = stack.enter_context(patch.object(supervisor.subprocess, "Popen"))
        assert supervisor.main(["--task-root", str(tmp_path), "--operation", "run",
                                "--launch-id", "offline", repair]) == 0

    pending.assert_not_called()
    consume.assert_not_called()
    spawn.assert_not_called()
    assert before == (path_for(cycle).read_bytes(), state_path.read_bytes())


@pytest.mark.parametrize("previous_attempt", [None, "offline", "previous-launch"])
def test_delivery_reassessment_failure_does_not_relaunch_the_same_revision(tmp_path, previous_attempt):
    from theory_master.supervisor_goal_reassessment import reassessment_launch_identity

    cycle = tmp_path / "5"
    plan = {"stopped_cycle": cycle.name, "anchor_cycle": "4", "reassessment_of": "older",
            "delivery_revision": "a" * 64}
    identity = {**reassessment_launch_identity(plan, "offline"), "status": "launching"}
    registry = {"launch_id": "offline", "requested_operation": "takeover", "takeover_cycle": cycle.name}
    if previous_attempt:
        registry["delivery_reassessment_attempt"] = {**identity, "launch_id": previous_attempt}
    child = MagicMock(pid=99999999)
    child.wait.return_value = 1

    with ExitStack() as stack:
        stack.enter_context(patch.object(supervisor.threading, "Thread"))
        stack.enter_context(patch.object(supervisor, "load_registry", side_effect=lambda *_: dict(registry)))
        stack.enter_context(patch.object(supervisor, "update_registry",
                                        side_effect=lambda _, **values: registry.update(values)))
        stack.enter_context(patch.object(supervisor, "_latest_scientific_state", return_value=(cycle.name, {}, None)))
        stack.enter_context(patch.object(supervisor, "_quota_pause_context", return_value=None))
        stack.enter_context(patch.object(supervisor, "_runtime_env", return_value={}))
        stack.enter_context(patch("theory_master.worker_store._master_complete", return_value=False))
        stack.enter_context(patch("theory_master.specification.TaskSpec.from_task_root"))
        stack.enter_context(patch("theory_master.supervisor_activation.latest_recovery",
                                  return_value=(cycle, {}, "goal_reassessment")))
        stack.enter_context(patch("theory_master.supervisor_goal_reassessment.delivery_reassessment_plan",
                                  return_value=plan))
        command = stack.enter_context(patch.object(supervisor, "_runner_command", return_value=["offline-runner"]))
        spawn = stack.enter_context(patch.object(supervisor.subprocess, "Popen", return_value=child))
        assert supervisor.main(["--task-root", str(tmp_path), "--operation", "takeover",
                                "--launch-id", "offline", "--error-repair"]) == 0

    if previous_attempt:
        spawn.assert_not_called()
    else:
        spawn.assert_called_once()
        assert command.call_args.args[1] == "reassess"
        assert registry["delivery_reassessment_attempt"] == {
            **identity, "status": "failed", "runner_exit_code": 1,
        }


@pytest.mark.parametrize("boundary", ["failed", "legacy", "adopted", "restart"])
def test_delivery_failure_retains_original_wait_and_only_wakes_for_new_revision(tmp_path, boundary):
    from theory_master import supervisor_activation
    from theory_master.supervisor_goal_reassessment import reassessment_launch_identity

    cycle = tmp_path / "5"
    plan = {"stopped_cycle": cycle.name, "anchor_cycle": "4", "reassessment_of": "older",
            "delivery_revision": "a" * 64}
    identity = reassessment_launch_identity(plan, "offline")
    registry = {"launch_id": "offline", "requested_operation": "takeover", "takeover_cycle": cycle.name}
    launched_here = boundary in {"failed", "legacy"}
    argv_operation = "takeover" if launched_here else "run"
    if not launched_here:
        registry["delivery_reassessment_attempt"] = {**identity, "status": "launching"}
    if boundary == "adopted":
        registry.update(runner_pid=99999999, runner_start_ticks=123)
    review = {"status": "waiting", "session_id": "SUP-original", "wake_condition": "verified delivery"}
    state = {"status": "no_qualified_candidate"}
    if boundary != "legacy":
        state["supervisor_qualification"] = review
    child = MagicMock(pid=99999998)
    child.wait.return_value = 1
    observed_waits = []

    def latest(_, *, include_wait=False):
        history = supervisor_activation.completed_delivery_attempts(registry, cycle.name)
        if not any(supervisor_activation.same_delivery_attempt(plan, item) for item in history):
            return cycle, state, "goal_reassessment"
        return (cycle, state, "qualification_wait") if include_wait else None

    def await_direction(root, target, launch_id, _stop):
        assert (root, target, launch_id) == (tmp_path, cycle, "offline")
        assert state.get("supervisor_qualification") == (None if boundary == "legacy" else review)
        observed_waits.append(registry["delivery_reassessment_attempt"]["status"])
        if len(observed_waits) == 1:
            plan["delivery_revision"] = "b" * 64
            return True
        return False

    with ExitStack() as stack:
        stack.enter_context(patch.object(supervisor.threading, "Thread"))
        stack.enter_context(patch.object(supervisor, "load_registry", side_effect=lambda *_: dict(registry)))
        stack.enter_context(patch.object(supervisor, "update_registry", side_effect=lambda _, **data: registry.update(data)))
        stack.enter_context(patch.object(supervisor, "_latest_scientific_state", return_value=(cycle.name, state, None)))
        stack.enter_context(patch.object(supervisor, "_quota_pause_context", return_value=None))
        stack.enter_context(patch.object(supervisor, "_runtime_env", return_value={}))
        stack.enter_context(patch("theory_master.worker_store._master_complete", return_value=False))
        stack.enter_context(patch("theory_master.specification.TaskSpec.from_task_root"))
        stack.enter_context(patch.object(supervisor_activation, "latest_recovery", side_effect=latest))
        stack.enter_context(patch.object(supervisor_activation, "wait_for_qualification_change", side_effect=await_direction))
        stack.enter_context(patch("theory_master.supervisor_goal_reassessment.delivery_reassessment_plan", side_effect=lambda *_: dict(plan)))
        if boundary == "adopted":
            old_runner = iter((True, True, False))
            stack.enter_context(patch.object(supervisor, "process_identity_alive", side_effect=lambda pid, *_:
                                            pid == 99999999 and next(old_runner, False)))
        command = stack.enter_context(patch.object(supervisor, "_runner_command", return_value=["offline-runner"]))
        spawn = stack.enter_context(patch.object(supervisor.subprocess, "Popen", return_value=child))
        assert supervisor.main(["--task-root", str(tmp_path), "--operation", argv_operation,
                                "--launch-id", "offline", "--error-repair"]) == 0

    assert observed_waits == ["failed" if launched_here else "unconfirmed", "failed"]
    assert spawn.call_count == (2 if launched_here else 1)
    assert all(call.args[1] == "reassess" for call in command.call_args_list)
    assert [receipt["delivery_revision"] for receipt in registry["delivery_reassessment_history"]] == ["a" * 64, "b" * 64]


@pytest.mark.parametrize("restart", [False, True])
def test_input_support_terminal_owner_reuses_quiet_wait_and_cumulatively_consumes_inputs(tmp_path, restart):
    from theory_master import supervisor_activation

    cycle = tmp_path / "5"
    registry = {"launch_id": "offline", "requested_operation": "run"}
    old_input, next_input = "a" * 64, "b" * 64
    available = []
    if restart:
        available.append(old_input)
        registry.update(requested_operation="takeover", takeover_cycle=cycle.name, input_support_takeover_cycle=cycle.name,
                        input_support_attempt={"launch_id": "offline", "stopped_cycle": cycle.name,
                                               "dependency_identity": "dependency", "wake_identity": "wake-a",
                                               "status": "launching", "attempted_input_ids": [old_input]})
    state = {"status": "over", "scientific_verdict": None}
    waits, launches = [], []

    def raw_dependency(*_, **__):
        common = {"route": "input_support_wait", "dependency_identity": "dependency", "invocation_id": "inv-exact"}
        if available:
            return {**common, "route": "input_support", "wake_identity": "wake-" + available[0][0],
                    "added_input_ids": list(available)}
        return common

    def latest(_, *, include_wait=False):
        target = supervisor_activation.input_support_target(cycle, state, registry=registry)
        return (cycle, state, target["route"]) if include_wait or target["route"] == "input_support" else None

    def await_input(root, directory, launch_id, _stop):
        assert (root, directory, launch_id) == (tmp_path, cycle, "offline")
        prior = registry.get("input_support_attempt") or {}
        waits.append(prior.get("status"))
        if not available:
            available.append(old_input)
        elif available == [old_input]:
            available[:] = [next_input]
        else:
            return False  # Operator stop after the second input set failed to advance.
        return True

    def command(args, operation):
        receipt = registry.get("input_support_attempt")
        if receipt and receipt["status"] == "launching":
            assert args.takeover_expected_cycle == cycle.name
            assert receipt["wake_identity"] == "wake-" + available[0][0]
            assert receipt["stopped_cycle"] == cycle.name and receipt["launch_id"] == "offline"
        launches.append((operation, receipt))
        return ["offline-runner"]

    def spawn(*_, **__):
        child = MagicMock(pid=99999998)
        child.wait.return_value = 1 if launches[-1][1] else 0
        return child

    with ExitStack() as stack:
        stack.enter_context(patch.object(supervisor.threading, "Thread"))
        stack.enter_context(patch.object(supervisor, "load_registry", side_effect=lambda *_: dict(registry)))
        stack.enter_context(patch.object(supervisor, "update_registry", side_effect=lambda _, **data: registry.update(data)))
        stack.enter_context(patch.object(supervisor, "_latest_scientific_state", return_value=(cycle.name, state, None)))
        stack.enter_context(patch.object(supervisor, "latest_cycle_name", return_value=cycle.name))
        stack.enter_context(patch.object(supervisor, "_quota_pause_context", return_value=None))
        stack.enter_context(patch.object(supervisor, "_runtime_env", return_value={}))
        stack.enter_context(patch("theory_master.worker_store._master_complete", return_value=False))
        stack.enter_context(patch("theory_master.supervisor_input_support.input_support_recovery", side_effect=raw_dependency))
        stack.enter_context(patch("theory_master.runtime_manager.load_registry", side_effect=lambda *_: dict(registry)))
        stack.enter_context(patch.object(supervisor_activation, "latest_recovery", side_effect=latest))
        stack.enter_context(patch.object(supervisor_activation, "wait_for_qualification_change", side_effect=await_input))
        stack.enter_context(patch.object(supervisor, "_runner_command", side_effect=command))
        stack.enter_context(patch.object(supervisor.subprocess, "Popen", side_effect=spawn))
        assert supervisor.main(["--task-root", str(tmp_path), "--operation", "run",
                                "--launch-id", "offline", "--error-repair"]) == 0

    assert waits == (["unconfirmed", "failed"] if restart else [None, "failed", "failed"])
    assert [operation for operation, _receipt in launches] == (["run"] if restart else ["run", "run", "run"])
    assert registry["input_support_attempt"]["attempted_input_ids"] == [old_input, next_input]
    assert registry["input_support_attempt"]["status"] == "failed"


@pytest.mark.parametrize("boundary", ["live", "restart_during_launch", "restart_adopting", "restart_after_release"])
@pytest.mark.parametrize("controlled", [False, True])
def test_input_support_new_cycle_releases_the_exact_takeover(tmp_path, boundary, controlled):
    from theory_master import supervisor_activation

    stopped, advanced = tmp_path / "5", tmp_path / "6"
    receipt = {"launch_id": "offline", "stopped_cycle": stopped.name, "dependency_identity": "dependency",
               "wake_identity": "wake", "status": "launching", "attempted_input_ids": ["a" * 64]}
    registry = {"launch_id": "offline", "requested_operation": "takeover", "takeover_cycle": stopped.name,
                "input_support_takeover_cycle": stopped.name}
    plan = {"route": "input_support", "dependency_identity": "dependency", "invocation_id": "inv-exact",
            "wake_identity": "wake", "added_input_ids": ["a" * 64]}
    current = stopped
    state = {"status": "over"}

    def allocate_new_cycle():
        nonlocal current, state
        current = advanced
        state = {"status": "active", "resumable": True}
        if controlled:
            advanced.mkdir(exist_ok=True)
            (advanced / "recovery_control.json").write_text('{"action": "pause"}', encoding="utf-8")

    if boundary != "live":
        allocate_new_cycle()
        registry["input_support_attempt"] = receipt
    if boundary == "restart_after_release":
        registry.update(requested_operation="resume", takeover_cycle=None, input_support_takeover_cycle=None,
                        input_support_attempt={**receipt, "status": "advanced", "advanced_cycle": advanced.name})
    if boundary == "restart_adopting":
        registry.update(runner_pid=99999999, runner_start_ticks=123)

    runs = []

    def command(args, operation):
        runs.append((operation, getattr(args, "takeover_expected_cycle", None)))
        return ["offline-runner"]

    def spawn(*_, **__):
        child = MagicMock(pid=99999998)
        if runs[-1][0] == "run":
            allocate_new_cycle()
            child.wait.return_value = 1  # The authorized child died after allocating Cycle 6.
        else:
            assert registry["takeover_cycle"] is None
            assert registry["input_support_attempt"]["status"] == "advanced"
            state["status"] = "over"
            child.wait.return_value = 0
        return child

    with ExitStack() as stack:
        stack.enter_context(patch.object(supervisor.threading, "Thread"))
        stack.enter_context(patch.object(supervisor, "load_registry", side_effect=lambda *_: dict(registry)))
        stack.enter_context(patch.object(supervisor, "update_registry",
                                        side_effect=lambda _, **data: registry.update(data)))
        stack.enter_context(patch.object(supervisor, "_latest_scientific_state", side_effect=lambda *_: (
            current.name, state, None)))
        stack.enter_context(patch.object(supervisor, "latest_cycle_name", side_effect=lambda *_: current.name))
        stack.enter_context(patch.object(supervisor, "_quota_pause_context", return_value=None))
        stack.enter_context(patch.object(supervisor, "_quota_resume_started"))
        stack.enter_context(patch.object(supervisor, "_notify_supervisor_terminal"))
        stack.enter_context(patch.object(supervisor, "_runtime_env", return_value={}))
        stack.enter_context(patch.object(supervisor.time, "sleep"))
        stack.enter_context(patch("theory_master.worker_store._master_complete", return_value=False))
        stack.enter_context(patch.object(supervisor_activation, "input_support_target", return_value=plan))
        stack.enter_context(patch.object(supervisor_activation, "latest_recovery", side_effect=lambda *_, **__:
                                        (stopped, state, "input_support") if current == stopped else None))
        if boundary == "restart_adopting":
            adopted_alive = iter((True, True, False))
            stack.enter_context(patch.object(supervisor, "process_identity_alive", side_effect=lambda pid, *_:
                                            pid == 99999999 and next(adopted_alive, False)))
        stack.enter_context(patch.object(supervisor, "_runner_command", side_effect=command))
        stack.enter_context(patch.object(supervisor.subprocess, "Popen", side_effect=spawn))
        assert supervisor.main(["--task-root", str(tmp_path), "--operation", "takeover",
                                "--launch-id", "offline", "--error-repair"]) == 0

    assert runs == ([("run", stopped.name)] if boundary == "live" else []) + (
        [] if controlled else [("resume", None)])
    assert registry["input_support_attempt"]["advanced_cycle"] == advanced.name
    assert registry["input_support_takeover_cycle"] is None
