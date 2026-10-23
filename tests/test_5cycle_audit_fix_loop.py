from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts/run_5cycle_audit_fix_loop.py"
spec = importlib.util.spec_from_file_location("five_cycle_supervisor", MODULE)
assert spec and spec.loader
loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loop)


def cycle(root: Path, number: int, status: str) -> Path:
    path = root / f"cycle-{number:04d}"
    path.mkdir()
    (path / ".theory-master-cycle").touch()
    (path / "research_state.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    return path


class FiveCycleSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_batch_selection_requires_five_new_over_cycles(self) -> None:
        cycle(self.root, 1, "completed")
        baseline = ["cycle-0001"]
        for n in range(2, 7):
            cycle(self.root, n, "over" if n < 6 else "needs_attention")
        found = loop.select_new_cycles(self.root, baseline)
        self.assertEqual([p.name for p in found], [f"cycle-{n:04d}" for n in range(2, 7)])
        self.assertFalse(loop.completed_batch(found))
        (found[-1] / "research_state.json").write_text('{"status":"over"}', encoding="utf-8")
        self.assertTrue(loop.completed_batch(found))
        cycle(self.root, 7, "over")
        self.assertFalse(loop.completed_batch(loop.select_new_cycles(self.root, baseline)))

    def test_selection_uses_cycle_id_order_not_state_mtime(self) -> None:
        cycle(self.root, 1, "completed")
        for n in range(2, 7):
            cycle(self.root, n, "over")
        # Updating an older state must not reorder a batch.
        (self.root / "cycle-0002/research_state.json").write_text('{"status":"over"}', encoding="utf-8")
        self.assertEqual([p.name for p in loop.select_new_cycles(self.root, ["cycle-0001"])],
                         [f"cycle-{n:04d}" for n in range(2, 7)])

    def test_batch_audit_prompt_uses_only_trajectory_skill(self) -> None:
        cycles = [self.root / f"cycle-{n:04d}" for n in range(1, 6)]
        prompt = loop.audit_prompt(SimpleNamespace(branch_canvas=None), cycles, self.root / "batch_audit.md")
        self.assertIn(str(loop.AUDIT_SKILL), prompt)
        for path in cycles:
            self.assertIn(str(path), prompt)
        self.assertNotIn("audi_list", prompt)
        self.assertNotIn("A. 长期", prompt)
        self.assertNotIn("B. 短期", prompt)

    def test_workflow_attaches_collector_without_automatic_actions(self) -> None:
        fake = SimpleNamespace(research_root=self.root, max_cycles=5, branch_canvas=None, next_step_start_id=None)
        with patch.object(loop, "replace", return_value=fake), patch.object(loop, "resolve_routes", return_value={}), \
             patch.object(loop, "CodexBackend") as backend, patch.object(loop.Workflow, "run", return_value={"status": "over"}):
            loop.run_workflow(fake, resume_id=None, remaining=5)
            collector = backend.return_value.collector
            self.assertIsInstance(collector, loop.Collector)
            self.assertFalse(collector.cycle_audit_actions)
            self.assertFalse(collector.system_failure_actions)
            self.assertFalse(collector.email_notifications)
            self.assertTrue(collector._routes)

    def test_written_reports_recover_missing_status_without_codex_or_duplicate_registration(self) -> None:
        supervisor = loop.Supervisor.__new__(loop.Supervisor)
        supervisor.spec = SimpleNamespace(research_root=self.root)
        supervisor.root = self.root / "audit_batches"
        supervisor.root.mkdir()
        supervisor.state_path = supervisor.root / "state.json"
        supervisor.stop_signal = False
        pending = {"batch_id": "batch-one", "cycle_ids": [f"cycle-{n:04d}" for n in range(1, 6)],
                   "started_at": "before", "interruptions": []}
        state = {"pending": pending}
        batch_dir = supervisor.root / "batch-one"
        batch_dir.mkdir()
        (batch_dir / "batch_audit.md").write_text("# Audit\nPASS: checked", encoding="utf-8")
        (batch_dir / "repair_result.md").write_text("# Repair\nNo issues", encoding="utf-8")
        with patch.object(loop, "invoke_codex", side_effect=AssertionError("must not rerun")):
            self.assertTrue(supervisor._audit_repair(state, pending, batch_dir))
            self.assertEqual(pending["audit_status"], "completed")
            self.assertEqual(pending["repair_status"], "completed")
            # Simulate a crash after history append but before state persistence.
            state["pending"] = pending
            self.assertTrue(supervisor._audit_repair(state, pending, batch_dir))
        self.assertEqual(len((supervisor.root / "history.jsonl").read_text().splitlines()), 1)
        self.assertIsNone(state["pending"])

    def test_check_only_is_read_only_and_validates_paths(self) -> None:
        executable = self.root / "codex"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
        fake = SimpleNamespace(research_root=self.root, max_cycles=5)
        skill = self.root / "audit-skill.md"
        skill.write_text("# Offline fixture audit skill\n", encoding="utf-8")
        with (patch.object(loop.TaskSpec, "from_task_root", return_value=fake),
              patch.object(loop, "AUDIT_SKILL", skill)):
            supervisor = loop.Supervisor(self.root, executable)
            locations = supervisor.check()
            self.assertEqual(locations["reports"], str(self.root / ".theory-master/audit_batches/<batch-id>"))
            self.assertFalse((self.root / ".theory-master").exists())
            executable.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "not executable"):
                supervisor.check()

    def test_registration_is_idempotent(self) -> None:
        manifest = {"batch_id": "batch-one", "cycle_ids": [f"cycle-{n:04d}" for n in range(1, 6)],
                    "registered_at": "now", "audit_status": "completed", "repair_status": "completed",
                    "next_loop_disposition": "continue"}
        self.assertTrue(loop.register_batch(self.root, manifest))
        self.assertFalse(loop.register_batch(self.root, manifest))
        lines = (self.root / "history.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["batch_id"], "batch-one")

    def test_pause_stop_priority_and_state(self) -> None:
        self.assertEqual(loop.control_disposition(self.root), "continue")
        (self.root / "PAUSE").touch()
        self.assertEqual(loop.control_disposition(self.root), "pause")
        (self.root / "STOP").touch()
        self.assertEqual(loop.control_disposition(self.root), "stop")
        (self.root / "STOP").unlink()
        self.assertEqual(loop.control_disposition(self.root), "pause")

    def test_prompt_authority_boundaries(self) -> None:
        fake = SimpleNamespace(branch_canvas=self.root / "branch.canvas", research_root=self.root)
        audit = loop.audit_prompt(fake, [self.root / f"cycle-{n:04d}" for n in range(1, 6)], self.root / "batch_audit.md")
        repair = loop.repair_prompt(fake, self.root / "batch_audit.md", self.root / "repair_result.md")
        recovery = loop.repair_prompt(fake, self.root / "state.json", self.root / "recovery.md", recovery=True)
        self.assertIn(str(loop.AUDIT_SKILL), audit)
        self.assertTrue(all(f"cycle-{n:04d}" in audit for n in range(1, 6)))
        self.assertNotIn("read-only authority", audit)
        self.assertNotIn("PASS/RISK/FAIL/NOT_VERIFIABLE", audit)
        self.assertIn("fix ONLY actionable SDK", repair)
        for forbidden in ("scientific verdicts", "Evidence", "Scientific Model conclusions", "fabricate missing scientific evidence"):
            self.assertIn(forbidden, repair)
        self.assertIn("scientific follow-up; no direct repair", repair)
        self.assertIn("engineering-recoverable", recovery)
        self.assertIn(str(self.root / "state.json"), recovery)
        self.assertIn("engineering-recoverable: yes", recovery)


if __name__ == "__main__":
    unittest.main()
