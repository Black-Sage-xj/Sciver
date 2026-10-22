"""Deterministic quota failover policy tests; no email delivery tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theory_master import supervisor


class QuotaFailoverPolicyTest(unittest.TestCase):
    @staticmethod
    def root(path: Path) -> Path:
        root = path / "research"
        (root / ".theory-master").mkdir(parents=True)
        (root / ".theory-master" / "env").write_text(
            "THEORY_MASTER_QUOTA_AUTO_ADJUST=1\n", encoding="utf-8",
        )
        cycle = root / "70"
        cycle.mkdir()
        (cycle / ".theory-master-cycle").write_text("theory-master/v2\n", encoding="utf-8")
        (cycle / "research_state.json").write_text(json.dumps({
            "master_id": 1, "cycle_id": "70", "goal_id": "fixture",
            "status": "paused", "phase": "provider_quota_exhausted",
            "current_agent": "judge",
            "recovery": {"code": "provider_quota_exhausted",
                         "message": "Native Goal usageLimited",
                         "checkpoint": "70/checkpoints/safe.json"},
        }), encoding="utf-8")
        return root

    def test_available_other_account_switches_without_terminal_notification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(Path(temp))
            snapshots = {
                "1": {"ok": True, "ordinary_usage_allowed": False},
                "2": {"ok": True, "ordinary_usage_allowed": True},
            }
            with patch.object(supervisor, "_quota_snapshots", return_value=snapshots), \
                 patch.object(supervisor, "_activate_quota_account", return_value="2") as activate, \
                 patch("theory_master.observability.email_notify.notify_cycle_terminal_once") as notify:
                chosen = supervisor._resolve_quota_pause(
                    root, {"provider": "CodexHTTPS", "failed_account": "1"},
                )
            self.assertEqual(chosen, "2")
            activate.assert_called_once()
            notify.assert_not_called()

    def test_probe_unknown_is_not_claimed_to_be_dual_exhaustion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(Path(temp))
            snapshots = {
                "1": {"ok": True, "ordinary_usage_allowed": False},
                "2": {"ok": False, "error": "network_timeout"},
            }
            with patch("theory_master.observability.email_notify.notify_cycle_terminal_once") as notify:
                supervisor._notify_verified_quota_wait(
                    root, snapshots, {"1": {"source": "rate_limit_probe"}},
                    failed_account="1",
                )
            notify.assert_not_called()

    def test_explicit_native_failure_counts_even_if_its_probe_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(Path(temp))
            snapshots = {
                "1": {"ok": False, "error": "network_timeout"},
                "2": {"ok": True, "ordinary_usage_allowed": False},
            }
            with patch("theory_master.observability.email_notify.notify_cycle_terminal_once") as notify:
                supervisor._notify_verified_quota_wait(
                    root, snapshots, {"1": {"source": "explicit_provider_error"}},
                    failed_account="1",
                )
            notify.assert_called_once()
            self.assertIsNone(notify.call_args.kwargs["payload"]["next_reset_at"])

    def test_an_unconfirmed_provider_error_is_not_evidence_about_other_account(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(Path(temp))
            snapshots = {
                "1": {"ok": False, "error": "network_timeout"},
                "2": {"ok": False, "error": "auth_missing"},
            }
            with patch("theory_master.observability.email_notify.notify_cycle_terminal_once") as notify:
                supervisor._notify_verified_quota_wait(
                    root, snapshots, {"1": {"source": "explicit_provider_error"}},
                    failed_account="1",
                )
            notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
