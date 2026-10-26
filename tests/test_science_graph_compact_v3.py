"""Offline invariants for the non-authoritative compact-frontier-v3 projection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from theory_master.prompt_stages import build_initial_prompt, stage_context
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.science_graph_tools import execute


def _serialized_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def test_v3_preserves_complete_index_and_active_incoming_lineage(tmp_path: Path) -> None:
    db = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(db) as store:
        for n in range(1, 161):
            store.add_hypothesis(f"H-{n:04d}", title=f"Historical mechanism {n}",
                                 summary=f"Detailed hypothesis {n}")
        store.db.execute("UPDATE frontier_branches SET status='parked'")
        store.db.execute("DELETE FROM frontier_branches WHERE hypothesis_id='H-0003'")
        store.db.execute("UPDATE frontier_branches SET status='closed' WHERE hypothesis_id='H-0004'")
        store.db.execute("UPDATE frontier_branches SET status='active' WHERE hypothesis_id IN ('H-0159','H-0160')")
        store.append_outcome(outcome_id="outcome-1", hypothesis_id="H-0001",
                             cycle_id="93", verdict="rejected", summary="Earlier test failed",
                             report_ref="notes/93--judge.md")
        store.add_evolution(edge_id="incoming", source="H-0001", target="H-0159",
                            outcome_id="outcome-1", operator="specialize")
        store.add_evolution(edge_id="outgoing", source="H-0159", target="H-0002",
                            outcome_id="", operator="alternative")
        (tmp_path / "notes/hypotheses").mkdir(parents=True)
        (tmp_path / "notes/hypotheses/H-0159.md").write_text("# Frozen claim", encoding="utf-8")
        view = store.agent_view()
        assert view["projection_version"] == "compact-frontier-v3"
        rows = [dict(zip(view["hypothesis_columns"], r)) for r in view["hypotheses"]]
        by_id = {r["id"]: r for r in rows}
        assert len(by_id) == 160
        assert by_id["H-0003"]["frontier_states"] == ["unmarked"]
        assert by_id["H-0004"]["frontier_states"] == ["closed"]
        assert by_id["H-0159"]["frontier_states"] == ["active"]
        assert by_id["H-0001"]["cycle_result_pattern"] == "rejected"
        assert "dossier_ref" not in view["hypothesis_columns"]
        assert ["H-0001", "H-0159", "specialize", "outcome-1"] in view["lineage"]
        assert not any(edge[1] == "H-0002" for edge in view["lineage"])
        assert view["lineage_omitted_count"] >= 1
        active = {item["id"]: item for item in view["current_frontiers"]}
        assert set(active) == {"H-0159", "H-0160"}
        assert all("status" not in row for entry in active.values() for row in entry["frontiers"])
        assert "states" in active["H-0159"]  # old fake/continuation readers
        focused = store.agent_view("H-0159")
        assert any(edge[1] == "H-0002" for edge in focused["lineage"])
        assert focused["focus_details"][0]["dossier_ref"] == "notes/hypotheses/H-0159.md"
        assert _serialized_size(view) < 18000
        assert store.agent_search("H-0001")["results"][0]["id"] == "H-0001"
        assert any(edge[1] == "H-0002" for edge in store.agent_hypothesis_detail("H-0159")["lineage"])
    assert execute(db, "cycle-detail", "93")["record_kind"] == "judge"
    assert execute(db, "cycle-detail", "93")["judge_verdict"] == "rejected"


def test_v3_does_not_mutate_checkpoint_and_reuses_stage_contract(tmp_path: Path) -> None:
    db = tmp_path / "checkpoint.sqlite3"
    with ScienceGraphStore(db) as store:
        store.add_hypothesis("H-0001", title="A testable scientific hypothesis")
        store.append_outcome(outcome_id="judge-one", hypothesis_id="H-0001",
                             cycle_id="1", verdict="open", summary="Still unproven")
    before = db.read_bytes()
    with ScienceGraphStore(db, readonly=True) as store:
        lead = store.agent_view("H-0001")
        other_worker = store.agent_view("H-0001")
    assert lead == other_worker
    assert db.read_bytes() == before
    scoped = stage_context("research_lead", "frontier_proposal", {
        "goal": "G", "research_contract": "Frozen C", "agent_graph": lead,
    })
    assert scoped["agent_graph"]["projection_version"] == "compact-frontier-v3"
    rendered = build_initial_prompt(
        prefix="Research Lead.", role="research_lead", stage="frontier_proposal",
        contract_ref="contract", contract_schema="{}", context=scoped,
        write_rule="Write one frozen candidate.",
    )
    assert "compact-frontier-v3" in rendered
    assert "H-0001" in rendered
    reuse = {"schema": lead["schema"], "projection_version": lead["projection_version"],
             "entry_hypothesis_id": lead["entry_hypothesis_id"]}
    resumed = stage_context("research_lead", "frontier_proposal", {
        "goal": "G", "research_contract": "Frozen C", "agent_graph_reuse": reuse,
    }, continuation=True)
    assert resumed["agent_graph_reuse"] == reuse
    assert "agent_graph" not in resumed


def test_goal_gate_failure_is_visible_on_closed_supported_outcome(tmp_path: Path) -> None:
    db = tmp_path / "science_graph.sqlite3"
    rationale = "A single D estimate is missing; the linked reproducibility detail cannot be opened."
    with ScienceGraphStore(db) as store:
        store.add_hypothesis("H-0001", title="Local result")
        store.append_outcome(outcome_id="cycle-outcome-1", hypothesis_id="H-0001",
                             cycle_id="1", verdict="supported")
        store.set_frontier(branch_id="frontier:H-0001", hypothesis_id="H-0001",
                           anchor_outcome=None, status="closed", reason="local test finished")
        store.record_goal_gate_not_passed(cycle_id="1", rationale=rationale)
        store.record_goal_gate_not_passed(cycle_id="1", rationale=rationale)
        with pytest.raises(ValueError, match="replay conflicts"):
            store.record_goal_gate_not_passed(cycle_id="1", rationale="different assessment")
        graph = store.agent_view()
        assert graph["hypotheses"][0][-1] == "supported"
        assert graph["hypotheses"][0][3] == ["closed"]
        assert graph["goal_gate_attention"] == [["H-0001", "1", "not_passed", rationale]]
        assert store.agent_hypothesis_detail("H-0001")["cycle_outcomes"][0]["goal_gate"] == {
            "verdict": "not_passed", "rationale": rationale,
        }
        challenger = store.agent_candidate_review_view([])
        assert challenger["goal_gate_attention"] == graph["goal_gate_attention"]
