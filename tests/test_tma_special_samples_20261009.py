"""Deterministic preflight samples for TMA-0004 and TMA-0006.

Do not substitute these no-provider tests for real credential/concurrent-Task
stress testing or a production crash replay with a paid external invocation.
"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from theory_master.contracts import role_contract
from theory_master.invocations import InvocationLedger, reconcile, stable_hash
from theory_master.codex_accounts import account_switch_lock


class InvocationContractVersionFenceSample(unittest.TestCase):
    def test_frozen_v8_persisted_ref_does_not_resolve_as_current_contract(self):
        current = role_contract("research_lead", phase="frontier_proposal")
        self.assertGreater(current.version, 8)
        # Invocation identity is an opaque persisted descriptor. Runtime does
        # not load historical schemas or promote old output to a current contract.
        v8 = {"contract_id": current.contract_id, "contract_version": 8,
              "schema_hash": "frozen-historical-schema-hash"}
        current_ref = current.descriptor()
        self.assertNotEqual(v8["schema_hash"], current_ref["schema_hash"])
        with tempfile.TemporaryDirectory() as td:
            ledger = InvocationLedger(Path(td) / "invocations.jsonl")
            payload = {"goal": "synthetic-historical", "scientific_model_version": 8}
            ledger.prepare(invocation_id="inv-v8", kind="agent", input_payload=payload,
                           contract_ref=v8, logical_key="one-slot",
                           role="research_lead", stage="frontier_proposal")
            ledger.transition("inv-v8", "committed", commit_state="committed",
                              closure_hash="sha-of-saved-result")
            restored = InvocationLedger(Path(td) / "invocations.jsonl")
            self.assertIsNotNone(restored.find_committed(
                logical_key="one-slot", contract_ref=v8,
                role="research_lead", stage="frontier_proposal"))
            self.assertIsNone(restored.find_committed(
                logical_key="one-slot", contract_ref=current_ref,
                role="research_lead", stage="frontier_proposal"))
            snap = restored.snapshot("inv-v8")
            self.assertEqual(snap["contract_ref"]["schema_hash"], v8["schema_hash"])
            self.assertEqual(reconcile(snap, input_hash=stable_hash(payload),
                                       artifacts_valid=True).action, "reuse")


class AccountConcurrencyLockSample(unittest.TestCase):
    def test_two_task_account_switch_operations_do_not_overlap(self):
        """Accounts are simulated; global account root and identities are untouched."""
        with tempfile.TemporaryDirectory() as td:
            lock_root = Path(td) / "accounts"
            active = 0
            peak = 0
            recorded = []
            gate = threading.Barrier(2)

            def worker(task, account):
                nonlocal active, peak
                gate.wait()
                with account_switch_lock():
                    active += 1
                    peak = max(peak, active)
                    recorded.append((task, account))
                    time.sleep(0.025)
                    active -= 1

            with patch("theory_master.codex_accounts.account_root", return_value=lock_root):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    left = pool.submit(worker, "task-A", "1")
                    right = pool.submit(worker, "task-B", "2")
                    left.result(timeout=5)
                    right.result(timeout=5)
            self.assertEqual(peak, 1)
            self.assertCountEqual(recorded, [("task-A", "1"), ("task-B", "2")])


if __name__ == "__main__":
    unittest.main()
