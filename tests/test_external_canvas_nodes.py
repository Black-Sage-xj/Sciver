"""External references remain outside the SQLite scientific frontier."""
from __future__ import annotations

from theory_master.science_graph_store import ScienceGraphStore


def test_external_reference_edges_do_not_change_sqlite_scientific_frontier(tmp_path):
    db = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(db) as store:
        store.add_hypothesis("H-0005", title="Frontier H", summary="A scientific claim")
        store.set_frontiers_batch([{"hypothesis_id": "H-0005", "status": "active", "reason": "test"}])
        store.db.execute(
            "INSERT INTO external_records(id,content_type,source,title,content,created_at) VALUES (?,?,?,?,?,?)",
            ("E-0001", "StageSummary", "manual-evidence-preservation", "Summary", "summary text", "test"),
        )
        store.db.execute(
            "INSERT INTO external_records(id,content_type,source,title,content,created_at) VALUES (?,?,?,?,?,?)",
            ("E-0002", "Finding", "manual-evidence-preservation", "Finding", "finding text", "test"),
        )
        store.db.execute(
            "INSERT INTO external_edges(id,hypothesis_id,external_id,edge_type) VALUES (?,?,?,?)",
            ("ext-1", "H-0005", "E-0001", "external_reference"),
        )
        store.db.execute(
            "INSERT INTO external_relations(id,source_external,target_external,edge_type) VALUES (?,?,?,?)",
            ("ext-2", "E-0001", "E-0002", "external_reference"),
        )
        assert set(store.frontier_view()) == {"H-0005"}
        view = store.agent_view()
        assert any(row[0] == "H-0005" for row in view["hypotheses"])
        assert "E-0001" not in view.get("frontier", {})
        detail = store.agent_external_detail("E-0001", max_content_chars=100)
        assert detail["id"] == "E-0001"
        assert detail["content"] == "summary text"
        assert detail["hypothesis_references"] and detail["hypothesis_references"][0][0] == "H-0005"
