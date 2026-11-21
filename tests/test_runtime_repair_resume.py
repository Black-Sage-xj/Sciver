"""Recover an authorized technical repair interrupted before Runner admission."""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.test_supervisor_activation_20261121 import stopped_task
from tests.test_supervisor_boot_active import BOOT, NEW_LAUNCH, OLD_BOOT, OLD_LAUNCH, SUPERVISOR_PID, SUPERVISOR_START_TICKS
from tests.test_supervisor_repair_receipts_20261022 import method_receipt, stopped
from theory_master import resume_preparation, runner, runtime_manager, supervisor, supervisor_recovery as jobs
from theory_master import supervisor_repair_receipts as receipts
from theory_master.backends import FakeBackend
from theory_master.cycle_runner import CycleRunner
from theory_master.runtime import write_json
from theory_master.supervisor_boot_recovery import eligible_boot_resume, recover_task_active
from theory_master.supervisor_intervention import MAX_RUNTIME_RESUMES, write_supervisor_setting
from theory_master.workflow import Workflow


class _SupervisorKilled(BaseException):
    pass


@pytest.fixture
def authorized_crash(stopped_task, stopped, monkeypatch, request):
    job_status, options = getattr(request, "param", ("resume_authorized", {}))
    _binding, spec, _initial_cycle, _initial_state = stopped_task
    engine, _source_cycle, stopped_state, log = stopped
    root = spec.research_root
    workflow = Workflow(spec, FakeBackend(reworks=0))
    workflow._ensure_cycle("33")
    cycle = workflow.cycle_dir
    checkpoint = cycle / "checkpoints/000001-locked.json"
    checkpoint.parent.mkdir(exist_ok=True)
    write_json(checkpoint, {"cycle_id": cycle.name, "hypothesis_lock": stopped_state["hypothesis_lock"]})
    state = {**workflow._new_state(), **stopped_state, "cycle_id": cycle.name,
             "last_safe_checkpoint": str(checkpoint), "supervisor_runtime_resume_count": MAX_RUNTIME_RESUMES,
             "invocation_cycle_index": 4}
    state_path = cycle / "research_state.json"
    write_json(state_path, state)
    validation = method_receipt((engine, cycle, state, log))
    assert validation["phase"] == "repair_validated"
    write_json(state_path, state)
    write_supervisor_setting(root, True)
    monkeypatch.setattr(receipts, "__file__", str(engine / "supervisor_repair_receipts.py"))
    monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: OLD_BOOT)
    runtime_manager.update_registry(
        root, state="RECOVERING", launch_id=OLD_LAUNCH, boot_id=OLD_BOOT, launcher="systemd-user",
        systemd_unit=runtime_manager.unit_name(root, spec.master_id), master_id=spec.master_id,
        requested_operation="run", current_cycle=cycle.name, takeover_cycle=None, supervisor_attempt=1,
        supervisor_pid=os.getpid(), supervisor_start_ticks=runtime_manager.process_start_ticks(os.getpid()),
        runner_pid=None, runner_start_ticks=None, runner_operation=None, runtime_repair_options=options,
    )
    backend = MagicMock()
    with patch.object(supervisor, "_supervisor_backend", return_value=backend):
        if job_status == "working":
            with patch.object(jobs, "finish_action", side_effect=_SupervisorKilled), pytest.raises(_SupervisorKilled):
                supervisor._handle_failed_state(root, repair_enabled=True)
        else:
            assert supervisor._handle_failed_state(root, repair_enabled=True)
    backend.invoke_runtime_review.assert_not_called()

    # The process died at this boundary. The original model was never resumed.
    state = json.loads(state_path.read_text())
    job = jobs.read_job(cycle)
    session = state["supervisor_runtime_review"]["session_id"]
    assert state["supervisor_runtime_resume_count"] == MAX_RUNTIME_RESUMES + 1
    assert state["supervisor_consumed_repairs"] == [receipts.repair_credit_key(state)]
    assert not receipts.unused_repair_credit(state)
    assert job["status"] == job_status and job["action_attempt"] == 1
    if job_status == "working":
        assert job.get("resume_session_id") is None and job["owner_pid"] == os.getpid()
    else:
        assert job["current_action"] == "verify_runtime_activity" and job["resume_session_id"] == session
    authorization = state["supervisor_runtime_resume_authorization"]
    assert authorization["session_id"] == session and authorization.get("claimed_by") is None
    assert runtime_manager.load_registry(root)["runner_pid"] is None
    runtime_manager.update_registry(root, supervisor_pid=SUPERVISOR_PID, supervisor_start_ticks=SUPERVISOR_START_TICKS)
    return SimpleNamespace(root=root, spec=spec, cycle=cycle, state=state, state_path=state_path,
                           job=job, session=session)


def test_handler_persists_expended_credit_before_any_runner(authorized_crash):
    crash = authorized_crash
    assert supervisor._runtime_supervisor_receipt(crash.root) == (crash.cycle.name, crash.session)
    assert crash.state["status"] == "failed"


@pytest.mark.parametrize("restart", ["same_boot", "host_reboot"])
@pytest.mark.parametrize("authorized_crash", [("resume_authorized", {}), ("working", {})],
                         indirect=True, ids=["resume_authorized", "working"])
def test_restarted_supervisor_dispatches_the_existing_authorization_once(authorized_crash, monkeypatch, restart):
    crash = authorized_crash
    state_before = crash.state_path.read_bytes()
    job_before = jobs.path_for(crash.cycle).read_bytes()
    if crash.job["status"] == "working":
        jobs.record_activity(crash.cycle, verified=False, detail="attempt before Runner claim", session_id=crash.session)
        assert jobs.path_for(crash.cycle).read_bytes() == job_before
    launch_id, operation = OLD_LAUNCH, "run"
    if restart == "host_reboot":
        monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: BOOT)
        with (
            patch.object(runtime_manager.shutil, "which", return_value="/usr/bin/offline-systemd"),
            patch.object(runtime_manager, "runtime_python", return_value="/usr/bin/offline-python"),
            patch.object(runtime_manager.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="")),
            patch.object(runtime_manager.subprocess, "Popen") as detached,
        ):
            launched = recover_task_active(crash.spec)
        assert launched["status"] == "started"
        detached.assert_not_called()
        launch_id, operation = launched["launch_id"], "takeover"

    class OfflineBackend(FakeBackend):
        def preflight_runtime(self):
            pytest.fail("Repair Resume must claim the authorization before provider preflight")

        def invoke(self, *_args, **_kwargs):
            pytest.fail("This test stops at admission to the original Cycle")

    continued = []

    def continue_cycle(workflow, state, **_kwargs):
        from theory_master.supervisor_runtime_resume import verify_child

        assert workflow.cycle_dir == crash.cycle
        assert state["supervisor_runtime_review"]["session_id"] == crash.session
        assert state["supervisor_consumed_repairs"] == crash.state["supervisor_consumed_repairs"]
        assert state["supervisor_runtime_resume_count"] == crash.state["supervisor_runtime_resume_count"]
        durable = json.loads(crash.state_path.read_bytes())
        assert durable["invocation_cycle_index"] == 1
        assert durable["supervisor_runtime_resume_authorization"]["claimed_by"] == {
            "launch_id": launch_id, "pid": os.getpid(), "start_ticks": runtime_manager.process_start_ticks(os.getpid()),
        }
        assert verify_child(crash.root, launch_id, crash.cycle.name) is None
        continued.append(workflow.cycle_dir)
        terminal = {**state, "status": "over", "execution_active": False, "owner_pid": None}
        write_json(crash.state_path, terminal)
        return terminal

    monkeypatch.setattr(Workflow, "_run_cycle", continue_cycle)
    monkeypatch.setattr(Workflow, "_can_continue_cycle_budget", lambda *_args, **_kwargs: False)

    def spawn(command, **kwargs):
        assert command[command.index("--operation") + 1] == "resume"
        assert command[command.index("--runtime-repair-resume-launch") + 1] == launch_id
        assert "--managed-child" in command
        assert "--boot-resume-launch" not in command
        assert command[command.index("--identifier") + 1] == str(crash.spec.master_id)
        assert command[command.index("--supervisor-takeover-cycle") + 1] == crash.cycle.name
        assert crash.state_path.read_bytes() == state_before
        assert jobs.path_for(crash.cycle).read_bytes() == job_before

        def exit_child(timeout):
            with monkeypatch.context() as child:
                child.setattr(runner.os, "getppid", os.getpid)
                return runner.main(command[3:])

        return SimpleNamespace(pid=os.getpid(), wait=exit_child)

    stop = MagicMock()
    stop.is_set.return_value = False
    stop.wait.side_effect = AssertionError("authorized repair must dispatch, not wait for a second triage")
    with (
        patch.object(supervisor.threading, "Thread"),
        patch.object(supervisor.threading, "Event", return_value=stop),
        patch.object(supervisor, "_handle_failed_state", side_effect=AssertionError("duplicate review")) as review,
        patch.object(supervisor, "_supervisor_backend") as backend,
        patch.object(supervisor, "_runtime_env", return_value={}),
        patch.object(supervisor, "_verified_model_activity", return_value=""),
        patch.object(supervisor, "_record_runtime_verification", wraps=supervisor._record_runtime_verification) as activity,
        patch.object(supervisor, "_notify_supervisor_terminal"),
        patch.object(supervisor.subprocess, "Popen", side_effect=spawn) as popen,
        patch.object(runner, "CodexBackend", return_value=OfflineBackend(reworks=0)),
        patch.object(runner, "model_route_preflight_lines", return_value=[]),
        patch.object(runner, "Collector", return_value=None),
        patch.object(resume_preparation, "consume_resume_repairs", wraps=resume_preparation.consume_resume_repairs) as consume,
    ):
        assert supervisor.main(["--task-root", str(crash.root), "--operation", operation,
                                "--identifier", str(crash.spec.master_id), "--launch-id", launch_id]) == 0
    review.assert_not_called()
    backend.assert_not_called()
    popen.assert_called_once()
    consume.assert_called_once()
    assert consume.call_args.args[0] == crash.cycle
    assert continued == [crash.cycle]
    assert activity.call_args.args[1] == (crash.cycle.name, crash.session)
    assert not activity.call_args.kwargs["verified"]
    finished = jobs.read_job(crash.cycle)
    assert finished["status"] == "waiting" and finished["owner_pid"] is None
    assert finished["resume_session_id"] == crash.session and finished["action_attempt"] == 1
    assert finished["history"][-1]["action_id"] == crash.job["action_id"]


@pytest.mark.parametrize("authorized_crash", [("resume_authorized", {"cycle_audit": False, "attention_takeover": True})],
                         indirect=True, ids=["saved_options"])
def test_physical_boot_pins_the_original_authorization_and_options(authorized_crash, monkeypatch, capsys):
    crash = authorized_crash
    monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: BOOT)
    paths = [crash.state_path, jobs.path_for(crash.cycle), runtime_manager.registry_path(crash.root)]
    before = [path.read_bytes() for path in paths]
    plan = eligible_boot_resume(crash.root)
    assert plan is not None and plan["kind"] == "runtime_repair_resume"
    assert (plan["boot_id"], plan["prior_launch_id"], plan["cycle"], plan["attempt"]) == (
        BOOT, OLD_LAUNCH, crash.cycle.name, 1,
    )
    authorization = plan["origin"]["authorization"]
    assert authorization["session_id"] == crash.session
    assert authorization["options"] == {"cycle_audit": False, "attention_takeover": True}
    assert plan["origin"]["job_sha256"] == hashlib.sha256(before[1]).hexdigest()
    with patch.object(runtime_manager, "launch_managed_runtime", return_value={"status": "started"}) as launch:
        assert recover_task_active(crash.spec) == {"status": "started"}
    launch.assert_called_once_with(crash.spec, operation="takeover", identifier=str(crash.spec.master_id),
                                  takeover_cycle=crash.cycle.name, boot_resume=plan,
                                  cycle_audit=False, attention_takeover=True)

    with (
        patch("theory_master.supervisor_runtime_resume.verify_child", return_value=plan["origin"]) as verify,
        patch.object(runner, "resolve_routes") as routes,
        patch.object(runner, "CodexBackend") as backend,
        pytest.raises(SystemExit) as rejected,
    ):
        runner.main([
            "--task-root", str(crash.root), "--operation", "resume", "--identifier", str(crash.spec.master_id),
            "--managed-child", "--supervisor-takeover-cycle", crash.cycle.name,
            "--runtime-repair-resume-launch", NEW_LAUNCH, "--cycle-audit", "--attention-takeover",
        ])
    assert rejected.value.code == 2 and "launch options changed" in capsys.readouterr().err
    verify.assert_called_once_with(crash.root, NEW_LAUNCH, crash.cycle.name)
    routes.assert_not_called()
    backend.assert_not_called()
    assert [path.read_bytes() for path in paths] == before


@pytest.mark.parametrize("change", ["pause", "other_incident", "checkpoint", "session", "consumption", "claimed"])
def test_physical_boot_plan_rejects_changed_authority(authorized_crash, monkeypatch, change):
    crash = authorized_crash
    monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: BOOT)
    assert eligible_boot_resume(crash.root) is not None
    if change == "pause":
        write_json(crash.cycle / "recovery_control.json", {"action": "pause"})
    elif change == "other_incident":
        write_json(crash.state_path, {**crash.state, "recovery": {**crash.state["recovery"], "at": "later incident"}})
    elif change == "checkpoint":
        write_json(Path(crash.state["last_safe_checkpoint"]), {"cycle_id": crash.cycle.name})
    elif change == "session":
        jobs.save_job(crash.cycle, {**crash.job, "resume_session_id": "SUP-other"})
    elif change == "consumption":
        write_json(crash.state_path, {**crash.state, "supervisor_consumed_repairs": []})
    elif change == "claimed":
        authorization = crash.state["supervisor_runtime_resume_authorization"]
        write_json(crash.state_path, {**crash.state, "supervisor_runtime_resume_authorization": {
            **authorization, "claimed_by": {"launch_id": OLD_LAUNCH, "pid": SUPERVISOR_PID,
                                             "start_ticks": SUPERVISOR_START_TICKS},
        }})
    assert eligible_boot_resume(crash.root) is None


def test_pause_after_durable_claim_interrupts_before_qualification(stopped_task, monkeypatch):
    from theory_master import supervisor_runtime_resume as repair

    _binding, spec, cycle, state = stopped_task
    path = cycle / "research_state.json"
    write_json(path, {**state, "resumable": True})
    expected_bytes = path.read_bytes()
    launch_id = "test-pending-authorization"
    authorization = {"session_id": "SUP-exact", "options": repair.launch_options()}
    ticket = {"cycle": cycle.name, "authorization": authorization,
              "state_sha256": hashlib.sha256(expected_bytes).hexdigest()}
    verifications = []

    def verify_child(root, launch, cycle_name):
        assert (root, launch, cycle_name) == (spec.research_root, launch_id, cycle.name)
        assert path.read_bytes() == expected_bytes
        verifications.append(cycle_name)
        return ticket

    class OfflineBackend(FakeBackend):
        def preflight_runtime(self):
            pytest.fail("Pause must reach the Cycle before provider preflight")

        def invoke(self, *_args, **_kwargs):
            pytest.fail("Pause must reach the Cycle before model invocation")

    persist = Workflow._persist

    def persist_with_pause(workflow, checked_state, **kwargs):
        persist(workflow, checked_state, **kwargs)
        if kwargs.get("sync_directory"):
            saved = json.loads(path.read_bytes())
            assert saved["execution_active"] and saved["owner_pid"] == os.getpid()
            assert saved[repair.AUTHORIZATION]["claimed_by"]["launch_id"] == launch_id
            write_json(cycle / "recovery_control.json", {"action": "pause"})

    def unexpected_qualification(_owner):
        pytest.fail("Pause must reach the Cycle before qualification")

    monkeypatch.setattr(repair, "verify_child", verify_child)
    monkeypatch.setattr(runner, "CodexBackend", lambda *_args, **_kwargs: OfflineBackend(reworks=0))
    monkeypatch.setattr(runner, "Collector", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Workflow, "_persist", persist_with_pause)
    monkeypatch.setattr(CycleRunner, "_qualify", unexpected_qualification)
    assert runner.main([
        "--task-root", str(spec.research_root), "--operation", "resume",
        "--identifier", str(spec.master_id), "--managed-child",
        "--supervisor-takeover-cycle", cycle.name, "--runtime-repair-resume-launch", launch_id,
    ]) == 0
    paused = json.loads(path.read_bytes())
    assert verifications == [cycle.name] * 3
    assert paused["status"] == "paused" and not paused["execution_active"]
    assert paused[repair.AUTHORIZATION]["session_id"] == "SUP-exact"
