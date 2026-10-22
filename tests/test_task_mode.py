from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from theory_master.core import FakeBackend, TaskSpec, Workflow, latest_cycle_dir, list_cycle_dirs
from theory_master.specification import make_run_id
from theory_master.task import PACKAGE_ROOT, TaskBinding, bootstrap, discover_task_root, ensure_runtime_outside_package
from theory_master.tui.render import render_header


class TaskModeTest(unittest.TestCase):
    def make_task(self, root: Path) -> None:
        plan = root / "research_plan.md"
        plan.write_text(
            "# Goal\n\nStart from a clean task root.\n\n"
            "# Research Contract\n\nUse the task-local state only.\n\n"
            "# Final Gate\n\nThe bounded task result is adjudicated.\n\n"
            "# Required Context\n\n- task-mode\n",
            encoding="utf-8",
        )
        binding = bootstrap(plan)
        payload = json.loads(binding.config_path.read_text(encoding="utf-8"))
        payload["max_cycles"] = 1
        binding.config_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_cycle_allocator_continues_after_read_only_legacy_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            canonical = root / "task" / ".theory-master" / "research"
            legacy = root / "legacy"
            canonical.mkdir(parents=True)
            legacy.mkdir()
            for cycle_id in ("94", "95", "96", "97"):
                (legacy / cycle_id).mkdir()
            self.assertEqual(
                make_run_id(
                    "task",
                    canonical,
                    task_mode=True,
                    legacy_research_root=legacy,
                ),
                "98",
            )
            (canonical / "98").mkdir()
            self.assertEqual(
                make_run_id(
                    "task",
                    canonical,
                    task_mode=True,
                    legacy_research_root=legacy,
                ),
                "99",
            )

    def test_runtime_root_inside_package_is_rejected(self):
        candidate = PACKAGE_ROOT / ".theory-master" / "research"
        with self.assertRaisesRegex(ValueError, "outside the TheoryMaster source tree"):
            ensure_runtime_outside_package(candidate)

    def test_task_mode_uses_fixed_master_and_plain_cycle_ordinals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "task"
            root.mkdir()
            self.make_task(root)
            self.assertEqual(discover_task_root(root / ".theory-master"), root.resolve())
            binding = TaskBinding.load(root)
            self.assertGreaterEqual(binding.master_id, 0)
            self.assertEqual(binding.task_home, root.resolve())
            self.assertNotIn(root, binding.research_root.parents)
            self.assertEqual(binding.research_root, Path(json.loads((root / "theory-master.config.json").read_text())["research_root"]))
            spec = TaskSpec.from_task_root(root)
            self.assertTrue(spec.task_mode)
            state = Workflow(spec, FakeBackend(reworks=0)).run()
            cycle = latest_cycle_dir(spec.research_root)
            self.assertIsNotNone(cycle)
            self.assertEqual(cycle.name, "1")
            self.assertEqual(state["master_id"], spec.master_id)
            self.assertEqual(state["cycle_number"], 1)
            self.assertIn("Cycle — / 1", render_header(state))

    def test_task_mode_failed_cycle_resumes_in_place_without_requalification(self):
        class Boom(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "investigator":
                    raise RuntimeError("temporary failure")
                return super().invoke(role, prompt, thread_id, model, provider)

        class ResumeBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = []
            def invoke(self, role, prompt, thread_id, model, provider):
                self.calls.append(role)
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "task"
            root.mkdir()
            self.make_task(root)
            spec = TaskSpec.from_task_root(root)
            with self.assertRaisesRegex(RuntimeError, "temporary failure"):
                Workflow(spec, Boom(reworks=0)).run()
            failed_cycle = latest_cycle_dir(spec.research_root)
            self.assertEqual(failed_cycle.name, "1")
            backend = ResumeBackend()
            resumed = Workflow(spec, backend).resume(0)
            self.assertEqual(resumed["status"], "over")
            self.assertEqual(latest_cycle_dir(spec.research_root).name, "1")
            self.assertEqual(len(list_cycle_dirs(spec.research_root, task_mode=True)), 1)
            self.assertNotIn("challenger", backend.calls)



if __name__ == "__main__":
    unittest.main()
