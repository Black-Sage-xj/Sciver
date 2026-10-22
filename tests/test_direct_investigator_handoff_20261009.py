"""Scientific handoff must not be pre-adjudicated by the auxiliary B model."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from theory_master.contract_closure import close_agent_output
from theory_master.evidence import EvidenceRegistry
from theory_master.prompt_stages import stage_context
from theory_master.workflow import Workflow


class DirectInvestigatorHandoffTest(unittest.TestCase):
    def test_findings_register_verbatim_without_semantic_scoring_or_scope_inference(self):
        findings = [
            {"statement": "Finite toy example disproves the local auxiliary claim.",
             "evidence_kind": "theoretical", "scope": "finite toy only",
             "limitations": "Not a claim about the full physical network."},
            {"statement": "Exact algebraic statement with no claim of universal scope.",
             "evidence_kind": "theoretical"},
            {"statement": "Source verification changes input eligibility.",
             "evidence_kind": "local_source", "source_refs": ["benchmark/input_protocol.md"]},
        ]
        original = {"findings": findings}
        closed = close_agent_output("investigator", "investigate", original, {}, {})
        self.assertEqual(len(closed["evidence"]), 3)
        self.assertEqual([x["summary"] for x in closed["evidence"]],
                         [x["statement"] for x in findings])
        self.assertTrue(all("relation" not in x for x in closed["evidence"]))
        self.assertTrue(all("criterion_ids" not in x for x in closed["evidence"]))
        self.assertNotIn("scope", closed["evidence"][1])
        self.assertNotIn("limitations", closed["evidence"][1])

        with tempfile.TemporaryDirectory() as directory:
            cycle = Path(directory) / "47"
            workspace = cycle / "workspace"
            workspace.mkdir(parents=True)
            registry = EvidenceRegistry(cycle, workspace)
            ids = registry.register_role_output(
                "investigator", closed, epoch="post_lock_test",
                producer_invocation={"role": "investigator", "invocation_id": "inv-test",
                                     "stage": "investigate"},
                hypothesis_ref="H-0046", default_scope="full H hypothesis range",
                known_criterion_ids=["C-1"],
            )
            self.assertEqual(len(ids), 3)
            manifest = registry.context()["evidence_manifest"]["payload"]["records"]
            self.assertEqual(len(manifest), 3)
            for index, row in enumerate(manifest):
                self.assertEqual(row["summary"], findings[index]["statement"])
                self.assertEqual(row["body"], findings[index]["statement"])
                self.assertIsNone(row["relation"])
                self.assertEqual(row["criterion_ids"], [])
                self.assertEqual(row["hypothesis_ref"], "H-0046")
            self.assertIsNone(manifest[1]["scope"])
            self.assertIsNone(manifest[0]["scope"])
            self.assertNotIn("source_refs", manifest[2])
            self.assertNotIn("evidence_kind", manifest[0])

            eligible = Workflow._adjudicable_judge_records(registry.context())
            self.assertEqual(len(eligible), 3)
            result = {"findings": [{"statement": row["summary"]} for row in eligible]}
            visible = stage_context("judge", "terminal_assessment", {
                "hypothesis_lock": {"hypothesis": "H", "decisive_test": "T"},
                "investigation_result": result,
            })["investigation_result"]["findings"]
            self.assertEqual(len(visible), 3)
            self.assertTrue(all("relation" not in row for row in visible))
            self.assertNotIn("scope", visible[1])

    def test_qualification_rows_cannot_become_judge_findings(self):
        rows = [
            {"evidence_id": "E-1", "evidence_kind": "qualification",
             "producer_invocation": {"role": "challenger"}, "adjudicable": True,
             "lifecycle_status": "active", "criterion_ids": ["C-1"]},
            {"evidence_id": "E-2", "evidence_kind": "theoretical",
             "producer_invocation": {"role": "investigator"}, "adjudicable": True,
             "lifecycle_status": "active", "criterion_ids": []},
            {"evidence_id": "E-3", "evidence_kind": "local_source",
             "producer_invocation": {"role": "investigator"}, "adjudicable": False,
             "lifecycle_status": "active", "criterion_ids": []},
            {"evidence_id": "E-4", "evidence_kind": "theoretical",
             "producer_invocation": {"role": "investigator"}, "adjudicable": True,
             "lifecycle_status": "superseded", "criterion_ids": []},
            {"evidence_id": "E-5", "evidence_kind": "theoretical",
             "producer_invocation": {"role": "theorist"}, "adjudicable": True,
             "lifecycle_status": "active", "criterion_ids": []},
            {"evidence_id": "E-6", "evidence_kind": "numerical",
             "adjudicable": True, "lifecycle_status": "active", "criterion_ids": []},
        ]
        context = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(context)
        self.assertEqual([r["evidence_id"] for r in accepted], ["E-2", "E-5", "E-6"])

    def test_legacy_label_not_delivered_as_judge_science(self):
        view = stage_context("judge", "terminal_assessment", {
            "hypothesis_lock": {"hypothesis": "H", "decisive_test": "T"},
            "investigation_result": {"findings": [
                {"statement": "Prior result", "relation": "supports",
                 "criterion_ids": ["C-1"], "evidence_kind": "theoretical"}
            ]},
        })
        self.assertEqual(view["investigation_result"]["findings"],
                         [{"statement": "Prior result"}])


if __name__ == "__main__":
    unittest.main()
