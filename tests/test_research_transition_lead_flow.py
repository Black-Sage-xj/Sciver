from __future__ import annotations

from copy import deepcopy
import json

import pytest

from theory_master.backend_fakes import FakeBackend
from theory_master.contract_closure import close_agent_output
from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import STAGE_CONTEXT_FIELDS, stage_context
from theory_master.scientific_objective import frozen_science_view
from theory_master.stage_registry import procedure_card_text
from theory_master.workflow import Workflow
from theory_master.workflow_qualification import _challenger_candidate_seed


TRANSITION = {
    "prior_gap": "H-0001 leaves the prediction outside the measured range unresolved.",
    "current_change": "This H tests the prediction in the previously unmeasured range.",
}


def candidate():
    return {
        "hypothesis": "The proposed relation extends to the new range.",
        "scope": "The original model family and the specified new range.",
        "why_now": "The required inputs are now available.",
        "goal_advance": "Resolve the Goal's remaining prediction range.",
        "decisive_test": "Compare the relation with the prespecified new-range observations.",
        "transition": deepcopy(TRANSITION),
    }


@pytest.mark.parametrize("transition", [None, {}, {"prior_gap": "gap"},
    {**TRANSITION, "extra": "new science"}, {**TRANSITION, "prior_gap": " "},
    {**TRANSITION, "current_change": "x" * 401}])
def test_new_proposals_require_a_bounded_structured_transition(transition):
    value = candidate()
    if transition is None:
        value.pop("transition")
    else:
        value["transition"] = transition
    with pytest.raises(ValueError, match="transition"):
        role_contract("research_lead", phase="frontier_proposal").validate({"candidates": [value]}, {})


def test_root_and_multiple_sources_keep_their_authored_transition():
    contract = role_contract("research_lead", phase="frontier_proposal")
    root = candidate()
    root["transition"]["prior_gap"] = "The original Goal has no established prediction in this range."
    contract.validate({"candidates": [root]}, {})
    derived = {**candidate(), "lineage_main_node_id": "H-0001",
               "lineage_other_node_ids": ["H-0002"], "evolution_operator": "combine"}
    context = {"_known_hypothesis_ids": ["H-0001", "H-0002"]}
    contract.validate({"candidates": [derived]}, context)
    closed = close_agent_output("research_lead", "frontier_proposal", {"candidates": [derived]}, context, {})
    assert closed["candidates"][0]["transition"] == TRANSITION
    assert "transition" not in frozen_science_view(derived)


def test_challenger_sees_transition_with_main_and_other_sources():
    item = {**candidate(), "id": "cand-1", "lineage_main_node_id": "H-0001",
            "lineage_other_node_ids": ["H-0002"], "evolution_operator": "combine"}
    seed = _challenger_candidate_seed([item])
    prompt_context = stage_context("challenger", "candidate_review", {"candidate": seed, "agent_graph": {}})
    background = prompt_context["candidate"][0]["proposal_context"]
    assert background["transition"] == TRANSITION
    assert background["lineage_main_node_id"] == "H-0001"
    assert background["lineage_other_node_ids"] == ["H-0002"]
    procedure = procedure_card_text("challenger", "candidate_review")
    assert "Wording quality alone is not a scientific qualification blocker" in procedure
    assert "unresolved, not failures" in procedure


def test_display_context_preserves_only_frozen_reasoning_without_new_output_fields():
    objective = {key: candidate()[key] for key in ("hypothesis", "scope", "decisive_test")}
    reasoning = {**candidate(), "lineage_main_node_id": "H-0001", "post_lock_finding": "hindsight"}
    context = stage_context("research_lead", "hypothesis_display", {
        "frozen_hypothesis": objective, "frozen_reasoning": reasoning,
        "scientific_synthesis": {"conclusions": ["later result"]},
    })
    assert context["frozen_hypothesis"] == objective
    assert context["frozen_reasoning"]["transition"] == TRANSITION
    assert context["frozen_reasoning"]["why_now"] == candidate()["why_now"]
    assert context["frozen_reasoning"]["lineage_main_node_id"] == "H-0001"
    assert "post_lock_finding" not in json.dumps(context)
    assert "hindsight" not in json.dumps(context)
    assert set(context) <= set(STAGE_CONTEXT_FIELDS[("research_lead", "hypothesis_display")])
    assert set(role_contract("research_lead", phase="hypothesis_display").prompt_schema) == {
        "canvas_title", "canvas_claim", "canvas_conditions",
    }


@pytest.mark.parametrize("legacy", [False, True])
def test_repair_inherits_transition_and_rejects_changed_frozen_reasoning(legacy):
    parent = {**candidate(), "id": "cand-1"}
    if legacy:
        parent.pop("transition")
    blockers = [{"id": "block-1", "problem": "Definition missing", "required_change": "Define the observable"}]
    review = {"candidate_id": "cand-1", "verdict": "revise", "reason": "Clarify the test", "blocking_issues": blockers}
    repair = {"outcome": "resolved", "repair": {
        "repaired_decisive_test": "Compare the relation using the explicit observable definition.",
        "blocker_closures": [{"blocker_id": "block-1", "resolution": "The observable is explicitly defined."}],
    }}
    closed = close_agent_output("research_lead", "selection_repair", repair,
                                {"candidates": [parent], "candidate_reviews": [review]}, {})
    repaired = closed["replacement_candidates"][0]
    assert repaired.get("transition") == parent.get("transition")
    assert ("transition" in repaired) is not legacy
    context = {"original_candidate": parent, "repaired_candidate": repaired, "original_review": review}
    verdict = {"candidate_id": repaired["id"], "verdict": "confirmed", "reason": "The blocker is closed."}
    contract = role_contract("challenger", phase="repair_verification")
    contract.validate(verdict, context)
    repaired["transition"] = {**TRANSITION, "prior_gap": "An invented new reason"}
    with pytest.raises(ValueError, match="changed frozen transition"):
        contract.validate(verdict, context)


def test_single_qualified_candidate_keeps_proposal_reasoning_and_one_display_call(tmp_path):
    from tests.test_core import TheoryMasterTest

    class CaptureBackend(FakeBackend):
        def __init__(self):
            super().__init__(reworks=0)
            self.calls = []

        def invoke(self, role, prompt, thread_id, model, provider):
            stage = prompt.split("Current task: ", 1)[1].split(" ", 1)[0]
            context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
            self.calls.append((role, stage, context))
            return super().invoke(role, prompt, thread_id, model, provider)

    spec, _ = TheoryMasterTest().make(tmp_path, reworks=0)
    backend = CaptureBackend()
    state = Workflow(spec, backend).run()
    provenance = state["hypothesis_lock"]["candidate_provenance"]
    assert provenance["transition"]["current_change"] == "This H tests the bounded deterministic smoke claim."
    assert "Only Challenger-qualified candidate" in state["cycle_start_rationale"]
    assert not any(stage == "candidate_selection" for _, stage, _ in backend.calls)
    display = [context for _, stage, context in backend.calls if stage == "hypothesis_display"]
    assert len(display) == 1
    assert display[0]["frozen_reasoning"]["transition"] == provenance["transition"]
    assert display[0]["frozen_reasoning"]["why_now"] == provenance["why_now"]
    review = [context for _, stage, context in backend.calls if stage == "candidate_review"][0]
    assert review["candidate"][0]["proposal_context"]["transition"] == provenance["transition"]


@pytest.mark.parametrize("legacy", [False, True])
def test_display_reads_frozen_provenance_without_using_later_candidate_reasoning(tmp_path, legacy):
    from theory_master.science_graph_store import ScienceGraphStore
    from theory_master.workflow_qualification import QualificationMixin

    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database):
        pass
    frozen = candidate()
    if legacy:
        frozen.pop("transition")
    selected = {**candidate(), "transition": {"prior_gap": "A later invented gap", "current_change": "Hindsight"}}
    captured = []

    class DisplayWorkflow(QualificationMixin):
        def _graph_database_path(self):
            return database

        def _call(self, state, role, context, **kwargs):
            captured.append(deepcopy(context))
            return {"canvas_title": "The bounded relation", "canvas_claim": "The relation extends",
                    "canvas_conditions": "Within the frozen range"}

    state = {"hypothesis_lock": {
        "status": "committing", "id": "H-0001", **frozen_science_view(frozen),
        "candidate_provenance": deepcopy(frozen),
    }}
    DisplayWorkflow()._author_hypothesis_display(state, selected)
    assert len(captured) == 1
    assert captured[0]["frozen_reasoning"]["why_now"] == frozen["why_now"]
    assert ("transition" in captured[0]["frozen_reasoning"]) is not legacy
    if not legacy:
        assert captured[0]["frozen_reasoning"]["transition"] == TRANSITION
    assert "Hindsight" not in json.dumps(captured)
    assert state["hypothesis_lock"]["candidate_provenance"].get("transition") == frozen.get("transition")
