"""Non-Judge preserved Cycle is embedded in H Outcome; never an External graph node."""
from pathlib import Path

import pytest

from theory_master.science_graph_store import ScienceGraphStore


def _register(store):
    store.add_hypothesis("H-0155", title="Preserved frozen H")
    store.register_cycle_hypothesis(cycle_id="155", hypothesis_id="H-0155")


def _preserve(store, **overrides):
    args = {
        "hypothesis_id": "H-0155",
        "cycle_id": "155",
        "summary": "Topology search found no qualifying contrast; decisive test unfinished.",
        "report_ref": "notes/scientific_reports/Cycle155-preserve_research.md",
        "source_attention_code": "investigator_budget_review_rejected",
        "prior_judge_objection": "Judge requested an independently discriminating comparison",
        "stage_output_sha256": "a" * 64,
    }
    args.update(overrides)
    return store.record_preserved_cycle(**args)


def test_preserved_cycle_is_h_outcome_and_not_a_second_node(tmp_path):
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as store:
        _register(store)
        outcome = _preserve(store)
        assert outcome["verdict"] == "unadjudicated"
        assert _preserve(store) == outcome  # Idempotent.
        assert store.cycle_history("H-0155") == []  # No Judge.
        v = store.graph_view()
        assert len(v["nodes"]) == 1
        h = v["nodes"][0]
        assert h["type"] == "Hypothesis"
        assert h["cycle_outcomes"] == [dict(outcome)]
        assert h["cycle_count"] == 1
        assert h["whole_h_assessment"]["cycle_id"] == ""
        assert h["cycle_outcomes"][0]["report_ref"].endswith("Cycle155-preserve_research.md")
        assert not v["external_edges"]
        assert store.frontier_view()["H-0155"]["outcome"] == "open"  # Placeholder, NOT verdict.
        assert store.agent_hypothesis_detail("H-0155")["cycle_outcomes"][0][
            "termination_reason"] == "investigator_budget_review_rejected"


def test_stage_replay_fails_closed_and_cannot_adjudicate_same_cycle(tmp_path):
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as store:
        _register(store)
        _preserve(store)
        with pytest.raises(ValueError, match="replay conflicts"):
            _preserve(store, summary="altered after freeze")
        with pytest.raises(ValueError, match="preserved Cycle"):
            store.append_outcome(outcome_id="155-decision", hypothesis_id="H-0155",
                                 cycle_id="155", verdict="rejected", summary="not proven")
        assert store.cycle_history("H-0155") == []


def test_existing_scientific_outcome_cannot_be_reclassified_as_preserved(tmp_path):
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as store:
        _register(store)
        store.append_outcome(outcome_id="155-decision", hypothesis_id="H-0155",
                             cycle_id="155", verdict="supported", summary="Judged")
        with pytest.raises(ValueError, match="Judge-adjudicated"):
            _preserve(store)
