"""Research Chat follows relocated notebooks and canonical graph records."""
from __future__ import annotations

import hashlib
import json

import pytest

from theory_master.science_graph_store import ScienceGraphStore
from theory_master.task import write_binding
from theory_master.vault_canvas import refresh_registered_canvases, register_canvas_binding
from tm_observer.research_chat import (
    ResearchChatError, _append_focus, _canvas_path, _load_canvas, _tips, create, send,
)
from tm_observer.research_chat_links import document_destination


@pytest.fixture
def moved_notebook(tmp_path):
    anchor = tmp_path / "stable-task"
    research = tmp_path / "data" / "research"
    vault = tmp_path / "chosen-library"
    for path in (anchor, research, vault):
        path.mkdir(parents=True)
    (vault / "notes" / "hypotheses").mkdir(parents=True)
    (vault / "notes" / "outcomes").mkdir()
    (vault / "research_plan.md").write_text("# Goal\n\nPlan in the relocated notebook.")
    (vault / "notes" / "hypotheses" / "H-0001.md").write_text("# Hypothesis\nEditable source.")
    (vault / "notes" / "outcomes" / "1.md").write_text("# User report\nHuman report revision.")
    database = research / "science_graph.sqlite3"
    config = anchor / "theory-master.config.json"
    config.write_text(json.dumps({
        "schema": "theory-master/system/v1", "path_model": "theory-master/task-path/v2",
        "task_id": 1, "goal_id": "example", "task_home": str(anchor),
        "vault_root": str(vault), "research_root": str(research),
        "benchmark_root": str(tmp_path / "data" / "benchmark"),
        "graph_database": str(database), "plan_path": str(vault / "research_plan.md"),
        "branch_canvas": str(vault / "exploration.canvas"), "notes_dir": str(vault / "notes"),
    }))
    for path in (anchor, research, vault):
        write_binding(path, config)
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Canonical hypothesis")
        store.append_outcome(outcome_id="result-1", hypothesis_id="H-0001", cycle_id="1",
                             verdict="supported", title="Canonical result", summary="A bounded result",
                             report_ref="notes/outcomes/1.md", record_only=True)
    register_canvas_binding(database, vault_root=vault)
    refresh_registered_canvases(database)
    ident = hashlib.sha256(str(research).encode()).hexdigest()[:16]
    task = {"observer_id": ident, "task_name": "Example", "task_root": str(anchor),
            "research_root": str(research)}
    return tmp_path, vault, database, task


@pytest.mark.parametrize("anchor_is_runtime", [False, True])
def test_chat_reads_canonical_graph_after_notebook_move(moved_notebook, anchor_is_runtime):
    _, vault, database, task = moved_notebook
    if anchor_is_runtime:
        task = {**task, "task_root": str(database.parent)}
    assert _canvas_path(task) == vault / "exploration.canvas"
    raw = json.loads((vault / "exploration.canvas").read_text())
    assert next(node for node in raw["nodes"] if node["id"] == "H-0001")["type"] == "file"
    # Local Canvas changes are one-way presentation changes and cannot invent a frontier.
    (vault / "exploration.canvas").write_text(json.dumps({"nodes": [{"id": "H-9999",
        "type": "text", "text": "type: Hypothesis\nid: H-9999\nfrontier_status: active"}], "edges": []}))
    _, graph = _load_canvas(task)
    assert [tip["id"] for tip in _tips(graph)] == ["H-0001"]


def test_chat_focus_persists_in_graph_and_survives_canvas_refresh(moved_notebook):
    _, vault, database, task = moved_notebook
    with ScienceGraphStore(database, readonly=True) as store:
        before = dict(store.db.execute("SELECT * FROM frontier_branches WHERE hypothesis_id='H-0001'").fetchone())
    ident = _append_focus(task, "Check another measurement", "H-0001", True, "session", "a" * 32)
    with ScienceGraphStore(database, readonly=True) as store:
        external = dict(store.db.execute("SELECT * FROM external_records WHERE id=?", (ident,)).fetchone())
        frontier = dict(store.db.execute("SELECT * FROM frontier_branches WHERE hypothesis_id='H-0001'").fetchone())
        edge = store.db.execute("SELECT hypothesis_id FROM external_edges WHERE external_id=?", (ident,)).fetchone()
        hypothesis = store.db.execute("SELECT scientific_status FROM hypotheses WHERE id='H-0001'").fetchone()
    assert external["content_type"] == "ResearchFocus" and external["source"] == "human"
    assert "Check another measurement" in external["content"] and edge["hypothesis_id"] == "H-0001"
    assert frontier["priority"] == "preferred"
    assert (frontier["status"], frontier["anchor_outcome"], frontier["reason"]) == (before["status"], before["anchor_outcome"], before["reason"])
    assert hypothesis["scientific_status"] == "none"
    document = vault / "notes" / "external" / (ident + ".md")
    assert document.is_file()
    document.write_text("# Human edited focus\nKeep this edit.")
    assert _append_focus(task, "Check another measurement", "H-0001", True, "session", "a" * 32) == ident
    refresh_registered_canvases(database)
    generated = json.loads((vault / "exploration.canvas").read_text())
    assert any(node["id"] == ident for node in generated["nodes"])
    assert document.read_text() == "# Human edited focus\nKeep this edit."
    with ScienceGraphStore(database, readonly=True) as store:
        assert store.db.execute("SELECT COUNT(*) FROM external_records WHERE id=?", (ident,)).fetchone()[0] == 1


def test_stale_focus_selection_cannot_write_a_scientific_record(moved_notebook):
    _, _, database, task = moved_notebook
    with pytest.raises(ResearchChatError):
        _append_focus(task, "Invalid target", "H-9999", False, "session", "b" * 32)
    with ScienceGraphStore(database, readonly=True) as store:
        assert store.db.execute("SELECT COUNT(*) FROM external_records").fetchone()[0] == 0


def test_chat_report_links_follow_independent_graph_database(moved_notebook):
    search, vault, _, task = moved_notebook
    target = document_destination(search, {"runs": [task]}, task["observer_id"], str(vault / "notes/outcomes/1.md"))
    assert target.endswith("#report%3A1")


def test_fake_chat_context_reads_relocated_plan(moved_notebook, monkeypatch):
    search, _, _, task = moved_notebook
    state = {"runs": [task]}

    class FakeBackend:
        prompt = ""

        def invoke(self, role, prompt, thread, model, provider):
            self.prompt = prompt
            return "fake-thread", "Read the current notebook."

        def drop_thread(self, thread):
            pass

    backend = FakeBackend()
    # The separately developed chat-card skill loader is outside path resolution.
    monkeypatch.setattr("tm_observer.research_chat._skill_context", lambda row: "", raising=False)
    state_dir = search / "observer"
    ident = create(state_dir, state, {"mode": "project", "task_id": task["observer_id"]})["session_id"]
    send(state_dir, search, state, ident, {"content": "Inspect the current task"}, backend=backend)
    assert "Plan in the relocated notebook" in backend.prompt
    assert "Canonical hypothesis" in backend.prompt
