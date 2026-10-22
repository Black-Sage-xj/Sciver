"""Offline regression tests for bounded Supervisor recovery of a formerly busy provider thread."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.runtime_supervisor_review import _recovered_capacity_thread_is_idle


THREAD = "01a152da-f27d-7cc0-ba7d-dd0b52165227"
CAPACITY = "Selected model is at capacity. Please try a different model."


def fixture(root: Path):
    research = root / "research"
    cycle = research / "154"
    cycle.mkdir(parents=True)
    home = research / ".theory-master" / "workers" / "worker-1" / "codex-home"
    home.mkdir(parents=True)
    records = [
        {"type": "role.started", "payload": {"role": "investigator", "thread_id": THREAD}},
        {"type": "run.failed", "payload": {"error": (
            f"Provider capacity recovery timed out after 1800s (thread busy=32): "
            f"JSON-RPC error -32600: thread must be idle before starting a goal: {THREAD}")}},
        {"type": "role.started", "payload": {"role": "investigator", "thread_id": THREAD}},
        {"type": "run.failed", "payload": {"error": CAPACITY}},
    ]
    (cycle / "events.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return research, cycle, {"worker_id": "worker-1", "recovery": {"message": CAPACITY}}


class FakeCodex:
    root_type = "idle"
    invoked = []

    def __init__(self, config):
        self.invoked.append(config)
        self._client = self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def thread_resume(self, thread_id, **kwargs):
        assert thread_id == THREAD
        assert kwargs.get("include_turns") is False

    def thread_read(self, thread_id):
        return SimpleNamespace(thread=SimpleNamespace(
            id=thread_id, status=SimpleNamespace(
                root=SimpleNamespace(type=self.root_type)
            )
        ))


class CapacityIdleRecoveryTests(unittest.TestCase):
    def setUp(self):
        FakeCodex.invoked = []
        FakeCodex.root_type = "idle"

    def probe(self, research, cycle, state, *, worker_status="FAILED", process_alive=False):
        worker = {"worker_id": "worker-1", "status": worker_status,
                  "process_alive": process_alive}
        with patch("theory_master.worker_runtime.list_workers", return_value=[worker]), \
             patch("openai_codex.Codex", FakeCodex):
            return _recovered_capacity_thread_is_idle(research, cycle, state)

    def test_idle_original_thread_allows_bounded_managed_resume(self):
        with tempfile.TemporaryDirectory() as td:
            research, cycle, state = fixture(Path(td))
            self.assertTrue(self.probe(research, cycle, state))
            self.assertEqual(len(FakeCodex.invoked), 1)
            self.assertIn("worker-1/codex-home", FakeCodex.invoked[0].env["CODEX_HOME"])

    def test_active_goal_must_not_be_cleared_or_restarted(self):
        with tempfile.TemporaryDirectory() as td:
            research, cycle, state = fixture(Path(td))
            FakeCodex.root_type = "active"
            self.assertFalse(self.probe(research, cycle, state))
            self.assertEqual(len(FakeCodex.invoked), 1)

    def test_no_probe_without_capacity_history_or_inactive_worker(self):
        with tempfile.TemporaryDirectory() as td:
            research, cycle, state = fixture(Path(td))
            self.assertFalse(self.probe(research, cycle,
                {**state, "recovery": {"message": "authentication failed"}}))
            self.assertFalse(self.probe(research, cycle, state, process_alive=True))
            self.assertFalse(self.probe(research, cycle, state, worker_status="RUNNING"))
            (cycle / "events.jsonl").write_text(json.dumps({
                "type": "run.failed", "payload": {"error": CAPACITY}}) + "\n")
            self.assertFalse(self.probe(research, cycle, state))
            self.assertEqual(FakeCodex.invoked, [])


if __name__ == "__main__":
    unittest.main()
