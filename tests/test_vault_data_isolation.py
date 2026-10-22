"""Isolation and fail-closed v1/v2 test without touching production Task 1."""
from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from theory_master.task import PATH_MODEL, LEGACY_PATH_MODEL, effective_path_model, non_sync_data_area, bootstrap

PLAN = "# Goal\n\nTest a scientific claim.\n\n# Research Contract\n\nUse reproducible evidence.\n\n# Final Gate\n\nThe claim is adjudicated.\n\n# Required Context\n\n- Local notes\n"

class VaultIsolationTest(unittest.TestCase):
    def test_new_vault_task_uses_external_scientific_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            vault=root/"笔记区"/"探索台账"/"scientific"
            vault.mkdir(parents=True)
            (root/"gs_study").mkdir()
            (root/".git").mkdir()
            plan=vault/"research_plan.md"
            plan.write_text(PLAN, encoding="utf-8")
            with patch.dict(os.environ, {"THEORY_MASTER_SEARCH_ROOT":str(root)}, clear=False):
                task=bootstrap(plan)
            cfg=json.loads((vault/"theory-master.config.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["path_model"], PATH_MODEL)
            self.assertFalse(str(task.research_root).startswith(str(root/"笔记区")))
            self.assertFalse(str(Path(cfg["benchmark_root"])).startswith(str(root/"笔记区")))
            self.assertFalse((vault/"benchmark").exists())
            self.assertFalse((vault/".theory-master"/"research").exists())
            self.assertTrue((vault/".theory-master"/"task.json").exists())
            self.assertTrue((task.research_root/".theory-master"/"task.json").exists())

    def test_v2_rejects_benchmark_inside_same_vault(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); note=root/"笔记区"/"Task"; note.mkdir(parents=True)
            cfg={"plan_path":str(note/"research_plan.md"),"task_home":str(note),
                 "research_root":str(root/"research"),"benchmark_root":str(root/"笔记区"/"OTHER"/"benchmark"),
                 "path_model":PATH_MODEL}
            with self.assertRaisesRegex(ValueError, "outside task_home/vault"):
                effective_path_model(cfg)

    def test_legacy_v1_root_is_not_silently_relocated(self):
        with tempfile.TemporaryDirectory() as temp:
            note=Path(temp)/"note"; note.mkdir()
            cfg={"plan_path":str(note/"research_plan.md"),"task_home":str(note),
                 "research_root":str(note/".theory-master"/"research"),
                 "path_model":LEGACY_PATH_MODEL}
            self.assertEqual(effective_path_model(cfg)[1],note/".theory-master"/"research")

    def test_data_root_cannot_be_inside_vault_via_env(self):
        with tempfile.TemporaryDirectory() as temp:
            note=Path(temp)/"笔记区"/"experiment";note.mkdir(parents=True)
            with patch.dict(os.environ, {"THEORY_MASTER_DATA_ROOT":str(note.parent/"hidden")},clear=False):
                with self.assertRaisesRegex(ValueError, "outside the human note area/vault"):
                    non_sync_data_area(note/"research_plan.md", 1, "a")

if __name__=="__main__":
    unittest.main()
