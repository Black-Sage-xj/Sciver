from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from theory_master.change_register import main


class ChangeRegisterTest(unittest.TestCase):
    def test_add_idempotent_verify_independent_of_cycle_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "changes" / "register.json"
            def run(*args):
                output, error = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    result = main(["--store", str(store), *args])
                return result, output.getvalue(), error.getvalue()
            params = ("add", "--title", "Icon only", "--source", "user")
            self.assertEqual(run(*params)[0], 0)
            first = json.loads(store.read_text())["items"][0]
            self.assertEqual(first["state"], "registered")
            self.assertEqual(run(*params)[0], 0)
            self.assertEqual(len(json.loads(store.read_text())["items"]), 1)
            self.assertEqual(run("verify", first["id"], "--reviewer", "tester",
                "--evidence", "test_ui: 4 passed")[0], 0)
            self.assertEqual(json.loads(store.read_text())["items"][0]["state"], "verified")

    def test_legacy_source_dedup_and_no_false_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "changes.json"
            args = ["--store", str(store), "add", "--title", "Old UI",
                    "--source", "legacy", "--source-tma", "TMA-0001",
                    "--legacy-status", "failed"]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 0)
            records = json.loads(store.read_text())["items"]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["legacy_audit_status"], "failed")
            self.assertEqual(records[0]["state"], "registered")


if __name__ == "__main__":
    unittest.main()
