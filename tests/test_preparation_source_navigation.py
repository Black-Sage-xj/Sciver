"""Frozen source navigation is local, deterministic and leaves science untouched."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.preparation_sources import preparation_source_anchors
from theory_master.prompt_stages import stage_context
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
