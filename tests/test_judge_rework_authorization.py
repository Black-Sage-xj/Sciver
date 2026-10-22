from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from theory_master.core import FakeBackend, TaskSpec, Workflow
from theory_master.contract_stages import role_contract


class JudgeReworkAuthorizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "cycle-0001" / "workspace"
        self.workspace.mkdir(parents=True)
        self.workflow = Workflow.__new__(Workflow)
        self.workflow.backend = FakeBackend(reworks=0)
        self.workflow.task_root = self.workspace
        self.workflow.spec = SimpleNamespace(research_root=Path(self.temp.name), task_mode=True)
        self.state = {
            "hypothesis_lock": {
                "cycle_progress_criteria": {
                    "success": [{"id": "M-S1", "criterion": "test"}],
                    "failure": [{"id": "M-F1", "criterion": "failure"}],
                }
            }
        }
        self.record = {"criterion_coverage": {"M-S1": {"status": "not_evaluated"}}}

    @staticmethod
    def plan(**updates):
        value = {
            "target_executor": "investigator",
            "expected_output": "one new discriminating result",
            "new_search_lead": "",
        }
        value.update(updates)
        return value

    @staticmethod
    def judge(plan=None, *, criterion="M-S1", executor="investigator"):
        objection = {
            "objection_id": "J-0001",
            "criterion_id": criterion,
            "reason": "one discriminating gap remains",
            "requested_resolution": "run one check",
            "executor_role": executor,
        }
        return {
            "assessment_state": "rework_requested",
            "verdict": None,
            "rework_plan": plan,
            "objections": [objection],
        }

    def test_single_scientific_objection_is_authorized_without_evidence_or_artifact_prerequisites(self):
        allowed, reason = self.workflow._authorize_judge_rework(
            self.state, self.record, self.judge(self.plan()), "investigator")
        self.assertTrue(allowed)
        self.assertEqual(reason, "single_scientific_objection")

    def test_missing_or_wrong_sdk_plan_is_not_authorized(self):
        allowed, reason = self.workflow._authorize_judge_rework(
            self.state, self.record, self.judge(None), "investigator")
        self.assertFalse(allowed)
        self.assertEqual(reason, "sdk_rework_plan_missing_or_mismatched")
        allowed, reason = self.workflow._authorize_judge_rework(
            self.state, self.record, self.judge(self.plan(target_executor="literature")), "investigator")
        self.assertFalse(allowed)
        self.assertEqual(reason, "sdk_rework_plan_missing_or_mismatched")

    def test_legacy_evidence_and_artifact_prerequisites_are_rejected(self):
        for legacy_field, value in (
            ("required_evidence_ids", ["E-old"]),
            ("required_artifact_paths", ["result.json"]),
            ("blocked_by", ["missing input"]),
            ("requires_model_change", False),
        ):
            allowed, reason = self.workflow._authorize_judge_rework(
                self.state, self.record, self.judge(self.plan(**{legacy_field: value})), "investigator")
            self.assertFalse(allowed)
            self.assertEqual(reason, "sdk_rework_plan_contains_legacy_prerequisites")

    def test_objection_must_bind_one_frozen_criterion_and_target(self):
        allowed, reason = self.workflow._authorize_judge_rework(
            self.state, self.record, self.judge(self.plan(), criterion="M-UNKNOWN"), "investigator")
        self.assertFalse(allowed)
        self.assertEqual(reason, "criterion_not_in_frozen_decisive_test")
        allowed, reason = self.workflow._authorize_judge_rework(
            self.state, self.record, self.judge(self.plan(), executor="literature"), "investigator")
        self.assertFalse(allowed)
        self.assertEqual(reason, "target_executor_does_not_match_objection")

    def test_multiple_objections_are_not_authorized(self):
        judge = self.judge(self.plan())
        judge["objections"].append({**judge["objections"][0], "objection_id": "J-0002"})
        allowed, reason = self.workflow._authorize_judge_rework(
            self.state, self.record, judge, "investigator")
        self.assertFalse(allowed)
        self.assertEqual(reason, "missing_single_structured_objection")

    def test_current_contract_rejects_legacy_unplanned_rework_shape(self):
        contract = role_contract("judge", phase="terminal_assessment")
        legacy = {
            "assessment_state": "insufficient_evidence", "verdict": None,
            "recommended_start_executor": "investigator",
            "objections": [{"objection_id": "J-unplanned", "criterion_id": "M-S1",
                "reason": "missing check", "requested_resolution": "run check",
                "evidence_that_would_resolve_it": ["new check"],
                "evidence_that_would_not_resolve_it": ["old result"]}],
            "rationale": "needs one check",
        }
        with self.assertRaisesRegex(ValueError, "SDK-owned/obsolete fields"):
            contract.validate(legacy, {
                "branch": {"cycle_criteria": [{"id": "C-1", "criterion": "test"}]},
                "investigation_result": {"findings": [{"statement": "bounded result"}]},
            })
            artifact = Path(state["recovery"]["artifact"])
            saved = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(saved["role"], "judge")
            self.assertEqual(saved["stage"], "terminal_assessment")
            self.assertTrue(saved["validation_errors"])
            first_raw = json.loads(saved["raw_outputs"][0])
            self.assertNotIn("rework_plan", first_raw)
            self.assertEqual(first_raw["assessment_state"], "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
