"""Cold-boot recovery of an active managed Cycle uses its committed boundary."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from theory_master import runtime_manager
from theory_master.backends import FakeBackend
from theory_master.invocations import InvocationLedger
from theory_master.runtime import write_json
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.specification import TaskSpec
from theory_master.supervisor_intervention import write_supervisor_setting
from theory_master.task import bootstrap
from theory_master.workflow import Workflow

OLD_BOOT = "fd89ca11-e3fe-4f80-bded-15107243b493"
BOOT = "62358be5-dfa4-4cef-a94b-60d0691967f0"
OLD_LAUNCH = "7" * 32
NEW_LAUNCH = "9" * 32
RUNNER_PID = 99999997
RUNNER_START_TICKS = 1234567
SUPERVISOR_PID = 99999998
SUPERVISOR_START_TICKS = 1234500
RUNNER_CHECKPOINT_PROTOCOL = "durable-invocations-v1"


@pytest.fixture
def previous_boot_active(tmp_path, monkeypatch):
    plan = tmp_path / "research_plan.md"
    plan.write_text(
        "# Goal\nFind the transfer relation\n\n# Research Contract\nAuthorized sources only\n\n"
        "# Final Gate\nA bounded independent test\n\n# Required Context\nLocal inputs\n",
        encoding="utf-8",
    )
    spec = TaskSpec.from_config(bootstrap(plan).config_path)
    root = spec.research_root
    workflow = Workflow(spec, FakeBackend(reworks=0))
    workflow._ensure_cycle("2")
    cycle = workflow.cycle_dir
    ledger = InvocationLedger(cycle / "invocations.jsonl")
    invocation = "inv-2-research_lead-0123456789ab"
    ledger.prepare(invocation_id=invocation, kind="agent",
                   role="research_lead", stage="hypothesis_display",
                   input_payload={"hypothesis_id": "H-0002"})
    ledger.transition(invocation, "running", role="research_lead", stage="hypothesis_display")
    ledger.transition(invocation, "completed", role="research_lead", stage="hypothesis_display")
    ledger.transition(invocation, "committed", role="research_lead", stage="hypothesis_display",
                      commit_state="committed")

    state = {
        **workflow._new_state(),
        "cycle_id": cycle.name,
        "master_id": spec.master_id,
        "last_seq": 10,
        "hypothesis_id": "H-0002",
        "status": "active",
        "phase": "qualification:locked",
        "execution_active": True,
        "owner_pid": RUNNER_PID,
        "owner_start_ticks": RUNNER_START_TICKS,
        "invocation_cycle_index": 2,
        "attempt_id": "a" * 32,
        "resumable": True,
        "protocol_health": "valid",
        "hypothesis_locked": True,
        "hypothesis_lock": {
            "status": "locked", "id": "H-0002",
            "hypothesis": "A bounded transfer relation", "scope": "Frozen public inputs",
            "decisive_test": "Compare the prescribed transfer models",
        },
        "active_invocation_id": None,
        "last_invocation_id": invocation,
        "recovery": {},
        "role_outputs": {"research_lead_hypothesis_display": {"canvas_title": "A bounded transfer relation"}},
    }
    checkpoint = cycle / "checkpoints" / "000010-hypothesis_locked.json"
    write_json(checkpoint, state)
    state["last_safe_checkpoint"] = str(checkpoint)
    write_json(cycle / "research_state.json", state)
    write_supervisor_setting(root, True)

    monkeypatch.setattr(runtime_manager, "host_boot_id", lambda: BOOT)
    registry = runtime_manager.update_registry(
        root, state="RUNNING", launch_id=OLD_LAUNCH, requested_operation="run",
        initial_latest_cycle="1", current_cycle=cycle.name, launcher="systemd-user",
        systemd_unit=runtime_manager.unit_name(root, spec.master_id), master_id=spec.master_id,
        supervisor_pid=SUPERVISOR_PID, supervisor_start_ticks=SUPERVISOR_START_TICKS,
        runner_pid=RUNNER_PID, runner_start_ticks=RUNNER_START_TICKS,
        runner_checkpoint_protocol=RUNNER_CHECKPOINT_PROTOCOL,
        launch_requested_at="2000-01-01T00:00:00+00:00",
    )
    registry["boot_id"] = OLD_BOOT
    write_json(runtime_manager.registry_path(root), registry)
    return spec, cycle, state, registry


@pytest.mark.parametrize("previous_state, operation, runner_operation", [
    ("RUNNING", "run", None), ("RECOVERING", "resume", None), ("RECOVERING", "takeover", "run"),
])
def test_boot_can_plan_the_same_active_cycle_without_writing(
    previous_boot_active, previous_state, operation, runner_operation,
):
    from theory_master.supervisor_boot_recovery import eligible_boot_resume

    spec, cycle, state, registry = previous_boot_active
    registry.update(state=previous_state, requested_operation=operation)
    if operation == "takeover":
        registry.update(takeover_cycle=cycle.name, runner_operation=runner_operation)
    path = runtime_manager.registry_path(spec.research_root)
    write_json(path, registry)
    files = (path, cycle / "research_state.json", cycle / "invocations.jsonl", Path(state["last_safe_checkpoint"]))
    before = [file.read_bytes() for file in files]

    plan = eligible_boot_resume(spec.research_root)
    assert plan is not None
    assert plan["cycle"] == cycle.name and plan["boot_id"] == BOOT
    assert plan["prior_launch_id"] == OLD_LAUNCH
    assert plan["attempt"] == 1
    assert plan["origin"] == {
        "boot_id": OLD_BOOT, "launch_id": OLD_LAUNCH, "research_root": str(spec.research_root),
        "master_id": spec.master_id, "cycle": cycle.name, "runner_pid": RUNNER_PID,
        "runner_start_ticks": RUNNER_START_TICKS, "invocation_cycle_index": 2,
        "runner_checkpoint_protocol": RUNNER_CHECKPOINT_PROTOCOL,
        "state_sha256": hashlib.sha256(before[1]).hexdigest(),
        "ledger_sha256": hashlib.sha256(before[2]).hexdigest(),
        "checkpoint_sha256": hashlib.sha256(before[3]).hexdigest(),
    }
    assert eligible_boot_resume(spec.research_root, registry=registry) == plan
    assert [file.read_bytes() for file in files] == before


@pytest.mark.parametrize("defect", [
    "same_boot", "no_boot", "wrong_root", "wrong_master", "detached", "wrong_systemd_unit",
    "legacy_takeover", "goal_only_takeover", "no_checkpoint_protocol",
    "wrong_cycle", "runner_identity", "runner_start_ticks", "state_owner_pid", "state_owner_ticks", "worker",
    "missing_checkpoint", "foreign_checkpoint", "checkpoint_science", "checkpoint_sequence",
    "checkpoint_attempt", "unspent_invocation_slot", "prepared", "running", "active_invocation",
    "native_goal", "invalid_protocol", "paused", "control", "disabled",
])
def test_boot_rejects_ambiguous_or_protected_boundaries(previous_boot_active, defect):
    from theory_master.supervisor_boot_recovery import eligible_boot_resume

    spec, cycle, state, registry = previous_boot_active
    root = spec.research_root
    if defect == "same_boot":
        registry["boot_id"] = BOOT
    elif defect == "no_boot":
        registry.pop("boot_id")
    elif defect == "wrong_root":
        registry["research_root"] = str(root.parent)
    elif defect == "wrong_master":
        registry["master_id"] += 1
    elif defect == "detached":
        registry["launcher"] = "detached"
    elif defect == "wrong_systemd_unit":
        registry["systemd_unit"] = "test-stale-owner"
    elif defect == "legacy_takeover":
        registry.update(requested_operation="takeover", takeover_cycle=cycle.name)
    elif defect == "goal_only_takeover":
        registry.update(requested_operation="takeover", takeover_cycle=cycle.name, runner_operation="reassess")
    elif defect == "no_checkpoint_protocol":
        registry.pop("runner_checkpoint_protocol")
    elif defect == "wrong_cycle":
        registry["current_cycle"] = "1"
    elif defect == "runner_identity":
        registry["runner_pid"] = None
    elif defect == "runner_start_ticks":
        registry["runner_start_ticks"] = None
    elif defect == "state_owner_pid":
        state["owner_pid"] = RUNNER_PID - 1
    elif defect == "state_owner_ticks":
        state["owner_start_ticks"] = RUNNER_START_TICKS + 1
    elif defect == "worker":
        state["worker_id"] = "worker-001"
    elif defect == "missing_checkpoint":
        state["last_safe_checkpoint"] = None
    elif defect == "foreign_checkpoint":
        outside = root / "foreign-checkpoint.json"
        write_json(outside, state)
        state["last_safe_checkpoint"] = str(outside)
    elif defect == "checkpoint_science":
        checkpoint = Path(state["last_safe_checkpoint"])
        write_json(checkpoint, {**state, "hypothesis_lock": {**state["hypothesis_lock"], "id": "H-OTHER"}})
    elif defect == "checkpoint_sequence":
        write_json(Path(state["last_safe_checkpoint"]), {**state, "last_seq": state["last_seq"] + 1})
    elif defect == "checkpoint_attempt":
        write_json(Path(state["last_safe_checkpoint"]), {**state, "attempt_id": "unrelated-attempt"})
    elif defect == "unspent_invocation_slot":
        state["invocation_cycle_index"] = 0
    elif defect in {"prepared", "running"}:
        ledger = InvocationLedger(cycle / "invocations.jsonl")
        ident = "inv-2-investigator-pending"
        ledger.prepare(invocation_id=ident, kind="agent", role="investigator", stage="investigate", input_payload={})
        if defect == "running":
            ledger.transition(ident, "running", role="investigator", stage="investigate")
    elif defect == "active_invocation":
        state["active_invocation_id"] = "inv-2-investigator-unfinished"
    elif defect == "native_goal":
        state["native_goal"] = {"status": "active", "thread_id": "thread-still-running"}
    elif defect == "invalid_protocol":
        state["protocol_health"] = "violation"
    elif defect == "paused":
        state["recovery"] = {"action": "pause"}
    elif defect == "control":
        write_json(cycle / "recovery_control.json", {"action": "interrupt"})
    elif defect == "disabled":
        write_supervisor_setting(root, False)
    write_json(runtime_manager.registry_path(root), registry)
    write_json(cycle / "research_state.json", state)

    assert eligible_boot_resume(root) is None



def test_previous_runner_checkpoint_can_belong_to_same_scientific_attempt(previous_boot_active):
    from theory_master.supervisor_boot_recovery import eligible_boot_resume

    spec, _, state, _ = previous_boot_active
    # A successfully resumed Cycle can still point to the last safe checkpoint
    # made by its earlier Runner. Recovery binds the same scientific attempt.
    write_json(Path(state["last_safe_checkpoint"]), {**state, "owner_pid": RUNNER_PID - 1})
    plan = eligible_boot_resume(spec.research_root)
    assert plan is not None
    assert plan["origin"]["runner_pid"] == RUNNER_PID

def test_current_goal_seal_blocks_old_active_owner(previous_boot_active):
    from theory_master.supervisor_boot_recovery import eligible_boot_resume

    spec, _, _, _ = previous_boot_active
    with ScienceGraphStore(spec.graph_database) as store:
        store.add_hypothesis("H-0001")
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
        store.append_outcome(outcome_id="cycle-outcome-1", hypothesis_id="H-0001",
                             cycle_id="1", verdict="supported")
        store.seal_goal(goal_id=spec.goal_id, source_cycle="1",
                        research_plan_sha256=hashlib.sha256(spec.document.read_bytes()).hexdigest(),
                        judge_receipt_ref=None, detail={"rationale": "Completed Goal"})
    assert eligible_boot_resume(spec.research_root) is None


def prepare_handoff(root: Path, supervisor_pid: int | None = None):
    from theory_master.supervisor_boot_recovery import eligible_boot_resume

    plan = eligible_boot_resume(root)
    assert plan is not None
    pid = supervisor_pid if supervisor_pid is not None else os.getpid()
    runtime_manager.update_registry(
        root, state="STARTING", requested_operation="resume", boot_id=BOOT, launch_id=NEW_LAUNCH,
        supervisor_pid=pid, supervisor_start_ticks=runtime_manager.process_start_ticks(pid),
        runner_pid=None, runner_start_ticks=None, boot_resume={**plan, "launch_id": NEW_LAUNCH},
    )
    return plan


@pytest.mark.parametrize("managed_child", [False, True])
def test_handoff_is_verified_again_by_its_live_owner(previous_boot_active, managed_child):
    from theory_master.supervisor_boot_recovery import verify_boot_resume

    spec, cycle, state, _ = previous_boot_active
    root = spec.research_root
    owner = os.getppid() if managed_child else os.getpid()
    plan = prepare_handoff(root, owner)
    files = (runtime_manager.registry_path(root), cycle / "research_state.json",
             cycle / "invocations.jsonl", Path(state["last_safe_checkpoint"]))
    before = [file.read_bytes() for file in files]
    checked = verify_boot_resume(root, NEW_LAUNCH, managed_child=managed_child)
    assert checked is not None
    assert {key: checked[key] for key in plan} == plan
    assert [file.read_bytes() for file in files] == before


@pytest.mark.parametrize("change", ["state", "ledger", "checkpoint", "control", "launch", "owner", "parent"])
def test_handoff_authority_cannot_be_reused_after_changes(previous_boot_active, change):
    from theory_master.supervisor_boot_recovery import verify_boot_resume

    spec, cycle, state, _ = previous_boot_active
    root = spec.research_root
    prepare_handoff(root)
    launch = NEW_LAUNCH
    if change == "state":
        write_json(cycle / "research_state.json", {**state, "last_seq": state["last_seq"] + 1})
    elif change == "ledger":
        ledger = InvocationLedger(cycle / "invocations.jsonl")
        ledger.transition(state["last_invocation_id"], "committed", commit_state="committed",
                          transport_verified_after_checkpoint=True)
    elif change == "checkpoint":
        write_json(Path(state["last_safe_checkpoint"]), {**state, "last_seq": state["last_seq"] - 1})
    elif change == "control":
        write_json(cycle / "recovery_control.json", {"action": "pause"})
    elif change == "launch":
        launch = OLD_LAUNCH
    elif change == "owner":
        runtime_manager.update_registry(root, supervisor_pid=SUPERVISOR_PID,
                                        supervisor_start_ticks=SUPERVISOR_START_TICKS)

    assert verify_boot_resume(root, launch, managed_child=change == "parent") is None


def test_recovery_requests_managed_resume_of_exact_cycle(previous_boot_active):
    from unittest.mock import patch

    from theory_master.supervisor_boot_recovery import eligible_boot_resume, recover_task_active

    spec, cycle, _, _ = previous_boot_active
    root = spec.research_root
    plan = eligible_boot_resume(root)
    assert plan is not None
    before = runtime_manager.registry_path(root).read_bytes(), (cycle / "research_state.json").read_bytes()
    with patch.object(runtime_manager, "launch_managed_runtime", return_value={"status": "started"}) as launch:
        assert recover_task_active(spec) == {"status": "started"}
    launch.assert_called_once_with(spec, operation="resume", identifier=cycle.name, boot_resume=plan)
    assert (runtime_manager.registry_path(root).read_bytes(), (cycle / "research_state.json").read_bytes()) == before


def test_cold_boot_startup_retries_keep_original_checkpoint_and_are_bounded(previous_boot_active):
    from theory_master.supervisor_boot_recovery import eligible_boot_resume

    spec, cycle, _, _ = previous_boot_active
    root = spec.research_root
    original = eligible_boot_resume(root)
    assert original is not None
    runtime_manager.update_registry(
        root, state="LAUNCHING", requested_operation="resume", boot_id=BOOT, launch_id=NEW_LAUNCH,
        supervisor_pid=None, supervisor_start_ticks=None, runner_pid=None, runner_start_ticks=None,
        launch_requested_at="2000-01-01T00:00:00+00:00",
        boot_resume={**original, "launch_id": NEW_LAUNCH},
    )
    retry = eligible_boot_resume(root)
    assert retry == {**original, "attempt": 2, "prior_launch_id": NEW_LAUNCH}
    runtime_manager.update_registry(root, boot_resume={**retry, "attempt": 3, "launch_id": NEW_LAUNCH})
    assert eligible_boot_resume(root) is None
    assert (root / cycle.name / "research_state.json").is_file()


def test_boot_workflow_resumes_exact_cycle_without_using_another_slot(previous_boot_active, monkeypatch):
    spec, cycle, state, _ = previous_boot_active
    root = spec.research_root
    prepare_handoff(root, os.getppid())
    workflow = Workflow(spec, FakeBackend(reworks=0))
    checkpoint = Path(state["last_safe_checkpoint"])
    ledger = cycle / "invocations.jsonl"
    before = checkpoint.read_bytes(), ledger.read_bytes()
    observed = []

    def continue_boundary(resume_state, *, same_cycle_continuation):
        assert workflow.cycle_dir == cycle
        assert same_cycle_continuation
        assert workflow._claim_invocation_cycle_slot(same_cycle_continuation=same_cycle_continuation) == 2
        assert resume_state["hypothesis_lock"] == state["hypothesis_lock"]
        assert resume_state["recovery"]["allow_thread_fallback"] is False
        observed.append(resume_state["cycle_id"])
        return {**resume_state, "status": "needs_attention"}

    monkeypatch.setattr(workflow, "_run_cycle", continue_boundary)
    restored = workflow.resume(cycle.name, boot_resume_launch=NEW_LAUNCH)
    assert observed == [cycle.name]
    assert restored["cycle_id"] == cycle.name
    assert workflow.invocation_cycle_index == state["invocation_cycle_index"]
    assert [p for p in root.iterdir() if p.is_dir() and p.name.isdigit()] == [cycle]
    assert (checkpoint.read_bytes(), ledger.read_bytes()) == before


def test_boot_workflow_rechecks_and_preserves_late_operator_control(previous_boot_active, monkeypatch):
    spec, cycle, _, _ = previous_boot_active
    root = spec.research_root
    prepare_handoff(root, os.getppid())
    workflow = Workflow(spec, FakeBackend(reworks=0))
    control = cycle / "recovery_control.json"
    sensitive = cycle / "research_state.json"
    before = sensitive.read_bytes()
    master_id_for_cycle = workflow._master_id_for_cycle

    def operator_pauses_before_persist(cycle_name):
        write_json(control, {"action": "pause"})
        return master_id_for_cycle(cycle_name)

    def unexpected_runner(*args, **kwargs):
        pytest.fail("automatic recovery started a controlled Cycle")

    monkeypatch.setattr(workflow, "_master_id_for_cycle", operator_pauses_before_persist)
    monkeypatch.setattr(workflow, "_run_cycle", unexpected_runner)
    with pytest.raises(ValueError, match="interrupted Cycle changed"):
        workflow.resume(cycle.name, boot_resume_launch=NEW_LAUNCH)

    assert control.is_file()
    assert '"pause"' in control.read_text()
    assert sensitive.read_bytes() == before
