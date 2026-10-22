from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from theory_master.abc_pipeline import DeterministicSemanticBackend, annotate_specialist
from theory_master.contracts import role_contract


class InvestigatorSourceBoundaryTest(unittest.TestCase):
    def test_local_source_claim_is_statement_bound_without_manifest_authorization(self):
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp) / "task"
            source = task / "benchmark" / "source.py"
            source.parent.mkdir(parents=True)
            source.write_text("LABELS = ['MCNC', 'MCLCNC', 'all']\n", encoding="utf-8")
            context = {
                "required_context": "- benchmark/source.py",
                "task_root": str(task),
            }
            contract = role_contract("investigator", phase="investigate")
            prompt_schema = contract.prompt_projection(context)
            self.assertNotIn("source_manifest", prompt_schema)
            self.assertIn("provenance", prompt_schema)
            output = {"findings": [{"statement": "The supplied benchmark/source.py defines three label groups."}]}
            contract.validate(output, context)

            # Legacy model-authored evidence typing and path lists are not part
            # of the current finding contract; source conditions stay in prose.
            legacy = {"findings": [{**output["findings"][0], "source_refs": ["benchmark/source.py"]}]}
            with self.assertRaises(ValueError):
                contract.validate(legacy, context)

    def test_semantic_closure_preserves_declared_provenance(self):
        provenance = {"source": "benchmark_provided", "split": "fit", "declaration_ref": "fixture-declaration"}
        artifact = {"findings": [{
            "statement": "The supplied benchmark defines the label mapping.",
            "provenance": provenance,
        }]}
        closed, _audit = annotate_specialist(
            artifact,
            {"hypothesis_lock": {"hypothesis": "H", "cycle_progress_criteria": {}}},
            DeterministicSemanticBackend(),
        )
        self.assertEqual(closed["evidence"][0]["provenance"], provenance)


if __name__ == "__main__":
    unittest.main()
