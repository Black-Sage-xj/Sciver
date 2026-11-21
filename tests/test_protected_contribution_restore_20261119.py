"""Contract repairs retain the original valid lineage explanations."""

from __future__ import annotations

import copy

from theory_master.hypothesis_lineage import (
    assert_repair_lineage_preserved,
    lineage_repair_snapshot,
    restore_protected_contributions,
)


def _proposal():
    return {"candidates": [{
        "hypothesis": "Compare the two existing predictors",
        "lineage_main_node_id": "H-0002",
        "lineage_other_node_ids": ["H-0001"],
        "transition": {"source_contributions": {
            "H-0001": "Defines the first predictor.",
            "H-0002": "Defines the second predictor.",
        }},
    }]}


def test_repair_paraphrase_restores_exact_authored_explanation():
    original = _proposal()
    frozen = lineage_repair_snapshot(original, {"H-0001", "H-0002"})
    repaired = copy.deepcopy(original)
    repaired["candidates"][0]["transition"]["source_contributions"]["H-0002"] = "Second predictor."
    repaired["candidates"][0]["reconsideration_trigger"] = "A new comparison is available."
    result, paths = restore_protected_contributions(frozen, repaired)
    assert paths == ["candidates[0].transition.source_contributions[H-0002]"]
    assert result["candidates"][0]["transition"] == original["candidates"][0]["transition"]
    assert result["candidates"][0]["reconsideration_trigger"] == repaired["candidates"][0]["reconsideration_trigger"]
    assert repaired["candidates"][0]["transition"] != original["candidates"][0]["transition"]
    assert_repair_lineage_preserved(frozen, result)


def test_missing_note_and_changed_lineage_remain_invalid():
    original = _proposal()
    frozen = lineage_repair_snapshot(original, {"H-0001", "H-0002"})
    for change in ("missing_note", "changed_main"):
        repaired = copy.deepcopy(original)
        if change == "missing_note":
            repaired["candidates"][0]["transition"]["source_contributions"].pop("H-0002")
        else:
            repaired["candidates"][0]["lineage_main_node_id"] = "H-0001"
            repaired["candidates"][0]["lineage_other_node_ids"] = ["H-0002"]
        result, paths = restore_protected_contributions(frozen, repaired)
        assert paths == []
        try:
            assert_repair_lineage_preserved(frozen, result)
        except ValueError:
            pass
        else:
            raise AssertionError("A changed source decision passed lineage validation")
