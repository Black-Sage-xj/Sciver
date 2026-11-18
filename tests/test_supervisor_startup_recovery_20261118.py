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
