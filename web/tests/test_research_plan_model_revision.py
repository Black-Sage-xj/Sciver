"""Human Model revisions protect CAS, provenance, history and sealed Goals offline."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from theory_master.goal_terminal import GoalSealedError
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.scientific_model import ScientificModelVersionConflict, commit_authored_model, load, state_path
from theory_master.task import bootstrap
from tm_observer.research_plan_editor import ResearchPlanError, read_plan, save_section


@pytest.fixture
def task(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "runtime"))
    home = tmp_path / "workspace" / "task"
    home.mkdir(parents=True)
    plan = home / "research_plan.md"
    plan.write_text("# Goal\n\nG\n\n# Research Contract\n\nC\n\n# Final Gate\n\nF\n\n"
                    "# Required Context\n\nContext\n\n# Scientific Model\n\nInitial synthesis.\n")
    binding = bootstrap(plan, allocation_root=home.parent)
    run = {"task_root": str(home), "research_root": str(binding.research_root),
           "observer_id": hashlib.sha256(str(binding.research_root).encode()).hexdigest()[:16]}
    return home.parent, run, plan, binding


def edit_args(root, run, text="Human proposed explanation; requires evidence."):
    model = read_plan(root, run)["sections"][-1]
    return {"section": "Scientific Model", "text": "\n\n" + text + "\n",
            "expected_revision": model["revision"], "change_reason": "Separate assumption from evidence"}


def test_human_save_advances_runtime_cas_and_preserves_other_scopes(task):
    root, run, plan, binding = task
    before = read_plan(root, run)
    initial = load(binding.research_root)
    response = save_section(root, run, edit_args(root, run))
    current = load(binding.research_root)
    assert response["scientific_model_version"] == current["version"] == initial["version"] + 1
    assert response["scientific_model_source"] == "human"
    assert current["provenance"]["scientific_status"] == "human_revision_not_evidence"
    assert read_plan(root, run)["sections"][:-1] == before["sections"][:-1]
    with pytest.raises(ScientificModelVersionConflict):
        commit_authored_model(binding.research_root, {"summary": "Obsolete worker synthesis."},
                              cycle_id="1", action="replace", expected_version=initial["version"])
    assert load(binding.research_root) == current
    assert (binding.research_root / "scientific_model_history" / "v1.json").is_file()
    next_model = commit_authored_model(binding.research_root, {"summary": "Later adjudicated synthesis."},
                                       cycle_id="2", action="replace", expected_version=current["version"])
    assert next_model["provenance"]["created_by"] == "research_lead"
    assert "scientific_status" not in next_model["provenance"]
    assert "correction_reason" not in next_model["provenance"]


def test_model_restore_uses_new_cas_version_and_preserves_latest_goal(task):
    root, run, plan, binding = task
    read_plan(root, run)
    save_section(root, run, edit_args(root, run, "Human revised summary."))
    current = read_plan(root, run)
    save_section(root, run, {"section": "Goal", "text": "\nLatest Goal\n\n",
                            "expected_revision": current["sections"][0]["revision"], "change_reason": "Goal clarification"})
    current = read_plan(root, run)
    restored = save_section(root, run, {"operation": "restore", "scope": "model", "version": 1,
                                       "expected_revision": current["scope_revisions"]["model"],
                                       "change_reason": "Restore original scientific picture"})
    assert restored["scientific_model_version"] == 2
    assert load(binding.research_root)["summary"] == "Initial synthesis."
    assert "Latest Goal" in plan.read_text()


@pytest.mark.parametrize("damage", ["missing_metadata", "bad_metadata", "missing_graph", "bad_graph", "missing_goal_id"])
def test_broken_bound_runtime_cannot_fall_back_to_markdown_edit(task, damage):
    root, run, plan, binding = task
    payload = edit_args(root, run)
    model_state = state_path(binding.research_root)
    graph = Path(binding.config["graph_database"])
    if damage == "missing_metadata":
        model_state.unlink()
    elif damage == "bad_metadata":
        model_state.write_text("{invalid")
    elif damage == "missing_graph":
        graph.unlink()
    elif damage == "bad_graph":
        with ScienceGraphStore(graph) as store:
            store.db.execute("UPDATE graph_meta SET value='invalid' WHERE key='schema_version'")
    else:
        config = json.loads(binding.config_path.read_text())
        del config["goal_id"]
        binding.config_path.write_text(json.dumps(config))
    before = plan.read_bytes()
    metadata = model_state.read_bytes() if model_state.exists() else None
    with pytest.raises(ResearchPlanError):
        save_section(root, run, payload)
    assert plan.read_bytes() == before
    assert (model_state.read_bytes() if model_state.exists() else None) == metadata
    if damage == "missing_graph":
        assert not graph.exists()


def test_goal_seal_blocks_plan_and_model_edits(task):
    root, run, plan, binding = task
    response = read_plan(root, run)
    graph = Path(binding.config["graph_database"])
    with ScienceGraphStore(graph) as store:
        with store.transaction():
            store.db.execute("INSERT INTO task_goal_terminal VALUES (?,?,?,?,?,?,?)",
                             (binding.goal_id, "passed", "1", "2026-11-18T00:00:00Z", "0" * 64, "receipt", "{}"))
    before = plan.read_bytes(), state_path(binding.research_root).read_bytes()
    for section in response["sections"]:
        with pytest.raises(GoalSealedError):
            save_section(root, run, {"section": section["name"], "text": "\nChanged\n",
                                    "expected_revision": section["revision"], "change_reason": "must refuse"})
    assert (plan.read_bytes(), state_path(binding.research_root).read_bytes()) == before
    config = json.loads(binding.config_path.read_text())
    del config["goal_id"]
    binding.config_path.write_text(json.dumps(config))
    with pytest.raises(ResearchPlanError):
        save_section(root, run, edit_args(root, run))
    assert (plan.read_bytes(), state_path(binding.research_root).read_bytes()) == before


def test_failed_text_replace_reserves_cas_without_attributing_unapplied_human_revision(task, monkeypatch):
    root, run, plan, binding = task
    payload = edit_args(root, run)
    initial = load(binding.research_root)
    before = plan.read_bytes()
    real_replace = os.replace
    def fail_only_plan(source, target):
        if Path(target) == plan:
            raise OSError("synthetic replacement failure")
        return real_replace(source, target)
    monkeypatch.setattr(os, "replace", fail_only_plan)
    with pytest.raises(ResearchPlanError, match="已预留"):
        save_section(root, run, payload)
    current = load(binding.research_root)
    assert plan.read_bytes() == before
    assert current["version"] == initial["version"] + 1 and current["summary"] == initial["summary"]
    assert current["provenance"]["created_by"] == initial["provenance"]["created_by"]
    assert "correction_reason" not in current["provenance"]
    assert current["provenance"]["human_revision_reservation"]["status"] == "reserved"
    with pytest.raises(ScientificModelVersionConflict):
        commit_authored_model(binding.research_root, {"summary": "Stale synthesis"}, cycle_id="1",
                              action="replace", expected_version=initial["version"])
    monkeypatch.setattr(os, "replace", real_replace)
    response = save_section(root, run, edit_args(root, run))
    assert response["scientific_model_version"] == 2
    assert "human_revision_reservation" not in load(binding.research_root)["provenance"]


def test_load_recovers_attribution_only_after_target_text_was_actually_replaced(task, monkeypatch):
    import theory_master.scientific_model as models

    root, run, plan, binding = task
    real_finalize = models.finalize_human_revision
    def fail(*args):
        raise RuntimeError("crash after authoritative text replacement")
    monkeypatch.setattr(models, "finalize_human_revision", fail)
    with pytest.raises(ResearchPlanError, match="内容已保存.*尚待恢复") as partial:
        save_section(root, run, edit_args(root, run, "Actually saved human picture."))
    assert partial.value.status == 409
    assert "Actually saved human picture." in plan.read_text()
    monkeypatch.setattr(models, "finalize_human_revision", real_finalize)
    recovered = models.load(binding.research_root)
    assert recovered["version"] == 1 and recovered["provenance"]["created_by"] == "human"
    assert "human_revision_reservation" not in recovered["provenance"]
