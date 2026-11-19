"""Real Worker control/claim/child paths with offline scientific execution."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from theory_master import resume_preparation as preparation
from theory_master import worker_actions, worker_execution, worker_store
from theory_master.contract_stages import role_contract
from theory_master.invocations import InvocationLedger
from theory_master.multi_worker import CycleClaims
from theory_master.native_goal import native_goal_setup_turn
from theory_master.native_goal_recovery import find_native_goal_origin
from theory_master.runtime import write_json
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.scientific_objective import serialize_frozen_science
from theory_master.workflow_state import WorkflowStateMixin
from theory_master.workflow_support import RecoveryStop


def rows(path, values):
    path.write_text("".join(json.dumps(value) + "\n" for value in values))


@pytest.fixture
def paused(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "isolated-runtime"))
    root, cycle = tmp_path / "research", tmp_path / "research/9"
    (cycle / "workspace/.tm-handoff").mkdir(parents=True)
    (cycle / ".theory-master-cycle").write_text("theory-master/v2\noriginal\n")
    graph = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(graph) as store:
        store.add_hypothesis("H-0009", title="Original frozen H")
    plan = tmp_path / "plan.md"
    plan.write_text("# Goal\n\nOriginal unfinished task.\n")
    spec = SimpleNamespace(
        research_root=root,
        branch_canvas=tmp_path / "exploration.canvas",
        graph_database=graph,
        document=plan,
    )
    claims = CycleClaims(root, graph)
    claim = claims.claim_cycle("worker-1")
    claims.bind_cycle("worker-1", claim["claim_id"], claim["fencing_epoch"], "9")
    claims.bind_hypothesis(
        "worker-1", claim["claim_id"], claim["fencing_epoch"], "H-0009"
    )
    claims.update("worker-1", claim["claim_id"], claim["fencing_epoch"], action="pause")
    worker_store.save_worker(
        root,
        "worker-1",
        status="PAUSED",
        pid=None,
        pid_start_ticks=None,
        current_cycle="9",
        claim_id=claim["claim_id"],
        epoch=claim["fencing_epoch"],
        account=None,
    )
    origin, monitor, thread = (
        "inv-9-investigator-original",
        "inv-9-investigator-old-monitor",
        "original-thread",
    )
    lock = {
        "id": "H-0009",
        "status": "locked",
        "hypothesis": "Original hypothesis",
        "scope": "Original scope",
        "decisive_test": "Original test",
    }
    state = {
        "cycle_id": "9",
        "worker_id": "worker-1",
        "status": "paused",
        "phase": "paused",
        "resumable": True,
        "execution_active": False,
        "hypothesis_lock": lock,
        "active_invocation_id": monitor,
        "threads": {"investigator": thread},
        "recovery": {
            "action": "pause",
            "reason": "multi_worker_operator",
            "requested_at": "2026-11-19T10:25:00+08:00",
            "kind": "original-recovery-kind",
        },
        "runtime_source_provenance": {"marker": "original-scientific-source"},
    }
    write_json(cycle / "research_state.json", state)
    contract = role_contract("investigator", phase="investigate").descriptor()
    rows(
        cycle / "invocations.jsonl",
        [
            {
                "invocation_id": origin,
                "state": "prepared",
                "role": "investigator",
                "stage": "investigate",
                "logical_key": "original-frozen-slot",
                "contract_ref": contract,
                "commit_state": "not_committed",
            },
            {"invocation_id": origin, "state": "running", "thread_id": thread},
        ],
    )
    start = "2026-11-19T01:00:00+08:00"
    rows(
        cycle / "events.jsonl",
        [
            {
                "type": "role.started",
                "at": start,
                "payload": {
                    "invocation_id": origin,
                    "role": "investigator",
                    "stage": "investigate",
                    "thread_id": thread,
                },
            }
        ],
    )
    handoff = cycle / "workspace/.tm-handoff" / f"{origin}.json"
    objective = "Finish frozen science: " + serialize_frozen_science(lock)
    rows(
        cycle / "runtime_events.jsonl",
        [
            {
                "event": "AGENT_INPUT",
                "ts": start,
                "role": "investigator",
                "stage": "investigate",
                "input_kind": "native_goal_setup_turn",
                "content": native_goal_setup_turn(
                    json.dumps({"delivery": str(handoff)}), objective
                ),
            },
            {
                "event": "AGENT_INPUT",
                "ts": start,
                "role": "investigator",
                "stage": "investigate",
                "input_kind": "native_goal_objective",
                "content": objective,
            },
        ],
    )
    control = {
        "action": "pause",
        "reason": "multi_worker_operator",
        "requested_at": "2026-11-19T10:25:00+08:00",
    }
    write_json(cycle / "recovery_control.json", control)
    launches, visits = [], []
    monkeypatch.setattr(
        "theory_master.worker_routes.resolve_worker_routes", lambda *a: {}
    )
    monkeypatch.setattr(
        "theory_master.worker_launch._task_scoped_process_env", lambda *a: {}
    )
    monkeypatch.setattr(
        "theory_master.worker_quota._verified_codex_quota_wait", lambda: None
    )
    monkeypatch.setattr(
        "theory_master.worker_quota._verified_pinned_codex_quota_wait", lambda *a: None
    )
    monkeypatch.setattr(
        "theory_master.worker_execution._update_cycle_memory_index",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "theory_master.worker_launch._launch_detached_service",
        lambda args, **k: launches.append(args),
    )
    from theory_master import backends, workflow

    class Backend(backends.FakeBackend):
        def __init__(self, cwd, routes):
            super().__init__(reworks=0)

        def preflight_runtime(self):
            pass

    class Workflow:
        def __init__(self, *a, **k):
            self.cycle_dir = None

        def _bind_cycle(self, target, **k):
            self.cycle_dir = target

        def _restore_resumed_cycle_routes(self, state):
            assert self.cycle_dir == cycle

        def _control_action(self):
            return WorkflowStateMixin._control_action(self)

        def _emit(self, *a):
            pass

        def _persist(self, current, **k):
            write_json(cycle / "research_state.json", current)

        def _run_cycle(self, resume_state):
            # Exercise the same native origin discovery the real Investigator
            # uses, before substituting scientific/model execution.
            assert resume_state["recovery"]["action"] == "resume"
            assert resume_state["recovery"]["resumed_at"]
            current_control = self._control_action()
            try:
                WorkflowStateMixin._check_recovery_control(self, resume_state)
            except RecoveryStop:
                visits.append({"new_control": current_control, "origin": None})
                return resume_state
            session = SimpleNamespace(
                state=resume_state,
                owner=self,
                invocation_ledger=InvocationLedger(cycle / "invocations.jsonl"),
                invocation_id="inv-9-investigator-new-monitor",
                stage="investigate",
                logical_key="original-frozen-slot",
                identifier=thread,
                contract=role_contract("investigator", phase="investigate"),
                handoff_path=cycle
                / "workspace/.tm-handoff/inv-9-investigator-new-monitor.json",
            )
            original = find_native_goal_origin(session)
            assert original["invocation_id"] == origin and original[
                "handoff_path"
            ] == str(handoff)
            visits.append(
                {
                    "new_control": None,
                    "origin": original,
                    "state": copy.deepcopy(resume_state),
                }
            )
            return {
                **resume_state,
                "status": "paused",
            }  # End this offline observation, no model call.

    monkeypatch.setattr(backends, "CodexBackend", Backend)
    monkeypatch.setattr(workflow, "Workflow", Workflow)
    monkeypatch.setattr("theory_master.observability.collector.Collector", MagicMock())
    return spec, cycle, state, control, claims, launches, visits


def test_explicit_worker_resume_consumes_old_pause_and_reaches_original_goal(paused):
    spec, cycle, state, control, claims, launches, visits = paused
    old_claim = worker_store.read_worker(spec.research_root, "worker-1")["claim_id"]
    original_files = {
        path: path.read_bytes()
        for path in (
            cycle / "events.jsonl",
            cycle / "runtime_events.jsonl",
            cycle / "invocations.jsonl",
        )
    }
    result = worker_actions.worker_action(spec, "worker-1", "resume")
    assert result["cycle"] == "9" and result["hypothesis_id"] == "H-0009"
    assert result["claim_id"] != old_claim and len(launches) == 1
    assert not (cycle / "recovery_control.json").exists()
    archived = json.loads(Path(result["superseded_control"]["path"]).read_text())
    assert archived["superseded_control"] == control
    assert archived["authorization"]["claim_id"] == result["claim_id"]
    assert json.loads((cycle / "research_state.json").read_text()) == state
    assert (
        worker_execution.run_once(
            spec, "worker-1", result["claim_id"], result["epoch"], resume_cycle="9"
        )
        == 2
    )
    assert (
        len(visits) == 1
        and visits[0]["origin"]["thread_id"] == state["threads"]["investigator"]
    )
    restored = visits[0]["state"]
    assert restored["hypothesis_lock"] == state["hypothesis_lock"]
    assert restored["runtime_source_provenance"] == state["runtime_source_provenance"]
    assert restored["recovery"]["kind"] == "original-recovery-kind"
    assert all(path.read_bytes() == value for path, value in original_files.items())
    assert claims.list_claims()[0]["hypothesis_id"] == "H-0009"


def test_pause_after_resume_authorization_is_consumed_only_by_running_control_boundary(
    paused,
):
    spec, cycle, _, _, _, _, visits = paused
    result = worker_actions.worker_action(spec, "worker-1", "resume")
    newer = {
        "action": "pause",
        "reason": "new operator decision",
        "requested_at": "2026-11-19T11:00:00+08:00",
    }
    write_json(cycle / "recovery_control.json", newer)
    assert (
        worker_execution.run_once(
            spec, "worker-1", result["claim_id"], result["epoch"], resume_cycle="9"
        )
        == 2
    )
    assert visits == [{"new_control": newer, "origin": None}]
    after = json.loads((cycle / "research_state.json").read_text())
    assert (
        after["status"] == "paused" and after["recovery"]["reason"] == newer["reason"]
    )


@pytest.mark.parametrize(
    "fault",
    [
        "abandon_control",
        "abandoned_state",
        "unknown",
        "malformed",
        "symlink",
        "oversize",
    ],
)
def test_worker_resume_rejects_unsafe_controls_before_claim_or_launch(paused, fault):
    spec, cycle, state, _, claims, launches, _ = paused
    path = cycle / "recovery_control.json"
    if fault == "abandon_control":
        write_json(path, {"action": "abandon_cycle"})
    elif fault == "abandoned_state":
        state["abandoned_cycle"] = {"reason": "explicit withdrawal"}
        write_json(cycle / "research_state.json", state)
    elif fault == "unknown":
        write_json(path, {"action": "invented"})
    elif fault == "malformed":
        path.write_text("not JSON")
    elif fault == "symlink":
        target = cycle / "outside.json"
        target.write_text(path.read_text())
        path.unlink()
        path.symlink_to(target)
    else:
        path.write_text(" " * 65_537)
    before = claims.list_claims()
    with pytest.raises((ValueError, OSError)):
        worker_actions.worker_action(spec, "worker-1", "resume")
    assert claims.list_claims() == before and launches == []
    assert path.exists()


@pytest.mark.parametrize("initially_present", [False, True])
def test_changed_request_cannot_be_deleted_as_an_old_pause(tmp_path, initially_present):
    control = tmp_path / "recovery_control.json"
    if initially_present:
        write_json(control, {"action": "pause", "requested_at": "old"})
    snapshot = preparation.snapshot_resume_control(tmp_path)
    newer = {"action": "pause", "requested_at": "new"}
    write_json(control, newer)
    with pytest.raises(ValueError, match="changed"):
        preparation.consume_resume_control(
            tmp_path, snapshot, authorization={"action": "resume"}
        )
    assert json.loads(control.read_text()) == newer
    assert not (tmp_path / "resume_controls").exists()


def test_recovery_preparation_preserves_source_and_restores_pending_guidance(tmp_path):
    from theory_master.attention_guidance import write_attention_guidance

    write_attention_guidance(
        tmp_path,
        "Use the approved original experiment.",
        source="human",
        attention_code="run_failed",
    )
    state = {
        "recovery": {"action": "pause", "kind": "original", "at": "original-time"},
        "runtime_source_provenance": {"sha": "original"},
        "native_goal": {"origin_identity": {"created_at": 12}},
    }
    original = copy.deepcopy(state)
    preparation.prepare_resume_recovery(state, tmp_path)
    assert (
        state["recovery"]["action"] == "resume"
        and state["recovery"]["at"] == "original-time"
    )
    assert (
        state["recovery"]["kind"] == "original"
        and state["recovery"]["allow_thread_fallback"] is True
    )
    assert state["native_goal"] == original["native_goal"]
    assert state["runtime_source_provenance"] == original["runtime_source_provenance"]
    assert state["operator_guidance_pending"] is True
