from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from theory_master.core import FakeBackend, TaskSpec, Workflow
from theory_master.delivery_repair import repair_delivery, science_authority_digest
from theory_master.goal_terminal import GoalSealedError
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.specification import list_cycle_dirs
from theory_master.task import bootstrap


class CountingGoalBackend(FakeBackend):
    def __init__(self):
        super().__init__(reworks=0)
        self.goal_calls = 0
        self.pass_goal = False
        self.goal_context = {}

    def invoke(self, role, prompt, thread_id, model, provider):
        if role == "judge" and "Current task: goal_completion " in prompt:
            self.goal_calls += 1
            self.goal_context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
            if self.pass_goal:
                return str(thread_id), json.dumps({"verdict": "passed", "rationale": "The repaired deliverable satisfies the fixture Final Gate."})
        return super().invoke(role, prompt, thread_id, model, provider)


def _completed(tmp_path: Path, *, max_cycles: int = 1):
    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal\n\nBounded fixture.\n\n# Research Contract\n\nLocal inputs.\n\n# Final Gate\n\nResult and reproducible report.\n\n# Required Context\n\n- local fixture\n")
    binding = bootstrap(plan)
    config = json.loads(binding.config_path.read_text())
    config["max_cycles"] = max_cycles
    binding.config_path.write_text(json.dumps(config))
    spec = TaskSpec.from_config(binding.config_path)
    backend = CountingGoalBackend()
    workflow = Workflow(spec, backend)
    state = workflow.run()
    assert state["goal_completion"]["verdict"] == "not_passed"
    cycle = list_cycle_dirs(spec.research_root, task_mode=True)[0]
    return spec, workflow, backend, cycle


def _report(spec, cycle):
    pointer = json.loads((cycle / "delivery_revision.json").read_text())
    frozen = json.loads(Path(pointer["revision_path"]).read_text())
    return Path(frozen["report_versions"][0]["path"])


def test_unchanged_revision_reuses_assessment_without_new_science(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    before = science_authority_digest(spec, spec.graph_database)
    initial = json.loads((cycle / "delivery_revision.json").read_text())
    first = repair_delivery(workflow)
    second = repair_delivery(workflow, cycle=1, reassess=True)
    assert first["delivery_revision"] == second["delivery_revision"] == initial["delivery_revision"]
    assert backend.goal_calls == 1
    assert list_cycle_dirs(spec.research_root, task_mode=True) == [cycle]
    assert science_authority_digest(spec, spec.graph_database) == before
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert len(store.goal_gate_history(cycle.name)) == 1
    access = backend.goal_context["delivery_material_access"]
    assert access["revision_path"] == initial["revision_path"]


def test_report_repair_creates_new_revision_and_preserves_old_assessment(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    old = json.loads((cycle / "goal_completion_out.json").read_text())
    report = _report(spec, cycle)
    report.write_text(report.read_text() + "\nReproduction navigation repaired.\n")
    rebuilt = repair_delivery(workflow)
    assert rebuilt["delivery_revision"] != old["delivery_revision"]
    assert backend.goal_calls == 1
    result = repair_delivery(workflow, reassess=True)
    assert result["assessment"]["verdict"] == "not_passed"
    assert backend.goal_calls == 2
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        history = store.goal_gate_history(cycle.name)
        assert len(history) == 2
        assert history[-1]["supersedes"] == history[0]["assessment_id"]
        assert Path(history[0]["receipt_path"]).is_file()
    assert json.loads(Path(old["revision_path"]).read_text())["report_versions"]
    assert len(list_cycle_dirs(spec.research_root, task_mode=True)) == 1


def test_repair_rejects_science_change_and_closed_guard(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    state_path = cycle / "research_state.json"
    original = json.loads(state_path.read_text())
    changed = json.loads(state_path.read_text())
    changed["hypothesis_lock"]["scope"] = "changed scientific scope"
    state_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="scientific authority"):
        repair_delivery(workflow, reassess=True)
    state_path.write_text(json.dumps({**original, "status": "running"}))
    with pytest.raises(ValueError, match="closed Cycle"):
        repair_delivery(workflow, cycle=1)
    assert backend.goal_calls == 1


def test_repaired_revision_can_pass_and_sealed_goal_refuses_repair(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    report = _report(spec, cycle)
    report.write_text(report.read_text() + "\nCompleted delivery index.\n")
    backend.pass_goal = True
    result = repair_delivery(workflow, reassess=True)
    assert result["assessment"]["verdict"] == "passed"
    assert backend.goal_calls == 2
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert [row["verdict"] for row in store.goal_gate_history(cycle.name)] == ["not_passed", "passed"]
    with pytest.raises(GoalSealedError, match="sealed Goal"):
        repair_delivery(workflow)


def test_cli_default_rebuild_has_no_real_backend(tmp_path):
    spec, _, _, _ = _completed(tmp_path)
    from theory_master.__main__ import main
    with patch("sys.argv", ["sciver", "repair-delivery", "fixture", "--cycle", "1"]), \
         patch("theory_master.__main__.spec_from_task", return_value=spec), \
         patch("theory_master.__main__.CodexBackend", side_effect=AssertionError("unexpected model backend")):
        assert main() == 0


def test_interrupted_assessment_commit_reuses_completed_goal_invocation(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    report = _report(spec, cycle)
    report.write_text(report.read_text() + "\nRepaired delivery navigation.\n")
    with patch.object(ScienceGraphStore, "record_goal_gate_not_passed", side_effect=OSError("interrupted commit")):
        with pytest.raises(OSError, match="interrupted commit"):
            repair_delivery(workflow, reassess=True)
    assert backend.goal_calls == 2
    result = repair_delivery(Workflow(spec, backend), reassess=True)
    assert result["assessment"]["verdict"] == "not_passed"
    assert backend.goal_calls == 2
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert len(store.goal_gate_history(cycle.name)) == 2


def test_hard_interruption_and_runtime_failed_wrapper_preserve_closed_goal_replay(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    report = _report(spec, cycle)
    report.write_text(report.read_text() + "\nRepaired delivery navigation.\n")

    class HardInterruption(BaseException):
        pass

    with patch.object(ScienceGraphStore, "record_goal_gate_not_passed", side_effect=HardInterruption()):
        with pytest.raises(HardInterruption):
            repair_delivery(workflow, reassess=True)
    state_path = cycle / "research_state.json"
    interrupted = json.loads(state_path.read_text())
    assert interrupted["goal_delivery_pending"]["closed_status"] == "over"
    # A global runtime failure wrapper may change execution status, without
    # invalidating the already committed scientific Cycle.
    interrupted.update(status="failed", phase="failed")
    state_path.write_text(json.dumps(interrupted))
    result = repair_delivery(Workflow(spec, backend), reassess=True)
    assert result["assessment"]["verdict"] == "not_passed"
    assert backend.goal_calls == 2
    restored = json.loads(state_path.read_text())
    assert restored["status"] == "over"
    assert "goal_delivery_pending" not in restored


def test_material_change_during_goal_call_cannot_seal_stale_revision(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    report = _report(spec, cycle)
    report.write_text(report.read_text() + "\nDelivery correction before assessment.\n")
    original_invoke = backend.invoke

    def mutate_during_goal(role, prompt, thread_id, model, provider):
        if role == "judge" and "Current task: goal_completion " in prompt:
            report.write_text(report.read_text() + "\nChanged during assessment.\n")
        return original_invoke(role, prompt, thread_id, model, provider)

    backend.pass_goal = True
    backend.invoke = mutate_during_goal
    with pytest.raises(ValueError, match="delivery materials changed"):
        repair_delivery(workflow, reassess=True)
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert store.goal_terminal() is None
        assert [row["verdict"] for row in store.goal_gate_history(cycle.name)] == ["not_passed"]
    receipt = json.loads((cycle / "goal_completion_out.json").read_text())
    assert receipt["verdict"] == "not_passed"


def test_missing_mutable_pointer_cannot_bypass_science_preflight(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    (cycle / "delivery_revision.json").unlink()
    path = cycle / "research_state.json"
    state = json.loads(path.read_text())
    state["hypothesis_lock"]["scope"] = "changed scientific scope"
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="scientific authority"):
        repair_delivery(workflow, reassess=True)
    assert backend.goal_calls == 1


def test_default_selects_latest_supported_closed_cycle(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path, max_cycles=2)
    assert cycle.name == "2"
    result = repair_delivery(workflow)
    assert result["cycle_id"] == "2"
    assert backend.goal_calls == 2
