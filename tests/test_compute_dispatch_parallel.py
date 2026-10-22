"""Safe independent scientific task dispatch: actual subprocess/concurrency tests."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from theory_master.skills import role_skills, skill_prompt_block


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ComputeDispatchParallelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        self.script = self.workspace / "worker.py"
        self.script.write_text(
            "import json,os,sys,time\n"
            "from pathlib import Path\n"
            "num=int(sys.argv[1]); payload=int(Path(sys.argv[2]).read_text())\n"
            "flag=Path('.failed_once')\n"
            "if os.environ.get('FAIL_ONCE')=='yes' and not flag.exists():\n"
            "    flag.write_text('first attempt failed')\n"
            "    sys.exit(23)\n"
            "start=time.time(); time.sleep(0.13); end=time.time()\n"
            "Path('result.json').write_text(json.dumps({'value':num*num+payload, 'start':start,'end':end,\n"
            "  'pid':os.getpid(),'blas':os.environ.get('OPENBLAS_NUM_THREADS'),"
            "'omp':os.environ.get('OMP_NUM_THREADS')}))\n",
            encoding="utf-8",
        )
        self.frozen = self.workspace / "frozen.txt"
        self.frozen.write_text("7", encoding="utf-8")
        self.manifest = self.workspace / "units.json"
        self.tool = (Path(__file__).resolve().parents[1] / "theory_master" / "resources"
                     / "skills" / "compute-dispatch" / "scripts" / "parallel_units.py")
        self.inputs = [{"path": str(f), "sha256": digest(f)} for f in (self.script, self.frozen)]
        self.state_dir = self.workspace / "units.dispatch"

    def unit(self, number: int, *, fail: bool = False) -> dict:
        return {
            "id": f"root_{number:03}", "cwd": f"units/root_{number:03}",
            "argv": [sys.executable, str(self.script), str(number), str(self.frozen)],
            "inputs": self.inputs,
            "outputs": ["result.json"],
            "env": {"FAIL_ONCE": "yes"} if fail else {},
        }

    def write_manifest(self, units: list[dict]):
        self.manifest.write_text(json.dumps({
            "schema": "theory-master/independent-compute-units/v1",
            "units": units,
        }), encoding="utf-8")

    def run_tool(self, *options: str, timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.tool), "--workspace", str(self.workspace),
             "--manifest", str(self.manifest), "--reserve-disk-mib", "0",
             "--reserve-memory-mib", "0", "--memory-mib-per-worker", "1",
             "--disk-mib-per-worker", "1", "--max-workers", "8", *options],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout,
        )

    def test_dry_run_checks_frozen_inputs_but_does_not_create_state(self):
        self.write_manifest([self.unit(n) for n in range(4)])
        run = self.run_tool("--dry-run", "--workers", "8", "--max-workers", "2")
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report["status"], "validated_dry_run")
        self.assertEqual(report["units"], 4)
        self.assertLessEqual(report["resource_plan"]["workers"], 2)
        self.assertFalse(self.state_dir.exists())

    def test_parallel_run_and_idempotent_resume_preserves_scientific_results(self):
        self.write_manifest([self.unit(n) for n in range(8)])
        first = self.run_tool("--workers", "4")
        self.assertEqual(first.returncode, 0, first.stderr)
        report = json.loads(first.stdout)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["executed_now"], 8)
        self.assertEqual(report["scientific_verdict"], None)
        self.assertEqual(report["failed"], [])
        intervals = []
        outputs = {}
        for i in range(8):
            raw = self.workspace / f"units/root_{i:03}/result.json"
            data = json.loads(raw.read_text())
            self.assertEqual(data["value"], i * i + 7)
            self.assertEqual(data["blas"], "1")
            self.assertEqual(data["omp"], "1")
            self.assertTrue((self.state_dir / "units" / f"root_{i:03}.json").exists())
            intervals.append((data["start"], data["end"]))
            outputs[str(raw)] = digest(raw)
        if report["resource_plan"]["workers"] > 1:
            intervals.sort()
            self.assertLess(intervals[1][0], intervals[0][1], "no observed concurrent jobs")
        again = self.run_tool("--workers", "4")
        self.assertEqual(again.returncode, 0, again.stderr)
        payload = json.loads(again.stdout)
        self.assertEqual(payload["skipped_verified"], 8)
        self.assertEqual(payload["executed_now"], 0)
        self.assertEqual({str(Path(p)): digest(Path(p)) for p in outputs}, outputs)

    def test_failed_unit_requires_explicit_retry_then_only_failed_reruns(self):
        self.write_manifest([self.unit(0), self.unit(1, fail=True), self.unit(2)])
        first = self.run_tool("--workers", "3")
        self.assertEqual(first.returncode, 1, first.stderr)
        result = json.loads(first.stdout)
        self.assertEqual(result["failed"], ["root_001"])
        previous_zero = digest(self.workspace / "units/root_000/result.json")
        retry_without_permission = self.run_tool("--workers", "3")
        self.assertEqual(retry_without_permission.returncode, 2)
        self.assertIn("--retry-failed", retry_without_permission.stderr)
        continued = self.run_tool("--workers", "3", "--retry-failed")
        self.assertEqual(continued.returncode, 0, continued.stderr)
        detail = json.loads(continued.stdout)
        self.assertEqual(detail["skipped_verified"], 2)
        self.assertEqual(detail["executed_now"], 1)
        self.assertEqual(digest(self.workspace / "units/root_000/result.json"), previous_zero)
        unit = json.loads((self.state_dir / "units/root_001.json").read_text())
        self.assertEqual(unit["attempt"], 2)
        self.assertTrue((self.state_dir / "logs/root_001.attempt-1.stderr.log").exists())
        self.assertTrue((self.state_dir / "logs/root_001.attempt-2.stderr.log").exists())

    def test_input_change_rejected_without_mutating_completed_unit(self):
        self.write_manifest([self.unit(0)])
        self.assertEqual(self.run_tool().returncode, 0)
        output = self.workspace / "units/root_000/result.json"
        before = digest(output)
        self.frozen.write_text("9")
        resumed = self.run_tool("--retry-failed")
        self.assertEqual(resumed.returncode, 2)
        self.assertIn("frozen input missing or changed", resumed.stderr)
        self.assertEqual(digest(output), before)

    def test_certified_completed_output_change_is_refused(self):
        self.write_manifest([self.unit(0)])
        self.assertEqual(self.run_tool().returncode, 0)
        output = self.workspace / "units/root_000/result.json"
        output.write_text('{"tampered":true}', encoding="utf-8")
        resumed = self.run_tool()
        self.assertEqual(resumed.returncode, 2)
        self.assertIn("completed output changed", resumed.stderr)

    def test_uncertified_existing_output_is_not_overwritten(self):
        self.write_manifest([self.unit(0)])
        output = self.workspace / "units/root_000/result.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("precious", encoding="utf-8")
        resumed = self.run_tool()
        self.assertEqual(resumed.returncode, 2)
        self.assertIn("existing outputs without certified state", resumed.stderr)
        self.assertEqual(output.read_text(), "precious")

    def test_changed_manifest_rejected_to_avoid_reinterpreting_history(self):
        self.write_manifest([self.unit(0)])
        self.assertEqual(self.run_tool().returncode, 0)
        original = self.unit(0)
        original["argv"].append("--different-science")
        self.write_manifest([original])
        resumed = self.run_tool()
        self.assertEqual(resumed.returncode, 2)
        self.assertIn("different scientific manifest", resumed.stderr)

    def test_duplicate_ids_directory_collision_and_shell_are_rejected(self):
        bad_cases = [
            [self.unit(0), self.unit(0)],
            [{**self.unit(0), "cwd": "../outside"}],
            [{**self.unit(0), "argv": ["bash", "-c", "echo unsafe"]}],
            [{**self.unit(0), "inputs": []}],
            [self.unit(0), {**self.unit(1), "cwd": self.unit(0)["cwd"]}],
            [{**self.unit(0), "outputs": ["../other-file.json"]}],
        ]
        for bad in bad_cases:
            with self.subTest(bad=bad):
                self.write_manifest(bad)
                result = self.run_tool()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse((self.workspace / "units/root_000/result.json").exists())

    def test_parallel_dispatch_is_locked_against_concurrent_processes(self):
        self.write_manifest([self.unit(0)])
        self.state_dir.mkdir()
        with (self.state_dir / ".lock").open("a+b") as file:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_tool()
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("already running", result.stderr)

    def test_expected_science_inputs_must_include_frozen_script_hash(self):
        self.write_manifest([self.unit(0)])
        self.script.write_text("raise SystemExit(42)", encoding="utf-8")
        result = self.run_tool("--dry-run")
        self.assertEqual(result.returncode, 2)
        self.assertIn("frozen input missing or changed", result.stderr)

    def test_readonly_frozen_input_cannot_live_under_writable_unit_directory(self):
        isolated = self.workspace / "units/root_000"
        isolated.mkdir(parents=True)
        science = isolated / "frozen_extra.txt"
        science.write_text("original science")
        unit = self.unit(0)
        unit["inputs"] = [*self.inputs, {"path": str(science), "sha256": digest(science)}]
        self.write_manifest([unit])
        result = self.run_tool("--dry-run")
        self.assertEqual(result.returncode, 2)
        self.assertIn("frozen scientific input lies inside a writable unit workspace", result.stderr)

    def test_low_disk_safety_guard_refuses_without_spawning_jobs(self):
        self.write_manifest([self.unit(0), self.unit(1)])
        result = self.run_tool("--dry-run", "--reserve-disk-mib", str(10**9))
        self.assertEqual(result.returncode, 2)
        self.assertIn("not enough free memory or disk", result.stderr)
        self.assertFalse(self.state_dir.exists())
        self.assertFalse((self.workspace / "units/root_000/result.json").exists())

    def test_existing_foreign_state_directory_is_not_overwritten(self):
        self.write_manifest([self.unit(0)])
        self.state_dir.mkdir()
        sentinel = self.state_dir / "foreign.log"
        sentinel.write_text("not from dispatcher")
        result = self.run_tool()
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a dispatcher state", result.stderr)
        self.assertEqual(sentinel.read_text(), "not from dispatcher")

    def test_manifest_is_not_allowed_in_a_unit_writable_directory(self):
        self.manifest = self.workspace / "units/root_000/manifest.json"
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.write_manifest([self.unit(0)])
        result = self.run_tool("--dry-run")
        self.assertEqual(result.returncode, 2)
        self.assertIn("manifest is inside a writable unit workspace", result.stderr)

    def test_real_skill_whitelist_and_execution_docs(self):
        refs = role_skills("investigator")
        compute = next(ref for ref in refs if ref.directory == "compute-dispatch")
        self.assertEqual(self.tool, compute.skill_md.parent / "scripts" / "parallel_units.py")
        self.assertTrue(self.tool.is_file())
        doc = compute.skill_md.read_text(encoding="utf-8")
        numerical = next(ref for ref in refs if ref.directory == "numerical-investigation")
        ndoc = numerical.skill_md.read_text(encoding="utf-8")
        self.assertIn("重新评估", doc)
        self.assertIn("并行", doc)
        self.assertIn("--dry-run", doc)
        self.assertIn("parallel_units.py", doc)
        self.assertIn("重新评估", ndoc)
        self.assertIn("compute-dispatch/scripts/parallel_units.py", ndoc)
        block = skill_prompt_block("investigator")
        self.assertIn(str(compute.skill_md), block)


if __name__ == "__main__":
    unittest.main()
