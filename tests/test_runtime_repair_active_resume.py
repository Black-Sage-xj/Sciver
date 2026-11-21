"""Replay only the committed invocation boundary of an already-claimed repair."""

import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.test_runtime_repair_resume import authorized_crash
from tests.test_supervisor_activation_20261121 import stopped_task
from tests.test_supervisor_boot_active import (
    BOOT,
    OLD_BOOT,
    OLD_LAUNCH,
    RUNNER_CHECKPOINT_PROTOCOL,
    RUNNER_PID,
    RUNNER_START_TICKS,
    SUPERVISOR_PID,
    SUPERVISOR_START_TICKS,
)
from tests.test_supervisor_repair_receipts_20261022 import stopped
from theory_master import resume_preparation, runner, runtime_manager, supervisor, supervisor_recovery as jobs
from theory_master.backends import FakeBackend
from theory_master.invocations import InvocationLedger
from theory_master.runtime import write_json
from theory_master.supervisor_boot_recovery import _active_evidence, eligible_boot_resume, recover_task_active
from theory_master.supervisor_runtime_resume import AUTHORIZATION
from theory_master.workflow import Workflow

OPTIONS = {"routes": ["research_lead=gpt-5.6@codex"], "cycle_audit": False, "attention_takeover": True}
OPTIONS_FLAGS = ["--route", OPTIONS["routes"][0], "--no-cycle-audit", "--attention-takeover"]
CHECKPOINT_SLOT = 2


def _offline_systemd_launch(spec):
    systemd = SimpleNamespace(returncode=0, stdout="offline systemd takeover", stderr="")
    with (
        patch.object(runtime_manager.shutil, "which", return_value="/usr/bin/offline-systemd"),
        patch.object(runtime_manager, "runtime_python", return_value="/usr/bin/offline-python"),
        patch.object(runtime_manager.subprocess, "run", return_value=systemd) as launch,
    ):
        result = recover_task_active(spec)
    assert result["status"] == "started"
    return result["launch_id"], launch.call_args.args[0]


@pytest.fixture
def paired_loss(authorized_crash, monkeypatch, request):
    crash = authorized_crash
    transition = getattr(request, "param", "cold_takeover")
    if transition != "ordinary_run":
        monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: BOOT)
        launch_id, _command = _offline_systemd_launch(crash.spec)
        boot, operation = BOOT, "takeover"
    else:
        launch_id, boot, operation = OLD_LAUNCH, OLD_BOOT, "run"
    launch_registry = runtime_manager.load_registry(crash.root)

    ledger_path = crash.cycle / "invocations.jsonl"
    invocation = "inv-33-research_lead-0123456789ab"
    ledger = InvocationLedger(ledger_path)
    ledger.prepare(invocation_id=invocation, kind="agent", role="research_lead", stage="hypothesis_display",
                   input_payload={"hypothesis_id": "H-1"})
    ledger.transition(invocation, "running", role="research_lead", stage="hypothesis_display")
    ledger.transition(invocation, "completed", role="research_lead", stage="hypothesis_display")
    ledger.transition(invocation, "committed", role="research_lead", stage="hypothesis_display",
                      commit_state="committed")

    checkpoint = crash.cycle / "checkpoints" / "000010-hypothesis_locked.json"
    state = {**crash.state, "status": "active", "phase": "qualification:locked", "execution_active": True,
             "master_id": crash.spec.master_id, "owner_pid": RUNNER_PID, "owner_start_ticks": RUNNER_START_TICKS,
             "last_seq": 10, "invocation_cycle_index": CHECKPOINT_SLOT, "attempt_id": "a" * 32,
             "hypothesis_id": "H-1", "hypothesis_locked": True, "last_safe_checkpoint": str(checkpoint),
             "role_outputs": {"research_lead_hypothesis_display": {"canvas_title": "A bounded transfer relation"}},
             "active_invocation_id": None, "current_turn_id": None, "last_invocation_id": invocation,
             "recovery": {**crash.state["recovery"], "action": "resume"}}
    state[AUTHORIZATION] = {**state[AUTHORIZATION], "claimed_by": {
        "launch_id": launch_id, "pid": RUNNER_PID, "start_ticks": RUNNER_START_TICKS,
    }}
    write_json(checkpoint, state)
    write_json(crash.state_path, state)
    active_bytes = crash.state_path.read_bytes()
    registry = runtime_manager.update_registry(
        crash.root, state="RECOVERING", runner_operation="resume", runner_pid=RUNNER_PID,
        runner_start_ticks=RUNNER_START_TICKS, runner_checkpoint_protocol=RUNNER_CHECKPOINT_PROTOCOL,
        supervisor_pid=SUPERVISOR_PID, supervisor_start_ticks=SUPERVISOR_START_TICKS, supervisor_attempt=1,
    )
    if operation == "takeover":
        assert registry["boot_resume"]["kind"] == "runtime_repair_resume"
        assert registry["boot_resume"]["launch_id"] == registry["launch_id"] == launch_id
    assert registry["boot_id"] == boot and not runtime_manager.runtime_health(crash.root)["active"]
    evidence = _active_evidence(crash.root, {**registry, "cycle": crash.cycle.name}, claimed_runtime_repair=True)
    assert evidence is not None and evidence["invocation_cycle_index"] == CHECKPOINT_SLOT
    origin_launch = launch_id
    identifier = str(crash.spec.master_id)
    if transition == "physical_reboot":
        boot = "975ff740-b3d8-4f34-95dd-e84db5e141d8"
        monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: boot)
        plan = eligible_boot_resume(crash.root)
        assert plan["kind"] == "runtime_repair_active_resume" and plan["prior_launch_id"] == origin_launch
        launch_id, command = _offline_systemd_launch(crash.spec)
        assert launch_id != origin_launch
        assert command[command.index("--route") + 1] == OPTIONS["routes"][0]
        assert "--no-cycle-audit" in command and "--attention-takeover" in command
        operation, identifier = "resume", crash.cycle.name
        registry = runtime_manager.load_registry(crash.root)
        assert registry["supervisor_attempt"] == 0
        assert registry["boot_resume"] == {**plan, "launch_id": launch_id}
    elif transition == "claimed_child_exit":
        # The original cold launch will first dispatch its unclaimed repair.
        # The simulated first child publishes the active checkpoint on exit.
        write_json(crash.state_path, crash.state)
        write_json(runtime_manager.registry_path(crash.root), launch_registry)
    argv = ["--task-root", str(crash.root), "--operation", operation,
            "--identifier", identifier, "--launch-id", launch_id]
    if state[AUTHORIZATION]["options"]:
        assert state[AUTHORIZATION]["options"] == OPTIONS
        argv += OPTIONS_FLAGS
    return SimpleNamespace(crash=crash, active=state, claim=state[AUTHORIZATION], launch_id=launch_id,
                           origin_launch=origin_launch, transition=transition, boot=boot, argv=argv,
                           ledger=ledger_path, checkpoint=checkpoint, active_bytes=active_bytes)


@pytest.mark.parametrize("authorized_crash", [("resume_authorized", OPTIONS)], indirect=True)
@pytest.mark.parametrize("paired_loss", [
    "cold_takeover", "ordinary_run", "physical_reboot", "monitored_runner", "claimed_child_exit",
], indirect=True)
def test_replays_original_claimed_invocation(paired_loss, monkeypatch):
    from theory_master.supervisor_boot_recovery import RUNTIME_REPAIR_ACTIVE_RESUME

    lost = paired_loss
    crash = lost.crash
    ledger_before = lost.ledger.read_bytes()
    checkpoint_before = lost.checkpoint.read_bytes()
    continued = []
    first_claim = lost.transition == "claimed_child_exit"
    if first_claim:
        real_ticks = supervisor.process_start_ticks
        monkeypatch.setattr(supervisor, "process_start_ticks", lambda pid: (
            RUNNER_START_TICKS if pid == RUNNER_PID else real_ticks(pid)
        ))
    runner_alive = lost.transition == "monitored_runner"
    if runner_alive:
        actual_identity = supervisor.process_identity_alive

        def previous_runner_alive(pid, ticks=None):
            return runner_alive if pid == RUNNER_PID else actual_identity(pid, ticks)

        def previous_runner_exits(_interval):
            nonlocal runner_alive
            runner_alive = False

        monkeypatch.setattr(supervisor, "process_identity_alive", previous_runner_alive)
        monkeypatch.setattr(supervisor.time, "sleep", previous_runner_exits)

    class OfflineBackend(FakeBackend):
        def preflight_runtime(self):
            pass

        def invoke(self, *_args, **_kwargs):
            pytest.fail("Stop at the durable continuation boundary before a scientific model call")

    def continue_cycle(owner, state, *, same_cycle_continuation):
        assert owner.cycle_dir == crash.cycle and same_cycle_continuation is True
        assert owner.invocation_cycle_index == CHECKPOINT_SLOT and owner._boot_resume_active
        assert state["last_invocation_id"] == lost.active["last_invocation_id"]
        assert state["supervisor_consumed_repairs"] == lost.active["supervisor_consumed_repairs"]
        assert state["supervisor_runtime_resume_count"] == lost.active["supervisor_runtime_resume_count"]
        durable = json.loads(crash.state_path.read_bytes())
        assert durable["execution_active"] and durable["last_invocation_id"] == state["last_invocation_id"]
        assert durable["invocation_cycle_index"] == CHECKPOINT_SLOT
        assert durable[AUTHORIZATION]["session_id"] == lost.claim["session_id"]
        assert durable[AUTHORIZATION]["active_resume_attempt"] == 1
        current_ticks = runtime_manager.process_start_ticks(os.getpid())
        assert durable["owner_pid"] == os.getpid() and durable["owner_start_ticks"] == current_ticks
        assert durable[AUTHORIZATION]["claimed_by"] == {
            "launch_id": lost.launch_id, "pid": os.getpid(), "start_ticks": current_ticks,
        }
        assert lost.ledger.read_bytes() == ledger_before and lost.checkpoint.read_bytes() == checkpoint_before
        continued.append(owner.cycle_dir)
        state = {**state, "status": "over", "execution_active": False, "owner_pid": None}
        write_json(crash.state_path, state)
        return state

    def spawn(command, **_kwargs):
        assert command[command.index("--operation") + 1] == "resume"
        if "--runtime-repair-resume-launch" in command:
            assert first_claim
            assert command[command.index("--runtime-repair-resume-launch") + 1] == lost.launch_id

            def original_child_claims_then_exits(timeout):
                assert runtime_manager.load_registry(crash.root)["runner_pid"] == RUNNER_PID
                crash.state_path.write_bytes(lost.active_bytes)
                return 1

            return SimpleNamespace(pid=RUNNER_PID, wait=original_child_claims_then_exits)

        assert command[command.index("--identifier") + 1] == crash.cycle.name
        assert command[command.index("--boot-resume-launch") + 1] == lost.launch_id
        assert "--runtime-repair-resume-launch" not in command and "--supervisor-takeover-cycle" not in command
        assert command[command.index("--route") + 1] == OPTIONS["routes"][0]
        assert "--no-cycle-audit" in command and "--attention-takeover" in command
        receipt = runtime_manager.load_registry(crash.root)["boot_resume"]
        assert (receipt["kind"], receipt["launch_id"], receipt["boot_id"], receipt["attempt"]) == (
            RUNTIME_REPAIR_ACTIVE_RESUME, lost.launch_id, lost.boot, 1,
        )
        origin = receipt["origin"]
        assert origin["options"] == OPTIONS and origin["invocation_cycle_index"] == CHECKPOINT_SLOT
        assert origin["launch_id"] == lost.origin_launch
        if lost.transition in {"monitored_runner", "claimed_child_exit"}:
            assert not runner_alive and origin["supervisor_pid"] == os.getpid()
        assert (origin["runner_pid"], origin["runner_start_ticks"]) == (RUNNER_PID, RUNNER_START_TICKS)
        assert origin["state_sha256"] == hashlib.sha256(lost.active_bytes).hexdigest()
        assert origin["ledger_sha256"] == hashlib.sha256(ledger_before).hexdigest()
        assert origin["checkpoint_sha256"] == hashlib.sha256(checkpoint_before).hexdigest()

        def exit_child(timeout):
            with monkeypatch.context() as child_process:
                child_process.setattr(runner.os, "getppid", os.getpid)
                return runner.main(command[3:])

        return SimpleNamespace(pid=os.getpid(), wait=exit_child)

    monkeypatch.setattr(Workflow, "_run_cycle", continue_cycle)
    monkeypatch.setattr(Workflow, "_can_continue_cycle_budget", lambda *_args, **_kwargs: False)
    with (
        patch.object(supervisor.threading, "Thread"),
        patch.object(supervisor, "_handle_failed_state") as review,
        patch.object(supervisor, "_supervisor_backend") as backend,
        patch.object(supervisor, "_runtime_env", return_value={}),
        patch.object(supervisor, "_verified_model_activity", return_value=""),
        patch.object(supervisor, "_notify_supervisor_terminal"),
        patch.object(supervisor.subprocess, "Popen", side_effect=spawn) as popen,
        patch.object(runner, "CodexBackend", return_value=OfflineBackend(reworks=0)),
        patch.object(runner, "model_route_preflight_lines", return_value=[]),
        patch.object(runner, "Collector", return_value=None),
        patch.object(resume_preparation, "consume_resume_repairs") as consume,
    ):
        assert supervisor.main(lost.argv) == 0
    review.assert_not_called()
    backend.assert_not_called()
    consume.assert_not_called()
    registry = runtime_manager.load_registry(crash.root)
    assert popen.call_count == (2 if first_claim else 1), (registry["state"], registry.get("boot_resume_outcome"))
    assert continued == [crash.cycle]
    assert registry["launch_id"] == lost.launch_id and registry["boot_id"] == lost.boot
    recorded_job = jobs.read_job(crash.cycle)
    assert recorded_job["action_attempt"] == crash.job["action_attempt"]
    assert recorded_job["resume_session_id"] == crash.job["resume_session_id"]


@pytest.fixture
def bound_replay(paired_loss):
    from theory_master.supervisor_boot_recovery import claimed_runtime_resume, verify_boot_resume

    lost = paired_loss
    root = lost.crash.root
    receipt = claimed_runtime_resume(root, runtime_manager.load_registry(root), launch_id=lost.launch_id)
    assert receipt is not None
    runtime_manager.update_registry(
        root, state="STARTING", boot_resume=receipt, supervisor_pid=os.getpid(),
        supervisor_start_ticks=runtime_manager.process_start_ticks(os.getpid()),
        runner_pid=None, runner_start_ticks=None, requested_operation="resume", takeover_cycle=None,
    )
    assert verify_boot_resume(root, lost.launch_id) == receipt
    return lost, receipt


@pytest.mark.parametrize("changed", ["checkpoint", "ledger", "control", "options"])
def test_late_change_revokes_the_exact_replay_ticket(bound_replay, changed):
    from theory_master.supervisor_boot_recovery import verify_boot_resume

    lost, _receipt = bound_replay
    crash = lost.crash
    if changed == "checkpoint":
        write_json(lost.checkpoint, {**lost.active, "operator_revision": 2})
    elif changed == "ledger":
        ledger = InvocationLedger(lost.ledger)
        invocation = "inv-33-research_lead-0123456789ac"
        ledger.prepare(invocation_id=invocation, kind="agent", role="research_lead", stage="hypothesis_display",
                       input_payload={"hypothesis_id": "H-1"})
        ledger.transition(invocation, "running", role="research_lead", stage="hypothesis_display")
        ledger.transition(invocation, "completed", role="research_lead", stage="hypothesis_display")
        ledger.transition(invocation, "committed", role="research_lead", stage="hypothesis_display",
                          commit_state="committed")
    elif changed == "control":
        write_json(crash.cycle / "recovery_control.json", {"action": "pause"})
    elif changed == "options":
        runtime_manager.update_registry(crash.root, runtime_repair_options={"cycle_audit": True})
    if changed in {"checkpoint", "ledger"}:
        # Both edits leave a readable ordinary boundary. Replay still owes the
        # previously pinned bytes, so a fresh proof is insufficient.
        registry = runtime_manager.load_registry(crash.root)
        assert _active_evidence(crash.root, {
            **registry, "cycle": crash.cycle.name, "runner_pid": RUNNER_PID,
            "runner_start_ticks": RUNNER_START_TICKS,
        }, claimed_runtime_repair=True) is not None
    assert verify_boot_resume(crash.root, lost.launch_id) is None


@pytest.mark.parametrize("identity", ["runner", "supervisor"])
def test_old_live_execution_identity_prevents_replay_mint(paired_loss, monkeypatch, identity):
    from theory_master.supervisor_boot_recovery import claimed_runtime_resume

    root = paired_loss.crash.root
    registry = runtime_manager.load_registry(root)
    assert claimed_runtime_resume(root, registry, launch_id=paired_loss.launch_id) is not None
    alive = runtime_manager.process_identity_alive
    old_pid = RUNNER_PID if identity == "runner" else SUPERVISOR_PID

    def identity_alive(pid, start_ticks=None):
        return pid == old_pid or alive(pid, start_ticks)

    monkeypatch.setattr(runtime_manager, "process_identity_alive", identity_alive)
    assert claimed_runtime_resume(root, registry, launch_id=paired_loss.launch_id) is None


@pytest.mark.parametrize("paired_loss", ["cold_takeover", "ordinary_run"], indirect=True)
def test_claimed_cycle_with_pending_pause_does_not_fall_back_to_ordinary_resume(paired_loss):
    lost = paired_loss
    crash = lost.crash
    control = crash.cycle / "recovery_control.json"
    write_json(control, {"action": "pause"})
    control_before = control.read_bytes()
    state_before = crash.state_path.read_bytes()
    with (
        patch.object(supervisor.threading, "Thread"),
        patch.object(supervisor, "_handle_failed_state") as review,
        patch.object(supervisor, "_notify_supervisor_terminal"),
        patch.object(supervisor.subprocess, "Popen") as popen,
    ):
        assert supervisor.main(lost.argv) == 0
    review.assert_not_called()
    popen.assert_not_called()
    assert runtime_manager.load_registry(crash.root)["state"] == "STOPPED"
    assert control.read_bytes() == control_before and crash.state_path.read_bytes() == state_before


@pytest.mark.parametrize("authorized_crash", [("resume_authorized", OPTIONS)], indirect=True)
def test_managed_runner_rejects_changed_launch_options_before_initializing_backend(bound_replay, monkeypatch):
    from theory_master.supervisor_boot_recovery import verify_boot_resume

    lost, receipt = bound_replay
    crash = lost.crash
    runtime_manager.update_registry(crash.root, state="RECOVERING", runner_pid=os.getpid(),
                                    runner_start_ticks=runtime_manager.process_start_ticks(os.getpid()))
    monkeypatch.setattr(runner.os, "getppid", os.getpid)
    assert verify_boot_resume(crash.root, lost.launch_id, managed_child=True) == receipt
    with (
        patch.object(runner, "resolve_routes") as routes,
        patch.object(runner, "CodexBackend") as backend,
        pytest.raises(SystemExit) as rejected,
    ):
        runner.main([
            "--task-root", str(crash.root), "--operation", "resume", "--identifier", crash.cycle.name,
            "--managed-child", "--boot-resume-launch", lost.launch_id,
            "--route", OPTIONS["routes"][0], "--cycle-audit", "--attention-takeover",
        ])
    assert rejected.value.code == 2
    routes.assert_not_called()
    backend.assert_not_called()


@pytest.mark.parametrize("authorized_crash", [("resume_authorized", OPTIONS)], indirect=True)
def test_restarted_supervisor_rejects_flags_that_differ_from_the_claim(paired_loss):
    lost = paired_loss
    crash = lost.crash
    modified_argv = ["--cycle-audit" if item == "--no-cycle-audit" else item for item in lost.argv]
    state_before = crash.state_path.read_bytes()
    with patch.object(supervisor.subprocess, "Popen") as popen:
        assert supervisor.main(modified_argv) == 0
    popen.assert_not_called()
    registry = runtime_manager.load_registry(crash.root)
    assert registry["state"] == "STOPPED" and registry["runtime_repair_options"] == OPTIONS
    assert crash.state_path.read_bytes() == state_before


class _SupervisorInterrupted(BaseException):
    pass


def test_dispatch_losses_before_claim_are_bounded_across_supervisor_restart(paired_loss):
    lost = paired_loss
    crash = lost.crash
    original_state = crash.state_path.read_bytes()
    original_ledger = lost.ledger.read_bytes()
    original_job = jobs.path_for(crash.cycle).read_bytes()
    receipts = []

    def lose_before_claim(_command, **_kwargs):
        registry = runtime_manager.load_registry(crash.root)
        receipts.append(registry["boot_resume"])
        assert crash.state_path.read_bytes() == original_state
        assert lost.ledger.read_bytes() == original_ledger
        if len(receipts) == 1:
            raise _SupervisorInterrupted
        return SimpleNamespace(pid=RUNNER_PID + 10, wait=lambda timeout: 1)

    process_ticks = runtime_manager.process_start_ticks

    def child_ticks(pid):
        return RUNNER_START_TICKS + 10 if pid == RUNNER_PID + 10 else process_ticks(pid)

    with (
        patch.object(supervisor.threading, "Thread"),
        patch.object(supervisor, "_handle_failed_state") as review,
        patch.object(supervisor, "_notify_supervisor_terminal"),
        patch.object(supervisor, "process_start_ticks", side_effect=child_ticks),
        patch.object(supervisor.subprocess, "Popen", side_effect=lose_before_claim),
    ):
        with pytest.raises(_SupervisorInterrupted):
            supervisor.main(lost.argv)
        runtime_manager.update_registry(crash.root, supervisor_pid=SUPERVISOR_PID,
                                        supervisor_start_ticks=SUPERVISOR_START_TICKS)
        assert supervisor.main(lost.argv) == 0
    assert [receipt["attempt"] for receipt in receipts] == [1, 2, 3]
    assert all(receipt["origin"] == receipts[0]["origin"] for receipt in receipts[1:])
    assert runtime_manager.load_registry(crash.root)["state"] == "STOPPED"
    review.assert_not_called()
    assert crash.state_path.read_bytes() == original_state
    assert lost.ledger.read_bytes() == original_ledger
    assert jobs.path_for(crash.cycle).read_bytes() == original_job
