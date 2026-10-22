from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from theory_master.core import FakeBackend, TaskSpec, Workflow, latest_cycle_dir
from theory_master.invocations import InvocationLedger


def make_spec(root: Path) -> TaskSpec:
    control = root / "control.canvas"
    control.write_text('{"nodes": [], "edges": []}', encoding="utf-8")
    plan = root / "research_plan.md"
    plan.write_text(
        '# Goal\n\ng\n\n# Research Contract\n\nc\n\n# Final Gate\n\nReproducible contract.\n\n# Required Context\n\nr\n',
        encoding="utf-8",
    )
    return TaskSpec.load(plan)


class ContractRefactorBaselineTest(unittest.TestCase):
    """Behavioral characterization that must survive contract minimization."""

    def test_one_submission_judge_eligibility_is_sdk_owned_not_agent_self_loop(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when criterion-bound active Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-current", "producer_invocation": {"role": "investigator"}, "evidence_kind": "theoretical", "criterion_ids": [], "lifecycle_status": "active", "adjudicable": True}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)


    def test_judge_rework_keeps_bounded_executable_delta_authorization(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "cycle-0001" / "workspace"
            workspace.mkdir(parents=True)
            (workspace.parent / ".theory-master-cycle").write_text("theory-master/v2\n", encoding="utf-8")
            workflow = Workflow.__new__(Workflow)
            workflow.backend = FakeBackend(reworks=0)
            workflow.task_root = workspace
            workflow.spec = SimpleNamespace(research_root=root, task_mode=True)
            workflow._evidence_context = lambda: {
                "coverage_matrix": {"payload": {"active_evidence_ids": ["E-current"]}},
                "evidence_manifest": {"payload": {"records": [{
                    "evidence_id": "E-current",
                    "lifecycle_status": "active",
                    "criterion_ids": ["M-S1"],
                    "artifact_pointers": [],
                }]}},
            }
            state = {
                "evidence_refs": ["E-current"],
                "hypothesis_lock": {
                    "cycle_progress_criteria": {
                        "success": [{"id": "M-S1", "criterion": "test"}],
                        "failure": [],
                    }
                },
            }
            record = {"criterion_coverage": {"M-S1": {"status": "not_evaluated"}}}
            judge = {
                "assessment_state": "rework_requested",
                "verdict": None,
                "rework_plan": {
                    "target_executor": "investigator",
                    "expected_output": "one new discriminating result",
                    "new_search_lead": "",
                },
                "objections": [{
                    "objection_id": "J-0001",
                    "criterion_id": "M-S1",
                    "reason": "one discriminating check is missing",
                    "requested_resolution": "run the check",
                    "executor_role": "investigator",
                }],
            }
            allowed, reason = workflow._authorize_judge_rework(state, record, judge, "investigator")
            self.assertTrue(allowed)
            self.assertEqual(reason, "single_scientific_objection")

    def test_evidence_registration_generates_stable_id_and_criterion_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = Workflow(make_spec(root), FakeBackend(reworks=0))
            cycle = workflow.spec.research_root / "cycle-0001"
            workflow._bind_cycle(cycle, create=True)
            state = {
                "last_invocation_id": "inv-baseline",
                "threads": {},
                "investigation_attempt": 1,
                "investigation_round_count": 1,
                "judge_rework_count": 0,
                "evidence_refs": [],
                "hypothesis_lock": {
                    "status": "locked",
                    "scope": "baseline scope",
                    "cycle_progress_criteria": {
                        "success": [{"id": "M-S1", "criterion": "test"}],
                        "failure": [],
                    },
                },
            }
            output = {
                "evidence": [{
                    "summary": "a criterion-bound result",
                    "relation": "supports",
                    "criterion_ids": ["M-S1"],
                    "scope": "baseline scope",
                    "verification": "verified",
                    "artifact_pointers": [],
                }]
            }
            refs = workflow._register_evidence(state, "investigator", {"evidence": [{**output["evidence"][0], "evidence_kind": "theoretical"}]})
            self.assertEqual(len(refs), 1)
            self.assertTrue(refs[0].startswith("E-cycle-0001-"))
            self.assertEqual(state["evidence_refs"], refs)
            coverage = workflow._evidence_context()["coverage_matrix"]["payload"]
            self.assertEqual(coverage["criteria"]["M-S1"], refs)



if __name__ == "__main__":
    unittest.main()
