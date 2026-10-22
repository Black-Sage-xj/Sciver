"""Offline quota waiting classification; no credential calls or paid models."""
import json
import time
from pathlib import Path
from unittest.mock import patch

from theory_master import worker_runtime as runtime


def _quota(remaining: int, *, age: int = 0, ok: bool = True, ordinary=False):
    now = int(time.time())
    return {
        "ok": ok, "ordinary_usage_allowed": ordinary, "queried_at": now - age,
        "primary": {"remaining_percent": remaining, "resets_at": now + 3600},
        "secondary": {"remaining_percent": 25, "resets_at": now + 86400},
    }


def test_wait_requires_two_fresh_confirmed_exhausted_accounts():
    with patch("theory_master.codex_accounts.account_rate_limits",
               side_effect=lambda _: _quota(0)):
        result = runtime._verified_codex_quota_wait()
        assert set(result) == {"1", "2"}
        assert result["1"] > time.time()
    for account_data in (
        {"1": _quota(0), "2": _quota(50, ordinary=True)},
        {"1": _quota(0), "2": _quota(0, age=1200)},
        {"1": _quota(0), "2": _quota(0, ok=False)},
        {"1": _quota(0), "2": _quota(0, ordinary=None)},
    ):
        with patch("theory_master.codex_accounts.account_rate_limits",
                   side_effect=lambda number: account_data[number]):
            assert runtime._verified_codex_quota_wait() is None
    with patch("theory_master.codex_accounts.account_rate_limits",
               side_effect=TimeoutError("network")):
        assert runtime._verified_codex_quota_wait() is None


def test_quota_wait_reclassifies_stopped_fenced_cycle_without_losing_science(tmp_path):
    root = tmp_path / "research"
    cycle = root / "184"
    cycle.mkdir(parents=True)
    original = {
        "status": "failed", "phase": "failed",
        "resumable": True, "execution_active": False, "worker_id": "worker-2",
        "hypothesis_locked": True, "hypothesis_lock": {"id": "H-0184"},
        "last_safe_checkpoint": "checkpoints/real.json",
        "recovery": {"action": "error", "kind": "NativeGoalRuntimeFailure",
                     "message": "stream disconnected"},
    }
    (cycle / "research_state.json").write_text(json.dumps(original))
    (cycle / "run_state.json").write_text(json.dumps({
        "status": "FAILED", "execution_active": False,
        "recovery": original["recovery"],
    }))
    with patch.object(runtime, "_checkpoint_matches_claim", return_value=True):
        assert runtime._persist_verified_quota_wait(
            root, "worker-2", "184", {"cycle_id": "184"}, {"1": 123, "2": 456},
            "provider-delivery-failed-during-exhausted-quota")
    science = json.loads((cycle / "research_state.json").read_text())
    run = json.loads((cycle / "run_state.json").read_text())
    assert science["status"] == "paused" and run["status"] == "PAUSED"
    assert science["phase"] == "provider_quota_exhausted"
    assert science["recovery"]["previous_failure"]["kind"] == "NativeGoalRuntimeFailure"
    assert science["hypothesis_lock"]["id"] == "H-0184"
    assert science["last_safe_checkpoint"] == "checkpoints/real.json"
    assert science["execution_active"] is False


def test_never_mask_running_or_unfenced_cycle(tmp_path):
    root = tmp_path / "research"
    cycle = root / "185"
    cycle.mkdir(parents=True)
    original = {"status": "active", "resumable": True, "execution_active": True}
    (cycle / "research_state.json").write_text(json.dumps(original))
    (cycle / "run_state.json").write_text(json.dumps({"status": "RUNNING"}))
    with patch.object(runtime, "_checkpoint_matches_claim", return_value=True):
        assert not runtime._persist_verified_quota_wait(
            root, "worker-1", "185", {}, {"1": 123, "2": 456}, "test")
    with patch.object(runtime, "_checkpoint_matches_claim", return_value=False):
        assert not runtime._persist_verified_quota_wait(
            root, "worker-1", "185", {}, {"1": 123, "2": 456}, "test")
    assert json.loads((cycle / "research_state.json").read_text()) == original
