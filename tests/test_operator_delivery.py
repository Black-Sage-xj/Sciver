"""Offline author delivery correction, with immutable scientific authority."""
from __future__ import annotations

import json
import copy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from test_delivery_repair import CountingGoalBackend, _report
from theory_master.core import TaskSpec, Workflow
from theory_master.task import bootstrap
from theory_master.delivery_repair import repair_delivery
from theory_master.operator_delivery import REQUEST_SCHEMA, assessment_authority_digest
from theory_master.operator_delivery import retain_frontier_projection_times
from theory_master.runtime_manager import _file_lock, owner_lock_path
from theory_master.science_graph_store import ScienceGraphStore


def setup_request(tmp_path):
    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal\n\nBounded delivery fixture.\n\n# Research Contract\n\nUse task-local inputs.\n\n"
        "# Final Gate\n\nResult and matching public interface.\n\n# Required Context\n\n- benchmark/inputs/interface.py\n")
    binding = bootstrap(plan)
    config = json.loads(binding.config_path.read_text())
    config.update(max_cycles=1)
    binding.config_path.write_text(json.dumps(config))
    requirement = Path(config["benchmark_root"]) / "inputs" / "interface.py"
    requirement.parent.mkdir(parents=True, exist_ok=True)
    requirement.write_text("def reconstruct(hits, layer_z, config):\n    raise NotImplementedError\n")
    spec = TaskSpec.from_config(binding.config_path)
    backend = CountingGoalBackend()
    workflow = Workflow(spec, backend)
    state = workflow.run()
    assert state["goal_completion"]["verdict"] == "not_passed"
    cycle = spec.research_root / "1"
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assessment = store.goal_gate_history(cycle.name)[-1]
    request = {"schema": REQUEST_SCHEMA, "cycle_id": cycle.name,
        "assessment_id": assessment["assessment_id"],
        "required_material_ref": "benchmark/inputs/interface.py",
        "instructions": "Package retained method and fixed parameters in the public interface; perform only deterministic technical checks."}
    path = Path(spec.research_root) / ".theory-master" / "operator-delivery-request.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(request))
    calls = []
    original = backend.invoke

    def invoke(role, prompt, thread_id, model, provider):
        if role == "investigator" and "operator_delivery_structure_only" in prompt:
            context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
            calls.append({"thread": thread_id, "context": context, "prompt": prompt})
            (cycle / "workspace" / "interface_export.py").write_text(
                "def reconstruct(hits, layer_z, config):\n    return hits\n")
            if getattr(backend, "author_mutate", None):
                backend.author_mutate()
            backend.pass_goal = True
        return original(role, prompt, thread_id, model, provider)
    backend.invoke = invoke
    return spec, workflow, backend, cycle, path, calls


def test_original_author_supplements_delivery_then_appends_goal_assessment(tmp_path):
    spec, workflow, backend, cycle, request, calls = setup_request(tmp_path)
    original = json.loads((cycle / "research_state.json").read_text())
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        original_assessment = store.goal_gate_history(cycle.name)[-1]
    science = assessment_authority_digest(spec, spec.graph_database, cycle, original_assessment)
    evidence = (cycle / "evidence" / "registry.jsonl").read_bytes()
    old_receipt_path = Path(original_assessment["receipt_path"])
    old_receipt = old_receipt_path.read_bytes()
    result = repair_delivery(workflow, cycle=1, author_request=request)
    assert result["reassessed"] is False
    assert len(calls) == 1
    assert calls[0]["thread"] == original["threads"]["investigator"]
    assert "supplemental public-interface deliverable" in calls[0]["prompt"]
    assert (cycle / "evidence" / "registry.jsonl").read_bytes() == evidence
    assert assessment_authority_digest(spec, spec.graph_database, cycle, original_assessment) == science
    # The existing Goal seal legitimately closes frontiers; compare scientific
    # authority before that normal terminal transaction, not after its effects.
    result = repair_delivery(workflow, cycle=1, reassess=True)
    assert result["assessment"]["verdict"] == "passed"
    assert list(spec.research_root.glob("[0-9]*")) == [cycle]
    state = json.loads((cycle / "research_state.json").read_text())
    assert state["hypothesis_lock"] == original["hypothesis_lock"]
    assert state.get("native_goal") == original.get("native_goal")
    assert state["scientific_verdict"] == "supported"
    assert state["investigator_budget"]["allowance_tokens"] == original["investigator_budget"]["allowance_tokens"]
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        history = store.goal_gate_history(cycle.name)
    assert [row["verdict"] for row in history] == ["not_passed", "passed"]
    assert history[-1]["supersedes"] == history[0]["assessment_id"]
    assert old_receipt_path.read_bytes() == old_receipt
    assert str(old_receipt_path) == history[0]["receipt_path"]
    access = backend.goal_context["delivery_material_access"]
    index = json.loads(Path(access["index_path"]).read_text())
    additions = index["operator_delivery_supplements"]
    assert any(asset.get("relative_path") == "interface_export.py" for row in additions for asset in row.get("assets", []))


def test_only_index_repair_does_not_call_author(tmp_path):
    spec, workflow, backend, cycle, request, calls = setup_request(tmp_path)
    repair_delivery(workflow, cycle=1)
    assert not calls and backend.goal_calls == 1


@pytest.mark.parametrize("change", ["wrong_assessment", "workspace_request", "active_task", "held_owner", "changed_plan",
                                    "dotdot_request", "symlink_parent", "symlink_file"])
def test_invalid_or_concurrent_author_request_fails_without_model_call(tmp_path, change):
    spec, workflow, backend, cycle, request, calls = setup_request(tmp_path)
    if change == "wrong_assessment":
        value = json.loads(request.read_text()); value["assessment_id"] = "another-assessment"
        request.write_text(json.dumps(value))
    if change == "workspace_request":
        moved = cycle / "workspace" / "operator-request.json"
        moved.write_bytes(request.read_bytes()); request = moved
    if change in {"dotdot_request", "symlink_parent"}:
        moved = cycle / "workspace" / "operator-request.json"
        moved.write_bytes(request.read_bytes())
        if change == "dotdot_request":
            request = spec.research_root / ".theory-master" / ".." / cycle.name / "workspace" / moved.name
        else:
            link = request.parent / "operator-directory"
            link.symlink_to(moved.parent, target_is_directory=True)
            request = link / moved.name
    if change == "symlink_file":
        link = request.parent / "request-alias.json"
        link.symlink_to(request)
        request = link
    if change == "changed_plan":
        Path(spec.document).write_text(Path(spec.document).read_text() + "\nChanged scientific condition.\n")
    if change == "held_owner":
        with _file_lock(owner_lock_path(spec.research_root), nonblocking=True):
            with pytest.raises(BlockingIOError):
                repair_delivery(workflow, cycle=1, author_request=request)
    elif change == "active_task":
        with patch("theory_master.runtime_manager.runtime_health", return_value={"active": True}):
            with pytest.raises(ValueError, match="idle Task"):
                repair_delivery(workflow, cycle=1, author_request=request)
    else:
        with pytest.raises(ValueError):
            repair_delivery(workflow, cycle=1, author_request=request)
    assert not calls and backend.goal_calls == 1


def test_later_empty_cycles_are_bound_but_real_science_is_not_ignored(tmp_path):
    spec, workflow, backend, cycle, request, calls = setup_request(tmp_path)
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assessment = store.goal_gate_history(cycle.name)[-1]
    later = spec.research_root / "2"
    later.mkdir(); (later / ".theory-master-cycle").write_text("{}")
    state = empty_qualification_state()
    (later / "research_state.json").write_text(json.dumps(state))
    assert assessment_authority_digest(spec, spec.graph_database, cycle, assessment) == assessment["science_digest"]
    state["hypothesis_lock"] = {"status": "locked", "id": "another-H"}
    (later / "research_state.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="scientific authority"):
        repair_delivery(workflow, cycle=1, author_request=request)
    assert not calls


def test_repeated_request_reuses_completed_author_turn(tmp_path):
    spec, workflow, backend, cycle, request, calls = setup_request(tmp_path)
    first = repair_delivery(workflow, cycle=1, author_request=request)
    second = repair_delivery(workflow, cycle=1, author_request=request)
    assert len(calls) == 1 and backend.goal_calls == 1
    assert first["delivery_revision"] == second["delivery_revision"]


@pytest.mark.parametrize("guard", ["exhausted_budget", "zero_reworks"])
def test_original_budget_and_rework_limits_block_author_and_goal(tmp_path, guard):
    spec, workflow, backend, cycle, request, calls = setup_request(tmp_path)
    from theory_master.workflow_support import RecoveryStop
    if guard == "zero_reworks":
        workflow.spec = replace(spec, max_judge_reworks_per_cycle=0)
        with pytest.raises(RecoveryStop, match="exhausted"):
            repair_delivery(workflow, cycle=1, author_request=request)
    else:
        with patch("theory_master.investigator_budget.budget_status", return_value={"exhausted": True}):
            with pytest.raises(RecoveryStop, match="token budget"):
                repair_delivery(workflow, cycle=1, author_request=request)
    assert not calls and backend.goal_calls == 1
    with pytest.raises(ValueError, match="closed Cycle|pending"):
        repair_delivery(workflow, cycle=1, reassess=True)
    assert backend.goal_calls == 1


def test_original_source_drift_during_author_cannot_reach_goal(tmp_path):
    spec, workflow, backend, cycle, request, calls = setup_request(tmp_path)
    report = _report(spec, cycle)
    backend.author_mutate = lambda: report.write_text("Changed original scientific report")
    from theory_master.workflow_support import RecoveryStop
    with pytest.raises(RecoveryStop, match="original frozen scientific file changed"):
        repair_delivery(workflow, cycle=1, author_request=request, reassess=True)
    assert len(calls) == 1 and backend.goal_calls == 1


def test_changed_delivery_supplement_cannot_be_reassessed(tmp_path):
    spec, workflow, backend, cycle, request, calls = setup_request(tmp_path)
    repair_delivery(workflow, cycle=1, author_request=request)
    (cycle / "workspace" / "interface_export.py").write_text("changed delivery identity")
    with pytest.raises(ValueError, match="supplement is missing or changed"):
        repair_delivery(workflow, cycle=1, reassess=True)
    assert backend.goal_calls == 1


def test_normal_goal_may_remain_not_passed_after_interface_correction(tmp_path):
    spec, workflow, backend, cycle, request, calls = setup_request(tmp_path)
    repair_delivery(workflow, cycle=1, author_request=request)
    backend.pass_goal = False
    result = repair_delivery(workflow, cycle=1, reassess=True)
    assert result["assessment"]["verdict"] == "not_passed"
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert store.goal_terminal() is None
        assert [row["verdict"] for row in store.goal_gate_history(cycle.name)] == ["not_passed", "not_passed"]


def test_frontier_timestamp_compatibility_preserves_every_scientific_field():
    baseline = {"nodes": [{"id": "H-1", "frontiers": [{"status": "active", "reason": "one unresolved question",
                 "changed_at": "before"}]}]}
    current = copy.deepcopy(baseline)
    current["nodes"][0]["frontiers"][0]["changed_at"] = "projection-refresh"
    retain_frontier_projection_times(current, baseline)
    assert current == baseline
    current["nodes"][0]["frontiers"][0].update(reason="different scientific opportunity", changed_at="new")
    retain_frontier_projection_times(current, baseline)
    assert current["nodes"][0]["frontiers"][0]["changed_at"] == "new"
    assert current != baseline


def empty_qualification_state():
    return {"status": "no_qualified_candidate", "phase": "qualification",
        "qualification_status": "no_qualified_candidate", "scientific_phase": "qualification",
        "hypothesis_lock": {"status": "unlocked"}, "scientific_verdict": None,
        "protocol_health": "valid", "execution_active": False,
        "attention": {"code": "no_qualified_candidate"}, "frontier_review": {"status": "unqualified"},
        "qualification_attempts": [{"candidate_ids": []}]}


@pytest.mark.parametrize("guard", ["active", "protocol", "resource", "operator_stop", "evidence"])
def test_later_qualification_retains_independent_stop_guards(tmp_path, guard):
    spec, workflow, backend, cycle, request, calls = setup_request(tmp_path)
    later = spec.research_root / "2"; later.mkdir()
    (later / ".theory-master-cycle").write_text("{}")
    state = empty_qualification_state()
    if guard == "active": state["execution_active"] = True
    if guard == "protocol": state["protocol_health"] = "violation"
    if guard == "resource": state["cycle_budget"] = {"exhausted": True}
    if guard == "operator_stop": state["recovery"] = {"action": "pause"}
    if guard == "evidence":
        (later / "evidence").mkdir()
        (later / "evidence" / "registry.jsonl").write_text('{"evidence_id":"existing-evidence"}\n')
    (later / "research_state.json").write_text(json.dumps(state))
    with pytest.raises(ValueError):
        repair_delivery(workflow, cycle=1, author_request=request)
    assert not calls and backend.goal_calls == 1


def test_author_and_reassessment_may_run_as_one_explicit_task_repair(tmp_path):
    spec, workflow, backend, cycle, request, calls = setup_request(tmp_path)
    result = repair_delivery(workflow, cycle=1, author_request=request, reassess=True)
    assert result["assessment"]["verdict"] == "passed"
    assert result["author_corrected"] is True and len(calls) == 1
