"""SQLite-native frontier policy: explicit H states, typed lineage and scoped Outcomes."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.graph_authority import SQLiteGraphAuthorityMixin
from theory_master.canvas_projection import CanvasProjectionMixin
from theory_master.multi_worker import frontier_snapshot, BranchClaims
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.science_canvas_export import canvas_projection
from theory_master.workflow_qualification import _require_v2_new_h_candidate, _scientific_anchor_types


class _Workflow(SQLiteGraphAuthorityMixin, CanvasProjectionMixin):
    def __init__(self, canvas: Path):
        self.branch_canvas = canvas
        self.spec = SimpleNamespace(branch_canvas=canvas, document=canvas.parent / "research_plan.md", notes_mirror_dir=None)


def _setup(tmp_path: Path):
    db = tmp_path / "science_graph.sqlite3"
    canvas = tmp_path / "exploration.canvas"
    with ScienceGraphStore(db) as store:
        store.add_hypothesis("H-0001", title="Parent", summary="Parent claim")
        store.add_hypothesis("H-0002", title="Child", summary="Child claim")
        store.add_evolution(edge_id="e1", source="H-0001", target="H-0002", operator="derive")
        store.append_outcome(outcome_id="outcome-h1", hypothesis_id="H-0001", cycle_id="1", verdict="open")
        store.append_outcome(outcome_id="outcome-h2", hypothesis_id="H-0002", cycle_id="2", verdict="open")
        store.set_frontiers_batch([
            {"hypothesis_id":"H-0001", "status":"active", "reason":"still scientifically relevant"},
            {"hypothesis_id":"H-0002", "status":"parked", "reason":"await a discriminating test"},
        ])
        canvas.write_text(json.dumps(canvas_projection(store), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "research_plan.md").write_text("# Goal\n", encoding="utf-8")
    return _Workflow(canvas), db, canvas


def test_parent_with_child_remains_explicit_active_frontier(tmp_path):
    _, db, _ = _setup(tmp_path)
    _, frontiers = frontier_snapshot(db)
    assert {row["hypothesis_id"] for row in frontiers} == {"H-0001"}
    with ScienceGraphStore(db, readonly=True) as store:
        assert store.frontier_view()["H-0001"]["frontier_status"] == "active"
        assert store.frontier_view()["H-0002"]["frontier_status"] == "parked"


def test_frontier_transition_does_not_rewrite_scientific_outcome(tmp_path):
    _, db, _ = _setup(tmp_path)
    with ScienceGraphStore(db) as store:
        store.append_outcome(outcome_id="outcome-3", hypothesis_id="H-0001", cycle_id="3", verdict="supported")
        store.set_frontiers_batch([{"hypothesis_id":"H-0001", "status":"parked", "reason":"supported claim may still be a useful origin"}])
    with ScienceGraphStore(db, readonly=True) as store:
        row = store.frontier_view()["H-0001"]
        assert row["frontier_status"] == "parked"
        assert row["outcome"] == "supported"


def test_invalid_frontier_batch_is_atomic(tmp_path):
    _, db, _ = _setup(tmp_path)
    with ScienceGraphStore(db) as store:
        with pytest.raises(ValueError, match="Invalid frontier status"):
            store.set_frontiers_batch([
                {"hypothesis_id":"H-0001", "status":"closed", "reason":"first update"},
                {"hypothesis_id":"H-0002", "status":"forbidden", "reason":"invalid update"},
            ])
    with ScienceGraphStore(db, readonly=True) as store:
        assert store.frontier_view()["H-0001"]["frontier_status"] == "active"


def test_same_h_reuse_requires_explicit_reconsideration_and_revision(tmp_path):
    _, db, _ = _setup(tmp_path)
    with ScienceGraphStore(db) as store:
        with pytest.raises(ValueError, match="reconsideration_trigger"):
            store.assert_eligible_hypothesis("H-0002", same_h=True)
        store.assert_eligible_hypothesis("H-0002", same_h=True, reconsideration_trigger="new independent evidence")
        store.set_frontiers_batch([{"hypothesis_id":"H-0002", "status":"closed", "reason":"branch exhausted"}])
        with pytest.raises(ValueError, match="closed H"):
            store.assert_eligible_hypothesis("H-0002", same_h=True, reconsideration_trigger="new evidence")
        # A closed/rejected H may still be cited as provenance for a genuinely new H.
        store.assert_eligible_hypothesis("H-0002", same_h=False)


def test_new_cycle_requires_explicit_evolution_operator_when_h_exists(tmp_path):
    _setup(tmp_path)
    with pytest.raises(ValueError, match="evolution_operator"):
        _require_v2_new_h_candidate({"hypothesis":"same proposition"}, has_existing_h=True)
    _require_v2_new_h_candidate({"hypothesis":"new scoped proposition", "evolution_operator":"specialize"}, has_existing_h=True)
    _require_v2_new_h_candidate({"hypothesis":"root proposition"}, has_existing_h=False)


def test_cycle_owner_is_unique_and_canvas_is_only_a_projection(tmp_path):
    workflow, db, canvas = _setup(tmp_path)
    with ScienceGraphStore(db) as store:
        store.register_cycle_hypothesis(cycle_id="3", hypothesis_id="H-0002")
        with pytest.raises(Exception):
            store.register_cycle_hypothesis(cycle_id="3", hypothesis_id="H-0001")
    with ScienceGraphStore(db, readonly=True) as store:
        projected = canvas_projection(store)
        assert {node["id"] for node in projected["nodes"]} >= {"H-0001", "H-0002"}
        assert all(node.get("type") != "Decision" for node in projected["nodes"])
        assert _scientific_anchor_types(workflow) == {"H-0001":"Hypothesis", "H-0002":"Hypothesis"}
    # Changing the generated projection cannot change the canonical graph.
    canvas.write_text("invalid projection", encoding="utf-8")
    with ScienceGraphStore(db, readonly=True) as store:
        assert set(store.frontier_view()) == {"H-0001", "H-0002"}


def test_frontier_snapshot_fails_closed_without_sqlite(tmp_path):
    with pytest.raises(FileNotFoundError, match="Canonical SQLite graph"):
        frontier_snapshot(tmp_path / "missing.sqlite3")


def test_branch_claims_derive_eligible_h_from_sqlite_not_canvas(tmp_path):
    _, db, canvas = _setup(tmp_path)
    root = tmp_path / "research"
    root.mkdir()
    claims = BranchClaims(root, db)
    canvas.write_text('{"nodes": [], "edges": []}', encoding="utf-8")
    claim = claims.claim("worker-1", hypothesis_id="H-0001")
    assert claim["hypothesis_id"] == "H-0001"
    assert claim["entry_node_id"] == "H-0001"
