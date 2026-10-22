"""Offline regression for one Supervisor identity and durable engineering recovery."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from theory_master.identity import identity_block
from theory_master.supervisor_continuity import (
    engineering_skill_eligible, attempt_engineering_skill,
)
from theory_master.worker_supervisor_dispatch import (
    _eligible, _fingerprint, _legacy_fingerprint, dispatch_pending,
)
from theory_master.worker_runtime import save_worker, worker_dir


class UnifiedSupervisorTests(unittest.TestCase):
    def test_one_identity_with_separate_skill_permissions(self):
        runtime = identity_block("runtime_supervisor")
        engineering = identity_block("incident_supervisor")
        self.assertIn("single TheoryMaster Supervisor", runtime)
        self.assertIn("single TheoryMaster Supervisor", engineering)
        self.assertIn("read-only runtime triage skill", runtime)
        self.assertIn("You are not read-only", engineering)
        self.assertNotIn("You are not read-only", runtime)

    def test_engineering_skill_dispatches_exact_repair_suggestion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cycle = root / "127"
            cycle.mkdir()
            state = {
                "status": "failed", "resumable": True,
                "role_outputs": {"lead": {"done": True}},
                "recovery": {"message": "Unknown frontier H"},
            }
            review = {"action": "hold", "repair_suggestion": "Repair Canvas resolver",
                      "session_id": "SUP-test"}
            with patch("theory_master.supervisor_incident_session.publish_review_incident",
                       return_value={"id": "SIS-test"}) as publish, \
                 patch("theory_master.supervisor._supervisor_backend", return_value=MagicMock()), \
                 patch("theory_master.supervisor_candidate_repair.attempt_candidate_repair",
                       return_value={"phase": "blocked", "reason": "needs evidence"}) as invoke:
                result = attempt_engineering_skill(root, cycle, state, review)
            self.assertEqual(result["status"], "engineering_attempted")
            self.assertEqual(result["verification"], "pending_runtime_recheck")
            self.assertIn("Repair Canvas resolver", publish.call_args.kwargs["system_note"])
            invoke.assert_called_once()
            self.assertEqual(invoke.call_args.kwargs["review"], review)

    def test_scientific_and_quota_boundaries_never_call_engineering_skill(self):
        baseline = {"resumable": True, "role_outputs": {"lead": True},
                    "recovery": {"message": "Unknown frontier H"}}
        review = {"action": "hold", "repair_suggestion": "Find bug"}
        self.assertTrue(engineering_skill_eligible(baseline, review))
        self.assertFalse(engineering_skill_eligible(
            {**baseline, "abandoned_cycle": True}, review))
        self.assertFalse(engineering_skill_eligible(
            {**baseline, "investigator_budget": {"exhausted": True}}, review))
        self.assertFalse(engineering_skill_eligible(
            {**baseline, "role_outputs": {}}, review))
        self.assertFalse(engineering_skill_eligible(
            {**baseline, "recovery": {"message": "Selected model is at capacity"}}, review))
        self.assertFalse(engineering_skill_eligible(
            {**baseline, "recovery": {"message": "usage limit reached"}}, review))
        self.assertFalse(engineering_skill_eligible(
            baseline, {"action": "resume", "repair_suggestion": "Find bug"}))

    def fixture(self, root: Path, message: str = "Traceback unexpected Failure"):
        cycle = root / "127"
        cycle.mkdir()
        state = {"status": "failed", "phase": "investigator:running",
                 "worker_id": "worker-1", "hypothesis_lock": {"id": "H-0127"},
                 "recovery": {"kind": "RuntimeError", "message": message,
                              "at": "2026-10-19T15:00:00+08:00"}}
        (cycle / "research_state.json").write_text(json.dumps(state))
        save_worker(root, "worker-1", status="FAILED", current_cycle="127",
                    entry_node_id="H-0127", target_h_id="H-0127",
                    account=None, claim_id="test", epoch=1, pid=None)
        (root / ".theory-master" / "cycle_claims.json").write_text(json.dumps({
            "schema": "theory-master/cycle-claims/v1", "epoch": 1, "history": [],
            "claims": {"test": {"claim_id": "test", "fencing_epoch": 1, "worker_id": "worker-1",
                                "cycle_id": "127", "hypothesis_id": "H-0127", "status": "claimed"}}}))
        (root / ".theory-master" / "env").write_text(
            "THEORY_MASTER_SUPERVISOR=1\n")
        return cycle, state

    def test_engineering_hold_at_attempt_cap_schedules_replanning_without_launch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, state = self.fixture(root)
            receipt = worker_dir(root, "worker-1") / "supervisor_dispatch.json"
            receipt.write_text(json.dumps({
                "cycle": "127", "worker_id": "worker-1",
                "fingerprint": _fingerprint(state),
                "failure_signature": _fingerprint(state), "status": "hold",
                "recovery_attempts": 3, "reason": "Runtime engineering error",
            }))
            with patch("theory_master.worker_supervisor_dispatch.time.time",
                       return_value=1000), \
                 patch("theory_master.worker_supervisor_dispatch.subprocess.Popen") as popen:
                self.assertTrue(dispatch_pending(root))
                popen.assert_not_called()
            recorded = json.loads(receipt.read_text())
            self.assertEqual(recorded["status"], "retry_wait")
            self.assertEqual(recorded["recovery_attempts"], 3)
            self.assertGreater(recorded["retry_at_epoch"], 1000)

    def test_failure_fingerprint_ignores_recovery_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, state = self.fixture(root)
            changed = json.loads(json.dumps(state))
            changed["recovery"]["at"] = "2026-10-20T17:00:00+08:00"
            self.assertEqual(_fingerprint(state), _fingerprint(changed))


    def test_legacy_receipt_migrates_without_resetting_retry_gate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, state = self.fixture(root)
            receipt = worker_dir(root, "worker-1") / "supervisor_dispatch.json"
            receipt.write_text(json.dumps({
                "cycle": "127", "worker_id": "worker-1",
                "fingerprint": _legacy_fingerprint(state), "status": "retry_wait",
                "recovery_attempts": 2, "retry_at_epoch": 2000,
                "reason": "Runtime engineering error",
            }))
            with patch("theory_master.worker_supervisor_dispatch.time.time", return_value=1000), \
                 patch("theory_master.worker_supervisor_dispatch.subprocess.Popen") as popen:
                self.assertTrue(dispatch_pending(root))
                popen.assert_not_called()
            recorded = json.loads(receipt.read_text())
            self.assertEqual(recorded["fingerprint"], _fingerprint(state))
            self.assertEqual(recorded["failure_signature"], _fingerprint(state))
            self.assertEqual(recorded["recovery_attempts"], 2)
            self.assertEqual(recorded["status"], "retry_wait")

    def test_changed_failure_signature_allows_a_new_bounded_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, state = self.fixture(root)
            receipt = worker_dir(root, "worker-1") / "supervisor_dispatch.json"
            receipt.write_text(json.dumps({
                "cycle": "127", "worker_id": "worker-1",
                "fingerprint": "previous-fingerprint", "failure_signature": "previous-signature",
                "status": "manual_review_required", "recovery_attempts": 3,
                "reason": "Previous deterministic failure",
            }))
            with patch("theory_master.worker_supervisor_dispatch.subprocess.Popen",
                       return_value=MagicMock(pid=1234)) as popen, \
                 patch("theory_master.worker_supervisor_dispatch.process_start_ticks",
                       return_value=17):
                self.assertTrue(dispatch_pending(root))
                popen.assert_called_once()
            recorded = json.loads(receipt.read_text())
            self.assertEqual(recorded["status"], "starting")
            self.assertEqual(recorded["recovery_attempts"], 1)
            self.assertEqual(recorded["failure_signature"], _fingerprint(state))

    def test_unsupported_model_routes_to_same_supervisor_but_quota_does_not(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cycle, state = self.fixture(root, "Selected model is not supported")
            worker = {"worker_id": "worker-1", "status": "FAILED",
                      "process_alive": False, "current_cycle": "127",
                      "claim_id": "test", "epoch": 1}
            self.assertEqual(_eligible(root, worker)[0], cycle)
            state["recovery"]["message"] = "usage limit reached"
            (cycle / "research_state.json").write_text(json.dumps(state))
            self.assertIsNone(_eligible(root, worker))
            state["recovery"]["message"] = "Transient unknown failure"
            state["abandoned_cycle"] = True
            (cycle / "research_state.json").write_text(json.dumps(state))
            self.assertIsNone(_eligible(root, worker))


if __name__ == "__main__":
    unittest.main()
