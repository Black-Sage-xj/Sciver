"""End-to-end cutover rehearsal: retain legacy nodes and rewrite same Canvas."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from theory_master.science_canvas_export import migrate_original_canvas
from theory_master.science_graph_store import ScienceGraphStore


def test_preview_preserves_source_and_creates_no_database(tmp_path: Path):
    canvas = tmp_path / "exploration.canvas"
    canvas.write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf8")
    before = canvas.read_bytes()
    result = migrate_original_canvas(canvas, tmp_path / "science_graph.sqlite3", preview=True)
    assert result["mode"] == "preview_new"
    assert result["visible_nodes"] == 0
    assert not (tmp_path / "science_graph.sqlite3").exists()
    assert canvas.read_bytes() == before
    assert list(tmp_path.glob("exploration.canvas.*.bak")) == []


def test_legacy_ids_contents_and_edges_survive_in_place_conversion(tmp_path: Path):
    canvas = tmp_path / "exploration.canvas"
    original = {
        "nodes": [
            {"id": "H-0001", "type": "text", "x": 14, "y": 11,
             "text": "id: H-0001\ntype: Hypothesis\nfrontier_status: active\n\n# H one\n\nOriginal content"},
            {"id": "historical-h", "type": "text", "x": 500, "y": 100,
             "text": "id: historical-h\ntype: Hypothesis\n\n# H two"},
            {"id": "13-decision", "type": "text",
             "text": "id: 13-decision\ntype: Decision\ncycle_id: 13\nverdict: rejected"
                     "\nfrontier_status: closed\n\n# Old judgement\n\nHistorical summary"
                     "\n\n**Judge rationale**: Refuted by data"
                     "\n\n## Established after this Cycle\n\n- Preserved exact lemma"},
            {"id": "E-annotation", "type": "text",
             "text": "id: E-annotation\ntype: External\n\n# Human direction"},
            {"id": "legacy-finding", "type": "text",
             "text": "id: legacy-finding\ntype: Finding\n\n# Prior theorem\n\nDetailed proof preserved"},
            {"id": "stage-note", "type": "text",
             "text": "id: stage-note\ntype: StageSummary\n\n# Human checkpoint"},
            {"id": "unowned-decision", "type": "text",
             "text": "id: unowned-decision\ntype: Decision\nverdict: open\n\n# Orphan test"},
        ],
        "edges": [
            {"id": "a", "fromNode": "H-0001", "toNode": "13-decision", "edge_type": "scientific_lineage"},
            {"id": "b", "fromNode": "13-decision", "toNode": "historical-h", "edge_type": "hypothesis_evolution"},
            {"id": "c", "fromNode": "H-0001", "toNode": "E-annotation", "edge_type": "external_reference"},
            {"id": "d", "fromNode": "legacy-finding", "toNode": "stage-note", "edge_type": "external_reference"},
        ],
    }
    canvas.write_text(json.dumps(original, ensure_ascii=False), encoding="utf8")
    raw = canvas.read_bytes()
    db_path = tmp_path / "science_graph.sqlite3"
    result = migrate_original_canvas(canvas, db_path)
    assert result["mode"] == "projected"
    assert Path(result["backup"]).read_bytes() == raw
    assert result["historical_nodes_archived"] == 7
    assert result["historical_edges_archived"] == 4
    assert result["outcomes"] == 1
    assert result["whole_h_assessments"] == 2
    assert result["cycle_evaluations_archived"] == 1
    new = json.loads(canvas.read_text(encoding="utf8"))
    assert new["tm_science_graph_projection"]["canonical"] == "sqlite"
    assert new["tm_science_graph_projection"]["read_only"] is True
    assert len(new["nodes"]) == 6  # two H and four External
    ids = {node["id"] for node in new["nodes"]}
    assert "13-decision" not in ids  # The verdict lives INSIDE H.
    assert "H-0001" in ids and "historical-h" in ids
    assert "legacy-finding" in ids and "unowned-decision" in ids
    assert all("type: Hypothesis" in node["text"] or "type: External" in node["text"]
               for node in new["nodes"])
    h = next(node for node in new["nodes"] if node["id"] == "H-0001")
    assert h["x"] == 14 and h["y"] == 11
    assert "## Cycle 13 · Outcome · rejected" in h["text"]
    assert h["whole_h_assessment"]["verdict"] == "open"
    assert any(e["fromNode"] == "legacy-finding" and e["toNode"] == "stage-note"
               for e in new["edges"])
    with ScienceGraphStore(db_path, readonly=True) as store:
        assert store.db.execute("SELECT COUNT(*) FROM legacy_records").fetchone()[0] == 7
        assert store.db.execute("SELECT COUNT(*) FROM legacy_edges").fetchone()[0] == 4
        assert store.db.execute("SELECT COUNT(*) FROM external_relations").fetchone()[0] == 1
        assert store.db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 2
        assert store.db.execute("SELECT COUNT(*) FROM cycle_evaluations").fetchone()[0] == 1
        imported = store.db.execute("SELECT raw_json FROM legacy_records WHERE id='13-decision'").fetchone()
        assert "type: Decision" in imported[0]
        persisted = store.graph_view()
        assert {v["type"] for v in persisted["nodes"]} == {"Hypothesis", "External"}
    # Explicit rebuild remains idempotent and does not re-interpret projection as history.
    old_bytes = canvas.read_bytes()
    again = migrate_original_canvas(canvas, db_path)
    assert again["mode"] == "projected"
    assert canvas.read_bytes() == old_bytes


def test_external_cards_are_short_linked_and_projection_metadata_is_current(tmp_path: Path):
    canvas = tmp_path / "exploration.canvas"
    body = "id: E-long\ntype: External\ncontent_type: Finding\nsource: long-source\n\n# Long source record\n\nCore result. " + ("full source detail " * 300)
    original = {"nodes": [{"id": "E-long", "type": "text", "text": body}], "edges": []}
    canvas.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

    migrate_original_canvas(canvas, tmp_path / "science_graph.sqlite3")
    projected = json.loads(canvas.read_text(encoding="utf-8"))
    metadata = projected["tm_science_graph_projection"]
    assert metadata["version"] == 2
    assert metadata["schema"] == "theorymaster/science-graph-canvas/v2"
    assert metadata["outcome_relation"] == (
        "one_whole_h_assessment_per_hypothesis; zero_or_more_cycle_outcomes_per_hypothesis"
    )
    card = projected["nodes"][0]["text"]
    assert "[[notes/external/E-long.md|详细内容]]" in card
    assert len(card) < 500
    detail = tmp_path / "notes" / "external" / "E-long.md"
    assert detail.is_file()
    assert detail.read_text(encoding="utf-8").count("full source detail") == 300
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3", readonly=True) as store:
        raw = store.db.execute("SELECT raw_json FROM legacy_records WHERE id='E-long'").fetchone()
        assert raw and "full source detail " * 300 in raw[0]


def test_schema_rejects_existing_canonical_database_without_import(tmp_path: Path):
    canvas = tmp_path / "exploration.canvas"
    canvas.write_text('{"nodes":[],"edges":[]}', encoding="utf8")
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3"):
        pass
    with pytest.raises(ValueError, match="pre-existing"):
        migrate_original_canvas(canvas, tmp_path / "science_graph.sqlite3")
