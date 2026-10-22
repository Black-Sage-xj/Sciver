"""Offline regressions for quota watcher and verified legacy budget preservation."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master import worker_runtime as runtime
from theory_master.budget_auto_preservation import (
    _verified_legacy_budget_rejection, dispatch_auto_budget_preservation,
)


def paused_codex_quota_state(*, goal_status="usageLimited", provider="CodexHTTPS",
                             account="2", frozen=True):
    return {
        "status": "paused", "phase": "provider_quota_exhausted",
        "execution_active": False, "worker_id": "worker-1",
        "native_goal": {"status": goal_status, "thread_id": "old-native-goal"},
        "recovery": {"code": "provider_quota_exhausted", "provider": provider,
                     "codex_account": account},
        "hypothesis_locked": frozen,
    }




def test_watcher_unblocks_terminated_quota_goal_without_cross_account_move(tmp_path):
    root = tmp_path / "research"
    wid = "worker-1"
    (root / ".theory-master" / "workers" / wid).mkdir(parents=True)
    state = paused_codex_quota_state()
    worker = {"status": "PAUSED", "current_cycle": "185", "account": "2"}
    spec = SimpleNamespace(research_root=root)
    records = {
        "1": {"ok": True, "ordinary_usage_allowed": True},
        "2": {"ok": True, "ordinary_usage_allowed": True},
    }
    with (patch("theory_master.quota_auto_adjust.quota_auto_adjust_enabled",
                side_effect=[True, False]),
          patch.object(runtime, "read_worker", return_value=worker),
          patch.object(runtime, "_read_json", return_value=state),
          patch.object(runtime, "process_identity_alive", return_value=False),
          patch("theory_master.codex_accounts.account_rate_limits",
                side_effect=lambda a: records[a]),
          patch("theory_master.quota_provider_fallback.investigator_native_goal_in_flight",
                return_value=False),
          patch.object(runtime, "worker_action", return_value={"status": "STARTING"}) as resume,
          patch("theory_master.worker_runtime.time.sleep")):
        assert runtime.watch_worker_quota(spec, wid) == 0
    resume.assert_called_once_with(
        spec, wid, "resume", account="2", provider_fallback=None,
    )
    receipt = json.loads((root / ".theory-master" / "workers" / wid /
                          "last_quota_recovery.json").read_text())
    assert receipt["cycle"] == "185" and receipt["from"] == receipt["to"] == "2"


def test_watcher_does_not_transfer_unfinished_goal(tmp_path):
    root = tmp_path / "research"
    wid = "worker-1"
    (root / ".theory-master" / "workers" / wid).mkdir(parents=True)
    state = paused_codex_quota_state(goal_status="running")
    worker = {"status": "PAUSED", "current_cycle": "185", "account": "2"}
    spec = SimpleNamespace(research_root=root)
    with (patch("theory_master.quota_auto_adjust.quota_auto_adjust_enabled",
                side_effect=[True, False]),
          patch.object(runtime, "read_worker", return_value=worker),
          patch.object(runtime, "_read_json", return_value=state),
          patch.object(runtime, "process_identity_alive", return_value=False),
          patch("theory_master.codex_accounts.account_rate_limits",
                side_effect=lambda account: {"ok": True, "ordinary_usage_allowed": account == "1"}),
          patch("theory_master.quota_provider_fallback.investigator_native_goal_in_flight",
                return_value=True),
          patch.object(runtime, "worker_action") as resume,
          patch("theory_master.worker_runtime.time.sleep")):
        assert runtime.watch_worker_quota(spec, wid) == 0
    resume.assert_not_called()


def _legacy_rejected(tmp_path: Path):
    root = tmp_path / "research"
    cycle = root / "184"
    (cycle / "budget").mkdir(parents=True)
    review = [
        {"schema": "theory-master/investigator-budget-review/v1",
         "decision": "approve", "review_index": 1, "tranche_tokens": 50000000,
         "prior_allowance": 50000000, "new_allowance": 100000000,
         "allowance_basis": "prior_allowance", "observed_tokens": 50100000},
        {"schema": "theory-master/investigator-budget-review/v1",
         "decision": "reject", "review_index": 2, "tranche_tokens": 50000000,
         "prior_allowance": 100000000, "new_allowance": 100000000,
         "allowance_basis": "prior_allowance", "observed_tokens": 100000500},
    ]
    ledger = cycle / "budget" / "investigator_reviews.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in review))
    state = {
        "status": "needs_attention", "phase": "investigator:budget_denied",
        "resumable": True, "execution_active": False, "worker_id": "worker-2",
        "investigator_budget_policy": "legacy",
        "investigator_budget": {"rejected": True},
        "scientific_verdict": None,
        "attention": {"code": "investigator_budget_review_rejected",
                      "reason": "Reviewer denied extra tranche"},
    }
    (cycle / "research_state.json").write_text(json.dumps(state))
    (cycle / "run_state.json").write_text(json.dumps(
        {**state, "status": "NEEDS_ATTENTION"}))
    return root, cycle, state


def test_legacy_rejection_requires_real_ledger_and_exact_attention(tmp_path):
    root, cycle, state = _legacy_rejected(tmp_path)
    assert _verified_legacy_budget_rejection(cycle, state)
    assert not _verified_legacy_budget_rejection(cycle, {
        **state, "phase": "investigator:budget_review"})
    assert not _verified_legacy_budget_rejection(cycle, {
        **state, "attention": {"code": "technical_model_error"}})
    state["investigator_budget_policy"] = "three_plus_three_v1"
    assert not _verified_legacy_budget_rejection(cycle, state)
    state["investigator_budget_policy"] = "legacy"
    (cycle / "budget" / "investigator_reviews.jsonl").write_text("")
    assert not _verified_legacy_budget_rejection(cycle, state)


def test_legacy_budget_auto_preserve_exactly_once_no_model_in_test(tmp_path):
    root, cycle, state = _legacy_rejected(tmp_path)
    spec = SimpleNamespace(research_root=root)
    worker = {"worker_id": "worker-2", "status": "NEEDS_ATTENTION",
              "current_cycle": "184", "process_alive": False}
    (root / ".theory-master" / "workers" / "worker-2").mkdir(parents=True)
    with (patch("theory_master.worker_runtime.list_workers", return_value=[worker]),
          patch("theory_master.research_preservation.blocked_checkpoint"),
          patch("theory_master.worker_runtime.worker_action",
                return_value={"status": "PRESERVING"}) as launch):
        assert dispatch_auto_budget_preservation(spec) is True
        assert dispatch_auto_budget_preservation(spec) is False
    launch.assert_called_once_with(spec, "worker-2", "preserve_release")
    receipt = json.loads((root / ".theory-master" / "workers" /
                          "worker-2" / "budget_auto_preserve_184.json").read_text())
    assert receipt["status"] == "launched"
    assert not (root / "185").exists()
