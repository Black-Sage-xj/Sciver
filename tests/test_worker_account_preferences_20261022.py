"""Deterministic account assignment tests; no science Cycle or provider calls."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master import worker_runtime as wr
from theory_master.worker_mode import configure_mode


def _workers(root):
    for i, account in ((1, "1"), (2, "2")):
        folder = root / ".theory-master" / "workers" / f"worker-{i}"
        folder.mkdir(parents=True)
        (folder / "runtime.json").write_text(json.dumps({
            "schema": wr.SCHEMA, "worker_id": f"worker-{i}",
            "status": "OVER", "account": account, "current_cycle": str(100 + i)}))


def test_pref_is_future_only_and_can_be_cleared(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    configure_mode(root, enabled=True, desired_agents=2)
    _workers(root)
    before = wr.read_worker(root, "worker-1").copy()
    with patch("theory_master.codex_accounts.account_status", return_value={
            "accounts": {"1": {"auth_present": True}, "2": {"auth_present": True}}}):
        result = wr.set_worker_account_preference(root, "worker-1", "2")
        assert result["effective_at"] == "next_new_cycle"
        assert wr.worker_account_preferences(root) == {"worker-1": "2"}
        with pytest.raises(ValueError, match="already assigned"):
            wr.set_worker_account_preference(root, "worker-2", "2")
        assert wr.set_worker_account_preference(root, "worker-1", "keep")["planned_account"] is None
        assert wr.worker_account_preferences(root) == {}
    assert wr.read_worker(root, "worker-1") == before


def test_pref_rejects_unregistered_or_unauthenticated(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    configure_mode(root, enabled=True, desired_agents=2)
    _workers(root)
    with patch("theory_master.codex_accounts.account_status", return_value={
            "accounts": {"1": {"auth_present": True}, "2": {"auth_present": False}}}):
        with pytest.raises(ValueError, match="authenticated"):
            wr.set_worker_account_preference(root, "worker-1", "2")
        with pytest.raises(ValueError, match="outside the configured active pool"):
            wr.set_worker_account_preference(root, "worker-3", "1")
        with pytest.raises(ValueError):
            wr.set_worker_account_preference(root, "worker-1", "../other")


def test_scheduler_honors_swap_at_new_cycle_without_touching_live_workers(tmp_path, monkeypatch):
    root = tmp_path / "research"
    root.mkdir()
    (root / ".theory-master").mkdir()
    (root / ".theory-master" / "worker_account_preferences.json").write_text(json.dumps({
        "schema": wr.WORKER_ACCOUNT_PREFERENCES_SCHEMA,
        "accounts": {"worker-1": "2", "worker-2": "1"}}))
    existing = [
        dict(worker_id="worker-1", account="1", effective_status="OVER", process_alive=False),
        dict(worker_id="worker-2", account="2", effective_status="OVER", process_alive=False),
    ]
    class FakeClaims:
        def __init__(self, *a): pass
        def claim_cycle(self, worker):
            return {"claim_id": f"claim-{worker}", "fencing_epoch": 1}
    monkeypatch.setattr(wr, "CycleClaims", FakeClaims)
    monkeypatch.setattr(wr, "runtime_health", lambda root: {"active": False})
    monkeypatch.setattr(wr, "_master_complete", lambda root, **kw: False)
    monkeypatch.setattr(wr, "_living_workers", lambda root: [])
    monkeypatch.setattr(wr, "_route_capacity", lambda spec, slots: (2, ["1", "2"]))
    monkeypatch.setattr(wr, "_share_enabled", lambda root: False)
    monkeypatch.setattr(wr, "list_workers", lambda root: existing)
    monkeypatch.setattr(wr, "read_worker", lambda root, name:
                        next(w for w in existing if w["worker_id"] == name))
    monkeypatch.setattr(wr, "save_worker", lambda *a, **kw: None)
    monkeypatch.setattr(wr, "_task_scoped_process_env", lambda root: {})
    monkeypatch.setattr(wr, "_pinned_codex_home", lambda *a: tmp_path)
    monkeypatch.setattr(wr, "_launch_detached_service", lambda *a, **kw: None)
    rebound = []
    monkeypatch.setattr(wr, "_rebind_idle_worker_home",
                        lambda root, worker, old, new: rebound.append((worker, old, new)))
    spec = SimpleNamespace(research_root=root, branch_canvas=root / "canvas.json")
    result = wr.schedule_once(spec, resource_slots=2,
                              eligible_worker_ids=frozenset({"worker-1", "worker-2"}))
    assert [(w["worker_id"], w["account"]) for w in result["started"]] == [
        ("worker-1", "2"), ("worker-2", "1")]
    assert rebound == [("worker-1", "1", "2"), ("worker-2", "2", "1")]


def test_idle_account_auth_rebind_rejects_live_worker(tmp_path, monkeypatch):
    from contextlib import nullcontext
    root = tmp_path / "research"
    accounts = tmp_path / "accounts"
    for account in ("1", "2"):
        (accounts / account).mkdir(parents=True)
        (accounts / account / "auth.json").write_text(f"dummy-{account}")
    home = root / ".theory-master" / "workers" / "worker-1" / "codex-home"
    home.mkdir(parents=True)
    (home / "auth.json").symlink_to(accounts / "1" / "auth.json")
    monkeypatch.setattr(wr, "_pinned_codex_home", lambda *a: home)
    monkeypatch.setattr(wr, "read_worker", lambda *a: {"status": "OVER", "pid": None})
    monkeypatch.setattr(wr, "process_identity_alive", lambda *a: False)
    monkeypatch.setattr("theory_master.codex_accounts.ensure_layout",
                        lambda: {"1": accounts / "1", "2": accounts / "2"})
    monkeypatch.setattr("theory_master.codex_accounts.account_switch_lock", nullcontext)
    wr._rebind_idle_worker_home(root, "worker-1", "1", "2")
    assert (home / "auth.json").resolve() == (accounts / "2" / "auth.json").resolve()
    monkeypatch.setattr(wr, "read_worker", lambda *a: {"status": "RUNNING", "pid": 123})
    monkeypatch.setattr(wr, "process_identity_alive", lambda *a: True)
    with pytest.raises(ValueError, match="cannot rebind"):
        wr._rebind_idle_worker_home(root, "worker-1", "2", "1")
    assert (home / "auth.json").resolve() == (accounts / "2" / "auth.json").resolve()
