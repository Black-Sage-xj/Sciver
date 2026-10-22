from __future__ import annotations

import io
from argparse import Namespace
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from theory_master.audit_queue import SCHEMA, _edit, _add, main


class AuditQueueCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Path(self.temporary.name) / "audit" / "pending_reviews.json"

    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(["--store", str(self.store), *args])
        return result, stdout.getvalue(), stderr.getvalue()

    def add(self, title="Review coverage", checks=("First evidence", "Independent replay")):
        args = [
            "add", "--title", title, "--priority", "P1", "--owner", "SDK",
            "--stage", "Qualification/recovery", "--description", "Check all boundary conditions",
            "--source", "audit/report.md",
        ]
        for check in checks:
            args.extend(("--check", check))
        result, stdout, stderr = self.run_cli(*args)
        self.assertEqual(result, 0, stderr)
        return stdout.split()[0]

    def test_add_and_list_are_persistent_with_stable_ids(self):
        first = self.add()
        second = self.add(title="Another failure")
        self.assertNotEqual(first, second)
        data = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], SCHEMA)
        self.assertEqual(data["next_sequence"], 3)
        self.assertEqual([c["id"] for c in data["items"][0]["checks"]], ["C01", "C02"])
        result, output, _ = self.run_cli("list")
        self.assertEqual(result, 0)
        self.assertIn(first, output)
        self.assertIn(second, output)
        result, output, _ = self.run_cli("show", first, "--json")
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["status"], "pending")

    def test_duplicate_same_title_and_source_must_reuse_existing_item(self):
        existing = self.add(title="Same issue")
        original = self.store.read_bytes()
        status, _, error = self.run_cli(
            "add", "--title", "same ISSUE", "--priority", "P1",
            "--owner", "SDK", "--stage", "Qualification/recovery",
            "--description", "same source", "--source", "audit/report.md",
            "--check", "A new check",
        )
        self.assertEqual(status, 2)
        self.assertIn(existing, error)
        self.assertEqual(self.store.read_bytes(), original)

    def test_cannot_close_before_all_evidence_is_reviewed(self):
        identifier = self.add()
        original = self.store.read_bytes()
        result, _, error = self.run_cli("close", identifier, "--reviewer", "auditor",
                                        "--summary", "Not actually finished")
        self.assertEqual(result, 2)
        self.assertIn("cannot close", error)
        self.assertEqual(self.store.read_bytes(), original)

        result, _, error = self.run_cli("check", identifier, "C01",
                                        "--result", "pass", "--reviewer", "auditor")
        self.assertEqual(result, 2)
        self.assertIn("evidence", error)
        self.assertEqual(self.store.read_bytes(), original)

    def test_revise_records_history_and_invalidates_evidence_from_old_contract(self):
        identifier = self.add()
        result, _, error = self.run_cli(
            "check", identifier, "C01", "--result", "blocked",
            "--evidence", "old-trace.json", "--note", "Old criterion is obsolete",
            "--reviewer", "auditor",
        )
        self.assertEqual(result, 0, error)

        result, output, error = self.run_cli(
            "revise", identifier, "--reason", "Architecture migrated to V2",
            "--description", "Rebased scope",
            "--check", "C01=Verify H-to-H lineage and one H+Outcome without legacy Decision nodes",
        )
        self.assertEqual(result, 0, error)
        self.assertIn("pending", output)

        result, output, error = self.run_cli("show", identifier, "--json")
        self.assertEqual(result, 0, error)
        item = json.loads(output)
        check = item["checks"][0]
        self.assertEqual(item["description"], "Rebased scope")
        self.assertEqual(item["status"], "pending")
        self.assertEqual(check["status"], "pending")
        self.assertEqual(check["evidence"], "")
        self.assertEqual(check["note"], "")
        self.assertEqual(check["reviewer"], "")
        self.assertIn("H-to-H lineage", check["requirement"])
        revision = item["history"][-1]
        self.assertEqual(revision["action"], "revise")
        self.assertEqual(revision["reason"], "Architecture migrated to V2")
        self.assertEqual(revision["previous_checks"][0]["requirement"], "First evidence")
        self.assertEqual(revision["previous_checks"][0]["evidence"], "old-trace.json")

    def test_revise_cannot_change_verified_or_closed_acceptance_contracts(self):
        identifier = self.add()
        result, _, error = self.run_cli(
            "check", identifier, "C01", "--result", "pass",
            "--evidence", "verified-trace.json", "--reviewer", "auditor",
        )
        self.assertEqual(result, 0, error)
        original = self.store.read_bytes()
        result, _, error = self.run_cli(
            "revise", identifier, "--reason", "try to rewrite a pass",
            "--check", "C01=Different criterion",
        )
        self.assertEqual(result, 2)
        self.assertIn("verified check C01 is immutable", error)
        self.assertEqual(self.store.read_bytes(), original)

        result, _, error = self.run_cli(
            "check", identifier, "C02", "--result", "pass",
            "--evidence", "second-trace.json", "--reviewer", "auditor",
        )
        self.assertEqual(result, 0, error)
        result, _, error = self.run_cli(
            "close", identifier, "--reviewer", "auditor", "--summary", "All checks passed",
        )
        self.assertEqual(result, 0, error)
        original = self.store.read_bytes()
        result, _, error = self.run_cli(
            "revise", identifier, "--reason", "try to rewrite a closed item",
            "--description", "not allowed",
        )
        self.assertEqual(result, 2)
        self.assertIn("closed/transferred item is immutable", error)
        self.assertEqual(self.store.read_bytes(), original)

    def test_failed_or_blocked_check_keeps_item_in_review_queue(self):
        identifier = self.add()
        status, _, _ = self.run_cli("check", identifier, "C01", "--result", "fail",
                                    "--note", "Behavior diverges", "--evidence", "trace.json",
                                    "--reviewer", "auditor")
        self.assertEqual(status, 0)
        record = json.loads(self.store.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(record["status"], "failed")
        result, output, _ = self.run_cli("list")
        self.assertEqual(result, 0)
        self.assertIn(identifier, output)
        result, _, error = self.run_cli("close", identifier, "--reviewer", "auditor",
                                        "--summary", "Cannot")
        self.assertEqual(result, 2)
        self.assertIn("C01", error)

    def test_passed_item_is_hidden_not_deleted_and_reopen_resets_evidence(self):
        identifier = self.add()
        self.assertEqual(self.run_cli("check", identifier, "C01", "--result", "pass",
                        "--reviewer", "independent reviewer", "--evidence", "tests/run.log")[0], 0)
        self.assertEqual(self.run_cli("check", identifier, "C02", "--result", "na",
                        "--reviewer", "independent reviewer", "--evidence", "scope memo",
                        "--note", "Approved out of scope")[0], 0)
        self.assertEqual(self.run_cli("close", identifier, "--reviewer", "independent reviewer",
                        "--summary", "All applicable controls reviewed")[0], 0)
        self.assertNotIn(identifier, self.run_cli("list")[1])
        self.assertIn(identifier, self.run_cli("list", "--all")[1])
        record = json.loads(self.run_cli("show", identifier, "--json")[1])
        self.assertEqual(record["status"], "passed")
        self.assertEqual(len(record["history"]), 4)
        self.assertEqual(self.run_cli("check", identifier, "C01", "--result", "pass",
                        "--reviewer", "auditor", "--evidence", "post-close")[0], 2)
        self.assertEqual(self.run_cli("reopen", identifier, "--reviewer", "new auditor",
                        "--reason", "Recurrence reported")[0], 0)
        reopened = json.loads(self.run_cli("show", identifier, "--json")[1])
        self.assertEqual([c["status"] for c in reopened["checks"]], ["pending", "pending"])
        self.assertEqual(reopened["history"][-1]["action"], "reopen")
        self.assertIn(identifier, self.run_cli("list")[1])

    def test_n_a_requires_explicit_independent_reason(self):
        identifier = self.add()
        status, _, error = self.run_cli("check", identifier, "C01", "--result", "na",
                                         "--reviewer", "auditor", "--evidence", "plan")
        self.assertEqual(status, 2)
        self.assertIn("note", error)

    def test_unknown_item_does_not_modify_registry(self):
        self.add()
        original = self.store.read_bytes()
        status, _, error = self.run_cli("check", "TMA-invalid", "C01",
                                         "--result", "pass", "--reviewer", "a",
                                         "--evidence", "log")
        self.assertEqual(status, 2)
        self.assertIn("unknown audit item", error)
        self.assertEqual(self.store.read_bytes(), original)

    def test_broken_store_is_not_overwritten(self):
        self.store.parent.mkdir(parents=True)
        self.store.write_text('{"schema": "bad"}', encoding="utf-8")
        original = self.store.read_bytes()
        status, _, error = self.run_cli("add", "--title", "X", "--owner", "SDK",
                 "--stage", "Gate", "--description", "Break", "--source", "audit",
                 "--check", "Check")
        self.assertEqual(status, 2)
        self.assertIn("schema", error)
        self.assertEqual(self.store.read_bytes(), original)

    def test_transfer_requires_real_linked_tmc_and_does_not_fake_pass(self):
        from theory_master.change_register import main as change_main
        change_store = Path(self.temporary.name) / "changes.json"
        identifier = self.add("Static webpage icon")
        result, _, error = self.run_cli(
            "transfer", identifier, "--change-record", "TMC-fake",
            "--change-store", str(change_store), "--reason", "Static UI test")
        self.assertEqual(result, 2)
        self.assertIn("matching persisted", error)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(change_main([
                "--store", str(change_store), "add", "--title", "Static webpage icon",
                "--source", "user", "--source-tma", identifier]), 0)
        rec = json.loads(change_store.read_text())["items"][0]["id"]
        self.assertEqual(self.run_cli("transfer", identifier,
            "--change-record", rec, "--change-store", str(change_store),
            "--reason", "No future scientific Cycle necessary")[0], 0)
        self.assertNotIn(identifier, self.run_cli("list")[1])
        self.assertIn(identifier, self.run_cli("list", "--all")[1])
        item = json.loads(self.run_cli("show", identifier, "--json")[1])
        self.assertEqual(item["status"], "transferred")
        self.assertEqual([c["status"] for c in item["checks"]], ["pending", "pending"])
        self.assertEqual(item["history"][-1]["action"], "transfer")
        self.assertEqual(self.run_cli("check", identifier, "C01",
            "--result", "pass", "--evidence", "x", "--reviewer", "y")[0], 2)

    def test_concurrent_adds_get_unique_ids(self):
        def create(index):
            # stdout redirection is process-global; exercise the writer itself
            # rather than sharing a captured stream between Python threads.
            args = Namespace(title=f"Audit {index}", priority="P1", owner="SDK",
                             stage="runtime", description="Concurrent writer",
                             source="audit/test", check=["C"])
            with _edit(self.store) as data:
                return _add(args, data)["id"]
        with ThreadPoolExecutor(max_workers=6) as executor:
            ids = list(executor.map(create, range(12)))
        self.assertEqual(len(set(ids)), 12)
        records = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(len(records["items"]), 12)
        self.assertEqual(records["next_sequence"], 13)


if __name__ == "__main__":
    unittest.main()
