"""SQLite-backed Observatory scientific graph projection regression tests."""
import json
from pathlib import Path

from tm_observer.canvas_md import build_index, load_canvas, search, read_document
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.science_canvas_export import write_external_detail_documents


def test_cycle_from_frozen_dossier_is_authoritative(tmp_path: Path):
    note = tmp_path / "notes" / "hypotheses" / "H-0099.md"
    note.parent.mkdir(parents=True)
    note.write_text("<!-- theory-master/hypothesis-dossier/v1 -->\n# H-0099\n\n"
                    "<!-- frozen-cycle:127 -->\n## Cycle 127 · 冻结科学定义\n", encoding="utf-8")
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as store:
        store.add_hypothesis("H-0099", title="First", dossier_ref="notes/hypotheses/H-0099")
        store.add_hypothesis("H-0100", title="No dossier")
    result = build_index(tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    assert nodes["H-0099"]["semantic"]["cycle"] == 127
    assert nodes["H-0100"]["semantic"]["cycle"] is None
    assert "created_cycle: 127" in nodes["H-0099"]["text"]


def test_cycle_memory_index_recovers_first_exact_h_cycle_and_fails_closed_on_gaps(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        for hypothesis_id in ("H-0042", "H-0043", "H-0044", "H-0045"):
            store.add_hypothesis(hypothesis_id, title=hypothesis_id)

        def add_memory(cycle: str, hypothesis_id: str, raw_hypothesis_id: str) -> None:
            provenance = {"source": "research_state.json", "raw_hypothesis_id": raw_hypothesis_id}
            store.db.execute(
                "INSERT INTO cycle_memory_index(cycle_id,hypothesis_id,worker_id,execution_mode,"
                "concurrent_workers_at_start,status,cycle_dir,memory_components_json,provenance_json,indexed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (cycle, hypothesis_id, None, "unknown_legacy", None, "over",
                 str(tmp_path / "research" / cycle), "[]", json.dumps(provenance, ensure_ascii=False),
                 "2026-10-18T00:00:00+00:00"),
            )

        add_memory("1", "H-0042", "H-0042")
        add_memory("2", "H-0042", "H-0042")
        add_memory("3", "H-0043", "H-0043")
        add_memory("4", "H-0044", "H-0043")  # inconsistent provenance must not infer
        add_memory("6", "H-0045", "H-0045")  # missing Cycle 5 makes the index incomplete

    nodes = {node["id"]: node for node in build_index(tmp_path)["nodes"]}
    assert nodes["H-0042"]["semantic"]["cycle"] == 1
    assert nodes["H-0043"]["semantic"]["cycle"] == 3
    assert nodes["H-0044"]["semantic"]["cycle"] is None
    assert nodes["H-0045"]["semantic"]["cycle"] is None


def test_sqlite_projection_embeds_outcome_and_avoids_legacy_canvas(tmp_path: Path):
    # No exploration.canvas is needed when science_graph.sqlite3 is canonical.
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as db:
        db.add_hypothesis("H-0001", title="Green hypothesis", summary="State for reader")
        db.add_hypothesis("H-0002", title="Follow-up")
        db.append_outcome(
            outcome_id="7-outcome", hypothesis_id="H-0001", cycle_id="7",
            verdict="rejected", summary="The assumed closure fails",
            judge_rationale="counterexample established", findings=["Exact response identity"],
            open_questions=["unknown high-order corrections"],
            report_ref="notes/cycles/7.md",
        )
        db.add_evolution(edge_id="edge-1", source="H-0001", target="H-0002",
                         operator="alternative", outcome_id="7-outcome")
        db.set_frontier(branch_id="continue-h1", hypothesis_id="H-0001", status="active",
                        anchor_outcome="7-outcome")
    doc = tmp_path / "notes" / "cycles" / "7.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Detailed outcome\n\n" + "Complete theory. " * 1000, encoding="utf-8")
    snap = load_canvas(tmp_path)
    assert len(snap["nodes"]) == 2
    assert {n["id"] for n in snap["nodes"]} == {"H-0001", "H-0002"}
    index = build_index(tmp_path)
    assert index["canvas"] == "science_graph.sqlite3"
    assert index["stats"]["node_count"] == 2
    assert index["stats"]["edge_count"] == 1
    original = next(n for n in index["nodes"] if n["id"] == "H-0001")
    assert original["semantic"]["cycle"] is None  # Outcome Cycle and H suffix are not freeze evidence
    assert original["semantic"]["frontier_status"] == "active"
    assert original["semantic"]["scientific_status"] == "rejected"
    outcome = original["semantic"]["outcome"]
    assert outcome["findings"] == ["Exact response identity"]
    assert outcome["judge_rationale"] == "counterexample established"
    assert any(hit["id"] == "H-0001" for hit in search(tmp_path, "Exact response identity"))
    assert "notes/cycles/7.md" in index["documents"][0]["id"]


def test_external_projection_is_compact_and_detail_link_is_readable(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    full_body = "type: External\nid: E-0001\ncontent_type: Finding\n\n# Full source\n\nCore result. " + ("full external body " * 300)
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Source hypothesis")
        store.db.execute(
            "INSERT INTO external_records(id,content_type,source,title,content,created_at) VALUES (?,?,?,?,?,?)",
            ("E-0001", "Finding", "test-source", "Full source", full_body, "test-time"),
        )
        store.db.execute(
            "INSERT INTO external_edges(id,hypothesis_id,external_id,edge_type,originating_outcome) VALUES (?,?,?,?,?)",
            ("ref-1", "H-0001", "E-0001", "external_reference", None),
        )
    with ScienceGraphStore(database, readonly=True) as store:
        write_external_detail_documents(store)

    snapshot = load_canvas(tmp_path)
    external = next(node for node in snapshot["nodes"] if node["id"] == "E-0001")
    assert len(external["text"]) < 500
    assert "[[notes/external/E-0001.md|详细内容]]" in external["text"]
    assert external["text"].count("full external body") < 15
    index = build_index(tmp_path)
    assert any(doc["id"] == "notes/external/E-0001.md" for doc in index["documents"])
    detail = read_document(tmp_path, "notes/external/E-0001.md")
    assert detail["content"].count("full external body") == 300


def test_source_canvas_timestamps_survive_sqlite_projection_without_migration_conflation(tmp_path: Path):
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as store:
        store.add_hypothesis("H-0042", title="Historical")
        store.add_hypothesis("H-0043", title="Native without historical header")
        store.append_outcome(
            outcome_id="42-outcome", hypothesis_id="H-0042", cycle_id="42",
            verdict="supported", summary="Scoped historical finding",
            source_decisions=[{"id": "42-decision", "raw_verdict": "supported"}],
        )
        originals = (
            ("H-0042", "Hypothesis", "10-07-22:43"),
            ("42-decision", "Decision", "2026-10-08T00:45:12+08:00"),
        )
        for ident, kind, timestamp in originals:
            raw = {"id": ident, "text": f"type: {kind}\nid: {ident}\ntimestamp: {timestamp}\n\n# Historical"}
            store.db.execute(
                "INSERT INTO legacy_records(id,node_type,raw_json,diagnostic) VALUES (?,?,?,?)",
                (ident, kind, json.dumps(raw), "timestamp regression fixture"),
            )
    indexed = {n["id"]: n["semantic"] for n in build_index(tmp_path)["nodes"]}
    h = indexed["H-0042"]
    assert h["source_timestamp"] == "10-07-22:43"
    assert h["registered_at"].startswith("20")
    cycle = h["cycle_outcomes"][0]
    assert cycle["source_timestamp"] == "2026-10-08T00:45:12+08:00"
    assert cycle["recorded_at"].startswith("20")
    native = indexed["H-0043"]
    assert native["source_timestamp"] is None
    assert native["registered_at"].startswith("20")
