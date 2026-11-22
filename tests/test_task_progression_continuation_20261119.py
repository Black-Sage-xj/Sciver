"""Serial task continuation after local route failure, without live model calls.

Real Workflow/qualification/invocation/Goal-seal paths use disposable fixtures.
Only Agent replies and the Native Goal execution transport are deterministic.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.backend_fakes import _fixture_prompt_context
from theory_master.native_goal import (
    NativeGoalExecutionLimit, NativeGoalIncomplete, NativeGoalResult, read_goal_findings,
)
from theory_master.scientific_objective import frozen_science_digest
from theory_master.specification import TaskSpec, list_cycle_dirs
from theory_master.supervisor_incident import NativeGoalRuntimeFailure
from theory_master.supervisor_intervention import KEY
from theory_master.task import bootstrap
from theory_master.task_progression import local_replan_reason
from theory_master.workflow import Workflow
import test_native_goal_execution_limit_20261119 as limit_fixture
import test_native_goal_quota_auto_adjust_20261010 as quota_fixture


def _stage(prompt):
    return prompt.split("Current task: ", 1)[1].split(" ", 1)[0].strip() if "Current task: " in prompt else ""


def _context(prompt):
    return _fixture_prompt_context(prompt)


def _wire_context(prompt):
    return json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0] if "Context:\n" in prompt else {}


def _task(tmp_path, monkeypatch, *, max_cycles):
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv(KEY, "0")
    notes = tmp_path / "notes"
    notes.mkdir()
    plan = notes / "research_plan.md"
    plan.write_text("# Goal\nFind a bounded mechanism with the available inputs.\n\n"
                    "# Research Contract\nUse task-local synthetic sources only.\n\n"
                    "# Final Gate\nAn independently checked scientific comparison and its report exist.\n\n"
                    "# Required Context\n- source A and source B are disposable fixture inputs\n")
    binding = bootstrap(plan, allocation_root=tmp_path)
    config = json.loads(binding.config_path.read_text())
    config.update(task_id=14, max_cycles=max_cycles)
    binding.config_path.write_text(json.dumps(config))
    return TaskSpec.from_config(binding.config_path)


def _change_budget(spec, budget):
    config = json.loads(spec.config_path.read_text())
    config["max_cycles"] = budget
    spec.config_path.write_text(json.dumps(config))
    return TaskSpec.from_config(spec.config_path)


class ProgressionBackend(quota_fixture.GoalQuotaBackend):
    """Exercise production Native Goal invocation plus deterministic scientific roles."""
    def __init__(self, mode="input_limit", *, pass_goal=True, fail_every_cycle=False):
        super().__init__("blocked")
        self.mode = mode
        self.pass_goal = pass_goal
        self.fail_every_cycle = fail_every_cycle
        self.cwd = None
        self.cycle_id = None
        self.inputs = []
        self.proposals = {}
        self.native_science = {}
        self.old_state_at_replan = None
        self.old_closure_at_replan = None

    def invoke(self, role, prompt, thread_id, model, provider):
        stage, context = _stage(prompt), _context(prompt)
        cycle = str(self.cycle_id)
        self.inputs.append({"cycle": cycle, "role": role, "stage": stage,
                            "thread": thread_id, "context": context, "wire_context": _wire_context(prompt)})
        if cycle == "2" and role == "research_lead" and stage == "frontier_proposal":
            previous = Path(self.cwd).parent.parent / "1"
            self.old_state_at_replan = (previous / "research_state.json").read_bytes()
            closure = previous / "operational_closure.json"
            self.old_closure_at_replan = closure.read_bytes() if closure.exists() else None
        if role == "attention_reviewer":
            return thread_id, json.dumps({"action": "hold", "reason": "The old bounded retry stays exhausted.",
                                           "guidance": ""})
        if role == "judge" and stage == "goal_completion":
            return thread_id, json.dumps({
                "verdict": "passed" if self.pass_goal else "not_passed",
                "rationale": "The deterministic task requirement is satisfied." if self.pass_goal
                             else "Another independent comparison is still required.",
            })
        thread, raw = super().invoke(role, prompt, thread_id, model, provider)
        value = json.loads(raw)
        if role == "research_lead" and stage == "frontier_proposal":
            self.proposals[cycle] = self.proposals.get(cycle, 0) + 1
            for item in value.get("candidates", []):
                item["hypothesis"] = f"A distinct bounded mechanism in Cycle {cycle}, proposal {self.proposals[cycle]}."
                item["decisive_test"] = f"Compare the controlled source-B response for Cycle {cycle}, proposal {self.proposals[cycle]}."
        if (self.mode == "qualification" and role == "challenger" and stage == "candidate_review"
                and (cycle == "1" or self.fail_every_cycle)):
            for review in value.get("reviews", []):
                review.update(verdict="reject", reason="The old source-A test has no discriminating comparison.")
        return thread, json.dumps(value)

    def invoke_goal(self, role, *, context_prompt, handoff_path, thread_id, **kwargs):
        cycle = str(self.cycle_id)
        self.started.append(thread_id)
        context, _ = json.JSONDecoder().raw_decode(context_prompt)
        self.native_science[cycle] = copy.deepcopy(context["frozen_objective"])
        limited = self.mode == "input_limit" and (cycle == "1" or self.fail_every_cycle)
        if limited:
            binding = context["sdk_execution_limit_handoff"]
            path, _, _ = limit_fixture._delivery(Path(binding["workspace_path"]), binding["invocation_id"],
                                                 context["frozen_objective"])
            limit = limit_fixture._read(path, binding)
            self.last_native_goal = {"status": "execution_limited", "thread_id": thread_id,
                                     "source": "sdk_turn_boundary_handoff", "native_goal_cancel_requested": True}
            raise NativeGoalExecutionLimit(thread_id, limit, path)
        if self.mode in {"provider_failure", "runtime_failure"}:
            self.last_native_goal = {"status": "blocked", "thread_id": thread_id}
            raise NativeGoalRuntimeFailure(thread_id=thread_id, error="A provider/transport failure needs recovery.",
                                            provider_event="LLM_FAILED")
        if self.mode in {"usageLimited", "budgetLimited", "paused"}:
            self.last_native_goal = {"status": self.mode, "thread_id": thread_id}
            raise NativeGoalIncomplete(self.mode, thread_id)
        path = Path(handoff_path)
        path.write_text(json.dumps({"findings": [{
            "statement": f"An independently checked controlled comparison in Cycle {cycle} establishes the bounded claim."
        }]}))
        self.last_native_goal = {"status": "complete", "thread_id": thread_id}
        return thread_id, read_goal_findings(NativeGoalResult("complete", thread_id, 1, 1), path)


def _rows(backend, *, cycle, role, stage):
    return [row for row in backend.inputs if row["cycle"] == str(cycle)
            and row["role"] == role and row["stage"] == stage]


def _cycles(spec):
    return sorted(list_cycle_dirs(spec.research_root, task_mode=True), key=lambda path: int(path.name))


def _assert_old_archive_unchanged(backend, first):
    assert first.joinpath("research_state.json").read_bytes() == backend.old_state_at_replan
    closure = first / "operational_closure.json"
    if backend.old_closure_at_replan is not None:
        assert closure.read_bytes() == backend.old_closure_at_replan


def test_run_replans_after_formal_input_limit_and_reaches_independent_goal_gate(tmp_path, monkeypatch):
    spec = _task(tmp_path, monkeypatch, max_cycles=4)
    backend = ProgressionBackend()
    workflow = Workflow(spec, backend)
    state = workflow.run()
    cycles = _cycles(spec)
    assert [p.name for p in cycles] == ["1", "2"]
    old = json.loads((cycles[0] / "research_state.json").read_text())
    assert local_replan_reason(old) == "input_support_unavailable"
    assert old["status"] == "over" and old["assessment_state"] == "not_adjudicated"
    assert old["scientific_verdict"] is None
    assert old["hypothesis_lock"]["id"] != state["hypothesis_lock"]["id"]
    assert old["hypothesis_lock"]["decisive_test"] == backend.native_science["1"]["decisive_test"]
    assert state["cycle_id"] == "2" and state["goal_completion"]["verdict"] == "passed"
    assert state["scientific_verdict"] == "supported" and workflow.invocation_cycle_index == 2
    assert not _rows(backend, cycle=1, role="judge", stage="terminal_assessment")
    assert len(_rows(backend, cycle=2, role="judge", stage="terminal_assessment")) == 1
    for role, stage in (("research_lead", "frontier_proposal"), ("challenger", "candidate_review")):
        row = _rows(backend, cycle=2, role=role, stage=stage)[0]
        history = row["context"]["recent_execution_limits"]
        assert history[0]["cycle_id"] == "1" and history[0]["assessment_state"] == "not_adjudicated"
        assert history[0]["frozen_objective_sha256"] == frozen_science_digest(backend.native_science["1"])
        previous_thread = _rows(backend, cycle=1, role=role, stage=stage)[0]["thread"]
        if role == "research_lead":
            assert row["thread"] == previous_thread
            assert "lead_stage_update" in row["wire_context"]
        else:
            assert row["thread"] != previous_thread
    _assert_old_archive_unchanged(backend, cycles[0])


@pytest.mark.parametrize("legacy", [False, True])
def test_closed_input_limit_waits_for_new_verified_bytes_without_reopening_cycle(tmp_path, monkeypatch, legacy):
    from theory_master.supervisor_input_support import input_support_recovery

    spec = _task(tmp_path, monkeypatch, max_cycles=1)
    manifest = spec.task_home / "input_manifest.json"
    sources = {"schema": "theorymaster/public-research-input-manifest/v1",
               "data_files": [], "derived_context_files": []}

    def add_input(name, data, *, kind="data_files", verified_bytes=None):
        (spec.task_home / name).write_bytes(data)
        trusted = data if verified_bytes is None else verified_bytes
        sha = hashlib.sha256(trusted).hexdigest()
        sources[kind].append({"path": name, "bytes": len(trusted), "sha256": sha})
        manifest.write_text(json.dumps(sources))
        return sha

    initial_id = add_input("original.csv", b"x\n1\n")
    backend = ProgressionBackend()
    state = Workflow(spec, backend).run()
    assert state["input_support_baseline"]["verified_research_input_ids"] == [initial_id]
    assert local_replan_reason(state) == "input_support_unavailable"
    assert _rows(backend, cycle=1, role="judge", stage="terminal_assessment") == []
    cycle, = _cycles(spec)
    state_path = cycle / "research_state.json"
    if legacy:
        state.pop("input_support_baseline")
        state_path.write_text(json.dumps(state))
    archived_state = state_path.read_bytes()
    closure_path = cycle / "operational_closure.json"
    archived_closure = closure_path.read_bytes()
    assert input_support_recovery(cycle, state, establish_baseline=True)["route"] == "input_support_wait"

    add_input("alias.csv", b"x\n1\n")
    add_input("method-note.md", b"Context returned by the original source", kind="derived_context_files")
    new_id = add_input("restored.csv", b"x\n0\n", verified_bytes=b"x\n2\n")
    spec.document.write_text(spec.document.read_text() + "\n")
    assert input_support_recovery(cycle, state)["route"] == "input_support_wait"

    (spec.task_home / "restored.csv").write_bytes(b"x\n2\n")
    recovery = input_support_recovery(cycle, state)
    assert recovery["route"] == "input_support"
    assert recovery["added_input_ids"] == [new_id] and recovery["wake_identity"]
    assert recovery["invocation_id"] == state["execution_limit_handoff"]["invocation_id"]
    assert input_support_recovery(cycle, {**state, "worker_id": "another-owner"}) is None
    assert state_path.read_bytes() == archived_state and closure_path.read_bytes() == archived_closure
    closure_path.write_text("{}")
    assert input_support_recovery(cycle, state) is None


@pytest.mark.parametrize("invalid", [None, "owner", "cycle_control", "new_input_digest"])
def test_input_support_runner_rechecks_live_owner_and_unchanged_recovery_target(tmp_path, monkeypatch, invalid):
    from theory_master.runner import _require_input_support_takeover

    cycle = tmp_path / "4"
    spec = SimpleNamespace(research_root=tmp_path)
    plan = {"route": "input_support", "dependency_identity": "a" * 64, "wake_identity": "b" * 64}
    registry = {"requested_operation": "takeover", "takeover_cycle": "4", "current_cycle": "4",
                "launch_id": "specific-run", "supervisor_pid": 41, "supervisor_start_ticks": 4,
                "input_support_attempt": {"launch_id": "specific-run", "stopped_cycle": "4",
                                          "dependency_identity": "a" * 64, "wake_identity": "b" * 64,
                                          "status": "launching"}}
    if invalid == "new_input_digest":
        plan["wake_identity"] = "c" * 64
    monkeypatch.setattr("theory_master.runner.os.getppid", lambda: 42 if invalid == "owner" else 41)
    monkeypatch.setattr("theory_master.runtime_manager.load_registry", lambda _: registry)
    monkeypatch.setattr("theory_master.runtime_manager.process_identity_alive", lambda pid, ticks: (pid, ticks) == (41, 4))
    monkeypatch.setattr("theory_master.supervisor_intervention.supervisor_enabled", lambda _: True)
    monkeypatch.setattr("theory_master.supervisor_activation.latest_recovery",
                        lambda _: None if invalid == "cycle_control" else (cycle, {}, "input_support"))
    monkeypatch.setattr("theory_master.supervisor_activation.input_support_target", lambda *args, **kwargs: plan)
    if invalid:
        with pytest.raises(ValueError, match="Input-support recovery|Verified input support"):
            _require_input_support_takeover(spec, "4")
    else:
        _require_input_support_takeover(spec, "4")


def test_run_replans_after_qualification_exhaustion_without_reopening_old_retry(tmp_path, monkeypatch):
    spec = _task(tmp_path, monkeypatch, max_cycles=3)
    backend = ProgressionBackend("qualification")
    workflow = Workflow(spec, backend)
    state = workflow.run()
    cycles = _cycles(spec)
    assert [p.name for p in cycles] == ["1", "2"]
    old = json.loads((cycles[0] / "research_state.json").read_text())
    assert local_replan_reason(old) == "no_qualified_candidate"
    assert old["status"] == "no_qualified_candidate" and old["hypothesis_lock"]["status"] == "unlocked"
    assert len(old["qualification_attempts"]) == 3 and backend.proposals == {"1": 3, "2": 1}
    assert not old.get("scientific_verdict") and not old.get("role_outputs", {}).get("judge")
    assert state["cycle_id"] == "2" and state["goal_completion"]["verdict"] == "passed"
    assert workflow.invocation_cycle_index == 2 and len(backend.started) == 1
    for role, stage in (("research_lead", "frontier_proposal"), ("challenger", "candidate_review")):
        row = _rows(backend, cycle=2, role=role, stage=stage)[0]
        history = row["context"]["recent_qualification_stops"]
        assert history and all(item["cycle_id"] == "1" for item in history)
        assert all(item["review"]["verdict"] == "reject" for item in history)
        assert all(item["frozen_objective_sha256"] == frozen_science_digest(item["frozen_objective"]) for item in history)
        previous_thread = _rows(backend, cycle=1, role=role, stage=stage)[0]["thread"]
        if role == "research_lead":
            assert row["thread"] == previous_thread
            assert "lead_stage_update" in row["wire_context"]
        else:
            assert row["thread"] != previous_thread
    _assert_old_archive_unchanged(backend, cycles[0])


@pytest.mark.parametrize("mode", ["input_limit", "qualification"])
def test_task_master_resume_starts_new_cycle_and_preserves_closed_local_attempt(tmp_path, monkeypatch, mode):
    spec = _task(tmp_path, monkeypatch, max_cycles=1)
    backend = ProgressionBackend(mode)
    stopped = Workflow(spec, backend).run()
    first = _cycles(spec)[0]
    before = (first / "research_state.json").read_bytes()
    assert local_replan_reason(stopped) is not None
    assert len(_cycles(spec)) == 1 and not _rows(backend, cycle=2, role="research_lead", stage="frontier_proposal")
    spec = _change_budget(spec, 2)
    resumed = Workflow(spec, backend)
    state = resumed.resume(14)
    assert [p.name for p in _cycles(spec)] == ["1", "2"]
    assert state["cycle_id"] == "2" and state["goal_completion"]["verdict"] == "passed"
    assert (first / "research_state.json").read_bytes() == before
    assert resumed.invocation_cycle_index == 1  # New invocation, no phantom slot for archived C1.


def test_exact_old_cycle_resume_does_not_become_task_master_replanning(tmp_path, monkeypatch):
    spec = _task(tmp_path, monkeypatch, max_cycles=1)
    backend = ProgressionBackend("qualification")
    first = Workflow(spec, backend).run()
    assert first["cycle_id"] == "1" and first["status"] == "no_qualified_candidate"
    spec = _change_budget(spec, 2)
    monkeypatch.setenv(KEY, "1")
    state = Workflow(spec, backend).resume("1")
    assert state["cycle_id"] == "1" and state["status"] == "no_qualified_candidate"
    assert [p.name for p in _cycles(spec)] == ["1"]
    assert not _rows(backend, cycle=2, role="research_lead", stage="frontier_proposal")


@pytest.mark.parametrize("mode", ["input_limit", "qualification"])
def test_each_local_replan_consumes_remaining_batch_budget_without_reset(tmp_path, monkeypatch, mode):
    spec = _task(tmp_path, monkeypatch, max_cycles=2)
    backend = ProgressionBackend(mode, fail_every_cycle=True)
    workflow = Workflow(spec, backend)
    state = workflow.run()
    assert [p.name for p in _cycles(spec)] == ["1", "2"]
    assert state["cycle_id"] == "2" and workflow.invocation_cycle_index == 2
    assert local_replan_reason(state) is not None
    assert not _rows(backend, cycle=3, role="research_lead", stage="frontier_proposal")



def test_repeated_input_limits_stop_after_one_alternate_despite_spare_budget(tmp_path, monkeypatch):
    spec = _task(tmp_path, monkeypatch, max_cycles=4)
    backend = ProgressionBackend("input_limit", fail_every_cycle=True)
    workflow = Workflow(spec, backend)
    state = workflow.run()
    assert [p.name for p in _cycles(spec)] == ["1", "2"]
    assert state["cycle_id"] == "2" and workflow.invocation_cycle_index == 2
    assert local_replan_reason(state) == "input_support_unavailable"
    assert not _rows(backend, cycle=3, role="research_lead", stage="frontier_proposal")

@pytest.mark.parametrize("mode", ["provider_failure", "runtime_failure", "usageLimited", "budgetLimited", "paused"])
def test_runtime_provider_and_resource_stops_never_open_another_cycle(tmp_path, monkeypatch, mode):
    spec = _task(tmp_path, monkeypatch, max_cycles=3)
    backend = ProgressionBackend(mode)
    workflow = Workflow(spec, backend)
    if mode in {"provider_failure", "runtime_failure"}:
        with pytest.raises(NativeGoalRuntimeFailure):
            workflow.run()
        state = json.loads((_cycles(spec)[0] / "research_state.json").read_text())
    else:
        state = workflow.run()
    assert [p.name for p in _cycles(spec)] == ["1"]
    assert local_replan_reason(state) is None
    assert not _rows(backend, cycle=2, role="research_lead", stage="frontier_proposal")


def _canonical_input_limit():
    science = {"hypothesis": "H", "scope": "fixed supplied sources", "decisive_test": "Fit eligible observations"}
    report = {"kind": "input_support_unavailable", "invocation_id": "inv-local",
              "frozen_objective_sha256": frozen_science_digest(science),
              "reason": "No eligible observations", "checked_alternatives": ["Source recovery checked"],
              "unexecuted_test": "Fit", "required_dependency": "Eligible observations",
              "evidence": [{"path": "gate.json", "sha256": "a" * 64}]}
    return {"status": "over", "phase": "complete", "protocol_health": "valid",
            "execution_active": False, "scientific_verdict": None, "assessment_state": "not_adjudicated",
            "hypothesis_lock": {"id": "H-0001", "status": "locked", **science},
            "operational_only_closure": {"kind": "input_support_unavailable", "reason": report["reason"],
                                         "execution_limit": report},
            "execution_limit_handoff": {"invocation_id": "inv-local", "execution_limit": report,
                                        "handoff_path": "/tmp/workspace/.tm-handoff/inv-local.json"}}


@pytest.mark.parametrize("guard", ["goal_passed", "pause", "interrupt", "abandon", "protocol", "budget",
    "budget_rejected", "cycle_budget", "runtime", "active", "unknown_closure", "changed_science",
    "missing_binding", "scientific_verdict"])
def test_local_replan_classifier_retains_independent_stop_guards(guard):
    state = _canonical_input_limit()
    assert local_replan_reason(state) == "input_support_unavailable"
    if guard == "goal_passed":
        state["goal_completion"] = {"verdict": "passed"}
    elif guard in {"pause", "interrupt"}:
        state["recovery"] = {"action": guard}
    elif guard == "abandon":
        state["abandoned_cycle"] = True
    elif guard == "protocol":
        state["protocol_health"] = "violation"
    elif guard in {"budget", "budget_rejected"}:
        state["investigator_budget"] = {"exhausted" if guard == "budget" else "rejected": True}
    elif guard == "cycle_budget":
        state["cycle_budget"] = {"exhausted": True}
    elif guard == "runtime":
        state["recovery"] = {"action": "error", "kind": "NativeGoalRuntimeFailure"}
    elif guard == "active":
        state["execution_active"] = True
    elif guard == "unknown_closure":
        state["operational_only_closure"]["kind"] = "literature_transport_blocked"
    elif guard == "changed_science":
        state["hypothesis_lock"]["scope"] = "changed sources"
    elif guard == "missing_binding":
        state["execution_limit_handoff"] = {}
    elif guard == "scientific_verdict":
        state["scientific_verdict"] = "open"
    assert local_replan_reason(state) is None
    assert not Workflow._can_continue_cycle_budget(state)


def test_sealed_goal_blocks_new_task_master_invocation(tmp_path, monkeypatch):
    spec = _task(tmp_path, monkeypatch, max_cycles=2)
    backend = ProgressionBackend("normal")
    state = Workflow(spec, backend).run()
    assert state["goal_completion"]["verdict"] == "passed"
    calls, cycles = len(backend.inputs), [p.name for p in _cycles(spec)]
    resumed = Workflow(spec, backend).resume(14)
    assert resumed["goal_completion"]["verdict"] == "passed"
    assert len(backend.inputs) == calls and [p.name for p in _cycles(spec)] == cycles
