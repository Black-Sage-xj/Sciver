"""Offline contracts and SQLite invariants for research quality safeguards."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from theory_master.contract_stages import role_contract
from theory_master.evaluation_history import evaluation_history, freeze_materials
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.scientific_objective import frozen_science_view, review_binding, require_review_binding
from theory_master.stage_registry import procedure_card_text


def material(**changes):
    return {"unit_id": "evaluation-units/all", "use": "evaluation", "independence": "protected_final",
            "prior_feedback": "none", "history_basis": "input manifest and primary reports establish the partition/history", **changes}


def candidate(**changes):
    return {"id": "cand-1", "hypothesis": "A specified mechanism predicts a transfer contrast",
            "scope": "Defined common units", "decisive_test": "Compare the prespecified contrast with its uncertainty",
            "why_now": "Resolve a necessary uncertainty", "goal_advance": "Establish the required transfer contrast",
            "transition": {"prior_gap": "Unknown transfer mechanism", "current_change": "Test one bounded mechanism", "source_contributions": {}},
            "evaluation_materials": [material()], **changes}


def checks():
    return {name: {"assessment": "clear", "basis": "The primary report/input definition and frozen test establish this condition"}
            for name in ("source_fidelity", "definition_consistency", "discriminating_test", "evidence_identity")}


def review(cand=None, **changes):
    cand = cand or candidate()
    item = {"candidate_id": cand["id"], "verdict": "qualified", "reason": "Executable scoped comparison",
            "scientific_checks": checks(), **changes}
    return {"reviews": [item]}, {"candidates": [cand]}


@pytest.mark.parametrize("feedback", ["present", "unknown"])
def test_protected_final_cannot_be_requalified_by_an_unlabeled_method(feedback):
    cand = candidate(evaluation_materials=[material(prior_feedback=feedback)])
    output, context = review(cand)
    with pytest.raises(ValueError, match="cannot restore"):
        role_contract("challenger", phase="candidate_review").validate(output, context)
    output["reviews"][0].update(verdict="revise", blocking_issues=[{"problem": "Exposed final units", "required_change": "Use truly eligible units without changing target"}])
    role_contract("challenger", phase="candidate_review").validate(output, context)


def test_ordinary_permitted_calibration_is_not_prohibited():
    cand = candidate(evaluation_materials=[material(use="calibration", independence="ordinary", prior_feedback="present")])
    output, context = review(cand)
    role_contract("challenger", phase="candidate_review").validate(output, context)


def test_known_history_blocks_a_false_freshness_declaration(tmp_path):
    path = tmp_path / "graph.sqlite3"
    with ScienceGraphStore(path) as store:
        store.add_hypothesis("H-0001")
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
        freeze_materials(store, "H-0001", candidate())
        # A lock declaration alone does not claim that an evaluation happened.
        assert not evaluation_history(store)[0]["terminal_outcome"]
        store.append_outcome(outcome_id="outcome-1", hypothesis_id="H-0001", cycle_id="1", verdict="rejected")
        for view in (store.agent_view(), store.agent_candidate_review_view([]), store.agent_goal_completion_view()):
            assert view["evaluation_history"][0]["terminal_outcome"]
            assert "never attestation" in view["evaluation_history_scope"]
        output, context = review()
        context["agent_graph"] = store.agent_candidate_review_view([])
        with pytest.raises(ValueError, match="protected_final identity unavailable"):
            role_contract("challenger", phase="candidate_review").validate(output, context)
        store.add_hypothesis("H-0002")
        with pytest.raises(ValueError, match="protected_final identity unavailable"):
            freeze_materials(store, "H-0002", candidate())
        # A source-grounded genuinely disjoint unit remains possible.
        freeze_materials(store, "H-0002", candidate(evaluation_materials=[material(unit_id="new-units/untouched")]))
        freeze_materials(store, "H-0001", candidate())  # exact replay, including after adjudication
        with pytest.raises(ValueError, match="cannot change on replay"):
            freeze_materials(store, "H-0001", candidate(evaluation_materials=[material(unit_id="renamed")]))
        assert store.graph_view()["nodes"][0]["evaluation_materials"] == candidate()["evaluation_materials"]
    with ScienceGraphStore(path, readonly=True) as store:
        assert len(evaluation_history(store)) == 2


def test_missing_history_on_a_legacy_graph_is_explicitly_incomplete(tmp_path):
    path = tmp_path / "graph.sqlite3"
    with ScienceGraphStore(path) as store:
        store.db.execute("DROP TABLE hypothesis_evaluation_materials")
    with ScienceGraphStore(path, readonly=True) as store:
        view = store.agent_view()
        assert view["evaluation_history"] == []
        assert "incomplete" in view["evaluation_history_scope"]
        assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='hypothesis_evaluation_materials'").fetchone()


def test_identity_is_part_of_the_frozen_review_binding():
    objective = frozen_science_view(candidate())
    binding = review_binding("cand-1", objective)
    changed = copy.deepcopy(objective)
    changed["evaluation_materials"][0]["unit_id"] = "renamed"
    with pytest.raises(ValueError, match="binding missing or changed"):
        require_review_binding("cand-1", changed, binding)


def test_explicit_scientific_checks_are_preserved_and_cannot_hide_a_blocker():
    from theory_master.workflow_science import WorkflowScienceMixin
    output, context = review()
    contract = role_contract("challenger", phase="candidate_review")
    missing = copy.deepcopy(output)
    missing["reviews"][0].pop("scientific_checks")
    with pytest.raises(ValueError, match="scientific_checks requires"):
        contract.validate(missing, context)
    output["reviews"][0]["scientific_checks"]["definition_consistency"]["assessment"] = "blocker"
    with pytest.raises(ValueError, match="cannot retain"):
        contract.validate(output, context)
    output["reviews"][0]["scientific_checks"] = checks()
    normalized = WorkflowScienceMixin._validate_candidate_reviews(output["reviews"], {"cand-1": candidate()})
    assert normalized[0]["scientific_checks"] == checks()


def test_goal_gap_types_survive_sqlite_replay_and_keep_scientific_verdict(tmp_path):
    path = tmp_path / "graph.sqlite3"
    gaps = [{"kind": "delivery", "requirement": "Provide the final comparison table", "basis": "The current delivery omits an already adjudicated comparison"}]
    contract = role_contract("judge", phase="goal_completion")
    contract.validate({"verdict": "not_passed", "rationale": "Missing final table", "gaps": gaps}, {})
    with pytest.raises(ValueError, match="nonempty gaps"):
        contract.validate({"verdict": "not_passed", "rationale": "Missing table"}, {})
    with pytest.raises(ValueError, match="unresolved gaps"):
        contract.validate({"verdict": "passed", "rationale": "Done", "gaps": gaps}, {})
    with ScienceGraphStore(path) as store:
        store.add_hypothesis("H-0001")
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
        store.append_outcome(outcome_id="result-1", hypothesis_id="H-0001", cycle_id="1", verdict="rejected")
        args = dict(cycle_id="1", rationale="Missing final table", assessment_id="a1", delivery_revision="a"*64, science_digest="b"*64, receipt_path="receipt.json", gaps=gaps)
        store.record_goal_gate_not_passed(**args)
        store.record_goal_gate_not_passed(**args)
        assert store.goal_gate_assessment("1")["gaps"] == gaps
        assert store.db.execute("SELECT verdict FROM cycle_evaluations").fetchone()[0] == "rejected"
        with pytest.raises(ValueError, match="gap replay conflicts"):
            store.record_goal_gate_not_passed(**{**args, "gaps": [{**gaps[0], "kind": "scientific"}]})
        assert store.agent_goal_completion_view()["goal_gate_history"][0]["gaps"] == gaps


@pytest.mark.parametrize("kind", ["delivery", "unavailable_material"])
def test_lead_can_exit_without_inventing_a_scientific_candidate(kind, tmp_path):
    from theory_master.core import FakeBackend, Workflow, TaskSpec
    from theory_master.task import bootstrap

    disposition = {"kind": kind, "requirement": "Supply the final task deliverable", "basis": "Existing adjudications and input inventory"}
    role_contract("research_lead", phase="frontier_proposal").validate({"no_new_science": disposition}, {})
    with pytest.raises(ValueError, match="exclusive"):
        role_contract("research_lead", phase="frontier_proposal").validate({"no_new_science": disposition, "candidates": []}, {})

    class Backend(FakeBackend):
        def invoke(self, role, prompt, thread_id, model, provider):
            if role == "research_lead" and "Current task: frontier_proposal " in prompt:
                return str(thread_id), json.dumps({"no_new_science": disposition})
            return super().invoke(role, prompt, thread_id, model, provider)

    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal\nComplete a bounded task\n# Research Contract\nUse existing inputs\n# Final Gate\nSupply the final task deliverable\n# Required Context\n- Existing task materials\n")
    spec = TaskSpec.from_config(bootstrap(plan).config_path)
    workflow = Workflow(spec, Backend())
    state = workflow.run()
    assert state["status"] == "needs_attention"
    assert state["qualification_status"] == "no_new_science"
    assert state["attention"]["kind"] == kind
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert not store.graph_view()["nodes"]
        assert not store.db.execute("SELECT 1 FROM cycle_evaluations").fetchone()


def test_runtime_stage_rules_cover_sources_definitions_information_and_history():
    lead = procedure_card_text("research_lead", "frontier_proposal")
    review = procedure_card_text("challenger", "candidate_review")
    for text in (lead, review):
        assert "evaluation_materials" in text and "evaluation_history" in text
        assert "protected_final" in text
    assert "no_new_science" in lead
    for field in ("source_fidelity", "definition_consistency", "discriminating_test", "evidence_identity"):
        assert field in review


@pytest.mark.parametrize("checkpoint", [False, True])
def test_material_declarations_reach_review_lock_investigation_and_canonical_history(tmp_path, checkpoint):
    from theory_master.core import FakeBackend, Workflow, TaskSpec
    from theory_master.task import bootstrap

    declared = [material(independence="ordinary")]

    class Backend(FakeBackend):
        investigator_objective = None
        challenger_objective = None

        def invoke(self, role, prompt, thread_id, model, provider):
            thread, raw = super().invoke(role, prompt, thread_id, model, provider)
            value = json.loads(raw)
            context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
            if role == "research_lead" and "Current task: frontier_proposal " in prompt:
                value["candidates"][0]["evaluation_materials"] = declared
            if role == "challenger" and "Current task: candidate_review " in prompt:
                self.challenger_objective = context["candidate"][0]["frozen_objective"]
                value["reviews"][0]["scientific_checks"] = checks()
            if role == "investigator" and "Current task: investigate " in prompt:
                self.investigator_objective = context["frozen_objective"]
            return thread, json.dumps(value)

    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal\nTest a bounded contrast\n# Research Contract\nUse provided inputs\n# Final Gate\nA reproducible contrast\n# Required Context\n- Local inputs\n")
    spec = TaskSpec.from_config(bootstrap(plan).config_path)
    backend = Backend(reworks=0)
    if checkpoint:
        stopped = Workflow(spec, backend, stop_before="hypothesis_lock").run()
        assert stopped["checkpoint"]["name"] == "pre_hypothesis_lock"
        state = Workflow(spec, backend).resume(0)
    else:
        state = Workflow(spec, backend).run()
    assert state["hypothesis_lock"]["evaluation_materials"] == declared
    assert backend.challenger_objective["evaluation_materials"] == declared
    assert backend.investigator_objective["evaluation_materials"] == declared
    assert state["hypothesis_lock"]["qualification"]["review"]["scientific_checks"] == checks()
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert evaluation_history(store)[0]["terminal_outcome"]
        assert store.agent_view()["goal_gap_details"][0]["gaps"][0]["kind"] == "scientific"


def test_reopening_is_rejected_in_the_stage_contract_before_store_commit(tmp_path):
    with ScienceGraphStore(tmp_path / "graph.sqlite3") as store:
        store.add_hypothesis("H-0001")
        store.set_frontier(branch_id="frontier:H-0001", hypothesis_id="H-0001", status="closed", reason="Exact question resolved")
        output = {"updates": [{"hypothesis_id": "H-0001", "status": "parked", "reason": "Source premise changed",
                                "remaining_question": "Does the new premise alter the conclusion?", "dependency": "Need the revised input"}]}
        with pytest.raises(ValueError, match="reopen_basis"):
            role_contract("research_lead", phase="frontier_maintenance").validate(output, {"agent_graph": store.agent_view()})


def test_existing_goal_assessment_cannot_acquire_new_gap_details(tmp_path):
    with ScienceGraphStore(tmp_path / "graph.sqlite3") as store:
        store.add_hypothesis("H-0001")
        store.append_outcome(outcome_id="result-1", hypothesis_id="H-0001", cycle_id="1", verdict="supported")
        store.record_goal_gate_not_passed(cycle_id="1", rationale="Missing delivery")
        with pytest.raises(ValueError, match="frozen historical assessment"):
            store.record_goal_gate_not_passed(cycle_id="1", rationale="Missing delivery", gaps=[
                {"kind": "delivery", "requirement": "Table", "basis": "Absent from delivery"}])
        assert "gaps" not in store.goal_gate_assessment("1")


def test_frontier_maintenance_preserves_unowned_metadata_and_exact_noop(tmp_path):
    with ScienceGraphStore(tmp_path / "graph.sqlite3") as store:
        store.add_hypothesis("H-0001")
        update = {"hypothesis_id": "H-0001", "status": "active", "reason": "A necessary limit remains",
                  "remaining_question": "Does the claim hold in the limiting regime?"}
        store.set_frontier(branch_id="frontier:H-0001", hypothesis_id="H-0001", status="active", reason=update["reason"],
                           anchor_outcome="source-result", preferred=True,
                           opportunity={"remaining_question": update["remaining_question"], "dependency": "", "reopen_basis": ""})
        before = store.db.execute("SELECT COUNT(*) FROM graph_events").fetchone()[0]
        store.set_frontiers_batch([update])
        assert store.db.execute("SELECT COUNT(*) FROM graph_events").fetchone()[0] == before
        row = store.frontier_view()["H-0001"]["branches"][0]
        assert row["anchor_outcome"] == "source-result" and row["priority"] == "preferred"
