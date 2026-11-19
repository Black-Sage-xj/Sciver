"""An unused optional trigger must not create a paid contract-repair detour."""
from copy import deepcopy
import json

import pytest

from theory_master.contracts import role_contract
from theory_master.hypothesis_lineage import (
    assert_repair_lineage_preserved,
    lineage_repair_snapshot,
)


def proposal(*, derived=True):
    candidate = {
        "hypothesis": "A bounded comparison of two predictors",
        "scope": "The observed population and fixed measurement interval",
        "why_now": "A source comparison leaves a discriminating question",
        "goal_advance": "Resolve the bounded predictive comparison",
        "decisive_test": "Compare paired errors on a held-out interval",
        "transition": {
            "prior_gap": "The predictive comparison remains unresolved",
            "current_change": "Compare two equally complex extensions",
            "source_contributions": {},
        },
    }
    if derived:
        candidate.update(lineage_main_node_id="H-0002", lineage_other_node_ids=["H-0001"],
                         evolution_operator="refine")
        candidate["transition"]["source_contributions"] = {
            "H-0001": "Defines the common baseline and solar radiation input.",
            "H-0002": "Defines the paired prediction comparison.",
        }
    return {"candidates": [candidate]}


def validate(value, *, derived=True):
    context = {"_known_hypothesis_ids": ["H-0001", "H-0002"] if derived else [],
               "_require_explicit_lineage": True}
    role_contract("research_lead", phase="frontier_proposal").validate(value, context)


@pytest.mark.parametrize("derived", [False, True])
@pytest.mark.parametrize("trigger", [None, "", " \t\n"])
def test_unused_optional_trigger_preserves_authored_proposal(derived, trigger):
    value = proposal(derived=derived)
    if trigger is not None:  # None represents the omitted key in this test.
        value["candidates"][0]["reconsideration_trigger"] = trigger
    original = deepcopy(value)
    validate(value, derived=derived)
    assert value == original


@pytest.mark.parametrize("trigger", [None, False, 0, 1.5, [], {}])
def test_present_nontext_trigger_is_rejected(trigger):
    value = proposal()
    value["candidates"][0]["reconsideration_trigger"] = trigger
    with pytest.raises(ValueError, match="reconsideration_trigger"):
        validate(value)


def test_nonempty_trigger_still_requires_declared_source():
    value = proposal(derived=False)
    value["candidates"][0]["reconsideration_trigger"] = "New independent measurement"
    with pytest.raises(ValueError, match="requires a lineage anchor"):
        validate(value, derived=False)
    value = proposal()
    value["candidates"][0]["reconsideration_trigger"] = "New independent measurement"
    validate(value)


def test_blank_trigger_does_not_make_unknown_source_eligible():
    value = proposal()
    value["candidates"][0].update(reconsideration_trigger="", lineage_main_node_id="H-9999")
    with pytest.raises(ValueError, match="existing SQLite H nodes"):
        validate(value)


@pytest.mark.parametrize("change", ["roles", "source", "contribution"])
def test_unused_trigger_does_not_relax_protected_source_decisions(change):
    value = proposal()
    value["candidates"][0]["reconsideration_trigger"] = ""
    protected = lineage_repair_snapshot(value, {"H-0001", "H-0002"})
    changed = deepcopy(value)
    candidate = changed["candidates"][0]
    if change == "roles":
        candidate.update(lineage_main_node_id="H-0001", lineage_other_node_ids=["H-0002"])
    elif change == "source":
        candidate["lineage_other_node_ids"] = []
    else:
        candidate["transition"]["source_contributions"]["H-0002"] += " Changed wording."
    with pytest.raises(ValueError):
        assert_repair_lineage_preserved(protected, changed)


@pytest.mark.parametrize("trigger", ["", " \t\n"])
def test_same_parked_h_still_needs_actual_reconsideration(tmp_path, trigger):
    from tests.test_science_frontier_v2 import _setup
    from theory_master.science_graph_store import ScienceGraphStore

    _, database, _ = _setup(tmp_path)
    with ScienceGraphStore(database) as store:
        with pytest.raises(ValueError, match="parked H requires reconsideration_trigger"):
            store.assert_eligible_hypothesis("H-0002", same_h=True,
                                             reconsideration_trigger=trigger)
        store.assert_eligible_hypothesis("H-0002", same_h=False,
                                         reconsideration_trigger=trigger)


def test_fake_workflow_accepts_blank_trigger_without_contract_repair(tmp_path):
    from theory_master.backend_fakes import FakeBackend
    from theory_master.workflow import Workflow
    from tests.test_core import TheoryMasterTest

    class BlankTriggerBackend(FakeBackend):
        def invoke(self, role, prompt, thread_id, model, provider):
            assert not prompt.startswith("Contract validation failed"), "Unused trigger caused a repair call"
            thread, raw = super().invoke(role, prompt, thread_id, model, provider)
            if role == "research_lead" and "Current task: frontier_proposal " in prompt:
                value = json.loads(raw)
                for candidate in value.get("candidates", []):
                    candidate["reconsideration_trigger"] = ""
                return thread, json.dumps(value)
            return thread, raw

    spec, _ = TheoryMasterTest().make(tmp_path, reworks=0)
    state = Workflow(spec, BlankTriggerBackend(reworks=0)).run()
    assert state["status"] in {"over", "completed"}
    assert state.get("contract_repair_count", 0) == 0
