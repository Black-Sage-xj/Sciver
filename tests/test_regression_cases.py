from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from theory_master.regression_cases import grade_case, materialize_case, resolve_case, verify_case


class RegressionCasesTest(unittest.TestCase):
    def test_only_test0_is_public(self):
        self.assertEqual(resolve_case("Test0").case_id, "test_0")
        for old_selector in ("UnknownCase", "999", "unsupported_case"):
            with self.subTest(selector=old_selector), self.assertRaisesRegex(Exception, "unknown TheoryMaster regression case"):
                resolve_case(old_selector)

    def test_test0_aliases_and_manifest(self):
        case = resolve_case("Test0")
        self.assertEqual(case.case_id, "test_0")
        self.assertEqual(resolve_case("brownian").case_id, "test_0")
        self.assertEqual(verify_case("test0")["files_verified"], 1)
        self.assertIn("original", case.metadata["kind"])
        source = json.loads((case.benchmark_dir / "source_manifest.json").read_text(encoding="utf8"))
        self.assertFalse(source["origin"]["third_party_data"])

    def test_test0_materialization_excludes_hidden_scoring(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = materialize_case("test0", Path(temp) / "fresh")
            self.assertTrue((dest / "benchmark/datasets/brownian_2d/trajectories.csv").is_file())
            for private_path in ("case.json", "expected.json", "evaluator", "evaluator/grade.py"):
                self.assertFalse((dest / private_path).exists())
            visible_text = "".join(p.read_text(encoding="utf8") for p in dest.rglob("*.md"))
            self.assertNotIn("0.37", visible_text)
            self.assertNotIn("0.22", visible_text)

    def test_test0_data_and_scoring(self):
        import csv
        import subprocess
        import sys
        case = resolve_case("test0")
        generator = case.root / "evaluator/generate_data.py"
        proc = subprocess.run([sys.executable, str(generator), "--check"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        dataset = case.benchmark_dir / "datasets/brownian_2d/trajectories.csv"
        with dataset.open(encoding="utf8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 40_000)
        self.assertEqual(sum(row["split"] == "train" for row in rows), 30_000)
        self.assertEqual(sum(row["split"] == "validation" for row in rows), 10_000)
        xy = np.asarray([(float(row["x"]), float(row["y"])) for row in rows]).reshape(200,200,2)
        def fit(partition):
            msd = [np.square(partition[:,k:,:]-partition[:,:-k,:]).sum(axis=2).mean()
                   for k in range(1,9)]
            return float(np.polyfit(np.arange(1,9)*0.1, msd, 1)[0]/4)
        estimated = fit(xy[:150])
        holdout = fit(xy[150:])
        self.assertLess(abs(estimated-0.37)/0.37, 0.1)
        self.assertLess(abs(estimated-holdout)/estimated, 0.1)
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp)/"result.json"
            result.write_text(json.dumps({"D_estimate":estimated,"method":"MSD slope","validation":"holdout slope"}))
            score = grade_case("Test0",result)
            self.assertTrue(score["passed"], score)
            self.assertLess(score["relative_error"], .1)
            result.write_text(json.dumps({"D_estimate":float(np.square(xy[:150,1,:]-xy[:150,0,:]).sum(axis=1).mean()/0.4)}))
            self.assertFalse(grade_case("Test0",result)["passed"])
            result.write_text(json.dumps({"D_estimate":"0.37"}))
            self.assertFalse(grade_case("Test0",result)["passed"])
            result.write_text(json.dumps({"D_estimate":float("nan")}))
            self.assertFalse(grade_case("Test0",result)["passed"])

    def test_nonempty_destination_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "case"
            root.mkdir()
            (root / "keep.txt").write_text("do not overwrite", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "absent or empty"):
                materialize_case("Test0", root)
            self.assertEqual((root / "keep.txt").read_text(encoding="utf-8"), "do not overwrite")


if __name__ == "__main__":
    unittest.main()
