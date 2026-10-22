"""MW-D: Lead proposal context no longer binds a Worker to one parent H."""
from theory_master.prompt_stages import stage_context
from theory_master.stage_registry import procedure_card_text, stage_card_text


def test_frontier_prompt_dedups_running_hypotheses_and_drops_claimed_parent():
    text = stage_card_text("research_lead", "frontier_proposal") + procedure_card_text(
        "research_lead", "frontier_proposal")
    assert "worker_claimed_frontier" not in text
    assert "preferred ResearchFocus" not in text
    assert "running_hypotheses" in text
    assert "shared parent is allowed" in text
    projected = stage_context("research_lead", "frontier_proposal", {
        "goal": "G",
        "agent_graph": {"schema": "theorymaster/agent-graph/v1", "hypotheses": []},
        "running_hypotheses": [{"hypothesis_id": "H-0002", "decisive_test": "sign"}],
    })
    assert projected["running_hypotheses"][0]["hypothesis_id"] == "H-0002"
    assert "worker_focus" not in projected
