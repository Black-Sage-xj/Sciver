"""Deterministic recovery reconciler tests; never touch actual Provider/Canvas."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master import worker_runtime as wr
from theory_master.budget_auto_preservation import dispatch_auto_budget_preservation
from theory_master.research_preservation import (
    PreservationGuard, _cached_preservation_model_output,
)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_scheduler_repairs_only_dead_fenced_quota_watchers_and_backs_off(tmp_path):
    root = tmp_path / "research"
    wid = "worker-1"
    cycle = "185"
    worker = {"worker_id": wid, "status": "PAUSED", "process_alive": False,
              "current_cycle": cycle, "claim_id": "claim", "epoch": 8,
              "pid": None, "pid_start_ticks": None}
    science = {"worker_id": wid, "status": "paused", "phase": "provider_quota_exhausted",
               "execution_active": False, "resumable": True,
               "recovery": {"code": "provider_quota_exhausted"}}
    _write(root / cycle / "research_state.json", science)
    claim = {"worker_id": wid, "claim_id": "claim", "fencing_epoch": 8,
             "cycle_id": cycle}
    spec = SimpleNamespace(research_root=root)
    with (patch("theory_master.quota_auto_adjust.quota_auto_adjust_enabled",
                return_value=True),
          patch.object(wr, "list_workers", return_value=[worker]),
          patch.object(wr, "_live_cycle_claims", return_value=[claim]),
          patch.object(wr, "process_identity_alive", return_value=False),
          patch.object(wr, "start_worker_quota_watch",
                       return_value={"status": "starting"}) as watch):
        first = wr._repair_missing_quota_watchers(spec)
        assert first == [{"worker_id": wid, "cycle": cycle,
                          "status": "starting", "attempts": 1}]
        assert wr._repair_missing_quota_watchers(spec) == []
        watch.assert_called_once_with(spec, wid)
        receipt = root / ".theory-master/workers/worker-1/quota_watch_repair.json"
        data = json.loads(receipt.read_text())
        assert data["retry_after_epoch"] > 0
        data["retry_after_epoch"] = 0
        _write(receipt, data)
        again = wr._repair_missing_quota_watchers(spec)
        assert again[0]["attempts"] == 2
        worker["status"] = "NEEDS_ATTENTION"
        assert wr._repair_missing_quota_watchers(spec) == []
        worker["status"] = "PAUSED"
        science["recovery"]["code"] = "run_failed"
        _write(root / cycle / "research_state.json", science)
        assert wr._repair_missing_quota_watchers(spec) == []
        worker["status"] = "PAUSED"


def test_scheduler_never_repairs_unfenced_or_disabled_quota_wait(tmp_path):
    root = tmp_path / "research"
    worker = {"worker_id": "worker-1", "status": "PAUSED", "process_alive": False,
              "current_cycle": "185", "claim_id": "not-ours", "epoch": 9}
    _write(root/"185/research_state.json", {
        "status": "paused", "phase": "provider_quota_exhausted",
        "execution_active": False, "resumable": True, "worker_id": "worker-1",
        "recovery": {"code": "provider_quota_exhausted"},
    })
    spec = SimpleNamespace(research_root=root)
    with (patch("theory_master.quota_auto_adjust.quota_auto_adjust_enabled",
                return_value=True),
          patch.object(wr, "list_workers", return_value=[worker]),
          patch.object(wr, "_live_cycle_claims", return_value=[{
              "worker_id": "worker-1", "claim_id": "different",
              "fencing_epoch": 9, "cycle_id": "185"}]),
          patch.object(wr, "process_identity_alive", return_value=False),
          patch.object(wr, "start_worker_quota_watch") as watch):
        assert wr._repair_missing_quota_watchers(spec) == []
        watch.assert_not_called()


def _cached_fixture(tmp_path):
    root = tmp_path/"research"
    cycle = root/"184"
    (cycle/"workspace").mkdir(parents=True)
    (cycle/"workspace/report.md").write_text("Existing scientific evidence")
    (cycle/"preservation").mkdir()
    stage = {
        "observations": [{"statement": "Existing result", "conditions": "Frozen network",
                          "evidence_paths": ["report.md"],
                          "limitations": "Not independent"}],
        "methods_exhausted": [{"method": "Computed baseline",
                               "limitation": "No protected cohort"}],
        "remaining_gap": "Bounded independent cohort",
        "unexecuted_test": "Frozen gate unfinished",
        "canvas_title": "Unadjudicated archived result",
        "canvas_summary": "Existing calculations do not resolve decisive test",
    }
    _write(cycle/"preservation/last_model_output.txt", stage)
    marker = {"policy": "three_plus_three_v1",
              "code": "investigator_budget_review_rejected",
              "source": "sdk_authorized_budget_terminal", "cycle": "184"}
    _write(cycle/"research_state.json", {
        "worker_id": "worker-2", "status": "needs_attention",
        "execution_active": False, "resumable": True,
        "phase": "investigator:budget_denied",
        "investigator_budget_policy": "three_plus_three_v1",
        "budget_auto_preserve": marker, "scientific_verdict": None,
        "attention": {"code": "investigator_budget_review_rejected"},
    })
    work = root/".theory-master/workers/worker-2"
    _write(work/"budget_auto_preserve_184.json", {
        "schema": "theory-master/automatic-budget-preservation/v1",
        "status": "launched", "cycle": "184", "worker_id": "worker-2"})
    _write(work/"preservation_result.json", {
        "status": "failed_preservation_retained_claim", "cycle": "184"})
    return root, cycle, work, stage


def test_cached_failure_can_recover_once_without_second_model_call(tmp_path):
    root, cycle, work, stage = _cached_fixture(tmp_path)
    worker = {"worker_id": "worker-2", "status": "NEEDS_ATTENTION",
              "current_cycle": "184", "process_alive": False}
    spec = SimpleNamespace(research_root=root)
    with (patch("theory_master.worker_runtime.list_workers",
                return_value=[worker]),
          patch("theory_master.research_preservation.blocked_checkpoint",
                return_value={"hypothesis_lock": {"id": "H-0184"}}),
          patch("theory_master.worker_runtime.worker_action",
                return_value={"status": "PRESERVING"}) as launch):
        assert dispatch_auto_budget_preservation(spec) is True
        assert dispatch_auto_budget_preservation(spec) is False
        launch.assert_called_once_with(spec, "worker-2", "preserve_release")
    guard = json.loads((cycle/"preservation/cached_replay_required.json").read_text())
    raw = (cycle/"preservation/last_model_output.txt").read_text()
    assert guard["sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert _cached_preservation_model_output(
        cycle, cycle/"preservation/last_model_output.txt") == raw
    assert json.loads((work/"budget_cached_replay_184.json").read_text())["status"] == "launched"


def test_lost_or_changed_cache_fails_without_model_fallback(tmp_path):
    root, cycle, work, stage = _cached_fixture(tmp_path)
    replay_file = cycle/"preservation/last_model_output.txt"
    raw = replay_file.read_text()
    _write(cycle/"preservation/cached_replay_required.json", {
        "schema": "theory-master/preservation-cached-replay/v1",
        "cycle": "184", "sha256": hashlib.sha256(raw.encode()).hexdigest()})
    replay_file.unlink()
    with pytest.raises(PreservationGuard, match="model call prohibited"):
        _cached_preservation_model_output(cycle, replay_file)
    replay_file.write_text(raw + "tampered")
    with pytest.raises(PreservationGuard, match="model call prohibited"):
        _cached_preservation_model_output(cycle, replay_file)



def test_only_pinned_account_exhausted_still_counts_as_quota_wait():
    import time
    t = int(time.time())
    statuses = {
        "1": {"ok": True, "ordinary_usage_allowed": False, "queried_at": t,
              "primary": {"remaining_percent": 0, "resets_at": t+3600},
              "secondary": {"remaining_percent": 30, "resets_at": t+86400}},
        "2": {"ok": True, "ordinary_usage_allowed": True, "queried_at": t,
              "primary": {"remaining_percent": 100, "resets_at": t+3600}},
    }
    with patch("theory_master.codex_accounts.account_rate_limits",
               side_effect=lambda account: statuses[account]):
        assert wr._verified_codex_quota_wait() is None
        assert wr._verified_pinned_codex_quota_wait("1") == {"1": t+3600}
        assert wr._verified_pinned_codex_quota_wait("2") is None
        assert wr._verified_pinned_codex_quota_wait("bad") is None
    statuses["1"]["queried_at"] = t - 2000
    with patch("theory_master.codex_accounts.account_rate_limits",
               side_effect=lambda account: statuses[account]):
        assert wr._verified_pinned_codex_quota_wait("1") is None
def test_orphaned_preservation_emits_once_without_releasing_science(tmp_path):
    from datetime import datetime, timedelta, timezone
    root = tmp_path / "research"
    (root / "184").mkdir(parents=True)
    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    worker = {"worker_id": "worker-2", "status": "PRESERVING",
              "current_cycle": "184", "updated_at": stale,
              "process_alive": False, "preservation_job_pid": 12345,
              "preservation_job_start_ticks": 777, "claim_id": "frozen-claim"}
    spec = SimpleNamespace(research_root=root)
    with (patch.object(wr, "list_workers", return_value=[worker]),
          patch.object(wr, "process_identity_alive", return_value=False),
          patch.object(wr, "worker_action") as action):
        result = wr._flag_orphaned_preservation_jobs(spec)
        assert result == [{"worker_id": "worker-2", "cycle": "184",
                           "status": "manual_reconciliation_required"}]
        assert wr._flag_orphaned_preservation_jobs(spec) == []
        action.assert_not_called()
    notice = root / ".theory-master/workers/worker-2/preservation_orphaned_184.json"
    assert json.loads(notice.read_text())["status"] == "manual_reconciliation_required"
    events = [json.loads(row) for row in
              (root/"184/important_events.jsonl").read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["code"] == "preservation_worker_orphaned"
    assert worker["status"] == "PRESERVING"
    assert worker["claim_id"] == "frozen-claim"


def test_short_preservation_startup_is_not_misclassified(tmp_path):
    from datetime import datetime, timezone
    root = tmp_path / "research"
    (root / "184").mkdir(parents=True)
    worker = {"worker_id": "worker-2", "status": "PRESERVING",
              "current_cycle": "184",
              "updated_at": datetime.now(timezone.utc).isoformat(),
              "process_alive": False, "preservation_job_pid": None}
    with (patch.object(wr, "list_workers", return_value=[worker]),
          patch.object(wr, "process_identity_alive", return_value=False)):
        assert wr._flag_orphaned_preservation_jobs(
            SimpleNamespace(research_root=root)) == []



def test_invalid_cached_result_must_not_restart_or_spend(tmp_path):
    root, cycle, work, stage = _cached_fixture(tmp_path)
    receipt = work/"preservation_result.json"
    _write(receipt, {"status": "failed_preservation_retained_claim", "cycle": "183"})
    worker = {"worker_id": "worker-2", "status": "NEEDS_ATTENTION",
              "current_cycle": "184", "process_alive": False}
    with (patch("theory_master.worker_runtime.list_workers", return_value=[worker]),
          patch("theory_master.worker_runtime.worker_action") as launch):
        assert dispatch_auto_budget_preservation(SimpleNamespace(research_root=root)) is False
        launch.assert_not_called()
        assert not (cycle/"preservation/cached_replay_required.json").exists()
