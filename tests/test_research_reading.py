"""SQLite graph details replace the retired Canvas-reading helper."""
from __future__ import annotations

from theory_master.science_graph_store import ScienceGraphStore
from theory_master.science_canvas_export import canvas_projection


def test_sqlite_detail_and_projection_are_separate_read_surfaces(tmp_path):
    db = tmp_path / "science_graph.sqlite3"
    canvas = tmp_path / "exploration.canvas"
    with ScienceGraphStore(db) as store:
        store.add_hypothesis("H-0001", title="Frontier", summary="Authoritative claim")
        store.add_hypothesis("H-0002", title="Child", summary="Derived claim")
        store.add_evolution(edge_id="edge-1", source="H-0001", target="H-0002", operator="derive")
        store.set_frontiers_batch([{"hypothesis_id":"H-0001","status":"active","reason":"current"}])
        projection = canvas_projection(store)
        detail = store.agent_hypothesis_detail("H-0002")
        assert detail["claim"] == "Derived claim"
        assert any(edge[0] == "H-0001" and edge[1] == "H-0002" for edge in detail["lineage"])
    canvas.write_text("invalid projection bytes", encoding="utf-8")
    with ScienceGraphStore(db, readonly=True) as store:
        assert store.agent_hypothesis_detail("H-0002")["claim"] == "Derived claim"
        assert store.frontier_view()["H-0001"]["frontier_status"] == "active"
    assert {node["id"] for node in projection["nodes"]} >= {"H-0001", "H-0002"}
