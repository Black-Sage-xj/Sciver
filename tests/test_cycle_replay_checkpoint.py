import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from theory_master.workflow_state import WorkflowStateMixin


class ReplayCheckpointTest(unittest.TestCase):
    def test_checkpoint_contains_exact_state_digest_and_boundary_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            cycle = Path(tmp) / "48"
            cycle.mkdir()
            workflow = SimpleNamespace(cycle_dir=cycle)
            state = {"cycle_number": 48, "cycle_id": "48", "run_id": "48", "attempt_id": "attempt-a", "hypothesis_lock": {"status": "locked"}}
            WorkflowStateMixin._write_replay_checkpoint(workflow, state, "investigator_start")
            checkpoint = json.loads((cycle / "replay_checkpoints/investigator_start.json").read_text())
            encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            self.assertEqual(checkpoint["state_sha256"], hashlib.sha256(encoded).hexdigest())
            self.assertEqual(checkpoint["logical_cycle"], 48)
            self.assertEqual(checkpoint["attempt_id"], "attempt-a")
            self.assertEqual(checkpoint["boundary"], "after_frozen_h_lock_before_investigator")
            self.assertEqual(checkpoint["state"], state)

    def test_start_replay_executes_in_a_distinct_private_task(self):
        from theory_master.backends import FakeBackend
        from theory_master.cycle_replay import replay_cycle
        from theory_master.specification import TaskSpec
        from theory_master.task import bootstrap
        from theory_master.workflow import Workflow
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plan = base / "source-task" / "research_plan.md"
            plan.parent.mkdir()
            plan.write_text("# Goal\n\nComplete a local deterministic check.\n\n# Research Contract\n\nUse the local fixture.\n\n# Final Gate\n\nA result is recorded.\n\n# Required Context\n\n- local fixture\n", encoding="utf-8")
            spec = TaskSpec.from_config(bootstrap(plan).config_path)
            source = spec.research_root / "1"
            workflow = Workflow(spec, FakeBackend(reworks=0))
            workflow._bind_cycle(source, create=True)
            state = {"cycle_number": 1, "cycle_id": "1", "attempt_id": "original-attempt", "status": "over",
                     "execution_active": False, "hypothesis_lock": {"status": "unlocked"}}
            workflow._write_replay_checkpoint(state, "cycle_start")
            before_plan = spec.document.read_bytes()
            before_canvas = spec.branch_canvas.read_bytes()
            before_state = json.loads((source / "replay_checkpoints/cycle_start.json").read_text())
            dry = replay_cycle(spec, source, "start", dry_run=True)
            self.assertEqual(dry["status"], "validated")
            self.assertFalse((spec.research_root / ".cycle-replays/1").exists())
            self.assertFalse((spec.research_root / ".cycle-replay.lock").exists())
            (source / "research_state.json").write_text(json.dumps({"status": "active", "execution_active": True,
                                                                      "owner_pid": __import__("os").getpid()}))
            with self.assertRaisesRegex(ValueError, "active writer"):
                replay_cycle(spec, source, "start", backend_factory=lambda _spec, _routes: FakeBackend(reworks=0))
            (source / "research_state.json").unlink()
            outcome = replay_cycle(spec, source, "start", backend_factory=lambda _spec, _routes: FakeBackend(reworks=0))
            duplicate = replay_cycle(spec, source, "start", backend_factory=lambda _spec, _routes: FakeBackend(reworks=0))
            attempt = Path(outcome["execution_root"])
            self.assertEqual(outcome["status"], "over")
            self.assertNotEqual(outcome["attempt_id"], duplicate["attempt_id"])
            self.assertNotEqual(outcome["attempt_id"], "original-attempt")
            self.assertTrue((attempt / "manifest.json").is_file())
            self.assertEqual((attempt / "frozen_inputs/research_plan.md").read_bytes(), before_plan)
            self.assertEqual((attempt / "frozen_inputs/branch_canvas.canvas").read_bytes(), before_canvas)
            self.assertTrue((attempt / "research" / outcome["replay_cycle_id"] / "research_state.json").is_file())
            self.assertEqual(spec.document.read_bytes(), before_plan)
            self.assertEqual(spec.branch_canvas.read_bytes(), before_canvas)
            self.assertEqual(json.loads((source / "replay_checkpoints/cycle_start.json").read_text()), before_state)

            class FailingBackend(FakeBackend):
                def invoke(self, *args, **kwargs):
                    raise RuntimeError("synthetic replay failure")

            with self.assertRaisesRegex(RuntimeError, "synthetic replay failure"):
                replay_cycle(spec, source, "start", backend_factory=lambda _spec, _routes: FailingBackend(reworks=0))
            manifests = sorted((spec.research_root / ".cycle-replays/1").glob("*/manifest.json"))
            failed = [json.loads(path.read_text()) for path in manifests if json.loads(path.read_text()).get("status") == "failed"]
            self.assertEqual(len(failed), 1)
            self.assertIn("synthetic replay failure", failed[0]["error"])
            self.assertEqual(spec.document.read_bytes(), before_plan)

    def test_investigator_replay_uses_frozen_lock_and_private_workspace(self):
        from theory_master.backends import FakeBackend
        from theory_master.cycle_replay import replay_cycle
        from theory_master.specification import TaskSpec
        from theory_master.task import bootstrap
        from theory_master.workflow import Workflow
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plan = base / "source-task" / "research_plan.md"
            plan.parent.mkdir()
            plan.write_text("# Goal\n\nComplete a local deterministic check.\n\n# Research Contract\n\nUse the local fixture.\n\n# Final Gate\n\nA result is recorded.\n\n# Required Context\n\n- local fixture\n", encoding="utf-8")
            spec = TaskSpec.from_config(bootstrap(plan).config_path)
            original = Workflow(spec, FakeBackend(reworks=0)).run()
            source = Path(original["cycle_dir"])
            before = json.loads((source / "replay_checkpoints/investigator_start.json").read_text())
            frozen_canvas = (source / "replay_checkpoints/investigator_start.inputs/branch_canvas.canvas").read_bytes()
            source_canvas = spec.branch_canvas.read_bytes()
            outcome = replay_cycle(spec, source, "investigator", backend_factory=lambda _spec, _routes: FakeBackend(reworks=0))
            attempt = Path(outcome["execution_root"])
            self.assertEqual(outcome["status"], "over")
            self.assertNotEqual(outcome["replay_cycle_id"], source.name)
            manifest = json.loads((attempt / "manifest.json").read_text())
            self.assertEqual(manifest["frozen_input_sha256"]["branch_canvas"], hashlib.sha256(frozen_canvas).hexdigest())
            self.assertTrue((attempt / "research" / outcome["replay_cycle_id"] / "workspace").is_dir())
            self.assertEqual(spec.branch_canvas.read_bytes(), source_canvas)
            self.assertEqual(json.loads((source / "replay_checkpoints/investigator_start.json").read_text()), before)
            with self.assertRaisesRegex(ValueError, "complete, reconciled Findings/Evidence"):
                replay_cycle(spec, source, "judge", dry_run=True)
            frozen_canvas_path = source / "replay_checkpoints/investigator_start.inputs/branch_canvas.canvas"
            frozen_canvas_path.write_bytes(frozen_canvas + b"tamper")
            with self.assertRaisesRegex(ValueError, "input digest mismatch: branch_canvas"):
                replay_cycle(spec, source, "investigator", dry_run=True)

    def test_large_benchmark_is_verified_by_hash_without_repeated_copy(self):
        from theory_master.backends import FakeBackend
        from theory_master.cycle_replay import replay_cycle
        from theory_master.specification import TaskSpec
        from theory_master.task import bootstrap
        from theory_master.workflow import Workflow
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "source" / "research_plan.md"
            plan.parent.mkdir()
            plan.write_text(
                "# Goal\n\nA check.\n\n# Research Contract\n\nOnly frozen data.\n\n"
                "# Final Gate\n\nCheck passed.\n\n# Required Context\n\n- fixture\n",
                encoding="utf-8",
            )
            binding = bootstrap(plan)
            config = json.loads(binding.config_path.read_text(encoding="utf-8"))
            benchmark = Path(config["benchmark_root"])
            benchmark.mkdir(parents=True, exist_ok=True)
            data = benchmark / "response.bin"
            data.write_bytes(b"scientific-input" * 8000)
            spec = TaskSpec.from_config(binding.config_path)
            wf = Workflow(spec, FakeBackend())
            cycle = spec.research_root / "1"
            wf._bind_cycle(cycle, create=True)
            wf._write_replay_checkpoint({"cycle_number": 1, "attempt_id": "a", "hypothesis_lock": {"status": "unlocked"}}, "cycle_start")
            record = json.loads((cycle / "replay_checkpoints/cycle_start.json").read_text())
            self.assertEqual(record["external_references"]["benchmark"], str(benchmark.resolve()))
            self.assertFalse((cycle / "replay_checkpoints/cycle_start.inputs/benchmark").exists())
            self.assertEqual(replay_cycle(spec, cycle, "start", dry_run=True)["status"], "validated")
            data.write_bytes(b"changed-dataset")
            with self.assertRaisesRegex(ValueError, "external scientific input has changed"):
                replay_cycle(spec, cycle, "start", dry_run=True)

    def test_large_optional_snapshot_is_unavailable_without_interrupting_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "48"
            workspace = cycle / "workspace"
            workspace.mkdir(parents=True)
            huge = workspace / "simulated-big-output.dat"
            with huge.open("wb") as handle:
                handle.truncate(513 * 1024 * 1024)
            state = {"cycle_number": 48, "attempt_id": "original", "hypothesis_lock": {"status": "locked"}}
            wf = SimpleNamespace(cycle_dir=cycle, cycle_workspace=workspace, spec=None, routes={})
            WorkflowStateMixin._write_replay_checkpoint(wf, state, "investigator_start")
            unavailable = cycle / "replay_checkpoints/investigator_start.unavailable.json"
            self.assertTrue(unavailable.is_file())
            self.assertIn("size/free-space safety cap", unavailable.read_text())
            self.assertFalse((cycle / "replay_checkpoints/investigator_start.json").exists())
            self.assertEqual(huge.stat().st_size, 513 * 1024 * 1024)

    def test_checkpoint_is_immutable_on_second_boundary_visit(self):
        with tempfile.TemporaryDirectory() as tmp:
            cycle = Path(tmp) / "48"
            cycle.mkdir()
            wf = SimpleNamespace(cycle_dir=cycle, spec=None, routes={})
            first = {"cycle_number": 48, "attempt_id": "original"}
            WorkflowStateMixin._write_replay_checkpoint(wf, first, "cycle_start")
            before = (cycle / "replay_checkpoints/cycle_start.json").read_bytes()
            WorkflowStateMixin._write_replay_checkpoint(wf, {"cycle_number": 48, "attempt_id": "rewritten"}, "cycle_start")
            self.assertEqual(before, (cycle / "replay_checkpoints/cycle_start.json").read_bytes())

    def test_rerun_rejects_missing_snapshot_without_modifying_source_cycle(self):
        from unittest.mock import patch
        from theory_master.__main__ import cycle_command
        with tempfile.TemporaryDirectory() as tmp:
            cycle = Path(tmp) / "48"
            cycle.mkdir()
            state = cycle / "research_state.json"
            state.write_text('{"status":"over"}\n')
            before = state.read_bytes()
            spec = SimpleNamespace(research_root=Path(tmp), task_mode=True)
            args = SimpleNamespace(master_id="1", cycle_number=48, cycle_action="rerun", from_stage="investigator")
            error = StringIO()
            with patch("theory_master.cli_discovery.spec_from_task", return_value=spec), \
                 patch("theory_master.specification.list_cycle_dirs", return_value=[cycle]), \
                 redirect_stderr(error):
                result = cycle_command(args)
            self.assertEqual(result, 2)
            self.assertIn("no complete investigator replay checkpoint", error.getvalue())
            self.assertEqual(state.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
