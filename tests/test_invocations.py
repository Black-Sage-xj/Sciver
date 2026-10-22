from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from theory_master.invocations import InvocationLedger, reconcile, stable_hash


class InvocationLedgerTest(unittest.TestCase):
    def test_append_only_prepare_and_transition(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = InvocationLedger(Path(temp) / "invocations.jsonl")
            prepared = ledger.prepare(invocation_id="inv-1", kind="agent", input_payload={"x": 1})
            ledger.transition("inv-1", "running", external_job_id="job-7")
            self.assertEqual(len(ledger.records()), 2)
            self.assertEqual(prepared["input_hash"], stable_hash({"x": 1}))
            self.assertEqual(ledger.latest("inv-1")["state"], "running")

    def test_reconciliation_is_at_least_once_not_exactly_once(self):
        digest = stable_hash({"x": 1})
        latest = {"state": "running", "input_hash": digest}
        self.assertEqual(reconcile(latest, input_hash=digest, artifacts_valid=True).action, "reuse")
        self.assertEqual(reconcile(latest, input_hash=digest, artifacts_valid=False, external_alive=True).action, "reattach")
        self.assertEqual(reconcile(latest, input_hash="different", artifacts_valid=True).action, "quarantine")


if __name__ == "__main__":
    unittest.main()
