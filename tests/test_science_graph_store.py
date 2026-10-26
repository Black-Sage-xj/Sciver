"""Regression coverage for the two-node science graph storage contract."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from theory_master.science_graph_store import ScienceGraphStore, import_legacy_canvas


def test_one_outcome_per_cycle_with_legacy_h_history(tmp_path: Path):
    with ScienceGraphStore(tmp_path / "science.sqlite3") as store:
        store.add_hypothesis("H-0001", title="Original")
        store.add_hypothesis("H-0002", title="Derived")
        store.append_outcome(
            outcome_id="11-outcome", hypothesis_id="H-0001", cycle_id="11",
            verdict="rejected", summary="negative", findings=["exact useful identity"],
        )
        store.append_outcome(
            outcome_id="12-outcome", hypothesis_id="H-0001", cycle_id="12",
            verdict="supported", summary="positive", judge_rationale="independent result",
            authorized_revision_reason="retrospective historical reconciliation",
        )
        store.add_evolution(edge_id="e1", source="H-0001", target="H-0002",
                            outcome_id="11-outcome", operator="alternative")
        store.set_frontier(branch_id="continue-original", hypothesis_id="H-0001",
                           anchor_outcome="12-outcome", status="active")
        view = store.graph_view()
        assert {n["type"] for n in view["nodes"]} == {"Hypothesis"}
        assert len(view["nodes"]) == 2
        first = next(n for n in view["nodes"] if n["id"] == "H-0001")
        assert first["outcome"]["id"] == "12-outcome"
        assert first["outcome"]["verdict"] == "supported"
        assert first["cycle_count"] == 2
        assert [x["id"] for x in first["cycle_outcomes"]] == ["11-outcome", "12-outcome"]
        assert len(store.cycle_history("H-0001")) == 2
        assert first["whole_h_assessment"]["id"] == "H-0001-outcome"
        assert first["scientific_status"] == "supported"
        assert first["legacy_scientific_status"] == "none"
        assert first["frontiers"][0]["status"] == "active"  # H may have descendants
        assert len(view["edges"]) == 1


def test_idempotent_replay_and_conflicting_replay_blocked(tmp_path: Path):
    args = dict(outcome_id="1-outcome", hypothesis_id="H-0001", cycle_id="1",
                verdict="open", summary="x" * 9001)
    with ScienceGraphStore(tmp_path / "graph.db") as store:
        store.add_hypothesis("H-0001")
        store.append_outcome(**args)
        store.append_outcome(**args)
        node = store.graph_view()["nodes"][0]
        assert len(node["outcome"]["summary"]) == 9001
        assert node["scientific_status"] == "open"  # An actual open assessment stays open.
        with pytest.raises(ValueError, match="replay conflicts"):
            store.append_outcome(**{**args, "verdict": "supported"})
        assert store.db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 1
        assert store.db.execute("SELECT COUNT(*) FROM cycle_evaluations").fetchone()[0] == 1


def test_cycle_identity_is_unique_and_new_h_lock_is_one_to_one(tmp_path: Path):
    with ScienceGraphStore(tmp_path / "graph.db") as store:
        store.add_hypothesis("H-0001")
        store.add_hypothesis("H-0002")
        store.register_cycle_hypothesis(cycle_id="17", hypothesis_id="H-0001")
        store.register_cycle_hypothesis(cycle_id="17", hypothesis_id="H-0001")
        with pytest.raises(ValueError, match="already bound"):
            store.register_cycle_hypothesis(cycle_id="17", hypothesis_id="H-0002")
        with pytest.raises(ValueError, match="already owned"):
            store.register_cycle_hypothesis(cycle_id="18", hypothesis_id="H-0001")
        store.append_outcome(
            outcome_id="cycle-outcome-17", hypothesis_id="H-0001", cycle_id="17",
            verdict="rejected", summary="specific counterexample",
            record_only=True,
        )
        view = store.graph_view()
        node = next(item for item in view["nodes"] if item["id"] == "H-0001")
        assert node["scientific_status"] == "none"
        assert node["whole_h_assessment"]["verdict"] == "open"
        assert node["cycle_outcomes"][0]["verdict"] == "rejected"
        with pytest.raises(ValueError, match="already has scientific Outcome"):
            store.append_outcome(
                outcome_id="cycle-outcome-17-second", hypothesis_id="H-0002", cycle_id="17",
                verdict="supported",
            )
        assert store.db.execute("SELECT COUNT(*) FROM cycle_evaluations").fetchone()[0] == 1


def test_legacy_import_preserves_cycles_science_and_sources(tmp_path: Path):
    canvas = {
        "nodes": [
            {"id": "H-0001", "type": "text", "text": "type: Hypothesis\nid: H-0001\nscientific_status: none\n\n# Old H"},
            {"id": "1-decision", "type": "text",
             "text": "id: 1-decision\ntype: Decision\nverdict: rejected\ncycle_id: 1\nfrontier_status: closed\n"
                     "\n# Cycle 1\n\nScientific answer\n\n**Decisive test**: a specific test\n"
                     "**Judge rationale**: contradicted\n\n## Established after this Cycle\n\n- useful identity"
                     "\n\n## Unresolved\n\n- some gap\n\n[[notes/cycle-1]]\n"
                     "\n**Evidence refs**: ev-1, ev-2"},
            {"id": "H-0002", "type": "text", "text": "type: Hypothesis\nid: H-0002\nfrontier_status: active\n\n# Next H"},
            {"id": "external-1", "type": "text",
             "text": "type: External\nid: external-1\ncontent_type: ResearchFocus\n\n# Human note"},
            {"id": "finding-1", "type": "text", "text": "type: Finding\nid: finding-1\n\n# Unverified historical F"},
        ],
        "edges": [
            {"id": "a", "fromNode": "H-0001", "toNode": "1-decision", "edge_type": "scientific_lineage"},
            {"id": "b", "fromNode": "1-decision", "toNode": "H-0002", "edge_type": "hypothesis_evolution",
             "evolution_operator": "alternative"},
            {"id": "c", "fromNode": "H-0002", "toNode": "external-1", "edge_type": "external_reference"},
            {"id": "d", "fromNode": "finding-1", "toNode": "H-0002", "edge_type": "research_source"},
        ],
    }
    source = tmp_path / "historical.canvas"
    source.write_text(json.dumps(canvas, ensure_ascii=False), encoding="utf8")
    target = tmp_path / "graph.sqlite3"
    result = import_legacy_canvas(source, target)
    assert result["status"] == "imported"
    assert result["hypotheses"] == 2 and result["outcomes"] == 1
    assert result["whole_h_assessments"] == 2
    assert result["cycle_evaluations"] == 1
    assert result["backup"] and Path(result["backup"]).is_file()
    assert "Finding finding-1 preserved as External" in " ".join(result["diagnostics"])
    assert import_legacy_canvas(source, target, create_backup=False)["status"] == "already_imported"
    with ScienceGraphStore(target) as store:
        view = store.graph_view()
        assert {n["type"] for n in view["nodes"]} == {"Hypothesis", "External"}
        original = next(n for n in view["nodes"] if n["id"] == "H-0001")
        outcome = original["outcome"]
        assert outcome["verdict"] == "rejected"  # Cycle-local result is visible.
        assert original["whole_h_assessment"]["verdict"] == "open"
        assert original["whole_h_assessment"]["judge_rationale"] == ""
        assert original["legacy_identity_review"]["status"] == "unadjudicated_whole_h"
        historical = store.cycle_history("H-0001")[0]
        assert historical["verdict"] == "rejected"
        assert historical["judge_rationale"] == "contradicted"
        assert historical["findings"] == ["useful identity"]
        assert historical["open_questions"] == ["some gap"]
        assert historical["evidence_refs"] == ["ev-1", "ev-2"]
        assert historical["report_ref"] == "notes/cycle-1"
        assert view["edges"][0]["source_hypothesis"] == "H-0001"
        assert view["edges"][0]["target_hypothesis"] == "H-0002"
        assert view["edges"][0]["originating_outcome"] == "1-decision"
        assert store.cycle_history("H-0001")[0]["id"] == "1-decision"
        assert original["frontiers"][0]["anchor_outcome"] == "1-decision"


def test_multiple_legacy_decisions_in_one_cycle_form_one_sourced_outcome(tmp_path: Path):
    canvas = tmp_path / "old.canvas"
    canvas.write_text(json.dumps({
        "nodes": [
            {"id": "H-0001", "text": "id: H-0001\ntype: Hypothesis\n\n# Broad H"},
            {"id": "7-decision-a", "text": "id: 7-decision-a\ntype: Decision\ncycle_id: 7\nverdict: supported\n\n# First\n\nFirst finding"},
            {"id": "7-decision-b", "text": "id: 7-decision-b\ntype: Decision\ncycle_id: 7\nverdict: supported\n\n# Second\n\nSecond finding"},
        ],
        "edges": [
            {"id": "a", "fromNode": "H-0001", "toNode": "7-decision-a"},
            {"id": "b", "fromNode": "H-0001", "toNode": "7-decision-b"},
        ],
    }), encoding="utf-8")
    database = tmp_path / "graph.db"
    imported = import_legacy_canvas(canvas, database)
    assert imported["cycle_evaluations"] == 1
    assert imported["legacy_decisions_linked"] == 2
    with ScienceGraphStore(database, readonly=True) as store:
        records = store.cycle_outcomes("H-0001")
        assert len(records) == 1
        assert records[0]["verdict"] == "supported"
        assert "First finding" in records[0]["summary"]
        assert "Second finding" in records[0]["summary"]
        assert [x["id"] for x in records[0]["source_decisions"]] == [
            "7-decision-a", "7-decision-b",
        ]


def test_conflicting_same_cycle_legacy_verdicts_are_not_synthesized(tmp_path: Path):
    canvas = tmp_path / "old.canvas"
    canvas.write_text(json.dumps({
        "nodes": [
            {"id": "H-0001", "text": "id: H-0001\ntype: Hypothesis\n\n# Broad H"},
            {"id": "7-decision-a", "text": "id: 7-decision-a\ntype: Decision\ncycle_id: 7\nverdict: supported\n\n# First"},
            {"id": "7-decision-b", "text": "id: 7-decision-b\ntype: Decision\ncycle_id: 7\nverdict: rejected\n\n# Second"},
        ],
        "edges": [
            {"id": "a", "fromNode": "H-0001", "toNode": "7-decision-a"},
            {"id": "b", "fromNode": "H-0001", "toNode": "7-decision-b"},
        ],
    }), encoding="utf-8")
    database = tmp_path / "graph.db"
    imported = import_legacy_canvas(canvas, database)
    assert imported["cycle_evaluations"] == 0
    assert any("conflicting historical Decision" in issue for issue in imported["diagnostics"])
    with ScienceGraphStore(database, readonly=True) as store:
        assert store.cycle_outcomes("H-0001") == []
        assert store.db.execute("SELECT COUNT(*) FROM external_records").fetchone()[0] == 2


def test_legacy_milestone_disposition_remains_visible_beside_compatibility_verdict(tmp_path: Path):
    canvas = tmp_path / "old.canvas"
    canvas.write_text(json.dumps({
        "nodes": [
            {"id": "H-0001", "text": "id: H-0001\ntype: Hypothesis\n\n# Broad H"},
            {"id": "8-decision", "text": "id: 8-decision\ntype: Decision\ncycle_id: 8\nverdict: milestone_advanced\n\n# Progress"},
        ],
        "edges": [{"id": "e", "fromNode": "H-0001", "toNode": "8-decision"}],
    }), encoding="utf-8")
    database = tmp_path / "graph.db"
    import_legacy_canvas(canvas, database)
    with ScienceGraphStore(database, readonly=True) as store:
        outcome = store.cycle_outcomes("H-0001")[0]
        assert outcome["verdict"] == "open"
        assert outcome["original_verdicts"] == ["milestone_advanced"]


def test_terminal_outcome_requires_scientific_revision_reason(tmp_path: Path):
    with ScienceGraphStore(tmp_path / "graph.db") as store:
        store.add_hypothesis("H-0003", title="Finite-range conjecture")
        store.append_outcome(
            outcome_id="41-decision", hypothesis_id="H-0003",
            cycle_id="41", verdict="supported", judge_rationale="verified in range")
        with pytest.raises(ValueError, match="Terminal H Outcome"):
            store.append_outcome(
                outcome_id="42-decision", hypothesis_id="H-0003",
                cycle_id="42", verdict="rejected")
        assert store.graph_view()["nodes"][0]["outcome"]["verdict"] == "supported"
        assert len(store.cycle_history("H-0003")) == 1
        store.append_outcome(
            outcome_id="42-decision", hypothesis_id="H-0003",
            cycle_id="42", verdict="rejected",
            authorized_revision_reason="independent counterexample changed the hypothesis fate")
        h = store.graph_view()["nodes"][0]
        assert h["outcome"]["verdict"] == "rejected"
        assert len(store.cycle_history("H-0003")) == 2
        assert store.db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 1
        changes = list(store.db.execute(
            "SELECT detail_json FROM graph_events WHERE action='outcome.revised'"))
        assert "independent counterexample" in changes[-1][0]


def test_agent_view_is_compact_and_keeps_status_lineage_and_detail_refs(tmp_path: Path):
    with ScienceGraphStore(tmp_path / "graph.db") as store:
        store.add_hypothesis("H-0001", title="Focused claim", summary="A precise focused claim.")
        store.add_hypothesis("H-0002", title="Explicit open claim", summary="An unresolved claim with an explicit open verdict.")
        store.add_hypothesis("H-0003", title="No assessment", summary="A claim without a whole-H assessment.")
        store.append_outcome(
            outcome_id="10-outcome", hypothesis_id="H-0001", cycle_id="10",
            verdict="rejected", summary="The focused prediction failed.",
            evidence_refs=["EV-10"], report_ref="notes/outcomes/10.md",
        )
        store.append_outcome(
            outcome_id="11-outcome", hypothesis_id="H-0002", cycle_id="11",
            verdict="open", summary="The test did not decide the wider claim.",
            evidence_refs=["EV-11"], report_ref="notes/outcomes/11.md",
        )
        store.add_evolution(
            edge_id="lineage-10-11", source="H-0001", target="H-0002",
            outcome_id="10-outcome", operator="derive",
        )
        store.db.execute("DELETE FROM frontier_branches WHERE hypothesis_id='H-0002'")
        external_content = (
            "type: External\nid: E-DETAIL-1\ncontent_type: Finding\n\n"
            "# Long external record\n\nCore finding. " + ("Full original detail. " * 80)
        )
        store.db.execute(
            "INSERT INTO external_records(id,content_type,source,title,content,created_at) VALUES (?,?,?,?,?,?)",
            ("E-DETAIL-1", "Finding", "test-source", "Long external record", external_content, "test-time"),
        )
        store.db.execute(
            "INSERT INTO external_edges(id,hypothesis_id,external_id,edge_type,originating_outcome) VALUES (?,?,?,?,?)",
            ("external-ref-1", "H-0001", "E-DETAIL-1", "external_reference", "10-outcome"),
        )

        view = store.agent_view("H-0001")
        by_id = {row[0]: dict(zip(view["hypothesis_columns"], row)) for row in view["hypotheses"]}
        assert view["schema"] == "theorymaster/agent-science-graph/v1"
        assert view["entry_hypothesis_id"] == "H-0001"
        assert by_id["H-0001"]["whole_h_status"] == "rejected"
        assert by_id["H-0002"]["whole_h_status"] == "open"
        assert by_id["H-0002"]["frontier_states"] == ["unmarked"]
        assert by_id["H-0003"]["whole_h_status"] == "none"
        assert by_id["H-0001"]["cycle_result_pattern"] == "rejected"
        assert view["lineage"] == [["H-0001", "H-0002", "derive", "10-outcome"]]
        external = dict(zip(view["external_source_columns"], view["external_sources"][0]))
        assert external["detail_ref"] == "notes/external/E-DETAIL-1.md"
        assert "content" not in external
        assert len(external["summary"]) <= 120
        serialized = json.dumps(view, ensure_ascii=False)
        assert external_content not in serialized
        for forbidden in ('"raw_json"', '"created_at"', '"recorded_at"', '"source_json"', '"x"', '"y"', '"width"', '"height"', '"color"'):
            assert forbidden not in serialized


def test_agent_detail_methods_expand_one_h_or_external_on_demand(tmp_path: Path):
    with ScienceGraphStore(tmp_path / "graph.db") as store:
        store.add_hypothesis("H-0001", title="Detailed claim", summary="The full scientific claim.")
        store.append_outcome(
            outcome_id="10-outcome", hypothesis_id="H-0001", cycle_id="10",
            verdict="open", summary="Cycle-local result.", decisive_test="Test the claim.",
            evidence_refs=["EV-10"], report_ref="notes/outcomes/10.md",
        )
        store.db.execute(
            "INSERT INTO external_records(id,content_type,source,title,content,created_at) VALUES (?,?,?,?,?,?)",
            ("E-0001", "Finding", "paper-X", "Full source", "Full original source body.", "test-time"),
        )
        store.db.execute(
            "INSERT INTO external_edges(id,hypothesis_id,external_id,edge_type,originating_outcome) VALUES (?,?,?,?,?)",
            ("ref-1", "H-0001", "E-0001", "external_reference", "10-outcome"),
        )
        h = store.agent_hypothesis_detail("H-0001")
        assert h["id"] == "H-0001"
        assert h["whole_h_assessment"]["verdict"] == "open"
        assert h["cycle_outcomes"][0]["decisive_test"] == "Test the claim."
        assert h["external_references"][0][0] == "E-0001"
        external = store.agent_external_detail("E-0001")
        assert external["content"] == "Full original source body."
        assert external["hypothesis_references"] == [["H-0001", "10-outcome"]]
        assert "created_at" not in external and "raw_json" not in external
        with pytest.raises(KeyError, match="Unknown hypothesis"):
            store.agent_hypothesis_detail("H-9999")
        with pytest.raises(KeyError, match="Unknown External"):
            store.agent_external_detail("E-9999")


def test_old_schema_refused_without_mutation(tmp_path: Path):
    import sqlite3
    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE graph_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT INTO graph_meta VALUES ('schema_version','1')")
    with pytest.raises(RuntimeError, match="explicit backup/migration required"):
        ScienceGraphStore(database)
    with sqlite3.connect(database) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name='cycle_evaluations'"
        ).fetchone() is None
        assert db.execute("SELECT value FROM graph_meta").fetchone()[0] == "1"
