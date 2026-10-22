"""Preserving every old Canvas record while producing the two-node-type graph."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from theory_master.science_canvas_export import (
    canvas_projection, migrate_original_canvas, restore_original_canvas,
)
from theory_master.science_graph_store import ScienceGraphStore


def test_rewrite_preserves_every_old_canvas_node_and_edge(tmp_path: Path):
    source = {
        "nodes": [
            {"id": "H-0001", "type": "text", "x": 10, "y": 20,
             "text": "type: Hypothesis\nid: H-0001\n\n# Original guess"},
            {"id": "1-decision", "type": "text",
             "text": "type: Decision\nid: 1-decision\ncycle_id: 1\nverdict: rejected"
                     "\n\n# Bad approximation\n\nA counterexample.\n"
                     "\n**Judge rationale**: The limit fails."
                     "\n\n## Established after this Cycle\n\n- Exact useful identity"},
            {"id": "finding-legacy", "type": "text",
             "text": "type: Finding\nid: finding-legacy\n\n# Unverified but valuable detail"},
            {"id": "unknown-1", "type": "mystery", "text": "old unknown content"},
            {"id": "orphan-D", "type": "text",
             "text": "type: Decision\nid: orphan-D\n\n# Unattached historical verdict"},
            {"id": "H-0002", "type": "text",
             "text": "type: Hypothesis\nid: H-0002\n\n# Follow-up"},
        ],
        "edges": [
            {"id": "a", "fromNode": "H-0001", "toNode": "1-decision"},
            {"id": "b", "fromNode": "1-decision", "toNode": "H-0002"},
            {"id": "c", "fromNode": "finding-legacy", "toNode": "H-0001"},
            {"id": "strange", "fromNode": "unknown-1", "toNode": "missing", "experimental": True},
        ],
        "legacy_custom_unknown_key": "DO NOT ERASE",
    }
    canvas = tmp_path / "exploration.canvas"
    source_bytes = json.dumps(source, ensure_ascii=False).encode("utf-8")
    canvas.write_bytes(source_bytes)
    database = tmp_path / "science_graph.sqlite3"
    status = migrate_original_canvas(canvas, database)
    assert status["historical_nodes_archived"] == 6
    assert status["historical_edges_archived"] == 4
    normalized = json.loads(canvas.read_text())
    assert normalized["tm_science_graph_projection"]["canonical"] == "sqlite"
    assert {node["id"] for node in normalized["nodes"]} == {
        "H-0001", "H-0002", "finding-legacy", "orphan-D",
    }
    kinds = {next(line for line in node["text"].splitlines()
                  if line.startswith("type: ")) for node in normalized["nodes"]}
    assert kinds == {"type: Hypothesis", "type: External"}
    h = next(node for node in normalized["nodes"] if node["id"] == "H-0001")
    assert h["outcome"]["verdict"] == "rejected"  # The Cycle result is visible.
    assert h["whole_h_assessment"]["verdict"] == "open"  # Whole-H fate stays separate.
    assert h["outcome"]["findings"] == ["Exact useful identity"]
    assert h["whole_h_assessment"]["findings"] == []
    assert h["cycle_count"] == 1
    backup = Path(status["backup"])
    assert backup.read_bytes() == source_bytes
    with ScienceGraphStore(database, readonly=True) as db:
        digest = hashlib.sha256(source_bytes).hexdigest()
        rows = db.db.execute("SELECT * FROM legacy_conversion_map WHERE source_digest=?",
                             (digest,)).fetchall()
        assert len(rows) == 6
        archive = db.db.execute(
            "SELECT original_bytes FROM legacy_canvas_archive WHERE digest=?", (digest,)
        ).fetchone()
        assert archive["original_bytes"] == source_bytes
        history = db.cycle_history("H-0001")
        assert len(history) == 1 and history[0]["findings"] == ["Exact useful identity"]
    restored = tmp_path / "restored-history.canvas"
    restore_original_canvas(database, hashlib.sha256(source_bytes).hexdigest(), restored)
    assert restored.read_bytes() == source_bytes
    with ScienceGraphStore(database, readonly=True) as db:
        assert canvas_projection(db)["nodes"] == normalized["nodes"]


def test_legacy_import_with_duplicate_node_ids_still_preserves_source(tmp_path: Path):
    raw = {
        "nodes": [
            {"id": "H-0001", "text": "type: Hypothesis\nid: H-0001\n\n# Example"},
            {"id": "H-0001", "text": "type: Hypothesis\nid: H-0001\n\n# duplicate"},
            {"text": "node without ID"},
        ],
        "edges": [{"fromNode": "not-present", "toNode": "another-unknown"}],
    }
    canvas = tmp_path / "exploration.canvas"
    original = json.dumps(raw).encode("utf-8")
    canvas.write_bytes(original)
    status = migrate_original_canvas(canvas, tmp_path / "science_graph.sqlite3")
    assert status["historical_nodes_archived"] == 3
    assert status["historical_edges_archived"] == 1
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3", readonly=True) as db:
        digest = hashlib.sha256(original).hexdigest()
        assert db.db.execute("SELECT count(*) FROM legacy_conversion_map WHERE source_digest=?",
                             (digest,)).fetchone()[0] == 3
        assert db.db.execute("SELECT original_bytes FROM legacy_canvas_archive WHERE digest=?",
                             (digest,)).fetchone()[0] == original
