"""Frozen source navigation is local, deterministic and leaves science untouched."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.preparation_sources import preparation_source_anchors
from theory_master.contracts import role_contract
from theory_master.prompt_stages import build_initial_prompt, build_continuation_prompt, stage_context
from theory_master.workflow_state import WorkflowStateMixin


def _snapshot(task: Path) -> Path:
    snapshot = task / "preparation_snapshot"
    material = snapshot / "materials" / "anderson_fig3" / "summary.csv"
    material.parent.mkdir(parents=True)
    material.write_text("width,value\n4,0.283\n", encoding="utf-8")
    plan = snapshot / "research_plan.md"
    plan.write_text("# Required Context\n\n- `anderson_fig3/summary.csv`\n", encoding="utf-8")
    files = [material, plan]
    (snapshot / "manifest.json").write_text(json.dumps({
        "schema": "sciver/preparation-snapshot/v1",
        "files": [{"path": path.relative_to(snapshot).as_posix(),
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                   "bytes": path.stat().st_size} for path in files],
    }), encoding="utf-8")
    return snapshot


def test_snapshot_navigation_maps_original_relative_path_without_changing_plan(tmp_path):
    task = tmp_path / "task"
    snapshot = _snapshot(task)
    plan = task / "research_plan.md"
    plan.write_text((snapshot / "research_plan.md").read_text(), encoding="utf-8")
    before = {path: path.read_bytes() for path in task.rglob("*") if path.is_file()}

    anchors = preparation_source_anchors(task)

    assert anchors == {
        "preparation_snapshot_root": str(snapshot),
        "preparation_materials_root": str(snapshot / "materials"),
        "preparation_manifest_path": str(snapshot / "manifest.json"),
    }
    resolved = Path(anchors["preparation_materials_root"]) / "anderson_fig3/summary.csv"
    assert resolved.is_file()
    assert not (task / "anderson_fig3/summary.csv").exists()
    assert {path: path.read_bytes() for path in task.rglob("*") if path.is_file()} == before


def test_workflow_anchors_reach_existing_workspace_roles_only(tmp_path):
    task = tmp_path / "task"
    snapshot = _snapshot(task)
    owner = SimpleNamespace(spec=SimpleNamespace(
        branch_canvas=task / "exploration.canvas", task_home=task,
        document=task / "research_plan.md", research_root=tmp_path / "research",
        benchmark_root=None, legacy_research_root=None,
    ), cycle_dir=None, cycle_workspace=None, notes_dir=None)
    anchors = WorkflowStateMixin._path_anchors(owner)
    graph = {"schema": "theorymaster/test-agent-graph/v1"}
    context = {**anchors, "agent_graph": graph, "hypothesis_lock": {"hypothesis": "H"}}
    for role, stage in (("research_lead", "frontier_proposal"),
                        ("challenger", "candidate_review"),
                        ("investigator", "investigate")):
        for continuation in (False, True):
            workspace = stage_context(role, stage, context, continuation=continuation)["workspace_access"]
            assert workspace["preparation_materials_root"] == str(snapshot / "materials")
            assert workspace["preparation_manifest_path"] == str(snapshot / "manifest.json")
    # Judge's formal evidence and Literature's assigned retrieval boundaries stay intact.
    for role, stage in (("judge", "terminal_assessment"), ("literature", "targeted_source")):
        assert "preparation_" not in json.dumps(stage_context(role, stage, context))


def test_current_required_context_remains_accessible_after_sources_are_added(tmp_path):
    task = tmp_path / "task"
    snapshot = _snapshot(task)
    plan = tmp_path / "notebook" / "research_plan.md"
    plan.parent.mkdir()
    original = (snapshot / "research_plan.md").read_text()
    plan.write_text(original, encoding="utf-8")
    source = plan.parent / "sources" / "original-paper.txt"
    source.parent.mkdir()
    source.write_text("Original primary source.", encoding="utf-8")
    plan.write_text(original + f"\n- {source}\n", encoding="utf-8")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    owner = SimpleNamespace(spec=SimpleNamespace(
        branch_canvas=task / "exploration.canvas", task_home=task,
        document=plan, research_root=tmp_path / "research",
        benchmark_root=None, legacy_research_root=None,
    ), cycle_dir=None, cycle_workspace=None, notes_dir=None)
    context = {**WorkflowStateMixin._path_anchors(owner),
               "agent_graph": {"schema": "theorymaster/test-agent-graph/v1"},
               "required_context": "large context " * 20000}
    for role, stage in (("research_lead", "frontier_proposal"),
                        ("challenger", "candidate_review"),
                        ("investigator", "investigate")):
        for continuation in (False, True):
            projected = stage_context(role, stage, context, continuation=continuation)
            access = projected["workspace_access"]["required_context_access"]
            assert Path(access["path"]) == plan
            assert access["section"] == "Required Context"
            assert str(source) in Path(access["path"]).read_text()
            assert str(source) not in (snapshot / "research_plan.md").read_text()
            assert len(json.dumps(projected)) < 5000
    for role, stage in (("judge", "terminal_assessment"), ("literature", "targeted_source")):
        assert "required_context_access" not in json.dumps(stage_context(role, stage, context))
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_required_context_revision_exposes_changes_at_the_same_plan_path(tmp_path):
    owner = SimpleNamespace(spec=SimpleNamespace(
        branch_canvas=tmp_path / "exploration.canvas", task_home=tmp_path,
        document=tmp_path / "research_plan.md", research_root=tmp_path / "research",
        benchmark_root=None, legacy_research_root=None, required_context="abc",
    ), cycle_dir=None, cycle_workspace=None, notes_dir=None)
    graph = {"schema": "theorymaster/test-agent-graph/v1"}

    def projected():
        context = {**WorkflowStateMixin._path_anchors(owner), "agent_graph": graph,
                   "required_context": owner.spec.required_context}
        return stage_context("research_lead", "frontier_proposal", context,
                             continuation=True)["workspace_access"]["required_context_access"]

    first = projected()
    assert first["content_sha256"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    owner.spec.required_context = "new operator-provided source"
    second = projected()
    assert second["path"] == first["path"]
    assert second["content_sha256"] != first["content_sha256"]
    assert not owner.spec.document.exists()


@pytest.mark.parametrize(("role", "stage", "timing"), [
    ("research_lead", "frontier_proposal", "Before proposing a candidate or reporting no admissible frontier"),
    ("challenger", "candidate_review", "Before issuing candidate qualification"),
])
def test_qualification_prompts_require_context_before_the_decision(tmp_path, role, stage, timing):
    context = {"research_plan_path": str(tmp_path / "research_plan.md"),
               "required_context": "Task-local comparison limit; alternative designs remain admissible.",
               "agent_graph": {"schema": "theorymaster/test-agent-graph/v1"}}
    contract = role_contract(role, phase=stage)
    for continuing in (False, True):
        projected = stage_context(role, stage, context, continuation=continuing)
        kwargs = dict(role=role, stage=stage, contract_ref=contract.ref,
                      contract_schema=json.dumps(contract.prompt_schema), context=projected)
        prompt = (build_continuation_prompt(**kwargs) if continuing else
                  build_initial_prompt(prefix="Offline", write_rule="JSON only.", **kwargs))
        assert timing in prompt
        assert "matching revision already read and still available in this thread" in prompt
        assert "current Stage and scientific authority boundaries" in prompt
        if role == "research_lead":
            assert "preserve honest empty-frontier exits" in prompt
        else:
            assert "comparator, inputs and design actually match" in prompt
            assert "Retain admissible alternative designs" in prompt


def test_context_timing_change_preserves_nonqualification_stage_boundaries(tmp_path):
    context = {"research_plan_path": str(tmp_path / "research_plan.md"),
               "required_context": "operator input", "agent_graph": {"schema": "theorymaster/test-agent-graph/v1"}}
    for role, stage in (("challenger", "repair_verification"), ("investigator", "investigate")):
        projected = stage_context(role, stage, context, continuation=True)
        rule = projected["workspace_access"]["required_context_access"]["rule"]
        assert "before reporting required material unavailable" in rule
        assert "Before issuing candidate qualification" not in rule
        assert "Before proposing a candidate" not in rule
    for role, stage in (("judge", "terminal_assessment"), ("literature", "targeted_source")):
        assert "required_context_access" not in json.dumps(stage_context(role, stage, context))


@pytest.mark.parametrize("name", ["../other/secret.txt", "/other/secret.txt",
                                  "materials/.hidden/secret.txt", "materials/../secret.txt",
                                  "materials/./secret.txt", "materials\\secret.txt"])
def test_unsafe_manifest_paths_do_not_grant_navigation(tmp_path, name):
    snapshot = _snapshot(tmp_path / "task")
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["path"] = name
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert preparation_source_anchors(tmp_path / "task") == {}


@pytest.mark.parametrize("linked", ["snapshot", "manifest", "material", "task"])
def test_symlinks_cannot_import_other_task_sources(tmp_path, linked):
    task = tmp_path / "task"
    snapshot = _snapshot(task)
    other = tmp_path / "other"
    other_snapshot = _snapshot(other)
    if linked == "snapshot":
        moved = task / "original_snapshot"
        snapshot.rename(moved)
        snapshot.symlink_to(other_snapshot, target_is_directory=True)
    elif linked == "manifest":
        manifest = snapshot / "manifest.json"
        manifest.unlink()
        manifest.symlink_to(other_snapshot / "manifest.json")
    elif linked == "material":
        material = snapshot / "materials/anderson_fig3/summary.csv"
        material.unlink()
        material.symlink_to(other_snapshot / "materials/anderson_fig3/summary.csv")
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(task, target_is_directory=True)
        task = alias
    assert preparation_source_anchors(task) == {}


def test_missing_or_invalid_manifest_does_not_discover_sibling_snapshot(tmp_path):
    _snapshot(tmp_path / "other")
    task = tmp_path / "task"
    task.mkdir()
    assert preparation_source_anchors(task) == {}
    snapshot = _snapshot(task)
    manifest = snapshot / "manifest.json"
    manifest.write_text('{"schema": "unknown", "files": []}', encoding="utf-8")
    assert preparation_source_anchors(task) == {}
    manifest.write_bytes(b"x" * (1024 * 1024 + 1))
    assert preparation_source_anchors(task) == {}
