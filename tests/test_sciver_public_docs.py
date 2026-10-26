"""Protect Sciver's public documentation branding without migrating legacy paths."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRANDED_DOCS = (
    "README.md",
    "CONTRIBUTING.md",
    "TASK_PATH_MODEL.md",
    "regression_cases/README.md",
    "identities/README.md",
    "theory_master/resources/identities/README.md",
    "theory_master/resources/skills/README.md",
    "theory_master/resources/skills/tm-scholar/README.md",
    "theory_master/model_profiles/README.md",
    "web/README.md",
    "web/research_chat_frontend/README.md",
    "TEST_ARCHITECTURE.md",
)


class SciverPublicDocsTest(unittest.TestCase):
    def test_public_docs_use_sciver_as_the_product(self):
        for rel_path in BRANDED_DOCS:
            with self.subTest(path=rel_path):
                contents = (ROOT / rel_path).read_text(encoding="utf-8")
                if rel_path != "README.md":
                    self.assertIn("Sciver", contents.splitlines()[0])
                if rel_path == "README.md":
                    # The old executable name is intentionally documented as an alias.
                    self.assertEqual(contents.count("TheoryMaster"), 1)
                    self.assertIn("Sciver", contents[:1500])
                    self.assertIn("`TheoryMaster`/`tm` naming", contents)
                else:
                    self.assertNotRegex(contents, r"\bTheoryMaster\b")

    def test_existing_internal_paths_are_preserved_in_examples(self):
        path_model = (ROOT / "TASK_PATH_MODEL.md").read_text(encoding="utf-8")
        for literal in (
            "theory-master.config.json",
            ".theory-master/task.json",
            "THEORY_MASTER_DATA_ROOT",
        ):
            self.assertIn(literal, path_model)
        example = (ROOT / "regression_cases/README.md").read_text(encoding="utf-8")
        self.assertIn("python -m theory_master.regression_cases", example)

    def test_model_profile_quick_start_uses_primary_command(self):
        text = (ROOT / "theory_master/model_profiles/README.md").read_text(encoding="utf-8")
        self.assertIn("sciver model-profile 1", text)
        self.assertIn("sciver model-profile status", text)
        self.assertIn("legacy `theory_master`", text)


if __name__ == "__main__":
    unittest.main()
