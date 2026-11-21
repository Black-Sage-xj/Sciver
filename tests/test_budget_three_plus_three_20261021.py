"""Deterministic 3+3 resource, failure and Worker preservation contracts.

All samples are synthetic; no Provider, model or live research is called.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from theory_master.investigator_budget import (
    BUDGET_ATTENTION_CODE, BUDGET_POLICY_3_PLUS_3, BUDGET_CAP_CODE,
    SUPERVISOR_DECISION_CODE, REVIEWER_TRANCHES, MAX_TRANCHES,
    apply_review, apply_supervisor_budget_decision, budget_status,
    grant_supervisor_tranche,
)
from theory_master.supervisor_budget_review import review_supervisor_extension
from theory_master.budget_auto_preservation import (
    budget_preservation_notice, dispatch_auto_budget_preservation,
    run_single_budget_preservation, single_budget_preservation_preflight,
)
from theory_master.research_preservation import preservation_blockage, PreservationGuard

BASE = 50_000_000


def _new_cycle(tmp_path):
    root = tmp_path / "research"
    cycle = root / "1"
    (cycle / "budget").mkdir(parents=True)
    (cycle / "evidence").mkdir()
    (cycle / "workspace").mkdir()
    state = {
        "cycle_id": "1", "research_root": str(root),
        "status": "needs_attention", "resumable": True,
        "execution_active": False, "phase": "investigator:budget_review",
        "attention": {"code": BUDGET_ATTENTION_CODE},
        "investigator_budget_policy": BUDGET_POLICY_3_PLUS_3,
        "hypothesis_lock": {"id": "H-0001", "status": "locked",
                            "hypothesis": "fixed test", "scope": "old inputs",
                            "decisive_test": "one criterion"},
        "agents": {"investigator": {"tokens_total_live": BASE}},
    }
    _save(cycle, state)
    return cycle, state


def _save(cycle, state):
    for name in ("research_state.json", "run_state.json"):
        (cycle / name).write_text(json.dumps(state), encoding="utf-8")


def _advance(cycle, state, *, tokens, code=BUDGET_ATTENTION_CODE):
    state["agents"]["investigator"]["tokens_total_live"] = tokens
    state["attention"]["code"] = code
    _save(cycle, state)
    return state


def _approve_first_three(cycle, state):
    root = cycle.parent
    evidence_file = cycle / "evidence" / "registry.jsonl"
    evidence_file.write_text(json.dumps({"evidence_id": "E-1", "summary": "progress-1"}) + "\n")
    a = apply_review(
        cycle, decision="approve", reason="First verified new scientific progress with executable next measurement",
        next_test="Execute a bounded decisive test on existing networks",
        evidence_ids=["E-1"])
    assert a["new_allowance"] == BASE * 2
    _advance(cycle, state, tokens=BASE * 2)
    with evidence_file.open("a") as sink:
        sink.write(json.dumps({"evidence_id": "E-2", "summary": "progress-2"}) + "\n")
    b = apply_review(
        cycle, decision="approve", reason="Second distinct validated scientific progress after prior checkpoint",
        next_test="Finish the frozen signed energy comparison on prior networks",
        evidence_ids=["E-2"])
    assert b["new_allowance"] == BASE * 3
    _advance(cycle, state, tokens=BASE * 3, code=SUPERVISOR_DECISION_CODE)
    status = budget_status(cycle, root, state)
    assert status["granted_tranches"] == REVIEWER_TRANCHES
    return status


def test_supervisor_once_then_sdk_grants_fifth_and_sixth_only(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    (cycle / "runtime_events.jsonl").write_text(
        json.dumps({"agent": "investigator", "event": "LLM_MESSAGE",
                    "content": "A concrete validated scientific derivation"}) + "\n")
    fake = SimpleNamespace(
        invoke_runtime_review=Mock(return_value=("supervisor-thread", json.dumps({
            "decision": "authorize",
            "reason": "Independent available inputs support a discriminating bounded next analysis",
            "next_step": "Use the exact frozen protocol on existing graph assets",
        }))),
        drop_thread=Mock(),
    )
    rec = review_supervisor_extension(
        backend=fake, cycle_dir=cycle, state=state,
        goal="Frozen test", research_contract="No new assets", final_gate="Judge only")
    assert rec["decision"] == "supervisor_authorize"
    assert rec["status"]["granted_tranches"] == 4
    assert rec["status"]["supervisor_authorized"]
    assert fake.invoke_runtime_review.call_count == 1
    original = (cycle / "budget/investigator_reviews.jsonl").read_bytes()
    with pytest.raises(ValueError):
        apply_supervisor_budget_decision(
            cycle, authorize=True,
            reason="This is a second and forbidden full Supervisor authorization",
            next_step="Repeat another scientific comparison")
    assert (cycle / "budget/investigator_reviews.jsonl").read_bytes() == original
    _advance(cycle, state, tokens=BASE * 4)
    x = grant_supervisor_tranche(cycle)
    assert x["grant_number"] == 5
    with pytest.raises(ValueError):
        grant_supervisor_tranche(cycle)  # cannot spend twice at same boundary
    _advance(cycle, state, tokens=BASE * 5)
    y = grant_supervisor_tranche(cycle)
    assert y["grant_number"] == 6
    _advance(cycle, state, tokens=BASE * 6)
    terminal = budget_status(cycle, cycle.parent, state)
    assert terminal["hard_cap_reached"] and terminal["granted_tranches"] == MAX_TRANCHES
    assert terminal["allowance_tokens"] == 300_000_000
    before = (cycle / "budget/investigator_reviews.jsonl").read_bytes()
    with pytest.raises(ValueError):
        grant_supervisor_tranche(cycle)  # never grant a seventh
    assert (cycle / "budget/investigator_reviews.jsonl").read_bytes() == before
    assert len((cycle / "budget/investigator_reviews.jsonl").read_text().splitlines()) == 6


def test_supervisor_denies_and_cannot_grant_later(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    rejected = apply_supervisor_budget_decision(
        cycle, authorize=False,
        reason="The candidate test cannot distinguish competing hypotheses using frozen inputs",
        next_step="Archive current evidence, then let Research Lead select a separate branch")
    status = budget_status(cycle, cycle.parent, state)
    assert rejected["decision"] == "supervisor_decline"
    assert status["supervisor_declined"] and status["rejected"]
    assert status["allowance_tokens"] == BASE * 3
    with pytest.raises(ValueError):
        grant_supervisor_tranche(cycle)
    with pytest.raises(ValueError):
        apply_supervisor_budget_decision(
            cycle, authorize=True,
            reason="Cannot override one immutable Supervisor decision after rejection",
            next_step="Spend more tokens contrary to the recorded rejection")


def test_supervisor_missing_dialogue_fails_closed_without_model_or_grant(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    fake = SimpleNamespace(invoke_runtime_review=Mock())
    before = (cycle / "budget/investigator_reviews.jsonl").read_bytes()
    with pytest.raises(RuntimeError, match="Missing original"):
        review_supervisor_extension(
            backend=fake, cycle_dir=cycle, state=state,
            goal="Research", research_contract="Contract", final_gate="Gate")
    fake.invoke_runtime_review.assert_not_called()
    assert (cycle / "budget/investigator_reviews.jsonl").read_bytes() == before


def test_reviewer_cannot_grant_fourth_tranche(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    _advance(cycle, state, tokens=BASE * 3, code=BUDGET_ATTENTION_CODE)
    with pytest.raises(ValueError, match="Budget Reviewer may authorize only"):
        apply_review(
            cycle, decision="approve",
            reason="This reviewer is incorrectly authorizing a fourth tranche beyond its authority",
            next_test="Attempt an extra statistical comparison without Supervisor approval",
            evidence_ids=[])
    assert budget_status(cycle, cycle.parent, state)["granted_tranches"] == 3


def test_terminal_cap_preservation_checks_both_ledgers(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    apply_supervisor_budget_decision(
        cycle, authorize=True,
        reason="Existing test has a concrete and independent discriminating route",
        next_step="Check a fixed trusted network before another costly simulation")
    _advance(cycle, state, tokens=BASE * 4)
    grant_supervisor_tranche(cycle)
    _advance(cycle, state, tokens=BASE * 5)
    grant_supervisor_tranche(cycle)
    _advance(cycle, state, tokens=BASE * 6, code=BUDGET_CAP_CODE)
    state["phase"] = "investigator:budget_denied"
    state["investigator_budget"] = budget_status(cycle, cycle.parent, state)
    _save(cycle, state)
    assert preservation_blockage(cycle, state, state)["code"] == BUDGET_CAP_CODE
    corrupt = dict(state, investigator_budget=dict(state["investigator_budget"]))
    corrupt["investigator_budget"]["hard_cap_reached"] = False
    with pytest.raises(PreservationGuard):
        preservation_blockage(cycle, corrupt, state)


def test_dispatch_is_explicit_new_policy_once_and_not_manual_pause(tmp_path):
    root = tmp_path / "research"
    wid = "worker-1"
    cycle = root / "1"
    cycle.mkdir(parents=True)
    worker_folder = root / ".theory-master/workers" / wid
    worker_folder.mkdir(parents=True)
    status = {"worker_id": wid, "status": "PAUSED",
              "current_cycle": "1", "process_alive": False}
    marker = {
        "policy": "three_plus_three_v1", "code": "investigator_budget_cap_reached",
        "source": "sdk_authorized_budget_terminal", "cycle": "1"}
    scientific = {"status": "needs_attention", "execution_active": False,
                  "investigator_budget_policy": "three_plus_three_v1",
                  "attention": {"code": marker["code"]}, "budget_auto_preserve": marker}
    (cycle / "research_state.json").write_text(json.dumps(scientific))
    spec = SimpleNamespace(research_root=root)
    with patch("theory_master.worker_runtime.list_workers", return_value=[status]), \
         patch("theory_master.research_preservation.blocked_checkpoint") as checkpoint, \
         patch("theory_master.worker_runtime.worker_action",
               return_value={"status": "PRESERVING"}) as worker_action:
        assert dispatch_auto_budget_preservation(spec) is False  # user pause means pause
        status["status"] = "NEEDS_ATTENTION"
        assert dispatch_auto_budget_preservation(spec) is True
        assert dispatch_auto_budget_preservation(spec) is False
        checkpoint.assert_called_once()
        worker_action.assert_called_once_with(spec, wid, "preserve_release")
    receipt = json.loads((worker_folder / "budget_auto_preserve_1.json").read_text())
    assert receipt["status"] == "launched"


def test_preservation_reminder_durable_and_idempotent(tmp_path):
    root = tmp_path / "research"
    (root / "1").mkdir(parents=True)
    first = budget_preservation_notice(root, "worker-2", "1", success=True,
                                       hypothesis_id="H-0001")
    second = budget_preservation_notice(root, "worker-2", "1", success=True)
    assert first["incident_id"] == second["incident_id"]
    assert first["severity"] == "important"
    assert first["persistent_reminder"] is True
    assert len((root / "1/important_events.jsonl").read_text().splitlines()) == 1


@pytest.fixture
def single_budget_stop(tmp_path):
    from theory_master.backends import FakeBackend
    from theory_master.science_graph_store import ScienceGraphStore
    from theory_master.specification import TaskSpec
    from theory_master.supervisor_intervention import write_supervisor_setting
    from theory_master.task import bootstrap
    from theory_master.workflow import Workflow

    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal\ng\n\n# Research Contract\nc\n\n# Final Gate\nf\n\n# Required Context\n- r\n")
    spec = TaskSpec.from_config(bootstrap(plan).config_path)
    workflow = Workflow(spec, FakeBackend())
    workflow._ensure_cycle()
    cycle = workflow.cycle_dir
    (cycle / "workspace/README.md").write_text("Recorded incomplete synthetic investigation.")
    state = {
        "cycle_id": cycle.name, "research_root": str(spec.research_root), "branch_canvas": str(spec.branch_canvas),
        "status": "needs_attention", "resumable": True, "execution_active": False,
        "phase": "investigator:budget_review", "attention": {"code": BUDGET_ATTENTION_CODE},
        "investigator_budget_policy": BUDGET_POLICY_3_PLUS_3,
        "hypothesis_lock": {"id": "H-0001", "status": "locked", "hypothesis": "fixed method",
                            "scope": "authorized inputs", "decisive_test": "compare the two local models"},
        "agents": {"investigator": {"tokens_total_live": BASE}},
    }
    (cycle / "evidence").mkdir(exist_ok=True)
    _save(cycle, state)
    _approve_first_three(cycle, state)
    apply_supervisor_budget_decision(cycle, authorize=False,
        reason="The frozen comparison cannot be completed under the remaining resource contract.",
        next_step="Retain the checked observations for archival preservation.")
    code = "investigator_budget_supervisor_declined"
    state.update(phase="investigator:budget_denied", attention={"code": code}, next_executor="investigator",
                 investigator_budget=budget_status(cycle, spec.research_root, state),
                 budget_auto_preserve={"policy": BUDGET_POLICY_3_PLUS_3, "code": code,
                                       "source": "sdk_authorized_budget_terminal", "cycle": cycle.name})
    _save(cycle, state)
    with ScienceGraphStore(spec.graph_database) as store:
        store.add_hypothesis("H-0001", title="Fixed method", summary="Fixed method")
        store.register_cycle_hypothesis(cycle_id=cycle.name, hypothesis_id="H-0001")
        store.set_frontier(branch_id="frontier:H-0001", hypothesis_id="H-0001",
                           status="active", reason="Budget preservation fixture")
    write_supervisor_setting(spec.research_root, True)
    return workflow, cycle, state


def _own_single_budget_stop(cycle):
    from theory_master.runtime_manager import process_start_ticks, update_registry

    update_registry(cycle.parent, state="RECOVERING", requested_operation="takeover", launch_id="budget-takeover",
                    takeover_cycle=cycle.name, current_cycle=cycle.name, supervisor_pid=os.getppid(),
                    supervisor_start_ticks=process_start_ticks(os.getppid()), runner_pid=os.getpid(),
                    runner_start_ticks=process_start_ticks(os.getpid()))


def test_single_budget_stop_archives_with_exact_owner_once_and_no_verdict(single_budget_stop):
    from test_research_preservation_cycle86 import _stage_output
    from theory_master.science_graph_store import ScienceGraphStore

    workflow, cycle, state = single_budget_stop
    assert single_budget_preservation_preflight(cycle, state, spec=workflow.spec)["status"] == "ready"
    assert not (cycle / "preservation").exists()
    with pytest.raises(ValueError, match="runtime owns"):
        run_single_budget_preservation(workflow, cycle, launch_id="budget-takeover")
    assert not (cycle / "preservation").exists()

    _own_single_budget_stop(cycle)
    workout = (cycle / "workspace/README.md").read_bytes()

    def archive(*_args):
        assert workflow.backend._preservation_read_only is True
        return "archiver", json.dumps(_stage_output())

    with patch.object(workflow.backend, "invoke", side_effect=archive) as invoke, \
            patch.object(workflow.backend, "ensure_thread", wraps=workflow.backend.ensure_thread) as ensure:
        result = run_single_budget_preservation(workflow, cycle, launch_id="budget-takeover")
        with pytest.raises(PreservationGuard):
            run_single_budget_preservation(workflow, cycle, launch_id="budget-takeover")
    invoke.assert_called_once()
    ensure.assert_called_once()
    assert invoke.call_args.args[0] == "investigator" and ensure.call_args.args[1] is None
    assert result["closure"]["authority"] == "sdk_budget_terminal"
    assert result["closure"]["scientific_verdict"] is None
    assert not result["closure"]["next_cycle_created"]
    assert workout == (cycle / "workspace/README.md").read_bytes()
    assert json.loads((cycle / "preservation/sdk_budget_terminal.json").read_text())["status"] == "completed"
    with ScienceGraphStore(workflow.spec.graph_database, readonly=True) as store:
        assert store.db.execute("SELECT COUNT(*) FROM cycle_evaluations").fetchone()[0] == 0


@pytest.mark.parametrize("guard", ["operator_control", "active_goal", "worker", "missing_ledger",
                                   "foreign_ledger", "wrong_terminal_code", "prior_paid_output"])
def test_single_budget_preflight_fails_closed(single_budget_stop, guard):
    workflow, cycle, state = single_budget_stop
    if guard == "operator_control":
        (cycle / "recovery_control.json").write_text('{"action":"pause"}')
    elif guard == "active_goal":
        state["native_goal"] = {"status": "active", "thread_id": "native"}
    elif guard == "worker":
        state["worker_id"] = "worker-1"
    elif guard == "missing_ledger":
        (cycle / "budget/investigator_reviews.jsonl").unlink()
    elif guard == "foreign_ledger":
        ledger = cycle / "budget/investigator_reviews.jsonl"
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        ledger.write_text("\n".join(json.dumps({**row, "cycle_id": "999"}) for row in rows) + "\n")
    elif guard == "wrong_terminal_code":
        code = "investigator_budget_review_rejected"
        state["attention"]["code"] = code
        state["budget_auto_preserve"]["code"] = code
    else:
        (cycle / "preservation").mkdir()
        (cycle / "preservation/last_model_output.txt").write_text("partial output from another attempt")
    _save(cycle, state)
    decision = single_budget_preservation_preflight(cycle, state, spec=workflow.spec)
    assert decision is None or decision["status"] == "hold"
    assert not (cycle / "preservation/sdk_budget_terminal.json").exists()


def test_single_budget_unknown_model_delivery_never_repeats(single_budget_stop):
    workflow, cycle, _ = single_budget_stop
    _own_single_budget_stop(cycle)
    with patch.object(workflow.backend, "invoke", side_effect=RuntimeError("transport interrupted")) as invoke:
        with pytest.raises(RuntimeError, match="transport interrupted"):
            run_single_budget_preservation(workflow, cycle, launch_id="budget-takeover")
        with pytest.raises(PreservationGuard, match="not ready"):
            run_single_budget_preservation(workflow, cycle, launch_id="budget-takeover")
    invoke.assert_called_once()
    receipt = json.loads((cycle / "preservation/sdk_budget_terminal.json").read_text())
    assert receipt["status"] == "requires_reconciliation"
    assert not (cycle / "preservation/closure.json").exists()


@pytest.mark.parametrize("corruption", [None, "scientific_field", "cache_after_claim"])
def test_single_budget_display_replay_uses_existing_paid_output_only(single_budget_stop, corruption):
    from test_research_preservation_cycle86 import _stage_output
    from theory_master.budget_auto_preservation import _single_budget_proof
    from theory_master.research_preservation import preserve_blocked_cycle

    workflow, cycle, state = single_budget_stop
    folder = cycle / "preservation"
    folder.mkdir()
    paid = folder / "sdk_budget_terminal.json"
    paid.write_text(json.dumps({
        "schema": "theory-master/single-budget-preservation/v1",
        "status": "requires_reconciliation", "error_type": "ValueError",
        "launch_id": "prior-launch", "runner_pid": 99_999_999, "runner_start_ticks": 1,
        "proof": _single_budget_proof(cycle, state, spec=workflow.spec),
    }))
    output = _stage_output()
    output["canvas_summary"] = "Overlong display only " + "x" * 170
    if corruption == "scientific_field":
        output["remaining_gap"] = ""
    raw = folder / "last_model_output.txt"
    raw.write_text(json.dumps(output))
    paid_before, raw_before = paid.read_bytes(), raw.read_bytes()
    decision = single_budget_preservation_preflight(cycle, state, spec=workflow.spec)
    if corruption == "scientific_field":
        assert decision["status"] == "hold"
        assert not (folder / "sdk_budget_cached_replay.json").exists()
        return
    assert decision["status"] == "ready" and decision["cached_only"]

    def try_archive(**kwargs):
        if corruption == "cache_after_claim":
            raw.write_bytes(raw_before + b"\n")
        return preserve_blocked_cycle(**kwargs)

    _own_single_budget_stop(cycle)
    with patch("theory_master.research_preservation.preserve_blocked_cycle", side_effect=try_archive), \
            patch.object(workflow.backend, "invoke") as invoke, \
            patch.object(workflow.backend, "ensure_thread") as ensure:
        if corruption:
            with pytest.raises(PreservationGuard):
                run_single_budget_preservation(workflow, cycle, launch_id="budget-takeover")
            assert not (folder / "closure.json").exists()
        else:
            result = run_single_budget_preservation(workflow, cycle, launch_id="budget-takeover")
            assert result["closure"]["scientific_verdict"] is None
            stage = json.loads((folder / "stage_output.json").read_text())
            assert stage == {**output, "canvas_summary": stage["canvas_summary"]}
            assert len(stage["canvas_summary"]) <= 160
            assert raw.read_bytes() == raw_before
            assert json.loads((folder / "sdk_budget_cached_replay.json").read_text())["status"] == "completed"
        with pytest.raises(PreservationGuard, match="not ready"):
            run_single_budget_preservation(workflow, cycle, launch_id="budget-takeover")
    assert paid.read_bytes() == paid_before
    invoke.assert_not_called()
    ensure.assert_not_called()


def test_single_budget_own_supervisor_can_admit_after_its_runner_exits(single_budget_stop):
    from theory_master.runtime_manager import process_start_ticks, update_registry

    workflow, cycle, state = single_budget_stop
    update_registry(cycle.parent, state="RUNNING", requested_operation="run", launch_id="existing-run",
                    current_cycle=cycle.name, supervisor_pid=os.getpid(),
                    supervisor_start_ticks=process_start_ticks(os.getpid()), runner_pid=None)
    assert single_budget_preservation_preflight(cycle, state, spec=workflow.spec)["status"] == "ready"
    update_registry(cycle.parent, supervisor_pid=os.getppid(),
                    supervisor_start_ticks=process_start_ticks(os.getppid()))
    assert single_budget_preservation_preflight(cycle, state, spec=workflow.spec)["status"] == "hold"


@pytest.mark.parametrize(("recorded", "observations", "thread_status", "ready"), [
    ("paused", ("paused", "paused"), "idle", True),
    ("paused", ("paused", "active"), "idle", False),
    ("paused", (None, None), "idle", False),
    (None, (None, None), "idle", True),
    ("paused", ("paused", "paused"), "notLoaded", True),
    ("paused", ("paused", "active"), "notLoaded", False),
    ("paused", (None, None), "notLoaded", False),
])
def test_retained_goal_must_still_be_in_the_verified_stop_state(tmp_path, monkeypatch, recorded, observations,
                                                                thread_status, ready):
    from theory_master.budget_auto_preservation import _require_idle_budget_goal
    from theory_master.native_goal_recovery import goal_identity

    def goal(status):
        return SimpleNamespace(thread_id="native", created_at=1, objective="frozen test", status=status,
                               tokens_used=5, time_used_seconds=7, token_budget=None)

    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text("")
    monkeypatch.setattr("theory_master.codex_accounts.runtime_home", lambda: home)
    idle = SimpleNamespace(id="native", status=SimpleNamespace(root=SimpleNamespace(type=thread_status)),
                           turns=[SimpleNamespace(status="completed"), SimpleNamespace(status="interrupted")])
    stop = {"thread_id": "native", "goal_identity": goal_identity(goal("paused")), "goal_status": recorded}
    with patch("openai_codex.Codex"), patch("theory_master.codex_sdk_compat.read_thread",
                                               return_value=SimpleNamespace(thread=idle)), \
            patch("theory_master.codex_sdk_compat.read_goal", side_effect=[
                goal(status) if status is not None else None for status in observations]) as reads, \
            patch("theory_master.codex_sdk_compat.server_pid", return_value=111) as pid, \
            patch("theory_master.native_goal_recovery.ensure_no_foreign_goal_owner") as owner:
        if ready:
            _require_idle_budget_goal(tmp_path, stop)
        else:
            with pytest.raises(ValueError):
                _require_idle_budget_goal(tmp_path, stop)
    assert reads.call_count == 2
    pid.assert_called_once()
    owner.assert_called_once_with({"workspace_path": str(tmp_path / "workspace"), "thread_id": "native"},
                                  current_server_pid=111)


@pytest.mark.parametrize("foreign_owner", [False, True])
def test_unloaded_goal_rejects_live_turn_or_foreign_owner(tmp_path, monkeypatch, foreign_owner):
    from theory_master.budget_auto_preservation import _require_idle_budget_goal
    from theory_master.native_goal_recovery import NativeGoalRecoveryConflict, goal_identity

    goal = SimpleNamespace(thread_id="native", created_at=1, objective="frozen test", status="paused",
                           tokens_used=5, time_used_seconds=7, token_budget=None)
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text("")
    monkeypatch.setattr("theory_master.codex_accounts.runtime_home", lambda: home)
    thread = SimpleNamespace(id="native", status=SimpleNamespace(root=SimpleNamespace(type="notLoaded")),
                             turns=[SimpleNamespace(status="interrupted" if foreign_owner else "inProgress")])
    stop = {"thread_id": "native", "goal_identity": goal_identity(goal), "goal_status": "paused"}
    with patch("openai_codex.Codex"), patch("theory_master.codex_sdk_compat.read_thread",
                                               return_value=SimpleNamespace(thread=thread)), \
            patch("theory_master.codex_sdk_compat.read_goal", return_value=goal), \
            patch("theory_master.codex_sdk_compat.server_pid", return_value=111), \
            patch("theory_master.native_goal_recovery.ensure_no_foreign_goal_owner",
                  side_effect=NativeGoalRecoveryConflict("foreign owner")):
        with pytest.raises(NativeGoalRecoveryConflict if foreign_owner else ValueError):
            _require_idle_budget_goal(tmp_path, stop)


def test_preserve_failure_keeps_one_shot_receipt_and_notifies(tmp_path):
    root = tmp_path / "research"
    cycle = root / "2"
    cycle.mkdir(parents=True)
    worker_folder = root / ".theory-master/workers/worker-2"
    worker_folder.mkdir(parents=True)
    marker = {"policy": "three_plus_three_v1",
              "code": "investigator_budget_cap_reached",
              "source": "sdk_authorized_budget_terminal", "cycle": "2"}
    state = {"status": "needs_attention", "execution_active": False,
             "investigator_budget_policy": "three_plus_three_v1",
             "attention": {"code": marker["code"]}, "budget_auto_preserve": marker}
    (cycle / "research_state.json").write_text(json.dumps(state))
    worker = {"worker_id": "worker-2", "status": "NEEDS_ATTENTION",
              "current_cycle": "2", "process_alive": False}
    spec = SimpleNamespace(research_root=root)
    with patch("theory_master.worker_runtime.list_workers", return_value=[worker]), \
         patch("theory_master.research_preservation.blocked_checkpoint"), \
         patch("theory_master.worker_runtime.worker_action",
               side_effect=RuntimeError("synthetic launch error")) as action:
        assert dispatch_auto_budget_preservation(spec) is True
        assert dispatch_auto_budget_preservation(spec) is False
        action.assert_called_once()
    receipt = json.loads((worker_folder / "budget_auto_preserve_2.json").read_text())
    assert receipt["status"] == "failed_closed"
    events = [json.loads(x) for x in (cycle / "important_events.jsonl").read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["code"] == "budget_preservation_failed"
    assert events[0]["severity"] == "important"


def test_forged_marker_and_old_policy_cannot_auto_preserve(tmp_path):
    root = tmp_path / "research"
    cycle = root / "3"
    cycle.mkdir(parents=True)
    worker = {"worker_id": "worker-1", "status": "NEEDS_ATTENTION",
              "current_cycle": "3", "process_alive": False}
    original = {"status": "needs_attention", "execution_active": False,
                "investigator_budget_policy": "legacy",
                "attention": {"code": "investigator_budget_cap_reached"},
                "budget_auto_preserve": {"policy": "three_plus_three_v1",
                    "code": "investigator_budget_cap_reached",
                    "source": "sdk_authorized_budget_terminal", "cycle": "3"}}
    spec = SimpleNamespace(research_root=root)
    with patch("theory_master.worker_runtime.list_workers", return_value=[worker]), \
         patch("theory_master.worker_runtime.worker_action") as action:
        (cycle / "research_state.json").write_text(json.dumps(original))
        assert dispatch_auto_budget_preservation(spec) is False
        original["investigator_budget_policy"] = "three_plus_three_v1"
        original["budget_auto_preserve"]["code"] = "forged_different_code"
        (cycle / "research_state.json").write_text(json.dumps(original))
        assert dispatch_auto_budget_preservation(spec) is False
        original["budget_auto_preserve"]["code"] = original["attention"]["code"]
        original["status"] = "paused"
        (cycle / "research_state.json").write_text(json.dumps(original))
        assert dispatch_auto_budget_preservation(spec) is False
        action.assert_not_called()


def test_supervisor_ledger_refuses_tampering(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    apply_supervisor_budget_decision(
        cycle, authorize=True,
        reason="A verified new analytic approach can address the unchanged decisive test",
        next_step="Check predicted release on available independent roots only")
    journal = cycle / "budget/investigator_reviews.jsonl"
    good = journal.read_bytes()
    rows = [json.loads(line) for line in good.splitlines()]
    rows[-1]["grant_number"] = 5  # forged fourth grant
    journal.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(ValueError, match="invalid or excessive Supervisor tranche"):
        budget_status(cycle, cycle.parent, state)
    journal.write_bytes(good)
    assert budget_status(cycle, cycle.parent, state)["granted_tranches"] == 4


def test_new_three_plus_three_policy_does_not_migrate_old_cycle(tmp_path):
    cycle, state = _new_cycle(tmp_path)
    state.pop("investigator_budget_policy")
    _save(cycle, state)
    assert budget_status(cycle, cycle.parent, state)["policy_version"] == "legacy"
    assert budget_status(cycle, cycle.parent, state)["granted_tranches"] == 1


def test_workflow_uses_exactly_one_supervisor_review_then_sdk_installments(tmp_path):
    from theory_master.workflow_investigation import InvestigationMixin
    from theory_master.workflow_support import RecoveryStop

    cycle, state = _new_cycle(tmp_path)
    _approve_first_three(cycle, state)
    (cycle / "runtime_events.jsonl").write_text(
        json.dumps({"agent": "investigator", "event": "LLM_MESSAGE",
                    "content": "Useful bounded numerical research"}) + "\n")
    backend = SimpleNamespace(
        invoke_runtime_review=Mock(return_value=("supervisor-1", json.dumps({
            "decision": "authorize",
            "reason": "A useful independent derivation warrants one last fixed-scope comparison",
            "next_step": "Test the frozen relation against the known independent root set",
        }))),
        drop_thread=Mock(),
    )
    events = Mock()

    def _snapshot(current):
        current["investigator_budget"] = budget_status(cycle, cycle.parent, current)

    fake = SimpleNamespace(
        cycle_dir=cycle, spec=SimpleNamespace(research_root=cycle.parent,
        goal="Frozen research", contract="Only old networks",
        final_gate="Separate Judge"),
        backend=backend, _persist=lambda current, **kw: _save(cycle, current),
        _persist_investigator_budget_snapshot=_snapshot, _emit=events,
        _adjudicable_judge_records=lambda _: [],
        _evidence_context=lambda: {},
    )
    fake._hold_budget_terminal = lambda current, **kwargs: (
        InvestigationMixin._hold_budget_terminal(fake, current, **kwargs))
    with patch("theory_master.supervisor_budget_review.supervisor_enabled",
               return_value=True):
        # No reviewer approval is asked after the third budget ends.
        assert InvestigationMixin._resolve_3plus3_budget(
            fake, state, budget_status(cycle, cycle.parent, state))
    assert backend.invoke_runtime_review.call_count == 1
    assert budget_status(cycle, cycle.parent, state)["granted_tranches"] == 4
    for expected in (5, 6):
        _advance(cycle, state, tokens=(expected - 1) * BASE)
        assert InvestigationMixin._resolve_3plus3_budget(
            fake, state, budget_status(cycle, cycle.parent, state))
        assert budget_status(cycle, cycle.parent, state)["granted_tranches"] == expected
        assert backend.invoke_runtime_review.call_count == 1
    _advance(cycle, state, tokens=6 * BASE)
    with pytest.raises(RecoveryStop, match="resource phase ended"):
        InvestigationMixin._resolve_3plus3_budget(
            fake, state, budget_status(cycle, cycle.parent, state))
    assert state["budget_auto_preserve"]["code"] == BUDGET_CAP_CODE
    assert state["status"] == "needs_attention"
    assert state["scientific_verdict"] is None
    assert backend.invoke_runtime_review.call_count == 1
    assert budget_status(cycle, cycle.parent, state)["granted_tranches"] == 6
