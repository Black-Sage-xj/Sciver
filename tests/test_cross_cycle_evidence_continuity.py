from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from theory_master.evidence import EvidenceRegistry
from theory_master.hypothesis_history import build_hypothesis_adjudication_history
from theory_master.prompt_stages import stage_context
from theory_master.specification import CYCLE_MARKER


class CrossCycleEvidenceContinuityTest(unittest.TestCase):
    def _write_cycle(
        self,
        root: Path,
        cycle_id: str,
        *,
        hypothesis_ref: str = "H-0001",
        decisive_test: str,
        verdict: str,
        result: str,
        with_evidence: bool = True,
    ) -> tuple[Path, str | None]:
        cycle = root / cycle_id
        workspace = cycle / "workspace"
        workspace.mkdir(parents=True)
        (cycle / CYCLE_MARKER).write_text("test\n", encoding="utf-8")

        evidence_id: str | None = None
        if with_evidence:
            raw_artifact = workspace / f"raw-{cycle_id}.csv"
            raw_artifact.write_text("x,y\n1,2\n", encoding="utf-8")
            registry = EvidenceRegistry(cycle, workspace)
            refs = registry.register_role_output(
                "investigator",
                {
                    "evidence": [{
                        "summary": result,
                        "scope": f"scope-{cycle_id}",
                        "limitations": f"limitation-{cycle_id}",
                        "relation": "supports",
                        "criterion_ids": ["C-1"],
                        "evidence_kind": "theoretical",
                        "artifact_pointers": [str(raw_artifact)],
                    }]
                },
                epoch="post_lock_test",
                producer_invocation={
                    "invocation_id": f"inv-{cycle_id}",
                    "kind": "agent",
                    "role": "investigator",
                },
                hypothesis_ref=hypothesis_ref,
                known_criterion_ids=["C-1"],
            )
            evidence_id = refs[0]

        state = {
            "hypothesis_lock": {
                "status": "locked",
                "id": hypothesis_ref,
                "hypothesis": "H remains the same scientific claim across Cycles.",
                "scope": "frozen H scope",
                "decisive_test": decisive_test,
                "cycle_criteria": [{"id": "C-1", "criterion": decisive_test}],
            },
            "scientific_verdict": verdict,
            "role_outputs": {
                "judge": {
                    "verdict": verdict,
                    "rationale": result,
                }
            },
            "scientific_synthesis": {
                "learned": [result],
                "invalidated": [],
                "strengthened": [],
                "new_anomalies": [],
                "resolved_questions": [],
                "new_open_questions": [],
                "cross_branch_implications": [],
            },
        }
        (cycle / "research_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return cycle, evidence_id

    def test_three_cycle_same_h_history_is_compact_and_namespaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            c1, e1 = self._write_cycle(
                root, "1", decisive_test="DT-A", verdict="milestone_advanced",
                result="DT-A established local result A.",
            )
            c2, e2 = self._write_cycle(
                root, "2", decisive_test="DT-B", verdict="milestone_advanced",
                result="DT-B established local result B.",
            )
            self._write_cycle(
                root, "3", decisive_test="DT-C", verdict="supported",
                result="DT-C completes the cumulative case for H.",
                with_evidence=False,
            )

            before_c1 = (c1 / "evidence" / "registry.jsonl").read_text(encoding="utf-8")
            before_c2 = (c2 / "evidence" / "registry.jsonl").read_text(encoding="utf-8")

            history = build_hypothesis_adjudication_history(
                root, "H-0001", current_cycle_id="3", task_mode=True,
            )
            self.assertEqual(history["hypothesis_ref"], "H-0001")
            self.assertEqual([row["cycle_id"] for row in history["cycles"]], ["1", "2"])
            self.assertEqual(
                [row["verdict"] for row in history["cycles"]],
                ["milestone_advanced", "milestone_advanced"],
            )
            self.assertEqual(
                [row["decisive_test"] for row in history["cycles"]],
                ["DT-A", "DT-B"],
            )
            self.assertEqual(
                [row["evidence"][0]["evidence_id"] for row in history["cycles"]],
                [e1, e2],
            )

            binding_1 = history["cycles"][0]["evidence"][0]["criterion_bindings"][0]
            binding_2 = history["cycles"][1]["evidence"][0]["criterion_bindings"][0]
            self.assertEqual(binding_1["criterion_id"], "C-1")
            self.assertEqual(binding_2["criterion_id"], "C-1")
            self.assertEqual(binding_1, {
                "hypothesis_ref": "H-0001", "cycle_id": "1", "criterion_id": "C-1",
            })
            self.assertEqual(binding_2, {
                "hypothesis_ref": "H-0001", "cycle_id": "2", "criterion_id": "C-1",
            })
            self.assertNotEqual(
                (binding_1["hypothesis_ref"], binding_1["cycle_id"], binding_1["criterion_id"]),
                (binding_2["hypothesis_ref"], binding_2["cycle_id"], binding_2["criterion_id"]),
            )

            judge_projection = stage_context(
                "judge",
                "terminal_assessment",
                {
                    "adjudication_scope": "current_decisive_test_plus_same_hypothesis_history",
                    "rule": "test",
                    "hypothesis_lock": {
                        "id": "H-0001",
                        "hypothesis": "H remains the same scientific claim across Cycles.",
                        "scope": "frozen H scope",
                        "decisive_test": "DT-C",
                        "cycle_criteria": [{"id": "C-1", "criterion": "DT-C"}],
                    },
                    "prior_adjudicated_context": history,
                    "investigation_result": {
                        "schema": "theory-master/investigation-result/v1",
                        "findings": [{
                            "statement": "DT-C completes the cumulative case for H.",
                            "scope": "scope-3",
                            "limitations": "limitation-3",
                        }],
                    },
                },
            )
            self.assertEqual(
                [row["decisive_test"] for row in judge_projection["prior_adjudicated_context"]["cycles"]],
                ["DT-A", "DT-B"],
            )
            self.assertEqual(
                [row["verdict"] for row in judge_projection["prior_adjudicated_context"]["cycles"]],
                ["milestone_advanced", "milestone_advanced"],
            )
            self.assertEqual(judge_projection["hypothesis_lock"], {
                "hypothesis": "H remains the same scientific claim across Cycles.",
                "scope": "frozen H scope",
                "decisive_test": "DT-C",
            })
            self.assertEqual(judge_projection["investigation_result"]["findings"], [{
                "statement": "DT-C completes the cumulative case for H.",
                "scope": "scope-3",
                "limitations": "limitation-3",
            }])

            serialized = json.dumps(judge_projection, ensure_ascii=False)
            for forbidden in (
                "raw-1.csv", "raw-2.csv", "artifact_pointers", "artifact_roles",
                "records_path", "registration_batches_path", "lifecycle_path",
                "workspace/evidence_manifest.json", "transcript", "cycle_id", "hypothesis_id",
                "adjudication_role", "evidence_id", "criterion_bindings", "criterion_ids",
                "investigation_attempt", "rounds",
            ):
                self.assertNotIn(forbidden, serialized)

            # Constructing Judge history is projection-only: old Registry bytes remain unchanged.
            self.assertEqual(
                (c1 / "evidence" / "registry.jsonl").read_text(encoding="utf-8"),
                before_c1,
            )
            self.assertEqual(
                (c2 / "evidence" / "registry.jsonl").read_text(encoding="utf-8"),
                before_c2,
            )

    def test_open_cycle_is_explicitly_context_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_cycle(
                root, "1", decisive_test="DT-open", verdict="open",
                result="The attempt found a bounded but unresolved result.",
            )
            self._write_cycle(
                root, "2", decisive_test="DT-next", verdict="milestone_advanced",
                result="Current placeholder.",
                with_evidence=False,
            )
            history = build_hypothesis_adjudication_history(
                root, "H-0001", current_cycle_id="2", task_mode=True,
            )
            self.assertEqual(len(history["cycles"]), 1)
            row = history["cycles"][0]
            self.assertEqual(row["verdict"], "open")
            self.assertEqual(row["adjudication_role"], "context_only_unresolved")

    def test_new_registry_records_are_sdk_bound_to_hypothesis_and_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle, evidence_id = self._write_cycle(
                root, "1", decisive_test="DT-A", verdict="milestone_advanced",
                result="Durable ownership check.",
            )
            raw = [
                json.loads(line)
                for line in (cycle / "evidence" / "registry.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(raw), 1)
            self.assertEqual(raw[0]["evidence_id"], evidence_id)
            self.assertEqual(raw[0]["hypothesis_ref"], "H-0001")
            self.assertNotIn("hypothesis_id", raw[0])
            self.assertEqual(raw[0]["cycle_id"], "1")
            self.assertEqual(raw[0]["criterion_ids"], ["C-1"])

    def test_judge_projection_drops_legacy_paired_cycle_criteria_bookkeeping(self):
        projected = stage_context(
            "judge", "terminal_assessment", {
                "hypothesis_lock": {
                    "hypothesis_id": "H-legacy",
                    "hypothesis": "legacy H",
                    "scope": "legacy scope",
                    "decisive_test": "legacy decisive test",
                    "cycle_progress_criteria": {
                        "success": [{"id": "C-S1", "criterion": "legacy success"}],
                        "failure": [{"id": "C-F1", "criterion": "legacy failure"}],
                    },
                },
                "investigation_result": {
                    "schema": "theory-master/investigation-result/v1",
                    "investigation_attempt": 2,
                    "rounds": 3,
                    "findings": [{
                        "statement": "legacy finding",
                        "criterion_ids": ["C-S1"],
                        "objection_ids": ["J-old"],
                    }],
                },
            },
        )
        self.assertEqual(projected["hypothesis_lock"], {
            "hypothesis": "legacy H",
            "scope": "legacy scope",
            "decisive_test": "legacy decisive test",
        })
        self.assertEqual(projected["investigation_result"], {
            "findings": [{"statement": "legacy finding"}],
        })


if __name__ == "__main__":
    unittest.main()
