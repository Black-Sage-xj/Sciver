"""Offline boot recovery of the existing Goal Judge delivery and stop."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.test_supervisor_archival_goal_delivery import archive
from tests.test_supervisor_boot_active import (
    BOOT, NEW_LAUNCH, OLD_BOOT, OLD_LAUNCH, RUNNER_PID, RUNNER_START_TICKS,
    SUPERVISOR_PID, SUPERVISOR_START_TICKS,
)
from theory_master import runtime_manager
from theory_master.goal_gate import GoalGate
from theory_master.invocations import InvocationLedger
from theory_master.runtime import write_json
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.specification import list_cycle_dirs
from theory_master.supervisor_boot_recovery import (
    DURABLE_RUNNER_PROTOCOL, eligible_boot_resume, recover_task_active, verify_boot_resume,
)
from theory_master.supervisor_goal_reassessment import (
    delivery_reassessment_plan, reassessment_launch_identity, run_goal_reassessment,
)
from theory_master.workflow import Workflow
from theory_master.workflow_support import RecoveryStop


class SimulatedPowerLoss(BaseException):
    pass


@pytest.fixture
def previous_boot_reassessment(archive, monkeypatch):
    spec = archive.spec
    root = spec.research_root
    write_json(archive.selector, archive.record)
    plan = delivery_reassessment_plan(spec)
    assert plan is not None
    attempt = {**reassessment_launch_identity(plan, OLD_LAUNCH), "status": "launching"}
    registry = runtime_manager.update_registry(
        root, state="RECOVERING", boot_id=OLD_BOOT, launch_id=OLD_LAUNCH, requested_operation="takeover",
        runner_operation="reassess", current_cycle=archive.stopped.name,
        takeover_cycle=archive.stopped.name, launcher="systemd-user", master_id=spec.master_id,
        systemd_unit=runtime_manager.unit_name(root, spec.master_id),
        supervisor_pid=SUPERVISOR_PID, supervisor_start_ticks=SUPERVISOR_START_TICKS,
        runner_pid=RUNNER_PID, runner_start_ticks=RUNNER_START_TICKS,
        runner_checkpoint_protocol=DURABLE_RUNNER_PROTOCOL,
        delivery_reassessment_attempt=attempt,
        launch_requested_at="2000-01-01T00:00:00+00:00",
    )
    monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: BOOT)
    return archive, plan, registry


def _interrupt_gate(archive, plan, monkeypatch, boundary):
    def power_loss(*_args, **_kwargs):
        raise SimulatedPowerLoss

    with monkeypatch.context() as interrupt:
        if boundary == "first_dispatch":
            interrupt.setattr(archive.workflow, "_call", power_loss)
        else:
            interrupt.setattr(GoalGate, "_commit", power_loss)
        with pytest.raises(SimulatedPowerLoss):
            run_goal_reassessment(archive.workflow, plan, watch_stopped=True)
    anchor_state = json.loads((archive.anchor / "research_state.json").read_bytes())
    assert anchor_state["goal_delivery_pending"]["delivery_revision"] == plan["delivery_revision"]
    return anchor_state


def _prepare_goal_invocation(ledger, invocation, pin):
    ledger.prepare(invocation_id=invocation, kind="agent", role="judge", stage="goal_completion",
                   input_payload={}, logical_key=pin["logical_key"], contract_ref=pin["contract_ref"])


@pytest.mark.parametrize("boundary", ["first_dispatch", "reuse_closure"])
def test_goal_boot_plan_is_read_only_and_requests_exact_takeover(previous_boot_reassessment, monkeypatch, boundary):
    archive, goal_plan, registry = previous_boot_reassessment
    _interrupt_gate(archive, goal_plan, monkeypatch, boundary)
    spec = archive.spec
    root = spec.research_root
    files = [runtime_manager.registry_path(root), archive.selector,
             archive.anchor / "research_state.json", archive.anchor / "invocations.jsonl",
             archive.stopped / "research_state.json"]
    before = [path.read_bytes() for path in files]

    ticket = eligible_boot_resume(root)
    assert ticket["kind"] == "goal_reassessment"
    assert ticket["cycle"] == archive.stopped.name and ticket["attempt"] == 1
    assert ticket["prior_launch_id"] == OLD_LAUNCH and ticket["boot_id"] == BOOT
    assert ticket["origin"]["delivery_attempt"] == registry["delivery_reassessment_attempt"]
    assert ticket["origin"]["recovery_pin"]["mode"] == boundary

    with patch.object(runtime_manager, "launch_managed_runtime", return_value={"status": "started"}) as launch:
        assert recover_task_active(spec) == {"status": "started"}
    launch.assert_called_once_with(spec, operation="takeover", identifier=str(spec.master_id),
                                  takeover_cycle=archive.stopped.name, boot_resume=ticket)
    assert [path.read_bytes() for path in files] == before


@pytest.mark.parametrize("defect", ["live-owner", "same-boot", "operator-stop", "anchor-stop",
                                    "wrong-anchor", "undurable-runner"])
def test_goal_boot_refuses_violated_prior_attempt_authority(previous_boot_reassessment, monkeypatch, defect):
    archive, _goal_plan, registry = previous_boot_reassessment
    root = archive.spec.research_root
    if defect == "live-owner":
        monkeypatch.setattr(runtime_manager, "runtime_health", lambda *_args: {"active": True})
    elif defect == "same-boot":
        registry["boot_id"] = BOOT
    elif defect in {"operator-stop", "anchor-stop"}:
        cycle = archive.stopped if defect == "operator-stop" else archive.anchor
        write_json(cycle / "recovery_control.json", {"action": "pause"})
    elif defect == "wrong-anchor":
        registry["delivery_reassessment_attempt"]["anchor_cycle"] = archive.stopped.name
    else:
        registry["runner_checkpoint_protocol"] = None
    write_json(runtime_manager.registry_path(root), registry)

    assert eligible_boot_resume(root) is None
    with patch.object(runtime_manager, "launch_managed_runtime") as launch:
        assert recover_task_active(archive.spec) == {"status": "skipped"}
    launch.assert_not_called()
    assert archive.backend.goal_calls == 1


def test_goal_boot_handoff_is_bound_to_live_owner_and_rechecks_control(previous_boot_reassessment, monkeypatch):
    archive, goal_plan, _registry = previous_boot_reassessment
    _interrupt_gate(archive, goal_plan, monkeypatch, "first_dispatch")
    root = archive.spec.research_root
    ticket = eligible_boot_resume(root)
    assert ticket and ticket["origin"]["recovery_pin"]["mode"] == "first_dispatch"

    systemd = SimpleNamespace(returncode=0, stdout="test managed Supervisor launch", stderr="")
    with (
        patch.object(runtime_manager.shutil, "which", return_value="/usr/bin/offline-test-systemd"),
        patch.object(runtime_manager, "runtime_python", return_value="/usr/bin/offline-test-python"),
        patch.object(runtime_manager.subprocess, "run", return_value=systemd) as systemd_run,
        patch.object(runtime_manager.subprocess, "Popen") as detached,
    ):
        launch = recover_task_active(archive.spec)
    assert launch["status"] == "started"
    assert systemd_run.call_count == 1
    detached.assert_not_called()
    launch_id = launch["launch_id"]
    assert runtime_manager.load_registry(root)["boot_resume"] == {**ticket, "launch_id": launch_id}

    owner = os.getpid()
    runtime_manager.update_registry(root, state="STARTING", supervisor_pid=owner,
                                    supervisor_start_ticks=runtime_manager.process_start_ticks(owner))
    assert verify_boot_resume(root, launch_id) == {**ticket, "launch_id": launch_id}
    assert verify_boot_resume(root, NEW_LAUNCH) is None
    write_json(archive.anchor / "recovery_control.json", {"action": "pause"})
    assert verify_boot_resume(root, launch_id) is None


def test_boot_restores_wait_after_delivery_finished_before_wait_publication(previous_boot_reassessment):
    archive, goal_plan, previous = previous_boot_reassessment
    result = run_goal_reassessment(archive.workflow, goal_plan, watch_stopped=True)
    assert result["assessment"]["verdict"] == "not_passed"
    root = archive.spec.research_root
    attempt = {**previous["delivery_reassessment_attempt"], "status": "finished", "runner_exit_code": 0}
    runtime_manager.update_registry(root, runner_pid=None, runner_start_ticks=None, runner_operation=None,
                                    delivery_reassessment_attempt=attempt)

    ticket = eligible_boot_resume(root)
    assert ticket["kind"] == "goal_reassessment" and ticket["cycle"] == archive.stopped.name
    assert ticket["origin"]["delivery_attempt"] == attempt and ticket["origin"]["recovery_pin"] is None
    assert archive.backend.goal_calls == 2


@pytest.mark.parametrize("boundary", ["first_dispatch", "reuse_closure"])
def test_resumed_goal_commits_exact_revision_with_one_dispatch(previous_boot_reassessment, monkeypatch, boundary):
    archive, goal_plan, _registry = previous_boot_reassessment
    archive.backend.pass_goal = True
    state = _interrupt_gate(archive, goal_plan, monkeypatch, boundary)
    assert archive.backend.goal_calls == (1 if boundary == "first_dispatch" else 2)
    pending = state["goal_delivery_pending"]
    snapshot = Path(pending["snapshot_manifest"])
    snapshot_bytes = snapshot.read_bytes()
    stop = archive.stopped / "research_state.json"
    stop_bytes = stop.read_bytes()
    spec = archive.spec
    ticket = eligible_boot_resume(spec.research_root)
    pin = ticket["origin"]["recovery_pin"]
    assert pin["mode"] == boundary

    resumed = Workflow(spec, archive.backend)
    result = run_goal_reassessment(resumed, goal_plan, watch_stopped=True, recovery_pin=pin)
    assert result["assessment"]["verdict"] == "passed"
    assert archive.backend.goal_calls == 2
    assert list_cycle_dirs(spec.research_root, task_mode=True) == [archive.stopped, archive.anchor]
    assert stop.read_bytes() == stop_bytes and snapshot.read_bytes() == snapshot_bytes
    ledger = InvocationLedger(archive.anchor / "invocations.jsonl")
    judge_calls = [row for row in ledger.snapshots() if row["stage"] == "goal_completion"]
    assert len(judge_calls) == 2  # Original assessment and the single recovered assessment.
    assert all(row["state"] == "committed" for row in judge_calls)
    with ScienceGraphStore(archive.graph, readonly=True) as store:
        first, recovered = store.goal_gate_history(archive.anchor.name)
        assert recovered["supersedes"] == first["assessment_id"]
        assert recovered["delivery_revision"] == goal_plan["delivery_revision"]
        assert store.goal_terminal()["source_cycle"] == archive.anchor.name
    assert eligible_boot_resume(spec.research_root) is None


@pytest.mark.parametrize("same_boot_restart", [False, True], ids=["host-reboot", "systemd-restart"])
def test_dispatched_goal_without_closure_only_recovers_the_supervisor_wait(
    previous_boot_reassessment, monkeypatch, same_boot_restart,
):
    from theory_master.supervisor import main as supervisor_main

    archive, goal_plan, registry = previous_boot_reassessment
    _interrupt_gate(archive, goal_plan, monkeypatch, "first_dispatch")
    root = archive.spec.research_root
    ticket = eligible_boot_resume(root)
    first_dispatch = ticket["origin"]["recovery_pin"]
    ledger = InvocationLedger(archive.anchor / "invocations.jsonl")
    invocation = "inv-1-judge-interrupted"
    _prepare_goal_invocation(ledger, invocation, first_dispatch)
    ledger.transition(invocation, "running", role="judge", stage="goal_completion", thread_id="test-turn")
    attempt = registry["delivery_reassessment_attempt"]
    if same_boot_restart:
        # The first recovered owner dispatched the exact revision before its
        # managed child disappeared. Its original boot pin is still stored.
        _admit_supervisor(ticket, root)
        attempt = {**attempt, "launch_id": NEW_LAUNCH}
        runtime_manager.update_registry(
            root, state="RECOVERING", supervisor_attempt=1, supervisor_pid=SUPERVISOR_PID,
            supervisor_start_ticks=SUPERVISOR_START_TICKS, runner_pid=RUNNER_PID,
            runner_start_ticks=RUNNER_START_TICKS, runner_operation="reassess",
            runner_checkpoint_protocol=DURABLE_RUNNER_PROTOCOL, delivery_reassessment_attempt=attempt,
        )
        assert eligible_boot_resume(root) is None
    else:
        current = eligible_boot_resume(root)
        assert current["kind"] == "goal_reassessment"
        assert current["origin"]["delivery_attempt"] == attempt
        assert current["origin"]["recovery_pin"] is None
        _admit_supervisor(current, root)

    def stop_waiting(wait_root, cycle, launch_id, _stop):
        assert (wait_root, cycle, launch_id) == (root, archive.stopped, NEW_LAUNCH)
        state = runtime_manager.load_registry(root)
        receipt = {**attempt, "status": "unconfirmed"}
        assert state["delivery_reassessment_attempt"] == receipt
        assert state["delivery_reassessment_history"] == [receipt]
        return False

    with (
        patch("theory_master.supervisor_activation.wait_for_qualification_change", side_effect=stop_waiting) as wait,
        patch("theory_master.supervisor.subprocess.Popen") as child,
    ):
        assert supervisor_main(_supervisor_args(root, archive.spec.master_id)) == 0
    wait.assert_called_once()
    child.assert_not_called()
    assert runtime_manager.load_registry(root)["boot_resume_outcome"] == "delivery_unconfirmed"
    assert archive.backend.goal_calls == 1


def _admit_supervisor(ticket, root):
    runtime_manager.update_registry(root, state="LAUNCHING", boot_id=BOOT, launch_id=NEW_LAUNCH,
                                    supervisor_pid=None, supervisor_start_ticks=None, supervisor_attempt=0,
                                    runner_pid=None, runner_start_ticks=None, runner_operation=None,
                                    runner_checkpoint_protocol=None, boot_resume={**ticket, "launch_id": NEW_LAUNCH})


def _supervisor_args(root, master_id):
    return ["--task-root", str(root), "--operation", "takeover", "--identifier", str(master_id),
            "--launch-id", NEW_LAUNCH]


def test_boot_supervisor_dispatches_only_the_verified_goal_reassessment(previous_boot_reassessment, monkeypatch):
    from theory_master.supervisor import main as supervisor_main

    archive, goal_plan, _registry = previous_boot_reassessment
    _interrupt_gate(archive, goal_plan, monkeypatch, "first_dispatch")
    root = archive.spec.research_root
    ticket = eligible_boot_resume(root)
    _admit_supervisor(ticket, root)
    child = SimpleNamespace(pid=RUNNER_PID, wait=lambda timeout: 0)

    with (
        patch("theory_master.supervisor.runtime_python", return_value="/usr/bin/offline-test-python"),
        patch("theory_master.supervisor.subprocess.Popen", return_value=child) as popen,
        patch("theory_master.supervisor_activation.wait_for_qualification_change", return_value=False),
    ):
        assert supervisor_main(_supervisor_args(root, archive.spec.master_id)) == 0
    popen.assert_called_once()
    command = popen.call_args.args[0]
    assert command[command.index("--operation") + 1] == "reassess"
    assert command[command.index("--supervisor-takeover-cycle") + 1] == archive.stopped.name
    assert command[command.index("--boot-resume-launch") + 1] == NEW_LAUNCH
    assert "--managed-child" in command
    state = runtime_manager.load_registry(root)
    assert state["delivery_reassessment_attempt"]["launch_id"] == NEW_LAUNCH
    assert state["delivery_reassessment_attempt"]["delivery_revision"] == goal_plan["delivery_revision"]
    assert state["boot_resume_outcome"] == "delivery_returned"


def test_second_boot_reuses_the_single_dispatched_closure_after_a_prepared_only_slot(
    previous_boot_reassessment, monkeypatch,
):
    archive, goal_plan, _registry = previous_boot_reassessment
    archive.backend.pass_goal = True
    _interrupt_gate(archive, goal_plan, monkeypatch, "first_dispatch")
    spec = archive.spec
    root = spec.research_root
    ledger = InvocationLedger(archive.anchor / "invocations.jsonl")
    pre_dispatch = eligible_boot_resume(root)["origin"]["recovery_pin"]
    _prepare_goal_invocation(ledger, "inv-1-judge-prepared-only", pre_dispatch)
    first_pin = eligible_boot_resume(root)["origin"]["recovery_pin"]
    assert first_pin["mode"] == "first_dispatch" and archive.backend.goal_calls == 1

    def interrupt_before_goal_commit(*_args, **_kwargs):
        raise SimulatedPowerLoss

    with monkeypatch.context() as interrupt:
        interrupt.setattr(GoalGate, "_commit", interrupt_before_goal_commit)
        with pytest.raises(SimulatedPowerLoss):
            run_goal_reassessment(Workflow(spec, archive.backend), goal_plan,
                                  watch_stopped=True, recovery_pin=first_pin)
    assert archive.backend.goal_calls == 2
    runtime_manager.update_registry(
        root, boot_id=BOOT, launch_id=NEW_LAUNCH,
        delivery_reassessment_attempt={**reassessment_launch_identity(goal_plan, NEW_LAUNCH), "status": "launching"},
    )
    monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: "f7754504-0922-4dbc-8e4c-3d2fc90c4d59")
    second_pin = eligible_boot_resume(root)["origin"]["recovery_pin"]
    assert second_pin["mode"] == "reuse_closure"
    resumed = run_goal_reassessment(Workflow(spec, archive.backend), goal_plan,
                                    watch_stopped=True, recovery_pin=second_pin)
    assert resumed["assessment"]["verdict"] == "passed"
    assert archive.backend.goal_calls == 2
    first_states = [row["state"] for row in ledger.snapshots() if row["logical_key"] == first_pin["logical_key"]]
    assert first_states == ["prepared", "committed"]


def test_closure_drift_at_invocation_cannot_fall_through_to_fresh_judge(previous_boot_reassessment, monkeypatch):
    archive, goal_plan, _registry = previous_boot_reassessment
    archive.backend.pass_goal = True
    _interrupt_gate(archive, goal_plan, monkeypatch, "reuse_closure")
    spec = archive.spec
    pin = eligible_boot_resume(spec.research_root)["origin"]["recovery_pin"]
    assert pin["mode"] == "reuse_closure"
    closure = archive.anchor / "semantic_closure" / f"{pin['invocation_id']}.json"
    workflow = Workflow(spec, archive.backend)
    call = workflow._call

    def altered_closure(*args, **kwargs):
        payload = json.loads(closure.read_bytes())
        payload["closed_output"]["rationale"] = "Different rationale from the authorized result."
        write_json(closure, payload)
        return call(*args, **kwargs)

    monkeypatch.setattr(workflow, "_call", altered_closure)
    with pytest.raises(RecoveryStop, match="invocation changed before recovery"):
        run_goal_reassessment(workflow, goal_plan, watch_stopped=True, recovery_pin=pin)

    assert archive.backend.goal_calls == 2
    with ScienceGraphStore(archive.graph, readonly=True) as store:
        assert store.goal_terminal() is None
        assert store.goal_gate_history(archive.anchor.name) == [archive.assessment]


@pytest.mark.parametrize(("entry", "boundary", "replacement_fails"), [
    ("child-exit", "first_dispatch", False),
    ("child-exit", "first_dispatch", True),
    ("child-exit", "reuse_closure", False),
    ("child-exit", "ambiguous", False),
    ("dead-child-restart", "first_dispatch", False),
    ("reattached-child", "reuse_closure", False),
])
def test_warm_supervisor_retries_only_the_exact_reassessment(
    previous_boot_reassessment, monkeypatch, entry, boundary, replacement_fails,
):
    from theory_master import runner, supervisor
    from theory_master.goal_reassessment_recovery import goal_reassessment_recovery_pin
    from theory_master.supervisor_reassessment_retry import goal_retry_plan, verify_goal_retry

    archive, plan, _previous = previous_boot_reassessment
    spec = archive.spec
    root = spec.research_root
    stopped_state = archive.stopped / "research_state.json"
    stopped_before = stopped_state.read_bytes()
    attempt = {**reassessment_launch_identity(plan, NEW_LAUNCH), "status": "launching"}
    restarting = entry != "child-exit"
    owner_pid = os.getpid()
    runtime_manager.update_registry(
        root, boot_id=BOOT, launch_id=NEW_LAUNCH, state="RUNNING" if restarting else "LAUNCHING",
        supervisor_attempt=int(restarting), runner_pid=RUNNER_PID if restarting else None,
        runner_start_ticks=RUNNER_START_TICKS if restarting else None,
        runner_operation="reassess" if restarting else None,
        delivery_reassessment_attempt=attempt if restarting else None,
    )
    archive.backend.pass_goal = True

    def interrupt_initial_runner():
        interruption = "first_dispatch" if boundary == "ambiguous" else boundary
        _interrupt_gate(archive, plan, monkeypatch, interruption)
        if boundary == "ambiguous":
            ledger = InvocationLedger(archive.anchor / "invocations.jsonl")
            invocation = "inv-1-judge-interrupted"
            pin = goal_reassessment_recovery_pin(spec, plan)
            assert pin is not None and pin["mode"] == "first_dispatch"
            _prepare_goal_invocation(ledger, invocation, pin)
            ledger.transition(invocation, "running", role="judge", stage="goal_completion", thread_id="test-turn")
        return 1

    if restarting:
        interrupt_initial_runner()

    if entry == "reattached-child":
        # Observe the original live child, then its death during attachment.
        observations = iter([True, True, False])
        identity_alive = supervisor.process_identity_alive

        def reattached_identity_alive(pid, ticks=None):
            if (pid, ticks) == (RUNNER_PID, RUNNER_START_TICKS):
                return next(observations, False)
            return identity_alive(pid, ticks)

        monkeypatch.setattr(supervisor, "process_identity_alive", reattached_identity_alive)

    process_start_ticks = supervisor.process_start_ticks
    monkeypatch.setattr(supervisor, "process_start_ticks",
                        lambda pid: RUNNER_START_TICKS if pid == RUNNER_PID else process_start_ticks(pid))

    def spawn(command, **_kwargs):
        assert command[command.index("--operation") + 1] == "reassess"
        assert command[command.index("--supervisor-takeover-cycle") + 1] == archive.stopped.name
        assert "--managed-child" in command and "--boot-resume-launch" not in command
        if "--goal-retry-launch" not in command:
            assert not restarting
            return SimpleNamespace(pid=RUNNER_PID, wait=lambda timeout: interrupt_initial_runner())

        def resume_goal(timeout):
            assert command[command.index("--goal-retry-launch") + 1] == NEW_LAUNCH
            with monkeypatch.context() as child_process:
                child_process.setattr(runner.os, "getppid", lambda: owner_pid)
                retry = verify_goal_retry(root, NEW_LAUNCH, managed_child=True)
                assert retry is not None and retry["attempt"] == attempt
                assert retry["recovery_pin"]["mode"] == boundary
                assert (retry["runner_pid"], retry["runner_start_ticks"]) == (RUNNER_PID, RUNNER_START_TICKS)
                assert goal_retry_plan(root, NEW_LAUNCH) is None
                if replacement_fails:
                    return 1
                return runner.main(command[3:])

        return SimpleNamespace(pid=owner_pid, wait=resume_goal)

    with (
        patch.object(supervisor, "runtime_python", return_value="/usr/bin/offline-test-python"),
        patch.object(supervisor.subprocess, "Popen", side_effect=spawn) as popen,
        patch.object(runner, "CodexBackend", return_value=archive.backend) as backend,
        patch("theory_master.supervisor_activation.wait_for_qualification_change", return_value=False) as wait,
    ):
        assert supervisor.main(_supervisor_args(root, spec.master_id)) == 0

    registry = runtime_manager.load_registry(root)
    history = registry["delivery_reassessment_history"]
    assert len(history) == 1 and history[0]["delivery_revision"] == plan["delivery_revision"]
    assert stopped_state.read_bytes() == stopped_before
    assert list_cycle_dirs(root, task_mode=True) == [archive.stopped, archive.anchor]
    with ScienceGraphStore(archive.graph, readonly=True) as store:
        if boundary == "ambiguous":
            popen.assert_called_once()
            backend.assert_not_called()
            assert archive.backend.goal_calls == 1
            assert registry.get("goal_reassessment_retry") is None
            assert store.goal_terminal() is None
        elif replacement_fails:
            assert popen.call_count == 2
            backend.assert_not_called()
            wait.assert_called_once()
            assert wait.call_args.args[:3] == (root, archive.stopped, NEW_LAUNCH)
            assert archive.backend.goal_calls == 1
            assert history[0]["status"] == "failed" and history[0]["runner_exit_code"] == 1
            assert registry["goal_reassessment_retry"]["status"] == "dispatching"
            assert goal_reassessment_recovery_pin(spec, plan) == registry["goal_reassessment_retry"]["recovery_pin"]
            assert store.goal_terminal() is None
        else:
            assert popen.call_count == (1 if restarting else 2)
            backend.assert_called_once()
            assert archive.backend.goal_calls == 2
            assert history[0]["status"] == "finished"
            assert store.goal_terminal()["source_cycle"] == archive.anchor.name
            assert store.goal_gate_history(archive.anchor.name)[-1]["delivery_revision"] == plan["delivery_revision"]
