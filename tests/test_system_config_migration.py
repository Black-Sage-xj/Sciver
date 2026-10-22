"""Current task authority and explicit rejection of obsolete plan formats."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from theory_master.__main__ import project_control, spec_from_resume_identifier, spec_from_task, spec_from_tui_identifier
from theory_master.specification import TaskSpec, list_cycle_dirs
from theory_master.core import FakeBackend, Workflow
from theory_master.observability.collector import Collector
from theory_master.task import SYSTEM_CONFIG_NAME, TaskBinding, bootstrap, load_discovered

PLAN = "# Goal\n\nTest a scientific claim.\n\n# Research Contract\n\nUse reproducible evidence.\n\n# Final Gate\n\nThe claim is adjudicated.\n\n# Required Context\n\n- Local notes\n"


class SystemConfigMigrationTest(unittest.TestCase):
    def test_one_plan_bootstrap_keeps_workspace_clean_and_config_owns_budgets(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "topic"
            workspace.mkdir()
            plan = workspace / "research_plan.md"
            plan.write_text(PLAN, encoding="utf-8")
            with mock.patch.dict(os.environ, {"THEORY_MASTER_SEARCH_ROOT": str(workspace)}):
                binding = bootstrap(plan)
            config = workspace / SYSTEM_CONFIG_NAME
            self.assertEqual(binding.task_home, workspace)
            self.assertEqual(binding.research_root, Path(json.loads(config.read_text())["research_root"]))
            self.assertNotIn(workspace, binding.research_root.parents)
            self.assertEqual(binding.task_root, binding.research_root)  # compatibility alias only
            self.assertEqual(binding.note_area, workspace)
            self.assertIsNone(binding.legacy_research_root)
            self.assertIsNone(binding.benchmark_root)
            workspace_names = {p.name for p in workspace.iterdir()
                               if not p.name.startswith("science_graph.sqlite3-")}
            self.assertEqual(workspace_names,
                             {"research_plan.md", SYSTEM_CONFIG_NAME, "science_graph.sqlite3",
                              "exploration.canvas", "notes", ".theory-master"})
            self.assertTrue((workspace / "science_graph.sqlite3").is_file())
            self.assertEqual(list(workspace.glob("*.canvas")), [workspace / "exploration.canvas"])
            self.assertEqual(TaskBinding.load(workspace).task_root, binding.task_root)
            self.assertEqual(TaskBinding.load(binding.task_root).config_path, config)
            payload = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(Path(payload["plan_path"]).parent, workspace)
            self.assertEqual(Path(payload["branch_canvas"]).parent, workspace)
            self.assertEqual(Path(payload["notes_dir"]).parent, workspace)
            self.assertEqual(payload["schema"], "theory-master/system/v1")
            self.assertEqual(payload["path_model"], "theory-master/task-path/v2")
            self.assertEqual(Path(payload["task_home"]), workspace)
            self.assertEqual(payload["task_id"], binding.master_id)
            self.assertEqual(binding.master_id, 0)
            payload["max_cycles"] = 8
            payload["max_rounds_per_investigation_attempt"] = 3
            config.write_text(json.dumps(payload), encoding="utf-8")
            spec = TaskSpec.load(plan)
            self.assertEqual((spec.max_cycles, spec.max_rounds), (8, 3))
            self.assertTrue(plan.read_text(encoding="utf-8").startswith(PLAN))
            self.assertEqual(payload["embedded_state_format"], 4)
            self.assertFalse(hasattr(spec, "control_canvas"))
            snapshot = Collector(spec).snapshot()
            self.assertNotIn("control_canvas", snapshot)
            self.assertNotIn("control_focus", snapshot)
            self.assertEqual(load_discovered(workspace).task_root, binding.task_root)
            self.assertEqual(load_discovered(binding.task_root).task_root, binding.task_root)
            with mock.patch("pathlib.Path.cwd", return_value=workspace):
                self.assertEqual(spec_from_tui_identifier(None).research_root, binding.task_root)
                self.assertEqual(spec_from_tui_identifier("0").research_root, binding.task_root)
                self.assertEqual(spec_from_tui_identifier(str(binding.master_id)).research_root, binding.task_root)
            self.assertEqual(spec_from_resume_identifier(str(binding.master_id), workspace).research_root, binding.task_root)
            self.assertEqual(json.loads((workspace / ".theory-master" / "task.json").read_text())["config_path"], str(config))
            Workflow(spec, FakeBackend(reworks=0)).run()
            self.assertTrue((binding.task_root / "1").is_dir())
            self.assertFalse(list_cycle_dirs(workspace, task_mode=True))
            self.assertEqual(list(workspace.glob("*.canvas")), [workspace / "exploration.canvas"])
            benchmark = Path(payload["benchmark_root"])
            self.assertFalse((workspace / "benchmark").exists())
            self.assertFalse(benchmark.exists())
            benchmark.mkdir()
            (benchmark / "webpage").mkdir()
            rebound = TaskBinding.load(workspace)
            self.assertEqual(rebound.benchmark_root, benchmark)
            self.assertEqual(TaskSpec.from_config(rebound.config_path).benchmark_root, benchmark)

    def test_explicit_task_id_resolves_from_repository_root(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            first = repo / "task-a"
            second = repo / "task-b"
            first.mkdir()
            second.mkdir()
            (first / "research_plan.md").write_text(PLAN, encoding="utf-8")
            (second / "research_plan.md").write_text(PLAN, encoding="utf-8")
            with mock.patch.dict(os.environ, {"THEORY_MASTER_SEARCH_ROOT": str(repo)}):
                first_binding = bootstrap(first / "research_plan.md")
                second_binding = bootstrap(second / "research_plan.md")
                self.assertEqual((first_binding.master_id, second_binding.master_id), (0, 1))
                with mock.patch("pathlib.Path.cwd", return_value=repo):
                    resolved = spec_from_task(master_id=1)
            self.assertEqual(resolved.master_id, 1)
            self.assertEqual(resolved.task_home, second.resolve())

    def test_new_task_rejects_runtime_frontmatter_in_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = Path(temp) / "research_plan.md"
            plan.write_text("---\nmax_cycles: 2\n---\n" + PLAN, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "plain scientific Markdown"):
                bootstrap(plan)
            self.assertFalse((Path(temp) / SYSTEM_CONFIG_NAME).exists())

    def test_unmigrated_canvas_only_task_fails_closed_and_keeps_config_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "canvas-only"
            workspace.mkdir()
            plan = workspace / "research_plan.md"
            plan.write_text(PLAN + "\n# Scientific Model\n\nInitial scoped model.\n", encoding="utf-8")
            binding = bootstrap(plan)
            config = json.loads(binding.config_path.read_text(encoding="utf-8"))
            self.assertEqual(Path(config["plan_path"]), plan)
            self.assertEqual(Path(config["branch_canvas"]), workspace / "exploration.canvas")
            self.assertTrue((workspace / "exploration.canvas").is_file())
            database = workspace / "science_graph.sqlite3"
            self.assertTrue(database.is_file())
            database.unlink()  # simulate an unmigrated Canvas-only workspace
            with self.assertRaisesRegex(FileNotFoundError, "canonical science_graph.sqlite3 is required"):
                TaskSpec.load(plan)
            self.assertTrue((workspace / "exploration.canvas").is_file())
            self.assertFalse(database.exists())

    def test_legacy_v2_plan_is_rejected_without_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            plan = workspace / "research_plan.md"
            plan.write_text("---\nschema: theory-master/v2\n---\n" + PLAN, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frontmatter|unsupported|plain scientific Markdown"):
                TaskSpec.load(plan)
            self.assertFalse((workspace / SYSTEM_CONFIG_NAME).exists())

    def test_extra_top_level_title_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            plan = workspace / "research_plan.md"
            plan.write_text("# Extra Title\n\n" + PLAN, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "top-level headings must be exactly"):
                bootstrap(plan)

    def test_project_rebuild_uses_sqlite_without_pending_reporter_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            plan = workspace / "research_plan.md"
            plan.write_text(PLAN, encoding="utf-8")
            binding = bootstrap(plan)
            spec = TaskSpec.from_config(binding.config_path)
            Workflow(spec, FakeBackend(reworks=0)).run()
            cycle = list_cycle_dirs(binding.task_root, task_mode=True)[0]
            state_before = json.loads((cycle / "research_state.json").read_text())
            self.assertNotIn("canvas_patch", state_before)
            self.assertNotIn("cycle_outcome_record", state_before)
            project_control(spec)
            project_control(spec)
            state_after = json.loads((cycle / "research_state.json").read_text())
            self.assertNotIn("canvas_patch", state_after)
            self.assertNotIn("cycle_outcome_record", state_after)
            canvas = json.loads(spec.branch_canvas.read_text())
            ids = [str(node.get("id") or "") for node in canvas.get("nodes", [])]
            self.assertEqual(len(ids), len(set(ids)))

    def test_live_cli_exposes_no_canvas_port(self):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        for command in ("run", "resume", "tui", "status", "project"):
            result = subprocess.run([sys.executable, "-m", "theory_master", command, "--help"],
                                    capture_output=True, text=True, env=env, check=True)
            self.assertNotIn("--canvas", result.stdout)
        result = subprocess.run([sys.executable, "-m", "theory_master", "--help"],
                                capture_output=True, text=True, env=env, check=True)
        self.assertNotIn("adopt-canvas", result.stdout)

    def test_resume_cli_accepts_the_same_model_routing_overrides_as_run(self):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        for command in ("run", "resume"):
            result = subprocess.run(
                [sys.executable, "-m", "theory_master", command, "--help"],
                capture_output=True, text=True, env=env, check=True,
            )
            self.assertIn("--model", result.stdout)
            self.assertIn("--provider", result.stdout)
            self.assertIn("--route", result.stdout)


if __name__ == "__main__":
    unittest.main()
