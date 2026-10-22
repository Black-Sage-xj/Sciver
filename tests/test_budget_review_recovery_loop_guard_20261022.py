"""Offline guards for repeated technical Budget Reviewer delivery failures."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

from theory_master import worker_runtime as runtime


def test_repeated_failed_review_boundaries_ignore_checkpoint_changes():
    with TemporaryDirectory() as tmp:
        cycle = Path(tmp) / "184"
        cycle.mkdir()
        events = [
            {"type": "investigation.budget_review_failed", "payload": {"review_index": 1}},
            {"type": "recovery.stage_resumed", "payload": {"checkpoint": "a"}},
            {"type": "investigation.budget_review_failed", "payload": {"review_index": 1}},
            {"type": "recovery.stage_resumed", "payload": {"checkpoint": "b"}},
            {"type": "investigation.budget_review_failed", "payload": {"review_index": 2}},
        ]
        (cycle / "events.jsonl").write_text("\n".join(json.dumps(x) for x in events) + "\n")
        assert runtime._budget_review_technical_failures(cycle, 1) == 2
        assert runtime._budget_review_technical_failures(cycle, 2) == 1
        events.append({"type": "investigation.budget_review_decided", "payload": {"review_index": 1}})
        (cycle / "events.jsonl").write_text("\n".join(json.dumps(x) for x in events) + "\n")
        assert runtime._budget_review_technical_failures(cycle, 1) == 0


def test_recovery_watcher_does_not_restart_same_failed_budget_boundary():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cycle = root / "184"
        cycle.mkdir()
        (root / ".theory-master/workers/worker-2").mkdir(parents=True)
        state = {
            "status": "needs_attention", "phase": "investigator:budget_review",
            "attention": {"code": "investigator_budget_review_failed"},
            "investigator_budget": {"review_index": 1, "rejected": False},
            "last_safe_checkpoint": "/tmp/frozen", "resumable": True,
            "worker_id": "worker-2",
        }
        (cycle / "research_state.json").write_text(json.dumps(state))
        (cycle / "events.jsonl").write_text("\n".join(json.dumps({
            "type": "investigation.budget_review_failed", "payload": {"review_index": 1},
        }) for _ in range(3)) + "\n")
        spec = SimpleNamespace(research_root=root)
        worker = {"status": "NEEDS_ATTENTION", "current_cycle": "184", "account": "2"}
        with (patch("theory_master.quota_auto_adjust.quota_auto_adjust_enabled", return_value=True),
              patch.object(runtime, "read_worker", return_value=worker),
              patch.object(runtime, "worker_action", side_effect=AssertionError("resume forbidden")),
              patch.object(runtime, "process_identity_alive", return_value=False),
              patch("theory_master.codex_accounts.account_rate_limits", side_effect=AssertionError("quota probe forbidden"))):
            assert runtime.watch_worker_quota(spec, "worker-2") == 0
        notice = json.loads((root / ".theory-master/workers/worker-2/budget_review_recovery_exhausted.json").read_text())
        assert notice["failed_sessions"] == 3
        assert notice["review_index"] == 1
