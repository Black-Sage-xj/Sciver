from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from theory_master.contracts import (
    PHASE_CONTRACTS,
    contract_by_id,
    role_contract,
)
from theory_master.contract_closure import close_agent_output
from theory_master.core import FakeBackend, TaskSpec, Workflow
from theory_master.role_registry import ROLES


class RoleContractTest(unittest.TestCase):
    def test_every_runtime_role_has_a_versioned_stage_contract(self):
        phase_roles = {role for role, _stage in PHASE_CONTRACTS}
        self.assertEqual(phase_roles, set(ROLES))
        for (role, stage), contract in PHASE_CONTRACTS.items():
            with self.subTest(role=role, stage=stage):
                self.assertTrue(contract.contract_id.startswith("theory-master/role/"), role)
                self.assertGreaterEqual(contract.version, 1)
                self.assertEqual(len(contract.schema_hash), 64)
                self.assertEqual(contract.descriptor()["schema_hash"], contract.schema_hash)
                self.assertIs(role_contract(role, phase=stage), contract)

    def test_only_current_stage_contracts_are_registered(self):
        for (role, stage), contract in PHASE_CONTRACTS.items():
            self.assertIs(contract_by_id(contract.contract_id, contract.version), contract)
            self.assertIsNone(contract_by_id(contract.contract_id, contract.version - 1))
            self.assertEqual(contract.ref, f"{contract.contract_id}/v{contract.version}")
        with self.assertRaisesRegex(ValueError, "no phase contract registered"):
            role_contract("research_lead", phase="frontier_planning")

    def test_lead_literature_detour_contract_is_bounded_and_stage_specific(self):
        proposal = role_contract("research_lead", phase="frontier_proposal")
        landscape = {
            "needs_literature": True,
            "literature_request": {
                "task_type": "landscape",
                "search_target": "external scholarly map of mechanism X",
                "why_external_needed": "the supplied local context does not establish the relevant prior mechanisms",
                "expected_external_evidence": "representative papers and competing mechanisms",
            },
        }
        proposal.validate(landscape, {})

        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            proposal.validate({
                **landscape,
                "candidates": [{
                    "hypothesis": "H", "why_now": "now", "goal_advance": "advance",
                    "decisive_test": "test",
                }],
            }, {})
        # Novelty/priority verification is a legal on-demand Stage, not a
        # compulsory part of every Literature detour.
        proposal.validate({
            "needs_literature": True,
            "literature_request": {
                **landscape["literature_request"],
                "task_type": "novelty_check",
            },
        }, {})
        with self.assertRaisesRegex(ValueError, "concrete new_search_lead"):
            proposal.validate({
                "needs_literature": True,
                "literature_request": {
                    **landscape["literature_request"],
                    "task_type": "targeted_source",
                },
            }, {})

        targeted = {
            "needs_literature": True,
            "literature_request": {
                **landscape["literature_request"],
                "task_type": "targeted_source",
                "new_search_lead": "DOI 10.0000/example",
            },
        }
        proposal.validate(targeted, {})

        synthesis = role_contract("research_lead", phase="cycle_synthesis")
        with self.assertRaisesRegex(ValueError, "cycle_synthesis no longer owns frontier planning"):
            synthesis.validate({
                "scientific_synthesis": {"conclusions": [], "open_questions": []},
                "scientific_model_update": {"action": "keep"},
                **landscape,
            }, {})

    def test_research_lead_stage_contracts_are_distinct(self):
        names = ["frontier_proposal", "candidate_selection", "selection_repair", "cycle_synthesis"]
        contracts = [role_contract("research_lead", phase=name) for name in names]
        self.assertEqual(len({item.contract_id for item in contracts}), 4)
        synthesis = role_contract("research_lead", phase="cycle_synthesis")
        model_schema = synthesis.prompt_schema["scientific_model_update"]["model"]
        self.assertEqual(set(model_schema), {"summary"})
        projection = synthesis.prompt_projection()
        self.assertIn("action=keep omits model", projection)
        self.assertNotIn("scientific_access", projection)
        self.assertEqual(synthesis.version, 7)
        self.assertNotIn("evolution_candidates", synthesis.prompt_schema)
        with self.assertRaisesRegex(ValueError, "no phase contract registered"):
            role_contract("research_lead", phase="frontier_planning")
        self.assertNotIn("current_frontier", synthesis.prompt_schema["scientific_model_update"]["model"])
        self.assertNotIn("established_knowledge", synthesis.prompt_schema["scientific_model_update"]["model"])
        judge = role_contract("judge", phase="terminal_assessment")
        self.assertNotIn("recommended_start_capability", judge.prompt_schema)
        self.assertNotIn("goal_advance", judge.prompt_schema)
        reporter = role_contract("reporter", phase="report")
        self.assertEqual(set(reporter.prompt_schema), {"title", "summary"})
        with self.assertRaisesRegex(ValueError, "no phase contract"):
            role_contract("reporter", phase="canvas_maintenance")
        self.assertIn("action=replace supplies only model.summary", projection)
        self.assertIn(("research_lead", "cycle_synthesis"), PHASE_CONTRACTS)
    def test_research_lead_finalization_contract_versions_are_minimal(self):
        selection = role_contract("research_lead", phase="candidate_selection")
        self.assertEqual(selection.version, 3)
        self.assertEqual(set(selection.prompt_schema), {"selected_candidate_id", "selection_reason"})
        self.assertIsNone(contract_by_id(selection.contract_id, 2))

        repair = role_contract("research_lead", phase="selection_repair")
        self.assertEqual(repair.version, 8)
        self.assertIsNone(contract_by_id(repair.contract_id, 6))

    def test_frontier_maintenance_prompt_schema_matches_sqlite_validator(self):
        contract = role_contract("research_lead", phase="frontier_maintenance")
        self.assertEqual(contract.version, 3)
        self.assertIsNone(contract_by_id(contract.contract_id, 2))
        projection = contract.prompt_projection({})
        self.assertIn("hypothesis_id", projection)
        self.assertIn("status", projection)
        self.assertIn("reason", projection)
        self.assertNotIn("current Canvas branch-tip node id", projection)
        contract.validate({"updates": [{
            "hypothesis_id": "H-0001", "status": "parked", "reason": "No unmet Goal opportunity remains on this line."
        }]}, {"_known_hypothesis_ids": ["H-0001"]})
        with self.assertRaisesRegex(ValueError, "unknown H id in canonical SQLite graph"):
            contract.validate({"updates": [{
                "hypothesis_id": "H-NOT-IN-GRAPH", "status": "parked", "reason": "test fixture"
            }]}, {"_known_hypothesis_ids": ["H-0001"]})
        with self.assertRaisesRegex(ValueError, "unsupported fields: frontier_status, node_id"):
            contract.validate({"updates": [{
                "node_id": "H-0001", "frontier_status": "parked", "reason": "legacy schema"
            }]}, {"_known_hypothesis_ids": ["H-0001"]})

    def test_current_closed_choice_fields_are_explicit_in_agent_facing_contracts(self):
        checks = {
            ("research_lead", "frontier_proposal"): [
                "landscape|targeted_source",
                "refine | specialize | generalize | alternative | combine | derive | counter_hypothesis",
            ],
            ("research_lead", "selection_repair"): ["resolved|cannot_repair"],
            ("research_lead", "cycle_synthesis"): ["keep|replace"],
            ("research_lead", "frontier_maintenance"): ["closed|parked|active"],
            ("challenger", "candidate_review"): ["qualified|revise|reject"],
            ("investigator", "investigate"): [
                "benchmark_provided|pre_specified_synthetic|agent_generated|external_source",
                "none|fit|validation|holdout|gate",
            ],
            ("literature", "targeted_source"): ["metadata|abstract|full_text|unavailable"],
            ("literature", "novelty_check"): [
                "metadata|abstract|full_text|unavailable",
                "known|partially_overlapping|apparently_distinct|uncertain",
            ],
            ("judge", "terminal_assessment"): ["supported|rejected|milestone_advanced|open", "verdict=null"],
            ("judge", "goal_completion"): ["passed|not_passed"],
        }
        for (role, stage), expected_fragments in checks.items():
            projection = role_contract(role, phase=stage).prompt_projection({})
            for fragment in expected_fragments:
                with self.subTest(role=role, stage=stage, fragment=fragment):
                    self.assertIn(fragment, projection)

    def test_dynamic_closed_choices_are_explicit_for_selection_and_repair(self):
        selection = role_contract("research_lead", phase="candidate_selection")
        selection_prompt = selection.prompt_projection({
            "qualified_candidates": [
                {"id": "cand-alpha", "title": "A"},
                {"id": "cand-beta", "title": "B"},
            ]
        })
        self.assertIn("selected_candidate_id is a closed choice", selection_prompt)
        self.assertIn("cand-alpha | cand-beta", selection_prompt)
        self.assertIn("Do not invent another candidate ID", selection_prompt)

        repair = role_contract("research_lead", phase="selection_repair")
        repair_prompt = repair.prompt_projection({
            "repair_options": [
                {"candidate_id": "cand-r1", "blockers": [{"id": "b-1"}, {"id": "b-2"}]},
                {"candidate_id": "cand-r2", "blockers": [{"id": "b-3"}]},
            ]
        })
        self.assertIn("selected_parent_candidate_id is a closed choice", repair_prompt)
        self.assertIn("cand-r1 | cand-r2", repair_prompt)
        self.assertIn("blocker_closures[].blocker_id is also closed", repair_prompt)
        self.assertIn("cand-r1: b-1 | b-2", repair_prompt)
        self.assertIn("cand-r2: b-3", repair_prompt)
        self.assertIn("Do not invent blocker IDs", repair_prompt)

    def test_contract_prompts_delegate_stage_rules_and_keep_format_guardrails(self):
        from theory_master.prompt_stages import build_initial_prompt

        frontier = role_contract("research_lead", phase="frontier_proposal")
        frontier_projection = frontier.prompt_projection()
        self.assertNotIn("feasible local test of a consequential hypothesis prediction", frontier_projection)
        self.assertNotIn("genuine scientific increment", frontier_projection)
        self.assertNotIn("Goal/Final Gate prerequisite only", frontier_projection)
        self.assertIn("lineage references, not evidence", frontier_projection)
        self.assertIn("Do not return canvas_title", frontier_projection)
        literature_schema = frontier.prompt_schema["literature_request"]
        self.assertEqual(literature_schema["task_type"], "landscape|targeted_source|novelty_check")
        self.assertIn("required for targeted_source", literature_schema["new_search_lead"])

        maintenance = role_contract("research_lead", phase="frontier_maintenance")
        self.assertEqual(maintenance.prompt_note, "")
        self.assertNotIn("Unmarked means unknown, not closed", maintenance.prompt_projection())
        from theory_master.stage_registry import procedure_card_text
        self.assertIn("unmarked is not closed", procedure_card_text("research_lead", "frontier_maintenance"))
        assembled_frontier = build_initial_prompt(
            prefix="test", role="research_lead", stage="frontier_proposal",
            contract_ref=frontier.ref, contract_schema=frontier_projection,
            context={}, write_rule="",
        )
        self.assertIn("Each Cycle should pursue a scientifically distinct, independently adjudicable claim", assembled_frontier)
        self.assertIn("Use evolution_operator only from:", assembled_frontier)

        challenger = role_contract("challenger", phase="candidate_review")
        self.assertEqual(challenger.prompt_schema["reviews"][0]["reason"], "concise rationale for the verdict")
        self.assertIn("ONLY for verdict=revise", challenger.prompt_projection())
        assembled_challenger = build_initial_prompt(
            prefix="test", role="challenger", stage="candidate_review",
            contract_ref=challenger.ref, contract_schema=challenger.prompt_projection(),
            context={"candidates": [{"id": "cand-1"}]}, write_rule="",
        )
        self.assertIn("unmet Goal/Final Gate requirement", assembled_challenger)
        self.assertIn("concise rationale for the verdict", assembled_challenger)

        repair = role_contract("research_lead", phase="selection_repair")
        self.assertIn("required for resolved:", repair.prompt_projection())
        self.assertIn("required only for cannot_repair:", repair.prompt_projection())

        self.assertLess(len(frontier_projection), 1800)
        self.assertLess(len(challenger.prompt_projection()), 450)
        self.assertLess(len(repair.prompt_projection()), 900)
        self.assertLess(len(role_contract("research_lead", phase="hypothesis_display").prompt_projection()), 700)
        self.assertLess(len(role_contract("research_lead", phase="cycle_synthesis").prompt_projection()), 1250)
        self.assertLess(len(role_contract("research_lead", phase="frontier_maintenance").prompt_projection()), 300)
        self.assertLess(len(role_contract("investigator", phase="investigate").prompt_projection()), 1700)
        self.assertLess(len(role_contract("judge", phase="terminal_assessment").prompt_projection()), 750)

    def test_frontier_proposal_lineage_prompt_and_legacy_validation(self):
        frontier = role_contract("research_lead", phase="frontier_proposal")
        prompt = frontier.prompt_projection()
        self.assertIn('"lineage_anchor_node_ids":', prompt)
        self.assertNotIn('"lineage_anchor_node_id":', prompt)
        self.assertIn("include that H in lineage_anchor_node_ids", prompt)
        self.assertNotIn("lineage_anchor_node_id", frontier.prompt_schema["candidates"][0])
        self.assertIn("lineage_anchor_node_id", frontier.validation_schema["candidates"][0])

        base = {
            "hypothesis": "bounded test hypothesis",
            "scope": "fixed scientific scope",
            "why_now": "a discriminating check is feasible",
            "goal_advance": "advance G1",
            "decisive_test": "DT-lineage",
        }
        # The empty-graph root H remains valid without fabricated lineage.
        frontier.validate({"candidates": [dict(base)]}, {})

        known_context = {"_known_hypothesis_ids": ["H-0007", "H-0008", "H-0009", "H-0010"]}
        single_source = {
            "candidates": [{
                **base, "evolution_operator": "derive", "lineage_anchor_node_ids": ["H-0007"],
            }],
        }
        frontier.validate(single_source, known_context)

        multi_source = {
            "candidates": [{
                **base,
                "evolution_operator": "combine",
                "lineage_anchor_node_ids": ["H-0007", "H-0008"],
                "reconsideration_trigger": "new evidence changes the prior scope boundary",
            }],
        }
        frontier.validate(multi_source, known_context)

        # Existing checkpoint/candidate payloads with the legacy singular field still parse.
        legacy_single = {
            "candidates": [{
                **base, "evolution_operator": "derive", "lineage_anchor_node_id": "H-0007",
            }],
        }
        frontier.validate(json.loads(json.dumps(legacy_single)), known_context)

        # Cycles 186–191 repeatedly produced this exact singular+plural conflict shape.
        conflicting = {
            "candidates": [{
                **base,
                "evolution_operator": "combine",
                "lineage_anchor_node_id": "H-0007",
                "lineage_anchor_node_ids": ["H-0007", "H-0008"],
                "reconsideration_trigger": "new evidence changes the prior scope boundary",
            }],
        }
        for cycle in range(186, 192):
            with self.subTest(historical_cycle=cycle):
                with self.assertRaisesRegex(ValueError, "must not mix singular and plural lineage anchors"):
                    frontier.validate(conflicting, known_context)

        invalid_operator = {
            "candidates": [{
                **base, "evolution_operator": "invented", "lineage_anchor_node_ids": ["H-0007"],
            }],
        }
        with self.assertRaisesRegex(ValueError, "evolution_operator is invalid"):
            frontier.validate(invalid_operator, known_context)

        unknown_source = {
            "candidates": [{
                **base, "evolution_operator": "derive", "lineage_anchor_node_ids": ["H-9999"],
            }],
        }
        with self.assertRaisesRegex(ValueError, "existing SQLite H nodes: H-9999"):
            frontier.validate(unknown_source, known_context)

        duplicate_sources = {
            "candidates": [{
                **base, "evolution_operator": "combine", "lineage_anchor_node_ids": ["H-0007", "H-0007"],
            }],
        }
        with self.assertRaisesRegex(ValueError, "1..3 unique canonical H IDs"):
            frontier.validate(duplicate_sources, known_context)

        too_many_sources = {
            "candidates": [{
                **base, "evolution_operator": "combine",
                "lineage_anchor_node_ids": ["H-0007", "H-0008", "H-0009", "H-0010"],
            }],
        }
        with self.assertRaisesRegex(ValueError, "1..3 unique canonical H IDs"):
            frontier.validate(too_many_sources, known_context)

        noncanonical_source = {
            "candidates": [{
                **base, "evolution_operator": "derive", "lineage_anchor_node_ids": [" H-0007"],
            }],
        }
        with self.assertRaisesRegex(ValueError, "1..3 unique canonical H IDs"):
            frontier.validate(noncanonical_source, known_context)

        missing_operator = {"candidates": [{**base, "lineage_anchor_node_ids": ["H-0007"]}]}
        with self.assertRaisesRegex(ValueError, "lineage anchors require evolution_operator"):
            frontier.validate(missing_operator, known_context)

    def test_research_lead_minimal_qualification_contracts(self):
        frontier = role_contract("research_lead", phase="frontier_proposal")
        frontier_prompt = frontier.prompt_projection()
        self.assertIn("Use evolution_operator only from: refine | specialize | generalize | alternative | combine | derive | counter_hypothesis", frontier_prompt)
        self.assertIn("For a root H, omit all lineage fields", frontier_prompt)
        self.assertIn('"lineage_anchor_node_ids":', frontier_prompt)
        self.assertNotIn('"lineage_anchor_node_id":', frontier_prompt)
        self.assertIn("omit for root H", frontier_prompt)
        self.assertIn("The SDK owns H identity", frontier_prompt)
        proposal = {"candidates": [{"hypothesis": "h", "scope": "fixed scientific scope",
                                    "why_now": "w", "goal_advance": "advance G1",
                                    "decisive_test": "t"}]}
        frontier.validate(proposal, {})
        frontier.validate({**proposal, "needs_literature": False}, {})
        continuation = {"candidates": [{
            "hypothesis": "same H, stated with refined wording", "scope": "a refined in-scope boundary",
            "why_now": "a second bounded test is now feasible",
            "goal_advance": "advance the same H",
            "decisive_test": "DT-B",
        }]}
        # Same-H continuity and typed lineage are resolved against canonical SQLite; the Agent never authors H identity pointers.
        frontier.validate(continuation, {})
        derived = json.loads(json.dumps(continuation))
        derived["candidates"][0]["evolution_operator"] = "derive"
        derived["candidates"][0]["lineage_anchor_node_id"] = "H-0007"
        frontier.validate(derived, {"_known_hypothesis_ids": ["H-0007"]})
        multi_parent = json.loads(json.dumps(continuation))
        multi_parent["candidates"][0]["evolution_operator"] = "combine"
        multi_parent["candidates"][0]["lineage_anchor_node_ids"] = ["H-0007", "H-0008"]
        multi_parent["candidates"][0]["reconsideration_trigger"] = "new evidence changes the prior scope boundary"
        frontier.validate(multi_parent, {"_known_hypothesis_ids": ["H-0007", "H-0008"]})
        unknown_parent = json.loads(json.dumps(multi_parent))
        unknown_parent["candidates"][0]["lineage_anchor_node_ids"] = ["H-9999"]
        with self.assertRaisesRegex(ValueError, "existing SQLite H nodes: H-9999"):
            frontier.validate(unknown_parent, {"_known_hypothesis_ids": ["H-0007", "H-0008"]})
        old_pointer = json.loads(json.dumps(continuation))
        old_pointer["candidates"][0]["hypothesis_id"] = "H-0007"
        with self.assertRaisesRegex(ValueError, "SDK-owned fields: hypothesis_id"):
            frontier.validate(old_pointer, {})
        old_parent_pointer = json.loads(json.dumps(continuation))
        old_parent_pointer["candidates"][0]["parent_hypothesis_ids"] = ["H-0007"]
        with self.assertRaisesRegex(ValueError, "SDK-owned fields: parent_hypothesis_ids"):
            frontier.validate(old_parent_pointer, {})
        obsolete_relation = json.loads(json.dumps(proposal))
        obsolete_relation["candidates"][0]["hypothesis_relation"] = {"kind": "new"}
        with self.assertRaisesRegex(ValueError, "unsupported fields: hypothesis_relation"):
            frontier.validate(obsolete_relation, {})
        with self.assertRaisesRegex(ValueError, "SDK-owned"):
            bad = json.loads(json.dumps(proposal))
            bad["candidates"][0]["id"] = "c1"
            frontier.validate(bad, {})
        for marked in ("[RECOMMENDED] h", "Recommended: h", "推荐：h"):
            bad = json.loads(json.dumps(proposal))
            bad["candidates"][0]["hypothesis"] = marked
            with self.assertRaisesRegex(ValueError, "workflow recommendation/ranking markers"):
                frontier.validate(bad, {})

        legacy = {
            "recommended_candidate_id": "cand-old",
            "candidates": [{
                "id": "cand-old", "hypothesis": "legacy h", "why_now": "legacy why",
                "goal_advance": "legacy goal", "decisive_test": "legacy test",
                "evidence_requirements": [{"kind": "numerical_evidence", "requirement": "legacy plan"}],
            }],
        }
        # Old ER-shaped output is invalid; there is no compatibility coercion.
        with self.assertRaisesRegex(ValueError, "SDK-owned"):
            frontier.validate(legacy, {})

        closed = close_agent_output("research_lead", "frontier_proposal", proposal, {}, {})
        self.assertTrue(closed["candidates"][0]["id"].startswith("cand-"))
        self.assertNotIn("evidence_requirements", closed["candidates"][0])

        selection = role_contract("research_lead", phase="candidate_selection")
        selection_context = {"candidates": [{"id": "c1"}, {"id": "c2"}]}
        selection_output = {
            "selected_candidate_id": "c2",
            "selection_reason": "c2 has the stronger local discriminator",
        }
        selection.validate(selection_output, selection_context)
        closed_selection = close_agent_output(
            "research_lead", "candidate_selection", selection_output, selection_context, {},
        )
        self.assertEqual(closed_selection, {
            "selected_candidate_id": "c2",
            "decision": "c2 has the stronger local discriminator",
        })
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            selection.validate({"selected_candidate_id": "c2", "decision": "legacy duplicate test prose"}, selection_context)

        challenger = role_contract("challenger", phase="candidate_review")
        challenger.validate({"reviews": [{"candidate_id": "c1", "verdict": "revise", "reason": "too broad",
                              "blocking_issues": [{"problem": "two questions are coupled",
                                                   "required_change": "keep only the first question"}]}]},
                            {"candidates": [{"id": "c1"}]})
        with self.assertRaisesRegex(ValueError, "exactly one review"):
            challenger.validate({"reviews": [{"candidate_id": "c1", "verdict": "qualified", "reason": "ok"}]},
                                {"candidates": [{"id": "c1"}, {"id": "c2"}]})
        with self.assertRaisesRegex(ValueError, "candidate_id"):
            challenger.validate({"reviews": [{"candidate_id": "unknown", "verdict": "qualified", "reason": "ok"}]},
                                {"candidates": [{"id": "c1"}]})

        repair = role_contract("research_lead", phase="selection_repair")
        repair_output = {"outcome": "resolved", "repair": {
            "hypothesis_clarification": "Define the already-promised term without changing the claim.",
            "repaired_decisive_test": "t2",
            "estimand_or_operational_definition": "Use the parent estimand with an explicit observable definition.",
            "blocker_closures": [{"blocker_id": "c1:block-1", "resolution": "define it inside the same claim"}],
        }}
        parent = {
            "id": "c1", "title": "h", "hypothesis": "h", "why_now": "w",
            "goal_advance": "preserve G1", "decisive_test": "t1", "scope": "frozen scope",
            "evolution_operator": "derive",
            "rationale": "w", "evidence_refs": [], "status": "provisional",
        }
        repair_context = {
            "candidates": [parent],
            "candidate_reviews": [{"candidate_id": "c1", "verdict": "revise", "reason": "under-defined",
                                  "blocking_issues": [{"id": "c1:block-1", "problem": "term undefined",
                                                       "required_change": "define it inside the same claim"}]}],
            "qualification_error": {"verdict": "revise", "blocking_issues": [
                {"id": "c1:block-1", "problem": "term undefined", "required_change": "define it"}]},
        }
        repair.validate(repair_output, repair_context)
        attempted_rewrite = json.loads(json.dumps(repair_output))
        attempted_rewrite["hypothesis"] = "different hypothesis"
        with self.assertRaisesRegex(ValueError, "may not rewrite frozen candidate fields"):
            repair.validate(attempted_rewrite, repair_context)
        repaired = close_agent_output("research_lead", "selection_repair", repair_output, repair_context, {})
        item = repaired["replacement_candidates"][0]
        self.assertEqual(item["addressed_issue_ids"], ["c1:block-1"])
        self.assertTrue(item["id"].startswith("cand-"))
        self.assertEqual(item["hypothesis"], parent["hypothesis"])
        self.assertEqual(item["goal_advance"], parent["goal_advance"])
        self.assertEqual(item["scope"], parent["scope"])
        self.assertNotIn("hypothesis_id", item)
        self.assertNotIn("parent_hypothesis_ids", item)
        self.assertNotIn("hypothesis_relation", item)
        self.assertEqual(item["evolution_operator"], parent["evolution_operator"])
        self.assertTrue(item["decisive_test"].startswith("t2"))
        self.assertIn(repair_output["repair"]["hypothesis_clarification"], item["decisive_test"])
        self.assertIn(repair_output["repair"]["estimand_or_operational_definition"], item["decisive_test"])
        already_complete = json.loads(json.dumps(repair_output))
        already_complete["repair"]["repaired_decisive_test"] = item["decisive_test"]
        second = close_agent_output("research_lead", "selection_repair", already_complete, repair_context, {})
        self.assertEqual(second["replacement_candidates"][0]["decisive_test"], item["decisive_test"])
        self.assertEqual(second["replacement_candidates"][0]["id"], item["id"])
        inherited_output = {"outcome": "resolved", "repair": {
            "blocker_closures": [{"blocker_id": "c1:block-1", "resolution": "define it inside the same claim"}],
        }}
        with self.assertRaisesRegex(ValueError, "repaired_decisive_test"):
            repair.validate(inherited_output, repair_context)
        with self.assertRaisesRegex(ValueError, "revised frozen decisive_test"):
            close_agent_output("research_lead", "selection_repair", inherited_output, repair_context, {})
        unchanged = json.loads(json.dumps(repair_output))
        unchanged["repair"]["repaired_decisive_test"] = parent["decisive_test"]
        with self.assertRaisesRegex(ValueError, "materially update"):
            repair.validate(unchanged, repair_context)
        cannot = {"outcome": "cannot_repair", "cannot_repair_reason": "no consequential test remains inside the frozen scope"}
        repair.validate(cannot, repair_context)
        closed_cannot = close_agent_output("research_lead", "selection_repair", cannot, repair_context, {})
        self.assertEqual(closed_cannot["outcome"], "cannot_repair")
        self.assertEqual(closed_cannot["selected_parent_candidate_id"], "c1")
        with self.assertRaisesRegex(ValueError, "close every frozen Challenger blocker"):
            missing_closure = json.loads(json.dumps(repair_output))
            missing_closure["repair"]["blocker_closures"] = []
            repair.validate(missing_closure, repair_context)
        with self.assertRaisesRegex(ValueError, "only for a Challenger revise"):
            repair.validate(repair_output, {"qualification_error": {"verdict": "reject"}})

    def test_reporter_summary_rejects_machine_payload_dumps(self):
        contract = role_contract("reporter", phase="report")
        contract.validate({"title": "Readable result", "summary": "## 结论\n\narm=4 increases beta1 under the frozen comparison."}, {})
        with self.assertRaisesRegex(ValueError, "human-first prose"):
            contract.validate({"title": "Bad report", "summary": "## ModelPatch JSON\n\n```json\n{\"status\":\"supported\"}\n```"}, {})
        with self.assertRaisesRegex(ValueError, "human-first prose"):
            contract.validate({"title": "Bad report", "summary": "## Candidate Review Delivery\n\nraw workflow payload"}, {})

    def test_reporter_is_narrative_only_and_canvas_phase_is_not_registered(self):
        contract = role_contract("reporter", phase="report")
        self.assertEqual(set(contract.prompt_schema), {"title", "summary"})
        contract.validate({"title": "Readable result", "summary": "Bounded result within the frozen scope."}, {})
        with self.assertRaisesRegex(ValueError, "no phase contract"):
            role_contract("reporter", phase="canvas_maintenance")

    def test_judge_rework_requires_one_minimal_objection_and_sdk_derives_routing(self):
        contract = role_contract("judge", phase="terminal_assessment")
        context = {
            "branch": {"cycle_criteria": [{"id": "C-1", "criterion": "decisive test"}]},
            "investigation_result": {"findings": [{"statement": "bounded result"}]},
        }
        internal = {
            "requested_resolution": "run one controlled check",
            "needs_external_scholarship": False,
        }
        contract.validate({"verdict": None, "rationale": "one check remains", "objection": internal}, context)
        closed = close_agent_output(
            "judge", "terminal_assessment",
            {"verdict": None, "rationale": "one check remains", "objection": internal},
            context, {},
        )
        self.assertEqual(closed["objections"][0]["criterion_id"], "C-1")
        self.assertEqual(closed["objections"][0]["reason"], "one check remains")
        self.assertEqual(closed["objections"][0]["executor_role"], "investigator")
        self.assertEqual(closed["rework_plan"]["target_executor"], "investigator")

        external = {
            "requested_resolution": "verify the primary scholarly definition",
            "needs_external_scholarship": True,
        }
        external_closed = close_agent_output(
            "judge", "terminal_assessment",
            {"verdict": None, "rationale": "primary literature is scientifically necessary", "objection": external},
            context, {},
        )
        self.assertEqual(external_closed["objections"][0]["executor_role"], "literature")
        self.assertTrue(external_closed["objections"][0]["source_gap_id"].startswith("external-gap-"))

        contract.validate({"verdict": "rejected", "rationale": "the decisive result contradicts H", "objection": None}, context)
        contract.validate({"verdict": "open", "rationale": "the result is inconclusive", "objection": None}, context)
        with self.assertRaisesRegex(ValueError, "requires exactly one objection object"):
            contract.validate({"verdict": None, "rationale": "gap", "objection": None}, context)
        with self.assertRaisesRegex(ValueError, "needs_external_scholarship must be boolean"):
            contract.validate({"verdict": None, "rationale": "gap", "objection": {"requested_resolution": "x"}}, context)
        with self.assertRaisesRegex(ValueError, "SDK-owned/obsolete fields"):
            contract.validate({
                "verdict": None,
                "rationale": "legacy v6 shape",
                "objection": {**internal, "criterion_id": "C-1", "scientific_gap": {"kind": "numerical"}},
            }, context)

    def test_literature_evidence_quote_is_contract_boundary(self):
        contract = role_contract("literature", phase="targeted_source")
        output = {
            "evidence_records": [{
                "source_ref": "paper:0",
                "scientific_insight": "secondary summary only",
                "exact_quote": "",
                "locator": {"page": "", "section": "II", "figure": "", "equation": "", "paragraph": ""},
                "interpretation": "cannot establish equivalence",
                "limitations": "not an original paper", "source_access": "abstract",
            }],
            "remaining_scientific_question": "",
        }
        context = {"literature_task": {
            "gap_id": "scanlan-source", "task_type": "targeted_source", "triggered_by": "investigator",
            "search_target": "Scanlan–Case original source", "why_external_needed": "local note has no primary citation",
            "expected_external_evidence": "primary source quote and locator",
        }}
        with self.assertRaisesRegex(ValueError, "exact_quote"):
            contract.validate(output, context)
        output["evidence_records"][0]["exact_quote"] = "有效 junction 至少有三条独立路径连接到贯通网络。"
        contract.validate(output, context)
        sdk_owned = json.loads(json.dumps(output))
        sdk_owned["evidence_records"][0]["criterion_ids"] = ["C-1"]
        with self.assertRaisesRegex(ValueError, "SDK-owned"):
            contract.validate(sdk_owned, context)
        obsolete = json.loads(json.dumps(output))
        obsolete["criterion_assessments"] = [{"criterion_id": "M-S1", "status": "resolved", "reason": "legacy"}]
        with self.assertRaisesRegex(ValueError, "Literature semantic-state fields are obsolete"):
            contract.validate(obsolete, context)

    def test_unverified_literature_retrieval_does_not_require_fake_quote_or_locator(self):
        contract = role_contract("literature", phase="targeted_source")
        context = {"literature_task": {
            "gap_id": "ER-1", "source_gap_id": "ER-1", "task_type": "targeted_source",
            "triggered_by": "judge", "search_target": "original Scanlan–Case source",
            "why_external_needed": "full text could not be verified locally",
            "expected_external_evidence": "primary-source definition and applicability boundary",
        }}
        output = {
            "evidence_records": [{
                "source_ref": "https://doi.org/10.1002/example",
                "scientific_insight": "metadata found; full text unavailable",
                "exact_quote": "", "locator": {},
                "interpretation": "do not use as claim evidence",
                "limitations": "no verified full text", "source_access": "unavailable",
            }],
            "remaining_scientific_question": "interpret the unresolved source boundary",
        }
        contract.validate(output, context)

    def test_literature_closure_links_evidence_to_judge_objection_without_status_authority(self):
        context = {"literature_task": {
            "gap_id": "ER-J1", "source_gap_id": "ER-J1", "objection_id": "J-0001",
            "task_type": "targeted_source", "triggered_by": "judge",
        }}
        output = {
            "evidence_records": [{
                "source_ref": "10.1002/example", "criterion_ids": ["C-1"],
                "scientific_insight": "the source addresses the frozen definition",
                "relation": "limits_scope", "exact_quote": "bounded source statement",
                "locator": {"section": "2"}, "interpretation": "relevant to the objection",
                "limitations": "one scoped source", "source_access": "full_text",
            }],
            "remaining_scientific_question": "",
        }
        closed = close_agent_output("literature", "targeted_source", output, context, {})
        self.assertNotIn("criterion_updates", closed)
        self.assertNotIn("objection_updates", closed)
        self.assertEqual(closed["evidence_records"][0]["objection_ids"], ["J-0001"])
        self.assertIsNone(closed["requested_next_executor"])

    def test_investigator_does_not_route_its_own_remaining_gap(self):
        contract = role_contract("investigator", phase="investigate")
        output = {"findings": []}
        contract.validate(output, {})
        self.assertEqual(close_agent_output("investigator", "investigate", output, {}, {}),
                         {"evidence": []})
        for obsolete in ("remaining_scientific_question", "gap_progress", "requested_next_executor"):
            bad = {**output, obsolete: "must continue"}
            with self.assertRaisesRegex(ValueError, "workflow routing fields"):
                contract.validate(bad, {})

    def test_investigator_finding_forbids_agent_authored_artifact_paths(self):
        contract = role_contract("investigator", phase="investigate")
        output = {
            "findings": [{
                "statement": "checked numerical result in the specified benchmark domain (fixture only)",
            }],
        }
        contract.validate(output, {})
        for field, value in (("artifacts", ["result.json"]),
                             ("artifact_roles", [{"path": "result.json", "role": "result"}]),
                             ("path", "result.json")):
            invalid = json.loads(json.dumps(output))
            invalid["findings"][0][field] = value
            with self.assertRaisesRegex(ValueError, "SDK-owned artifact fields"):
                contract.validate(invalid, {})

    def test_research_lead_qualification_cannot_plan_evidence_or_literature_routing(self):
        contract = role_contract("research_lead", phase="frontier_proposal")
        output = {"candidates": [{"hypothesis": "h", "scope": "fixed scientific scope",
                                   "why_now": "new field",
                                   "goal_advance": "advance the scientific question",
                                   "decisive_test": "a discriminating observation"}]}
        contract.validate(output, {})
        broken = json.loads(json.dumps(output))
        broken["candidates"][0]["required_evidence"] = ["map the literature"]
        with self.assertRaisesRegex(ValueError, "SDK-owned"):
            contract.validate(broken, {})

    def test_landscape_contract_separates_field_map_from_claim_level_evidence(self):
        contract = role_contract("literature", phase="landscape")
        context = {"literature_task": {
            "gap_id": "ER-L1", "task_type": "landscape", "triggered_by": "research_lead",
            "search_target": "effective polymer-network backbones", "why_external_needed": "new external field",
            "expected_external_evidence": "research landscape and representative papers",
        }}
        output = {
            "evidence_records": [],
            "remaining_scientific_question": "",
            "landscape": {
                "field_scope": "effective polymer-network backbones", "major_routes": [],
                "representative_papers": [{"source_ref": "paper:0", "relevance": "method map",
                                            "source_access": "metadata"}],
                "open_questions": ["periodic-network criterion"],
                "relation_to_current_research": "identifies external definitions to test",
                "recommended_reading_order": ["Representative paper"],
            },
        }
        contract.validate(output, context)

    def test_cycle_synthesis_rejects_invalid_evolution_operator_at_contract_boundary(self):
        contract = role_contract("research_lead", phase="cycle_synthesis")
        output = {
            "scientific_synthesis": {"conclusions": [], "open_questions": []},
            "scientific_model_update": {"action": "keep"},
            "evolution_candidates": [{
                "title": "Independent replication", "hypothesis": "H", "evolution_operator": "replicate",
                "rationale": "repeat on an independent batch",
                "goal_advance": "advance the replication dependency before the downstream decision",
            }],
            "next_frontier_index": 0,
        }
        with self.assertRaisesRegex(ValueError, "no longer owns frontier planning"):
            contract.validate(output, {})
        output.pop("evolution_candidates")
        output.pop("next_frontier_index")
        contract.validate(output, {})

    def test_challenger_prompt_and_validator_share_reviews_schema(self):
        contract = role_contract("challenger", phase="candidate_review")
        self.assertEqual(contract.version, 5)
        self.assertIn("candidate_id", contract.prompt_schema["reviews"][0])
        context = {"candidates": [{"id": "cand-1"}]}
        output = {"reviews": [{"candidate_id": "cand-1", "verdict": "qualified",
                               "reason": "atomic and executable"}]}
        contract.validate(output, context)
        with self.assertRaisesRegex(ValueError, "candidate_id"):
            contract.validate({"reviews": [{"verdict": "qualified", "reason": "missing identity"}]}, context)
        closed = close_agent_output("challenger", "candidate_review", output, context, {})
        self.assertEqual(closed["reviews"][0]["candidate_id"], "cand-1")

    def test_challenger_review_count_and_identity_are_authoritative(self):
        contract = role_contract("challenger", phase="candidate_review")
        context = {"candidates": [{"id": "cand-1"}, {"id": "cand-2"}]}
        a = {"candidate_id": "cand-1", "verdict": "qualified", "reason": "atomic"}
        b = {"candidate_id": "cand-2", "verdict": "reject", "reason": "invalid"}
        with self.assertRaisesRegex(ValueError, "exactly one review"):
            contract.validate({"reviews": [a]}, context)
        contract.validate({"reviews": [b, a]}, context)
        closed = close_agent_output("challenger", "candidate_review",
                                    {"reviews": [b, a]}, context, {})
        self.assertEqual([(item["candidate_id"], item["reason"]) for item in closed["reviews"]],
                         [("cand-1","atomic"), ("cand-2","invalid")])
        with self.assertRaisesRegex(ValueError, "candidate_id"):
            contract.validate({"reviews": [a,dict(a)]}, context)

    def test_cycle_0002_style_legacy_challenger_shape_fails_at_contract_boundary(self):
        class LegacyChallengerBackend(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                thread, raw = super().invoke(role, prompt, thread_id, model, provider)
                if role == "challenger":
                    raw = json.dumps({"critical_issues": [{"id": "c1", "issue": "legacy shape"}]})
                return thread, raw

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = root / "research_plan.md"
            plan.write_text(
                "# Goal\ng\n\n# Research Contract\nc\n\n# Final Gate\nf\n\n# Required Context\n- r\n",
                encoding="utf-8",
            )
            from theory_master.task import bootstrap
            binding = bootstrap(plan)
            cfg = json.loads(binding.config_path.read_text(encoding="utf-8"))
            cfg["max_cycles"] = 1
            binding.config_path.write_text(json.dumps(cfg), encoding="utf-8")
            state = Workflow(TaskSpec.from_config(binding.config_path), LegacyChallengerBackend(reworks=0)).run()
            self.assertEqual(state["status"], "needs_attention")
            self.assertEqual(state["closure_reason"], "contract_repair_failed")
            self.assertEqual(state["contract_repair_count"], 3)

    def test_workflow_prompt_exposes_registered_challenger_contract(self):
        class CaptureBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.challenger_prompt = ""

            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "challenger":
                    self.challenger_prompt = prompt
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = root / "research_plan.md"
            plan.write_text(
                "# Goal\ng\n\n# Research Contract\nc\n\n# Final Gate\nf\n\n# Required Context\n- r\n",
                encoding="utf-8",
            )
            from theory_master.task import bootstrap
            binding = bootstrap(plan)
            cfg = json.loads(binding.config_path.read_text(encoding="utf-8"))
            cfg["max_cycles"] = 1
            binding.config_path.write_text(json.dumps(cfg), encoding="utf-8")
            backend = CaptureBackend()
            Workflow(TaskSpec.from_config(binding.config_path), backend).run()
            self.assertIn("Output contract: theory-master/role/challenger/candidate-review/v5", backend.challenger_prompt)
            self.assertIn('exact stage contract: {"reviews":[{"candidate_id":"exact SDK-provided candidate ID', backend.challenger_prompt)
            self.assertIn('"candidate_id":', backend.challenger_prompt)


    def test_active_stage_contracts_reject_undeclared_fields_but_keep_free_form_results(self):
        reporter = role_contract("reporter", phase="report")
        with self.assertRaisesRegex(ValueError, "unsupported fields: frontier_status"):
            reporter.validate({"title": "T", "summary": "Readable prose", "frontier_status": "closed"}, {})

        synthesis = role_contract("research_lead", phase="cycle_synthesis")
        valid_synthesis = {
            "scientific_synthesis": {"conclusions": [], "open_questions": []},
            "scientific_model_update": {"action": "keep"},
        }
        with self.assertRaisesRegex(ValueError, "unsupported fields: frontier_status"):
            synthesis.validate({**valid_synthesis, "frontier_status": "closed"}, {})

        challenger = role_contract("challenger", phase="candidate_review")
        with self.assertRaisesRegex(ValueError, "unsupported fields: future_field"):
            challenger.validate({"reviews": [{
                "candidate_id": "cand-1", "verdict": "qualified", "reason": "atomic", "future_field": "x",
            }]}, {"candidates": [{"id": "cand-1"}]})

        investigator = role_contract("investigator", phase="investigate")
        investigator.validate({"findings": [{
            "statement": "A scoped result", "result": {"nested": [1, 2], "exact": True},
        }]}, {})
        with self.assertRaisesRegex(ValueError, "unsupported fields: future_field"):
            investigator.validate({"findings": [{"statement": "A scoped result", "future_field": "x"}]}, {})

    def test_literature_nested_enums_and_objects_are_closed(self):
        novelty = role_contract("literature", phase="novelty_check")
        context = {"literature_task": {"task_type": "novelty_check"}}
        valid = {
            "evidence_records": [], "remaining_scientific_question": "",
            "positioning": {
                "assessment": "apparently_distinct", "closest_prior_work": [],
                "priority_risks": [], "required_citations": [],
            },
        }
        novelty.validate(valid, context)
        invalid = json.loads(json.dumps(valid))
        invalid["positioning"]["assessment"] = "definitely_newish_invalid_enum"
        with self.assertRaisesRegex(ValueError, "positioning.assessment must be"):
            novelty.validate(invalid, context)
        extra_nested = json.loads(json.dumps(valid))
        extra_nested["positioning"]["unregistered_note"] = "not part of the contract"
        with self.assertRaisesRegex(ValueError, "unsupported fields: unregistered_note"):
            novelty.validate(extra_nested, context)

        landscape = role_contract("literature", phase="landscape")
        landscape_context = {"literature_task": {"task_type": "landscape"}}
        landscape_output = {
            "evidence_records": [], "remaining_scientific_question": "",
            "landscape": {
                "field_scope": "field", "major_routes": [],
                "representative_papers": [{"source_ref": "paper:0", "relevance": "map", "source_access": "invalid"}],
                "open_questions": [], "relation_to_current_research": "relation", "recommended_reading_order": [],
            },
        }
        with self.assertRaisesRegex(ValueError, "source_access must be metadata\\|abstract\\|full_text"):
            landscape.validate(landscape_output, landscape_context)

    def test_investigator_provenance_enums_and_declaration_boundaries_are_validated(self):
        contract = role_contract("investigator", phase="investigate")
        valid = {"findings": [{"statement": "A result", "provenance": {
            "source": "benchmark_provided", "split": "holdout",
            "declaration_ref": "benchmark_manifest.json", "frozen_before_current_cycle": True,
        }}]}
        contract.validate(valid, {})
        invalid_cases = [
            {"source": "made_up", "split": "fit"},
            {"source": "agent_generated", "split": "blindish"},
            {"source": "agent_generated", "split": "fit", "declaration_ref": 7},
            {"source": "agent_generated", "split": "fit", "frozen_before_current_cycle": "true"},
            {"source": "pre_specified_synthetic", "split": "fit"},
        ]
        for provenance in invalid_cases:
            with self.subTest(provenance=provenance), self.assertRaises(ValueError):
                contract.validate({"findings": [{"statement": "A result", "provenance": provenance}]}, {})

if __name__ == "__main__":
    unittest.main()
