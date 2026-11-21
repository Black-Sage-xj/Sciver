"""Exercise task-specific scope through both public prompt assembly paths."""
import json

import pytest

from theory_master.contracts import role_contract
from theory_master.evaluation_history import require_eligible_identity
from theory_master.prompt_stages import build_continuation_prompt, build_initial_prompt, stage_context


@pytest.mark.parametrize("role,stage", [
    ("research_lead", "frontier_proposal"),
    ("challenger", "candidate_review"),
    ("challenger", "repair_verification"),
])
@pytest.mark.parametrize("goal,gate", [
    ("Estimate Brownian diffusivity.", "Give the estimate from the supplied increments."),
    ("Submit the published predictions again.", "Reproduce the public reference on the supplied rows."),
    ("Predict the missing labels.", "Return a prediction for each input row. Public validation may be reused."),
    ("Design an analytic method.", "Deliver the derivation. Execution requires separate authorization."),
    ("Estimate transition boundaries.", "Cover both boundaries for the Hamiltonian on the periodic lattice."),
    ("Evaluate the frozen predictions.", "Score once against the designated unexposed final sample."),
])
def test_task_supplies_the_domain_and_gate_in_initial_and_followup(role, stage, goal, gate):
    raw = {"goal": goal, "research_contract": goal, "final_gate": gate, "agent_graph": {}}
    contract = role_contract(role, phase=stage)
    for continuation in (False, True):
        projected = stage_context(role, stage, raw, continuation=continuation)
        args = dict(role=role, stage=stage, contract_ref=contract.ref,
                    contract_schema=contract.prompt_projection(projected), context=projected)
        prompt = (build_continuation_prompt(**args) if continuation else
                  build_initial_prompt(**args, prefix="Test", write_rule=""))
        instructions, data = prompt.split("Context:\n", 1)
        supplied = json.loads(data)
        assert (supplied["goal"], supplied["final_gate"]) == (goal, gate)
        for project_detail in ("Hamiltonian", "symmetry class", "disorder channel", "periodic lattice",
                               "propagation counts", "FLOP convention", "descriptive reproduction alone"):
            assert project_detail.casefold() not in instructions.casefold()


def test_public_reuse_and_protected_final_have_different_evidence_claims():
    material = {"unit_id": "provided-data", "use": "evaluation", "prior_feedback": "present",
                "history_basis": "Public scores were available during development."}
    history = [{"unit_id": "provided-data", "terminal_outcome": True}]
    for allowed in ("ordinary", "not_required"):
        require_eligible_identity({"evaluation_materials": [dict(material, independence=allowed)]}, history)
    with pytest.raises(ValueError, match="protected_final identity unavailable"):
        require_eligible_identity({"evaluation_materials": [dict(material, independence="protected_final")]}, history)
