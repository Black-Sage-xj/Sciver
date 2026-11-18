"""Notebook placement and relocation preserve task identity and sole documents."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from theory_master.notebook import graph_database_path
from theory_master.notebook_move import move_notebook
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.specification import TaskSpec
from theory_master.task import TaskBinding, bootstrap
from theory_master.vault_canvas import canvas_bindings, register_canvas_binding

PLAN = "# Goal\n\nUnderstand a claim.\n\n# Research Contract\n\nUse evidence.\n\n# Final Gate\n\nResolve the claim.\n\n# Required Context\n\nLocal notes.\n"


def task(tmp_path):
    vault = tmp_path / "project" / "notebook"
    vault.mkdir(parents=True)
    plan = vault / "research_plan.md"
    plan.write_text(PLAN)
    return bootstrap(plan, allocation_root=tmp_path)


def test_new_notebook_has_a_root_canvas_and_external_graph_database(tmp_path):
    binding = task(tmp_path)
    vault = binding.note_area
    database = graph_database_path(vault)
    assert database.parent == binding.research_root
    assert database.is_file() and not database.is_relative_to(vault)
    assert binding.branch_canvas == vault / "exploration.canvas"
    assert binding.branch_canvas.is_file()
    assert TaskSpec.from_config(binding.config_path).graph_database == database
    assert [Path(b["canvas_path"]) for b in canvas_bindings(database)] == [binding.branch_canvas]


def test_new_task_can_choose_an_empty_notebook_directory(tmp_path):
    source = tmp_path / "incoming" / "research_plan.md"
    source.parent.mkdir()
    source.write_text(PLAN)
    selected = tmp_path / "selected"
    binding = bootstrap(source, allocation_root=tmp_path, vault_root=selected)
    assert binding.note_area == selected
    assert binding.task_home == source.parent
    assert not source.exists()
    assert (selected / "research_plan.md").is_file()
    assert (selected / "exploration.canvas").is_file()


def test_selected_vault_outside_search_root_keeps_a_discoverable_anchor(tmp_path):
    from tm_observer.canvas_md import vault_root
    import hashlib
    search = tmp_path / "project"
    source = search / "incoming" / "research_plan.md"
    source.parent.mkdir(parents=True)
    source.write_text(PLAN)
    selected = tmp_path / "another disk" / "notebook"
    binding = bootstrap(source, allocation_root=search, vault_root=selected)
    ident = hashlib.sha256(str(binding.research_root).encode()).hexdigest()[:16]
    state = {"runs": [{"observer_id": ident, "task_root": str(binding.task_home),
                       "research_root": str(binding.research_root)}]}
    assert binding.task_home.is_relative_to(search)
    assert not selected.is_relative_to(search)
    assert vault_root(search, ident, state) == selected


def test_move_keeps_markdown_bytes_database_identity_and_branch_updates(tmp_path):
    binding = task(tmp_path)
    vault = binding.note_area
    database = graph_database_path(vault)
    note = vault / "notes" / "hypotheses" / "H-0001.md"
    note.parent.mkdir(parents=True)
    note.write_bytes("# 人工编辑\n\n唯一原文。\n".encode())
    personal = vault / "Projects" / "Reading.md"
    personal.parent.mkdir()
    personal.write_text("# Freely organized notes\n")
    (vault / "Reading.md").write_text("# Root note\n")
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", dossier_ref="notes/hypotheses/H-0001.md")
    register_canvas_binding(database, vault_root=vault, branch_root="H-0001")
    target = tmp_path / "chosen notebook"
    before = note.read_bytes()
    outcome = move_notebook(vault, target)
    assert outcome["moved"] is True
    assert (target / note.relative_to(vault)).read_bytes() == before
    assert (target / "Projects" / "Reading.md").read_text() == "# Freely organized notes\n"
    assert (target / "Reading.md").is_file()
    assert not note.exists()
    reloaded = TaskBinding.load(binding.research_root)
    assert reloaded.master_id == binding.master_id
    assert reloaded.research_root == binding.research_root
    assert reloaded.note_area == target
    spec = TaskSpec.from_config(reloaded.config_path)
    assert spec.graph_database == database
    assert spec.document == target / "research_plan.md"
    assert graph_database_path(target) == database
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0002")
    assert not (vault / "exploration.canvas").exists()
    assert (target / "exploration.canvas").is_file()
    assert (target / "branch-H-0001.canvas").is_file()
    assert (target / note.relative_to(vault)).read_bytes() == before


def test_move_rejects_running_tasks_nonempty_destinations_and_nested_data(tmp_path):
    binding = task(tmp_path)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "user.md").write_text("untouched")
    with pytest.raises(ValueError, match="空文件夹"):
        move_notebook(binding.note_area, occupied)
    with pytest.raises(ValueError, match="分开"):
        move_notebook(binding.note_area, binding.research_root / "nested")
    with patch("theory_master.notebook_move.notebook_is_busy", return_value=True):
        with pytest.raises(ValueError, match="暂停"):
            move_notebook(binding.note_area, tmp_path / "moved")
    assert (occupied / "user.md").read_text() == "untouched"


def test_failed_move_restores_files_and_configuration(tmp_path):
    binding = task(tmp_path)
    before = binding.config_path.read_bytes()
    plan_before = (binding.note_area / "research_plan.md").read_bytes()
    old_bindings = canvas_bindings(graph_database_path(binding.note_area))
    target = tmp_path / "failed"
    with patch("theory_master.vault_canvas.replace_canvas_bindings", side_effect=[ValueError("failed binding"), old_bindings]):
        with pytest.raises(ValueError, match="failed binding"):
            move_notebook(binding.note_area, target)
    assert binding.config_path.read_bytes() == before
    assert (binding.note_area / "research_plan.md").read_bytes() == plan_before
    assert not (target / "research_plan.md").exists()


def test_two_tasks_cannot_move_into_the_same_empty_directory(tmp_path):
    first = task(tmp_path / "a")
    second = task(tmp_path / "b")
    target = tmp_path / "shared"
    target.mkdir()
    def attempt(binding):
        try:
            move_notebook(binding.note_area, target)
            return "moved"
        except ValueError:
            return "rejected"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [first, second]))
    assert sorted(results) == ["moved", "rejected"]
    assert (target / "research_plan.md").is_file()
    assert not (target / "notes" / "notes").exists()


def test_move_keeps_publication_records_for_deleted_markdown(tmp_path):
    from theory_master.cycle_outcome_documents import write_outcome_document
    binding = task(tmp_path)
    vault = binding.note_area
    outcome = {"id": "outcome-1", "cycle_id": "1", "hypothesis_id": "H-0001", "verdict": "open", "summary": "Original"}
    published = write_outcome_document(vault / "notes", outcome)
    published.unlink()
    external_ledger = vault / ".theory-master" / "external-publications.json"
    external_ledger.write_text('{"external-1":"notes/external/external-1.md"}')
    target = tmp_path / "relocated"
    move_notebook(vault, target)
    assert (target / "notes/outcomes/1.json").is_file()
    assert (target / ".theory-master/external-publications.json").read_text() == '{"external-1":"notes/external/external-1.md"}'
    with pytest.raises(FileNotFoundError, match="missing"):
        write_outcome_document(target / "notes", outcome)
    assert not (target / "notes/outcomes/1.md").exists()
