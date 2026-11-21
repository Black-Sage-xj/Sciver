"""Offline recovery of an enrolled Supervisor's existing technical wait."""

import hashlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.test_supervisor_boot_active import BOOT, OLD_BOOT, OLD_LAUNCH, SUPERVISOR_PID, SUPERVISOR_START_TICKS
from theory_master import runtime_manager, supervisor, supervisor_recovery as jobs
from theory_master.backends import FakeBackend
from theory_master.runtime import write_json
from theory_master.specification import TaskSpec
from theory_master.supervisor_activation import _qualification_wait_control
from theory_master.supervisor_boot_recovery import eligible_boot_wait, recover_task_wait
from theory_master.supervisor_intervention import write_supervisor_setting
from theory_master.supervisor_runtime_wait import runtime_wait_evidence
from theory_master.task import bootstrap
from theory_master.workflow import Workflow


class _SingleWait:
    """Observe one real scheduling pass without sleeping or starting a thread."""

    def __init__(self, callback):
        self.callback = callback
        self.stopped = False
        self.polls = 0

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        assert seconds > 0
        self.polls += 1
        self.callback()
        self.set()


@pytest.fixture
def technical_wait(tmp_path, monkeypatch):
    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal\ng\n\n# Research Contract\nc\n\n# Final Gate\nf\n\n# Required Context\n- r\n")
    spec = TaskSpec.from_config(bootstrap(plan).config_path)
    root = spec.research_root
    workflow = Workflow(spec, FakeBackend(reworks=0))
    workflow._ensure_cycle("33")
    cycle = workflow.cycle_dir
    state = {
        **workflow._new_state(), "status": "needs_attention",
        "phase": "research_lead:frontier_proposal:contract_repair_failed",
        "attention": {"code": "contract_repair_failed", "reason": "persisted contract failure"},
    }
    write_json(cycle / "research_state.json", state)
    write_json(cycle / "run_state.json", {"status": "NEEDS_ATTENTION"})
    write_supervisor_setting(root, True)
    prerequisites = {"fingerprint": jobs.digest("original runtime dependency")}
    monkeypatch.setattr(jobs, "inputs", lambda *_: prerequisites["fingerprint"])
    for reason in ("first repair disposition", "second repair disposition"):
        jobs.begin_action(cycle, state)
        job = jobs.finish_action(cycle, state, {
            "action": "hold", "reason": reason,
            "engineering_skill": {"validation": {"disposition": "needs_dependency", "dependency": "offline fixture"}},
        })
    # A passed deadline alone must not release this dependency wait.
    job["next_wakeup_epoch"] = 1_000_000_000
    jobs.save_job(cycle, job)

    def publish(operation="run"):
        monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: OLD_BOOT)
        runtime_manager.update_registry(
            root, state="RECOVERING", launch_id=OLD_LAUNCH, boot_id=OLD_BOOT,
            requested_operation=operation, takeover_cycle="32" if operation == "takeover" else None,
            current_cycle=cycle.name, master_id=spec.master_id, launcher="systemd-user",
            systemd_unit=runtime_manager.unit_name(root, spec.master_id),
            supervisor_pid=SUPERVISOR_PID, supervisor_start_ticks=SUPERVISOR_START_TICKS, supervisor_attempt=1,
            runner_pid=None, runner_start_ticks=None, runner_operation=None,
            launch_requested_at="2000-01-01T00:00:00+00:00",
        )

        def published():
            registry = runtime_manager.load_registry(root)
            assert registry["state"] == "RECOVERY_WAIT"
            assert registry["current_cycle"] == cycle.name
            assert registry["recovery_job"] == jobs.projection(cycle)

        stop = _SingleWait(published)
        with patch.object(supervisor, "_handle_failed_state") as repair:
            assert not supervisor._recover_failed_state(root, repair_enabled=True, stop=stop)
        assert stop.polls == 1
        repair.assert_not_called()
        previous = runtime_manager.load_registry(root)
        assert previous["requested_operation"] == operation
        monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: BOOT)
        return previous

    return SimpleNamespace(spec=spec, root=root, cycle=cycle, state=state,
                           prerequisites=prerequisites, publish=publish)


def _launch(technical_wait):
    systemd = SimpleNamespace(returncode=0, stdout="offline systemd launch", stderr="")
    with (
        patch.object(runtime_manager.shutil, "which", return_value="/usr/bin/offline-test-systemd"),
        patch.object(runtime_manager, "runtime_python", return_value="/usr/bin/offline-test-python"),
        patch.object(runtime_manager.subprocess, "run", return_value=systemd) as systemd_run,
        patch.object(runtime_manager.subprocess, "Popen") as detached,
    ):
        result = recover_task_wait(technical_wait.spec)
    assert result["status"] == "started"
    systemd_run.assert_called_once()
    detached.assert_not_called()
    return result["launch_id"]


def _supervisor_args(wait, launch_id):
    return ["--task-root", str(wait.root), "--operation", "takeover",
            "--identifier", str(wait.spec.master_id), "--launch-id", launch_id]


@pytest.mark.parametrize("operation", ["run", "resume", "takeover"])
def test_previous_boot_admits_the_exact_runtime_wait_without_changing_files(technical_wait, operation):
    wait = technical_wait
    previous = wait.publish(operation)
    paths = (jobs.path_for(wait.cycle), wait.cycle / "research_state.json", runtime_manager.registry_path(wait.root))
    before = tuple(path.read_bytes() for path in paths)

    plan = eligible_boot_wait(wait.root)
    assert plan == eligible_boot_wait(wait.root, registry=previous)
    assert plan is not None and plan["route"] == "runtime"
    assert (plan["cycle"], plan["boot_id"], plan["prior_launch_id"], plan["attempt"]) == (
        wait.cycle.name, BOOT, OLD_LAUNCH, 1,
    )
    origin = plan["origin"]
    assert origin["boot_id"] == OLD_BOOT and origin["launch_id"] == OLD_LAUNCH
    assert origin["master_id"] == wait.spec.master_id and origin["research_root"] == str(wait.root)
    assert origin["requested_operation"] == operation
    assert origin["takeover_cycle"] == ("32" if operation == "takeover" else None)
    dependency = origin["qualification_wait"]
    assert dependency["kind"] == "runtime_recovery"
    assert dependency["incident_key"] == jobs.incident_key(wait.state)
    assert dependency["job_sha256"] == hashlib.sha256(before[0]).hexdigest()
    with patch.object(runtime_manager, "launch_managed_runtime", return_value={"status": "started"}) as launch:
        assert recover_task_wait(wait.spec) == {"status": "started"}
    launch.assert_called_once_with(wait.spec, operation="takeover", identifier=str(wait.spec.master_id),
                                  takeover_cycle=wait.cycle.name, boot_wait=plan)
    assert tuple(path.read_bytes() for path in paths) == before


@pytest.mark.parametrize("restart", ["new_host_boot", "same_boot_service"])
def test_restarted_supervisor_polls_the_same_job_without_repair_or_runner(technical_wait, monkeypatch, restart):
    wait = technical_wait
    previous = wait.publish("takeover")
    state_path = wait.cycle / "research_state.json"
    job_before = jobs.path_for(wait.cycle).read_bytes()
    state_before = state_path.read_bytes()
    original_job = jobs.read_job(wait.cycle)
    assert original_job["plan_revision"] == 2 and original_job["action_attempt"] == 2
    assert len(original_job["history"]) == 2
    if restart == "new_host_boot":
        boot_id, launch_id = BOOT, _launch(wait)
    else:
        boot_id, launch_id = OLD_BOOT, OLD_LAUNCH
        assert previous["takeover_cycle"] == "32" and previous["current_cycle"] == "33"
        monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: OLD_BOOT)
        assert eligible_boot_wait(wait.root) is None

    def check_wait():
        registry = runtime_manager.load_registry(wait.root)
        assert registry["state"] == "RECOVERY_WAIT" and registry["launch_id"] == launch_id
        assert registry["current_cycle"] == registry["takeover_cycle"] == wait.cycle.name
        assert registry["recovery_job"] == jobs.projection(wait.cycle)
        assert registry["boot_wait"]["boot_id"] == boot_id
        assert registry["boot_wait"]["launch_id"] == launch_id
        assert registry["supervisor_attempt"] == (2 if restart == "same_boot_service" else 1)
        assert jobs.read_job(wait.cycle) == original_job
        assert not jobs.due(wait.cycle, wait.state, at=10**12)
        with patch.object(runtime_manager, "_write_json_atomic") as non_durable_write:
            saved = runtime_manager.update_registry(wait.root, heartbeat_at="heartbeat", current_cycle=wait.cycle.name)
            non_durable_write.assert_not_called()
        assert saved["boot_wait"] == registry["boot_wait"]

    stop = _SingleWait(check_wait)
    with (
        patch.object(supervisor.threading, "Thread"),
        patch.object(supervisor.threading, "Event", return_value=stop),
        patch.object(supervisor, "_handle_failed_state") as repair,
        patch.object(supervisor, "_supervisor_backend") as backend,
        patch.object(supervisor.subprocess, "Popen") as runner,
        patch.object(supervisor, "_notify_supervisor_terminal"),
    ):
        assert supervisor.main(_supervisor_args(wait, launch_id)) == 0
    assert stop.polls == 1
    repair.assert_not_called()
    backend.assert_not_called()
    runner.assert_not_called()
    assert jobs.path_for(wait.cycle).read_bytes() == job_before
    assert state_path.read_bytes() == state_before


@pytest.mark.parametrize("restart", ["new_host_boot", "same_boot_original_resume_command"])
def test_dependency_change_hands_same_incident_to_the_ordinary_reviewer(technical_wait, monkeypatch, restart):
    wait = technical_wait
    if restart == "new_host_boot":
        wait.publish()
        args = _supervisor_args(wait, _launch(wait))
    else:
        wait.publish("resume")
        monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: OLD_BOOT)
        assert str(wait.spec.master_id) != "32" and wait.cycle.name == "33"
        args = ["--task-root", str(wait.root), "--operation", "resume", "--identifier", "32",
                "--launch-id", OLD_LAUNCH]
    original = jobs.read_job(wait.cycle)
    wait.prerequisites["fingerprint"] = jobs.digest("repaired runtime dependency")
    assert jobs.due(wait.cycle, wait.state)
    state_path = wait.cycle / "research_state.json"
    state_before = state_path.read_bytes()

    def repair(root, *, repair_enabled):
        assert root == wait.root and repair_enabled
        assert jobs.read_job(wait.cycle) == original
        assert state_path.read_bytes() == state_before
        assert jobs.begin_action(wait.cycle, wait.state)["action_attempt"] == 3
        jobs.finish_action(wait.cycle, wait.state, {"action": "resume", "session_id": "SUP-offline-repair"})
        return True

    def runner_exit(timeout):
        assert timeout > 0
        assert jobs.read_job(wait.cycle)["resume_session_id"] == "SUP-offline-repair"
        write_json(state_path, {**wait.state, "status": "over", "attention": None})
        return 0

    child = SimpleNamespace(pid=99999999, wait=runner_exit)
    with (
        patch.object(supervisor.threading, "Thread"),
        patch.object(supervisor, "_handle_failed_state", side_effect=repair) as review,
        patch.object(supervisor, "_runtime_supervisor_receipt", return_value=None),
        patch.object(supervisor, "_runtime_env", return_value={}),
        patch.object(supervisor.subprocess, "Popen", return_value=child) as runner,
        patch.object(supervisor, "_notify_supervisor_terminal"),
    ):
        assert supervisor.main(args) == 0
    review.assert_called_once_with(wait.root, repair_enabled=True)
    runner.assert_called_once()
    command = runner.call_args.args[0]
    assert command[command.index("--operation") + 1] == "resume"
    assert command[command.index("--identifier") + 1] == str(wait.spec.master_id)
    assert command[command.index("--supervisor-takeover-cycle") + 1] == wait.cycle.name
    assert jobs.read_job(wait.cycle)["history"][:2] == original["history"]
    assert jobs.read_job(wait.cycle)["action_attempt"] == 3


@pytest.mark.parametrize("defect", ["different_job", "different_incident", "pause", "explicit_run",
                                    "maintenance_stop", "worker", "current_boot", "live_owner"])
def test_runtime_wait_boot_refuses_withdrawn_or_changed_authority(technical_wait, monkeypatch, defect):
    wait = technical_wait
    wait.publish()
    if defect == "different_job":
        job = jobs.read_job(wait.cycle)
        jobs.save_job(wait.cycle, {**job, "action_attempt": job["action_attempt"] + 1})
    elif defect == "different_incident":
        write_json(wait.cycle / "research_state.json", {**wait.state, "phase": "different-failure"})
    elif defect == "pause":
        write_json(wait.cycle / "recovery_control.json", {"action": "pause"})
    elif defect == "explicit_run":
        write_json(_qualification_wait_control(wait.root), {"action": "run", "launch_id": OLD_LAUNCH})
    elif defect == "maintenance_stop":
        job = jobs.read_job(wait.cycle)
        jobs.save_job(wait.cycle, {**job, "status": "stopped", "current_action": "maintenance_stop",
                                   "next_wakeup_epoch": None, "resume_session_id": None})
    elif defect == "worker":
        write_json(wait.cycle / "research_state.json", {**wait.state, "worker_id": "worker-1"})
    elif defect == "current_boot":
        monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: OLD_BOOT)
    else:
        monkeypatch.setattr(runtime_manager, "runtime_health", lambda *_: {"active": True})

    assert eligible_boot_wait(wait.root) is None
    with patch.object(runtime_manager, "launch_managed_runtime") as launch:
        assert recover_task_wait(wait.spec) == {"status": "skipped"}
    launch.assert_not_called()


@pytest.mark.parametrize("defect", ["different_job", "pause"])
def test_queued_runtime_wait_rechecks_authority_before_repair(technical_wait, defect):
    wait = technical_wait
    wait.publish()
    launch_id = _launch(wait)
    if defect == "pause":
        write_json(wait.cycle / "recovery_control.json", {"action": "pause"})
    else:
        job = jobs.read_job(wait.cycle)
        jobs.save_job(wait.cycle, {**job, "plan_revision": job["plan_revision"] + 1})
    before = jobs.path_for(wait.cycle).read_bytes()
    with (
        patch.object(supervisor.threading, "Thread"),
        patch.object(supervisor, "_recover_failed_state") as recover,
        patch.object(supervisor, "_notify_supervisor_terminal"),
        patch.object(supervisor.subprocess, "Popen") as runner,
    ):
        assert supervisor.main(_supervisor_args(wait, launch_id)) == 0
    recover.assert_not_called()
    runner.assert_not_called()
    assert jobs.path_for(wait.cycle).read_bytes() == before


def test_operator_pause_after_scheduler_admission_prevents_repair(technical_wait):
    wait = technical_wait
    before = jobs.path_for(wait.cycle).read_bytes()
    wait.prerequisites["fingerprint"] = jobs.digest("repaired runtime dependency")
    assert jobs.due(wait.cycle, wait.state)

    def pause_after_admission(root):
        assert root == wait.root
        write_json(wait.cycle / "recovery_control.json", {"action": "pause"})
        return []

    with (
        patch("theory_master.worker_store.list_workers", side_effect=pause_after_admission) as workers,
        patch("theory_master.supervisor_continuity.review_and_repair_runtime_failure") as review,
        patch.object(supervisor, "_supervisor_backend") as backend,
    ):
        assert not supervisor._handle_failed_state(wait.root, repair_enabled=True)
    workers.assert_called_once_with(wait.root)
    backend.assert_not_called()
    review.assert_not_called()
    assert jobs.path_for(wait.cycle).read_bytes() == before


@pytest.mark.parametrize(("field", "value"), [("next_wakeup_epoch", 0), ("input_digest", "invalid")])
def test_malformed_recovery_job_cannot_publish_boot_evidence(technical_wait, field, value):
    wait = technical_wait
    assert runtime_wait_evidence(wait.root, wait.cycle, wait.state) is not None
    job = jobs.read_job(wait.cycle)
    jobs.save_job(wait.cycle, {**job, field: value})
    assert runtime_wait_evidence(wait.root, wait.cycle, wait.state) is None
