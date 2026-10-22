from __future__ import annotations

import unittest
from unittest.mock import Mock

from theory_master.abc_pipeline import (
    DeterministicSemanticBackend,
    FallbackSemanticBackend,
    annotate_literature,
    annotate_specialist,
)


class ABCPipelineTest(unittest.TestCase):
    def test_semantic_annotator_identity_and_skill_boundary_is_registered(self):
        from theory_master.skills import developer_instructions
        text = developer_instructions("semantic_annotator", cycle_id="cycle-0001")
        self.assertIn("Semantic Annotator", text)
        self.assertIn("non-scientific B-layer semantic annotator", text)
        self.assertIn("Optional skills for role semantic_annotator: none", text)

    def test_agent_b_uses_registered_auxiliary_semantic_role(self):
        backend = Mock()
        backend.ensure_thread.return_value = "semantic-thread"
        backend.invoke.return_value = ("semantic-thread", '{"answers":{"q":"x"}}')
        from theory_master.abc_pipeline import AgentSemanticBackend
        semantic = AgentSemanticBackend(backend, model="gpt-6-luna", provider="CodexHTTPS")
        result = semantic.decide(task="t", state={"x": 1}, questions=[{"id": "q", "instruction": "pick", "options": ["x", "y"]}])
        self.assertEqual(result["answers"], {"q": "x"})
        backend.ensure_thread.assert_called_once_with("semantic_annotator", None, "gpt-6-luna", "CodexHTTPS")
        backend.invoke.assert_called_once()

    def test_agent_b_retries_completed_missing_final_once_in_a_fresh_thread(self):
        backend = Mock()
        backend.ensure_thread.side_effect = ["semantic-thread-1", "semantic-thread-2"]
        backend.invoke.side_effect = [
            RuntimeError("Codex backend delivery error: turn completed without assistant final response"),
            ("semantic-thread-2", '{"answers":{"q":"x"}}'),
        ]
        from theory_master.abc_pipeline import AgentSemanticBackend
        semantic = AgentSemanticBackend(backend, model="gpt-6-luna", provider="CodexHTTPS")
        result = semantic.decide(task="t", state={}, questions=[{"id": "q", "options": ["x", "y"]}])
        self.assertEqual(result["answers"], {"q": "x"})
        self.assertEqual(backend.ensure_thread.call_count, 2)
        self.assertEqual(backend.invoke.call_count, 2)

    def test_uncertain_primary_escalates_without_losing_audit(self):
        class Uncertain:
            name = "jev"
            def decide(self, **_kwargs):
                return {"answers": {"q": "x"}, "confidence": {"q": 0.4}, "uncertain": ["q"], "backend": "jev"}
        class Fallback:
            name = "agent"
            def decide(self, **_kwargs):
                return {"answers": {"q": "y"}, "confidence": {}, "backend": "agent"}
        result = FallbackSemanticBackend(Uncertain(), Fallback()).decide(
            task="t", state={}, questions=[{"id": "q", "options": ["x", "y"]}]
        )
        self.assertEqual(result["answers"]["q"], "y")
        self.assertTrue(result["escalated"])
        self.assertEqual(result["primary_decisions"]["uncertain"], ["q"])

    def test_lead_qualification_no_longer_uses_requirement_classification_b_layer(self):
        from theory_master import abc_pipeline
        self.assertFalse(hasattr(abc_pipeline, "annotate_requirements"))
        self.assertFalse(hasattr(abc_pipeline, "_requirement_questions"))

    def test_specialist_b_maps_links_without_scientific_status(self):
        artifact = {"findings": [{"statement": "x increases", "scope": "frozen ensemble",
                                    "limitations": "one regime", "artifacts": []}],
                    "remaining_scientific_question": ""}
        context = {"cycle_progress_criteria": {"success": [{"id": "M-S1", "criterion": "x increases"}]},
                   "judge_objections": [{"objection_id": "J-1", "reason": "x increases must be established",
                                          "requested_resolution": "provide evidence that x increases"}]}
        closed, audit = annotate_specialist(artifact, context, DeterministicSemanticBackend())
        evidence = closed["evidence"][0]
        self.assertEqual(evidence["local_ref"], "finding:0")
        self.assertEqual(evidence["criterion_ids"], ["M-S1"])
        self.assertEqual(evidence["objection_ids"], ["J-1"])
        self.assertNotIn("criterion_assessments", closed)
        self.assertNotIn("objection_responses", closed)
        question_ids = set(audit["semantic_decisions"]["answers"])
        self.assertFalse(any(qid.startswith("criterion:") for qid in question_ids))
        self.assertFalse(any(qid.startswith("objection:") for qid in question_ids))
        self.assertIn("finding:0/criterion:M-S1", question_ids)
        self.assertIn("finding:0/objection:J-1", question_ids)
        self.assertNotIn("relation", artifact["findings"][0])
        self.assertEqual(audit["local_objects"][0]["ref"], "finding:0")

    def test_literature_b_binds_relation_and_criterion_without_agent_echo(self):
        artifact = {
            "evidence_records": [{
                "source_ref": "paper:0",
                "scientific_insight": "The source states that x increases under the frozen conditions.",
                "interpretation": "directly relevant to the current claim",
                "limitations": "one source",
                "source_access": "full_text",
                "exact_quote": "x increases",
                "locator": {"section": "Results"},
            }],
            "remaining_scientific_question": "",
        }
        context = {
            "hypothesis_lock": {"hypothesis": "x increases", "cycle_criteria": [{"id": "C-1", "criterion": "x increases"}]},
            "cycle_criteria": [{"id": "C-1", "criterion": "x increases"}],
        }
        bound, audit = annotate_literature(artifact, context, DeterministicSemanticBackend())
        record = bound["evidence_records"][0]
        self.assertEqual(record["relation"], "supports")
        self.assertEqual(record["criterion_ids"], ["C-1"])
        self.assertNotIn("criterion_ids", artifact["evidence_records"][0])
        self.assertNotIn("relation", artifact["evidence_records"][0])
        self.assertEqual(audit["local_objects"][0]["statement"], artifact["evidence_records"][0]["scientific_insight"])

    def test_contradicting_finding_binds_the_single_cycle_criterion(self):
        class ContradictionBackend:
            name = "contradiction"

            def decide(self, *, task, state, questions):
                answers = {}
                for question in questions:
                    qid = question["id"]
                    if qid.endswith("/relation"):
                        answers[qid] = "contradicts"
                    elif "/criterion:C-1" in qid:
                        answers[qid] = "related"
                return {"answers": answers, "backend": self.name}

        artifact = {
            "findings": [{
                "statement": "The observed result contradicts the locked prediction.",
                "scope": "frozen scope", "limitations": "none",
                "evidence_kind": "numerical",
            }],
            "remaining_scientific_question": "",
        }
        context = {"cycle_criteria": [{"id": "C-1", "criterion": "test the locked prediction"}]}
        closed, _ = annotate_specialist(artifact, context, ContradictionBackend())
        self.assertEqual(closed["evidence"][0]["criterion_ids"], ["C-1"])
        self.assertEqual(closed["evidence"][0]["relation"], "contradicts")

    def test_empty_a_question_deterministically_closes_without_b_gap_question(self):
        class NoCallBackend:
            name = "must-not-be-called"

            def decide(self, *, task, state, questions):
                raise AssertionError("empty submission must not ask B to invent a gap")

        closed, audit = annotate_specialist(
            {"findings": []}, {"cycle_progress_criteria": {"success": [], "failure": []}}, NoCallBackend(),
        )
        self.assertEqual(closed, {"evidence": []})
        self.assertEqual(audit["semantic_decisions"]["backend"], "deterministic_noop")

    def test_b_leaves_external_and_literature_limitations_for_judge(self):
        context = {"cycle_progress_criteria": {"success": [{"id": "M-S1", "criterion": "verify provenance"}]}}
        for limitation in ("external record unknown", "primary paper needed"):
            artifact = {"findings": [{
                "statement": "provenance mapping not identified",
                "scope": "benchmark", "limitations": limitation,
            }]}
            closed, audit = annotate_specialist(artifact, context, DeterministicSemanticBackend())
            self.assertEqual(closed["evidence"][0]["limitations"], limitation)
            self.assertNotIn("remaining_gap", closed)
            self.assertNotIn("gap_progress", closed)
            self.assertNotIn("remaining_gap", audit["semantic_decisions"].get("answers", {}))

    def test_b_does_not_route_local_file_work_after_investigator_submission(self):
        artifact = {"findings": [{
            "statement": "descriptor collisions were observed", "scope": "supplied benchmark",
            "limitations": "row overlap not yet audited",
        }]}
        closed, _ = annotate_specialist(artifact, {}, DeterministicSemanticBackend())
        self.assertEqual(len(closed["evidence"]), 1)
        self.assertEqual(closed["evidence"][0]["limitations"], "row overlap not yet audited")
        self.assertNotIn("remaining_gap", closed)
        self.assertNotIn("requested_next_executor", closed)

    def test_investigator_semantic_closure_does_not_create_artifact_bookkeeping(self):
        artifact = {
            "findings": [{
                "statement": "computed output", "scope": "benchmark", "limitations": "none",
                "evidence_kind": "numerical",
            }],
            "remaining_scientific_question": "",
        }
        closed, _ = annotate_specialist(artifact, {}, DeterministicSemanticBackend())
        evidence = closed["evidence"][0]
        self.assertNotIn("artifacts", evidence)
        self.assertNotIn("artifact_pointers", evidence)
        self.assertNotIn("artifact_roles", evidence)

    def test_missing_test_labels_are_limitations_not_b_routing(self):
        artifact = {"findings": [{
            "statement": "The prediction set contains no outcome labels.",
            "scope": "supplied test.csv", "limitations": "test accuracy cannot be computed",
        }]}
        closed, _ = annotate_specialist(artifact, {}, DeterministicSemanticBackend())
        self.assertEqual(closed["evidence"][0]["limitations"], "test accuracy cannot be computed")
        self.assertNotIn("remaining_gap", closed)

    def test_no_findings_cannot_manufacture_self_continuation(self):
        closed, _ = annotate_specialist({"findings": []}, {}, DeterministicSemanticBackend())
        self.assertEqual(closed, {"evidence": []})

    def test_legacy_repeated_gap_history_is_not_scientific_authority(self):
        artifact = {"findings": [{"statement": "composition check completed",
                                  "scope": "rows", "limitations": "not unique"}],
                    "remaining_scientific_question": "The scaffold assignment convention remains unspecified."}
        context = {"recent_gap_history": [{"role": "numerical", "round": 1,
                                           "gap": {"kind": "theoretical", "description": "The scaffold assignment convention remains unspecified."}}]}
        closed, audit = annotate_specialist(artifact, context, DeterministicSemanticBackend())
        self.assertNotIn("gap_progress", closed)
        self.assertNotIn("gap_progress", audit["semantic_decisions"].get("answers", {}))
        self.assertEqual(len(closed["evidence"]), 1)

    def test_legacy_refined_gap_is_not_invented_as_continuation(self):
        artifact = {"findings": [{"statement": "graph descriptors expose 67 ambiguous rows",
                                  "scope": "all rows", "limitations": "provenance absent"}],
                    "remaining_scientific_question": "Compute a source-backed crosswalk."}
        closed, audit = annotate_specialist(artifact, {}, DeterministicSemanticBackend())
        self.assertNotIn("gap_progress", closed)
        self.assertNotIn("remaining_gap", closed)
        self.assertNotIn("gap_progress", audit["semantic_decisions"].get("answers", {}))
        self.assertEqual(closed["evidence"][0]["limitations"], "provenance absent")



if __name__ == "__main__":
    unittest.main()
