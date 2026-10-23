"""Resume invalid legacy frontiers through the existing qualification restart."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from theory_master.workflow_qualification import QualificationMixin


@pytest.mark.parametrize("working", [False, True])
def test_old_decision_anchor_restarts_without_forging_a_hypothesis(working):
    frontier = {"candidates": [{"id": "candidate-1", "hypothesis": "Preserve the original text",
                               "evolution_operator": "derive", "lineage_anchor_node_ids": ["1-decision"]}]}
    state = {"qualification_status": "pending", "hypothesis_lock": {"status": "unlocked"},
             "role_outputs": {"research_lead_frontier": deepcopy(frontier)}}
    if working:
        state["qualification_working_set"] = deepcopy(frontier)
    with patch("theory_master.workflow_qualification._scientific_anchor_types", return_value={"H-0001": "Hypothesis"}):
        assert QualificationMixin()._restart_unqualified_qualification(state)
    assert state["qualification_status"] == "pending"
    assert "research_lead_frontier" not in state["role_outputs"]
    assert "qualification_working_set" not in state
    assert state["qualification_resume_history"][0]["stale_frontier"] == frontier
    assert state["hypothesis_lock"]["status"] == "unlocked"
    assert state["recovery"]["qualification_restart"]


def test_valid_h_anchor_and_locked_checkpoint_remain_unchanged():
    for anchor, locked in [("H-0001", False), ("1-decision", True)]:
        state = {"qualification_status": "pending", "hypothesis_lock": {"status": "locked" if locked else "unlocked"},
                 "role_outputs": {"research_lead_frontier": {"candidates": [{"id": "candidate-1",
                     "evolution_operator": "derive", "lineage_anchor_node_ids": [anchor]}]}}}
        original = deepcopy(state)
        with patch("theory_master.workflow_qualification._scientific_anchor_types", return_value={"H-0001": "Hypothesis"}):
            assert not QualificationMixin()._restart_unqualified_qualification(state)
        assert state == original
