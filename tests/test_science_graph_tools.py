from __future__ import annotations

import json
from pathlib import Path

from theory_master.science_graph_store import ScienceGraphStore
from theory_master.science_graph_tools import (
    CHALLENGER_REVIEW_OPERATIONS, LEAD_MAINTENANCE_OPERATIONS, LEAD_PROPOSAL_OPERATIONS,
    execute,
)
from theory_master.stage_registry import procedure_card_text, stage_card_text


def test_hypothesis_detail_tool_returns_normalized_mult_parent_lineage_and_no_placeholder(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Parent A", summary="Parent claim.")
        store.add_hypothesis("H-0002", title="Parent B", summary="Second parent.")
        store.add_hypothesis("H-0003", title="Child", summary="Child claim.")
        store.add_evolution(edge_id="e1", source="H-0001", target="H-0003", outcome_id="o1", operator="derive")
        store.add_evolution(edge_id="e2", source="H-0002", target="H-0003", outcome_id="o2", operator="specialize")
    result = execute(database, "hypothesis-detail", "H-0003")
    assert result["id"] == "H-0003"
    assert result["whole_h_assessment"] == {}
    assert len(result["lineage"]) == 2
    assert {row[0] for row in result["lineage"]} == {"H-0001", "H-0002"}


def test_external_detail_is_bounded_and_marks_truncation(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.db.execute(
            "INSERT INTO external_records(id,content_type,source,title,content,created_at) VALUES (?,?,?,?,?,?)",
            ("E-0001", "paper", "test-source", "Source", "x" * 100, "test-time"),
        )
    result = execute(database, "external-detail", "E-0001", max_content_chars=12)
    assert result["content"] == "x" * 12
    assert result["content_truncated"] is True
    assert result["content_chars_total"] == 100


def test_missing_records_and_database_fail_closed(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Present", summary="Claim.")
    missing = execute(database, "hypothesis-detail", "H-9999")
    assert missing["status"] == "not_found"
    unavailable = execute(tmp_path / "missing.sqlite3", "view")
    assert unavailable["status"] == "database_unavailable"


def test_cli_result_is_json_serializable(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Present", summary="Claim.")
    result = execute(database, "view", "H-0001")
    json.dumps(result, ensure_ascii=False)


def _external(store: ScienceGraphStore, external_id: str, title: str, content: str, hypothesis_id: str) -> None:
    store.db.execute(
        "INSERT INTO external_records(id,content_type,source,title,content,created_at) VALUES (?,?,?,?,?,?)",
        (external_id, "ResearchExploration", "test", title, content, "test-time"),
    )
    store.db.execute(
        "INSERT INTO external_edges(id,hypothesis_id,external_id,edge_type,originating_outcome) VALUES (?,?,?,?,?)",
        (f"edge-{external_id}", hypothesis_id, external_id, "external_reference", ""),
    )


def test_search_finds_external_off_the_declared_parent_without_full_text(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    secret = "SECRET-" + ("x" * 3000)
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0169", title="Declared parent", summary="Local shell only.")
        store.add_hypothesis("H-0051", title="Older branch", summary="Historical interface.")
        _external(
            store, "external-20261013-negative-edge-balance-interface",
            "负谷机制新检验｜单链权重 × 双源交界",
            "Green 电流与预应力的联合相干。" + ("背景" * 120) + secret,
            "H-0051",
        )
    found = execute(database, "search", "双源交界")
    assert found["status"] == "ok"
    assert found["results"][0]["id"] == "external-20261013-negative-edge-balance-interface"
    assert found["results"][0]["linked_ids"] == ["H-0051"]
    assert "SECRET-" not in json.dumps(found, ensure_ascii=False)
    assert len(found["results"][0]["summary"]) <= 180
    by_body = execute(database, "search", "联合相干")
    assert by_body["results"][0]["id"].startswith("external-20261013")
    by_id = execute(database, "search", "H-0051")
    assert by_id["match"] == "id"
    assert {row["id"] for row in by_id["results"]} >= {
        "H-0051", "external-20261013-negative-edge-balance-interface",
    }


def test_search_matches_outcome_text_and_caps_results(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Carrier", summary="Unrelated claim.")
        store.append_outcome(
            outcome_id="cycle-outcome-1", hypothesis_id="H-0001", cycle_id="1",
            verdict="open", summary="branching winding scale remains unresolved",
            decisive_test="Measure the winding.",
        )
        for index in range(10):
            _external(store, f"E-{index:04d}", f"sharedtokenZZ {index}", "body", "H-0001")
    outcome = execute(database, "search", "winding scale")
    assert outcome["results"][0]["id"] == "H-0001"
    crowded = execute(database, "search", "sharedtokenZZ")
    assert len(crowded["results"]) == 8
    assert crowded["truncated"] is True
    assert execute(database, "search", "")["status"] == "invalid_arguments"
    assert execute(database, "search", "a")["status"] == "invalid_arguments"


def test_challenger_search_surface_has_no_global_view_and_prompts_stay_bounded():
    assert "search" in CHALLENGER_REVIEW_OPERATIONS
    assert "global_view" not in CHALLENGER_REVIEW_OPERATIONS
    assert "view" not in " ".join(CHALLENGER_REVIEW_OPERATIONS.values())
    assert "search" in LEAD_PROPOSAL_OPERATIONS
    assert "search" not in LEAD_MAINTENANCE_OPERATIONS
    proposal = stage_card_text("research_lead", "frontier_proposal") + procedure_card_text(
        "research_lead", "frontier_proposal")
    review = stage_card_text("challenger", "candidate_review") + procedure_card_text(
        "challenger", "candidate_review")
    assert len(proposal) < 6000
    assert len(review) < 4500
    assert "search formal research history across H, Outcomes and External records" in proposal
    assert "SQLite-derived source_hypotheses and typed lineage" in review
    assert "never open or parse exploration.canvas" in review
    assert "adjudicability alone is not sufficient for qualification" in review


def test_hypothesis_detail_text_is_bounded_and_annotated(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Present", summary="x" * 2500)
    result = execute(database, "hypothesis-detail", "H-0001")
    assert len(result["claim"]) == 2000
    assert "claim" in result["truncated_fields"]
