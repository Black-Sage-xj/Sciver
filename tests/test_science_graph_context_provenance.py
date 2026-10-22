"""Offline regression for compact Agent graph and provenance-only Cycle navigation."""
from __future__ import annotations

import json
from pathlib import Path

from theory_master.science_graph_store import ScienceGraphStore
from theory_master.science_graph_tools import execute


def _memory(store: ScienceGraphStore, cycle: str, hypothesis: str) -> None:
    store.db.execute(
        "INSERT INTO cycle_memory_index("
        "cycle_id,hypothesis_id,worker_id,execution_mode,"
        "concurrent_workers_at_start,status,cycle_dir,"
        "memory_components_json,provenance_json,indexed_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            cycle, hypothesis, "worker-1", "normal", 2, "completed",
            f"/private/local/something/research/{cycle}",
            json.dumps(["workspace", "notes", "evidence", "run_report.md",
                        "some-private-file", "../../not-safe"]),
            "{}", "2026-10-22",
        ),
    )


def test_cycle_detail_distinguishes_judge_preserved_and_missing_files(tmp_path: Path):
    database = tmp_path / "graph.sqlite3"
    with ScienceGraphStore(database) as store:
        for h in ("H-0183", "H-0184", "H-0185"):
            store.add_hypothesis(h, title=h, summary="Claim")
        store.append_outcome(
            outcome_id="j183", hypothesis_id="H-0183", cycle_id="183",
            verdict="rejected", summary="Frozen test rejected shared closure",
            evidence_refs=["evidence/test.csv", "/private/path/secret.txt", "../escape"],
            report_ref="notes/183--judge.md",
        )
        store.db.execute(
            "INSERT INTO cycle_preservations(cycle_id,hypothesis_id,source_json,recorded_at)"
            " VALUES (?,?,?,?)",
            (
                "184", "H-0184",
                json.dumps({"cycle_id": "184", "hypothesis_id": "H-0184",
                            "verdict": "unadjudicated", "summary": "Preserved only",
                            "report_ref": "notes/scientific_reports/Cycle184.md"}),
                "2026-10-22",
            ),
        )
        _memory(store, "183", "H-0183")
        _memory(store, "184", "H-0184")
        _memory(store, "185", "H-0185")
        store.db.execute(
            "UPDATE outcomes SET verdict='open',cycle_id='',summary='',"
            "judge_rationale='',decisive_test='' WHERE hypothesis_id='H-0184'"
        )

    formal = execute(database, "cycle-detail", "183")
    preserved = execute(database, "cycle-detail", "184")
    missing = execute(database, "cycle-detail", "185")
    assert formal["record_kind"] == "judge"
    assert formal["judge_verdict"] == "rejected"
    assert formal["adjudication_status"] == "adjudicated"
    assert formal["evidence_refs"] == ["evidence/test.csv"]
    assert formal["evidence_refs_omitted"] == 2
    assert formal["report_ref"] == "notes/183--judge.md"
    assert formal["research_root_relative_refs"]["workspace"] == "183/workspace"
    assert preserved["record_kind"] == "preserved"
    assert preserved["judge_verdict"] is None
    assert preserved["adjudication_status"] == "unadjudicated"
    assert preserved["preserved"] is True
    assert missing["record_kind"] == "unadjudicated"
    assert missing["judge_verdict"] is None
    for record in (formal, preserved, missing):
        serialized = json.dumps(record)
        assert "/private/" not in serialized
        assert "some-private-file" not in serialized
        assert record["file_availability"].startswith("not_checked")
        assert record["whole_h_status_inferred"] is False
    assert execute(database, "cycle-detail", "../184")["status"] == "database_error"
    assert execute(database, "cycle-detail", "9999")["status"] == "not_found"


def test_cycle_detail_rejects_conflicting_evaluation_and_preservation(tmp_path: Path):
    database = tmp_path / "graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001")
        store.append_outcome(outcome_id="j1", hypothesis_id="H-0001",
                             cycle_id="1", verdict="open", summary="Undecided")
        store.db.execute(
            "INSERT INTO cycle_preservations(cycle_id,hypothesis_id,source_json,recorded_at)"
            " VALUES (?,?,?,?)",
            ("1", "H-0001", '{"cycle_id":"1"}', "2026-10-22"),
        )
    assert execute(database, "cycle-detail", "1")["status"] == "database_error"


def test_cycle_detail_reads_older_sqlite_without_optional_tables(tmp_path: Path):
    database = tmp_path / "legacy.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001")
        store.append_outcome(
            outcome_id="j1", hypothesis_id="H-0001", cycle_id="1",
            verdict="rejected", summary="Legacy Judge conclusion",
        )
        store.db.execute("DROP TABLE cycle_preservations")
        store.db.execute("DROP TABLE cycle_memory_index")
    found = execute(database, "cycle-detail", "1")
    assert found["record_kind"] == "judge"
    assert found["judge_verdict"] == "rejected"
    assert found["research_root_relative_refs"] == {}


def test_compact_context_preserves_all_h_ids_and_cross_branch_detail(tmp_path: Path):
    database = tmp_path / "graph.sqlite3"
    with ScienceGraphStore(database) as store:
        for n in range(1, 161):
            store.add_hypothesis(
                f"H-{n:04d}", title=f"Mechanism {n}",
                summary=f"Mechanism {n} full scientific report",
            )
        for n in range(1, 160):
            store.add_evolution(
                edge_id=f"e{n}", source=f"H-{n:04d}", target=f"H-{n + 1:04d}",
                outcome_id="", operator="derive",
            )
        store.db.execute(
            "UPDATE frontier_branches SET status='parked' "
            "WHERE hypothesis_id NOT IN ('H-0159','H-0160')"
        )
        view = store.agent_view("H-0160")
        assert len(view["hypotheses"]) == 160
        assert view["projection_version"] == "compact-frontier-v3"
        assert view["lineage_omitted_count"] > 130
        assert len(view["current_frontiers"]) <= 2
        assert "H-0025" in {row[0] for row in view["hypotheses"]}
        assert store.agent_search("H-0025")["results"][0]["id"] == "H-0025"
        detail = store.agent_hypothesis_detail("H-0025")
        assert len(detail["lineage"]) == 2


def test_search_old_cycle_mechanisms_and_pagination(tmp_path: Path):
    database = tmp_path / "graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0063", title="Mechanism A", summary="Other title")
        store.add_hypothesis("H-0189", title="Mechanism B", summary="Other title")
        store.append_outcome(
            outcome_id="j93", hypothesis_id="H-0063", cycle_id="93",
            verdict="open", summary="Signed interface mismatch mechanism",
            decisive_test="Measure P(d) and cross-shell variance",
        )
        for n in range(12):
            ident = f"external-protocol-{n}"
            store.db.execute(
                "INSERT INTO external_records(id,content_type,source,title,content,created_at)"
                " VALUES (?,?,?,?,?,?)",
                (ident, "paper", "test", f"Protocol model {n}",
                 "deep " * 1100 + "calibration protocol test", "2026-10-22"),
            )
        store.db.execute(
            "INSERT INTO cycle_preservations(cycle_id,hypothesis_id,source_json,recorded_at)"
            " VALUES (?,?,?,?)",
            ("184", "H-0189",
             '{"id":"preserve184","cycle_id":"184","verdict":"unadjudicated","summary":"Preserved analysis"}',
             "2026-10-22"),
        )
        original = store.agent_search("interface mismatch")
        assert original["results"][0]["id"] == "H-0063"
        assert original["results"][0]["matched_cycles"][0] == {
            "cycle_id": "93", "record_kind": "judge", "verdict": "open"
        }
        assert store.agent_search("P(d)")["results"][0]["id"] == "H-0063"
        first = store.agent_search("calibration protocol test")
        second = store.agent_search("calibration protocol test", offset=8)
        assert first["total_matches"] == 12
        assert first["next_offset"] == 8
        assert first["truncated"] is True
        assert second["total_matches"] == 12
        assert len(second["results"]) == 4
        assert second["next_offset"] is None
        assert not second["truncated"]
        assert max(len(row["summary"]) for row in first["results"]) <= 180
        assert len(json.dumps(first)) < 5_000
        assert store.agent_search("Preserved analysis")["results"][0][
            "matched_cycles"
        ][0]["record_kind"] == "preserved"
        assert store.agent_search("calibration protocol test", offset=-1)[
            "status"
        ] == "invalid_arguments"
