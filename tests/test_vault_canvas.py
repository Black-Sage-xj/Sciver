"""Offline coverage of one-way vault views and fixed user Markdown."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from theory_master.science_canvas_export import write_external_detail_documents
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.vault_canvas import (
    canvas_bindings, canvas_status, refresh_canvas, refresh_registered_canvases,
    register_canvas_binding, replace_canvas_bindings,
)


def _note(root: Path, name: str, body: str = "# User document\n") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    database = tmp_path / "data" / "science_graph.sqlite3"
    vault = tmp_path / "vault"
    _note(vault, "notes/hypotheses/H-0001.md")
    with ScienceGraphStore(database, auto_refresh_canvas=False) as store:
        store.add_hypothesis("H-0001", title="First", dossier_ref="notes/hypotheses/H-0001")
    register_canvas_binding(database, vault_root=vault)
    refresh_registered_canvases(database)
    return database, vault


def _canvas(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_canvas_refresh_uses_existing_relative_files_and_preserves_user_markdown(tmp_path):
    database, vault = _seed(tmp_path)
    note = vault / "notes/hypotheses/H-0001.md"
    note.write_text("# Human replacement\nNo SDK marker.\n", encoding="utf-8")
    _note(vault, "notes/outcomes/1.md", "# Human report\n")
    before = {path: path.read_bytes() for path in vault.rglob("*.md")}
    with ScienceGraphStore(database) as store:
        store.append_outcome(outcome_id="outcome-1", hypothesis_id="H-0001", cycle_id="1",
                             verdict="open", report_ref="notes/outcomes/1.md")
    result = refresh_canvas(database, vault_root=vault)
    canvas = _canvas(vault / "exploration.canvas")
    assert result["status"] == "ready"
    assert all(node["type"] == "file" for node in canvas["nodes"])
    assert {node["file"] for node in canvas["nodes"]} == {
        "notes/hypotheses/H-0001.md", "notes/outcomes/1.md"}
    assert all(not Path(node["file"]).is_absolute() for node in canvas["nodes"])
    assert any(edge["label"] == "Cycle 1 · 研究报告" for edge in canvas["edges"])
    assert {path: path.read_bytes() for path in vault.rglob("*.md")} == before
    # A second refresh at the same revision does not touch Canvas or Markdown.
    previous_mtime = (vault / "exploration.canvas").stat().st_mtime_ns
    assert refresh_canvas(database)["changed"] is False
    assert (vault / "exploration.canvas").stat().st_mtime_ns == previous_mtime


def test_graph_commits_automatically_update_full_and_registered_branch(tmp_path):
    database, vault = _seed(tmp_path)
    register_canvas_binding(database, branch_root="H-0001")
    for hid in ("H-0002", "H-0003"):
        _note(vault, f"notes/hypotheses/{hid}.md")
    with ScienceGraphStore(database) as store:
        with store.transaction():
            store.add_hypothesis("H-0002", title="Child")
            store.add_evolution(edge_id="refinement", source="H-0001", target="H-0002",
                                operator="refine", lineage_role="main")
            store.add_hypothesis("H-0003", title="Other root")
    full = _canvas(vault / "exploration.canvas")
    branch = _canvas(vault / "branch-H-0001.canvas")
    assert {row["id"] for row in full["nodes"]} == {"H-0001", "H-0002", "H-0003"}
    assert {row["id"] for row in branch["nodes"]} == {"H-0001", "H-0002"}
    assert branch["edges"][0]["label"] == "refine · 主要来源"
    assert full["tm_science_graph_projection"]["revision"] == branch["tm_science_graph_projection"]["revision"]
    assert not (database.parent / "exploration.canvas").exists()


def test_other_lineage_sources_do_not_change_main_branch_membership(tmp_path):
    database, vault = _seed(tmp_path)
    register_canvas_binding(database, vault_root=vault, branch_root="H-0001")
    with ScienceGraphStore(database) as store:
        with store.transaction():
            store.add_hypothesis("H-0002")
            store.add_hypothesis("H-0003")
            store.add_evolution(edge_id="main", source="H-0002", target="H-0003", lineage_role="main")
            store.add_evolution(edge_id="other", source="H-0001", target="H-0003", lineage_role="other")
    full = _canvas(vault / "exploration.canvas")
    branch = _canvas(vault / "branch-H-0001.canvas")
    assert {n["id"] for n in branch["nodes"]} == {"H-0001"}
    assert {(e["fromNode"], e["toNode"]) for e in full["edges"]} == {("H-0002", "H-0003")}


def test_rollback_does_not_refresh_and_missing_document_is_not_recreated(tmp_path):
    database, vault = _seed(tmp_path)
    before = (vault / "exploration.canvas").read_bytes()
    with ScienceGraphStore(database) as store:
        with pytest.raises(RuntimeError):
            with store.transaction():
                store.add_hypothesis("H-0002")
                raise RuntimeError("abort")
    assert (vault / "exploration.canvas").read_bytes() == before
    note = vault / "notes/hypotheses/H-0001.md"
    note.unlink()
    result = refresh_canvas(database, vault_root=vault)
    assert result["missing_documents"] == ["notes/hypotheses/H-0001"]
    assert not note.exists()
    assert _canvas(vault / "exploration.canvas")["nodes"][0]["type"] == "text"


def test_refresh_failure_keeps_prior_canvas_and_next_commit_retries(tmp_path, monkeypatch):
    database, vault = _seed(tmp_path)
    canvas = vault / "exploration.canvas"
    prior = canvas.read_bytes()
    from theory_master import vault_canvas
    original = vault_canvas._atomic_json

    def fail_canvas(path, value):
        if path == canvas:
            raise OSError("simulated full disk")
        return original(path, value)

    monkeypatch.setattr(vault_canvas, "_atomic_json", fail_canvas)
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0002", title="Committed despite failed projection")
        assert store.db.execute("SELECT 1 FROM hypotheses WHERE id='H-0002'").fetchone()
    assert canvas.read_bytes() == prior
    assert canvas_status(database)[0]["status"] == "pending"
    assert "simulated full disk" in canvas_status(database)[0]["error"]
    monkeypatch.setattr(vault_canvas, "_atomic_json", original)
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0003")
    assert canvas_status(database)[0]["status"] == "ready"
    assert {node["id"] for node in _canvas(canvas)["nodes"]} == {"H-0001", "H-0002", "H-0003"}


def test_concurrent_graph_writers_cannot_publish_a_stale_snapshot(tmp_path):
    database, vault = _seed(tmp_path)

    def write(index):
        with ScienceGraphStore(database) as store:
            store.add_hypothesis(f"H-{index:04d}")

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(write, range(2, 9)))
    canvas = _canvas(vault / "exploration.canvas")
    assert {node["id"] for node in canvas["nodes"]} == {f"H-{index:04d}" for index in range(1, 9)}
    with ScienceGraphStore(database, readonly=True) as store:
        revision = int(store.db.execute("SELECT value FROM graph_meta WHERE key='graph_revision'").fetchone()[0])
    assert canvas["tm_science_graph_projection"]["revision"] == revision


def test_relocation_replaces_every_destination_and_retains_relative_links(tmp_path):
    database, vault = _seed(tmp_path)
    target = tmp_path / "relocated"
    target.mkdir()
    (vault / "notes").rename(target / "notes")
    replace_canvas_bindings(database, [{"vault_root": str(target), "canvas_path": str(target / "exploration.canvas"),
                                         "branch_root": None, "updated_at": "ignored metadata"}])
    old = (vault / "exploration.canvas").read_bytes()
    with ScienceGraphStore(database) as store:
        store.set_frontier(branch_id="frontier:H-0001", hypothesis_id="H-0001", status="parked", reason="test")
    assert (vault / "exploration.canvas").read_bytes() == old
    assert _canvas(target / "exploration.canvas")["nodes"][0]["file"] == "notes/hypotheses/H-0001.md"
    assert canvas_bindings(database)[0]["vault_root"] == str(target)


def test_canvas_is_root_level_and_cannot_reference_external_markdown(tmp_path):
    database, vault = _seed(tmp_path)
    outside = _note(tmp_path, "outside.md")
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0002", dossier_ref=str(outside))
    assert all(node.get("file") != str(outside) for node in _canvas(vault / "exploration.canvas")["nodes"])
    with pytest.raises(ValueError, match="vault root"):
        refresh_canvas(database, vault_root=vault, canvas_path=vault / "nested" / "graph.canvas")


def test_external_publication_never_rewrites_or_resurrects_user_markdown(tmp_path):
    database, vault = _seed(tmp_path)
    with ScienceGraphStore(database) as store:
        with store.transaction():
            store.db.execute("INSERT INTO external_records VALUES (?,?,?,?,?,?)",
                             ("E-0001", "Note", "user", "Source", "# Published body", "today"))
        assert write_external_detail_documents(store, notes_root=vault / "notes") == 1
        note = vault / "notes/external/E-0001.md"
        note.write_text("# Edited by user\n", encoding="utf-8")
        assert write_external_detail_documents(store, notes_root=vault / "notes") == 0
        assert note.read_text(encoding="utf-8") == "# Edited by user\n"
        note.unlink()
        assert write_external_detail_documents(store, notes_root=vault / "notes") == 0
        assert not note.exists()


def test_unregistered_projection_and_worker_use_explicit_task_data_binding(tmp_path):
    from types import SimpleNamespace
    from theory_master.worker_runtime import _graph_path
    data = tmp_path / "data"
    vault = tmp_path / "vault"
    database = data / "science_graph.sqlite3"
    _note(vault, "notes/hypotheses/H-0001.md")
    config = vault / "theory-master.config.json"
    config.write_text(json.dumps({"vault_root": str(vault), "branch_canvas": str(vault / "exploration.canvas")}),
                      encoding="utf-8")
    marker = data / ".theory-master" / "task.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"config_path": str(config)}), encoding="utf-8")
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001")
    assert (vault / "exploration.canvas").is_file()
    assert not (data / "exploration.canvas").exists()
    assert _graph_path(SimpleNamespace(graph_database=database, branch_canvas=vault / "exploration.canvas")) == database


def test_path_anchors_keep_current_notebook_root_after_relocation(tmp_path):
    from types import SimpleNamespace
    from theory_master.workflow_state import WorkflowStateMixin
    outer = tmp_path / "笔记区"
    (outer / ".obsidian").mkdir(parents=True)
    original = outer / "old notebook"
    original.mkdir()
    current = outer / "current notebook"
    current.mkdir()
    data = tmp_path / "data"
    cycle = data / "1"
    workspace = cycle / "workspace"
    workspace.mkdir(parents=True)
    workflow = SimpleNamespace(
        spec=SimpleNamespace(task_home=original, document=current / "research_plan.md",
                             branch_canvas=current / "exploration.canvas", research_root=data,
                             benchmark_root=None, legacy_research_root=None),
        cycle_dir=cycle, cycle_workspace=workspace, notes_dir=current / "notes")
    anchors = WorkflowStateMixin._path_anchors(workflow)
    assert anchors["notes_vault_root"] == str(current)
    assert anchors["note_area"] == str(current)
    assert anchors["task_home"] == str(original)
    workflow._path_anchors = lambda: anchors
    registry = WorkflowStateMixin._evidence_registry(workflow)
    assert ("task_local", current) in registry.artifact_anchors
