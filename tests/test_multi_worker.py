"""Deterministic claim tests; no real model or research run is started."""
from __future__ import annotations

import json
import multiprocessing
from datetime import timedelta
from pathlib import Path

import pytest

from theory_master.multi_worker import BranchClaims, frontier_snapshot, utcnow, reserve_hypothesis_id
from theory_master.science_graph_store import ScienceGraphStore


def _hypothesis(n: int, status: str = "active", priority: str = "") -> dict:
    return {"id": f"H-{n:04d}", "type": "text", "text": (
        f"type: Hypothesis\nfrontier_status: {status}\npriority: {priority}\n# H-{n:04d} scientific title")}


def _canvas(path: Path, count: int, *, nodes: list[dict] | None = None, edges=None) -> None:
    path.write_text(json.dumps({"nodes": nodes if nodes is not None else
        [_hypothesis(i) for i in range(1, count + 1)], "edges": edges or []}), encoding="utf-8")


def _make(tmp_path: Path, count: int = 5):
    root = tmp_path / "research"
    root.mkdir()
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        for n in range(1, count + 1):
            store.add_hypothesis(f"H-{n:04d}", title=f"Scientific title {n}")
            store.set_frontiers_batch([{"hypothesis_id": f"H-{n:04d}", "status": "active", "reason": "fixture"}])
    return BranchClaims(root, database)


def _compete(root: str, database: str, worker: str, output) -> None:
    try:
        output.put(BranchClaims(Path(root), Path(database)).claim(worker, hypothesis_id="H-0001"))
    except ValueError:
        output.put(None)


def test_dynamic_more_than_three_and_resource_capacity(tmp_path):
    manager = _make(tmp_path, 7)
    assert manager.plan()["suggested_additional_workers"] == 7
    assert manager.plan()["runnable_additional_workers"] == 0  # unknown capacity fails closed
    assert manager.plan(available_resource_slots=5)["runnable_additional_workers"] == 5
    assert manager.plan(available_resource_slots=12)["runnable_additional_workers"] == 7
    for n in range(1, 8):
        assert manager.claim(f"worker-{n}")["hypothesis_id"] == f"H-{n:04d}"
    assert manager.plan(available_resource_slots=12)["runnable_additional_workers"] == 0
    with pytest.raises(ValueError, match="no unclaimed"):
        manager.claim("worker-8")


def test_manual_preferred_order_and_nested_frontiers(tmp_path):
    manager = _make(tmp_path, 0)
    with ScienceGraphStore(manager.graph_path) as store:
        for n in range(1, 5):
            store.add_hypothesis(f"H-{n:04d}", title=f"H {n}")
        store.add_evolution(edge_id="edge-1", source="H-0001", target="H-0003", operator="derive")
        store.set_frontiers_batch([
            {"hypothesis_id":"H-0001","status":"active","reason":"parent remains eligible"},
            {"hypothesis_id":"H-0002","status":"active","reason":"preferred","preferred":True},
            {"hypothesis_id":"H-0003","status":"active","reason":"child eligible"},
            {"hypothesis_id":"H-0004","status":"closed","reason":"closed"},
        ])
    # I-12: historical Preferred is display-only and no longer orders frontiers.
    assert [x["hypothesis_id"] for x in frontier_snapshot(manager.graph_path)[1]] == ["H-0001", "H-0002", "H-0003"]
    assert manager.claim("worker-1")["hypothesis_id"] == "H-0001"
    assert manager.claim("worker-2", hypothesis_id="H-0003")["hypothesis_id"] == "H-0003"
    with pytest.raises(ValueError, match="not an eligible"):
        manager.claim("worker-3", hypothesis_id="H-0004")


def test_atomic_competing_processes(tmp_path):
    manager = _make(tmp_path, 1)
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    processes = [ctx.Process(target=_compete, args=(str(manager.root), str(manager.graph_path), f"worker-{i}", queue))
                 for i in range(1, 7)]
    for p in processes:
        p.start()
    for p in processes:
        p.join(10)
        assert p.exitcode == 0
    results = [queue.get(timeout=3) for _ in processes]
    assert sum(x is not None for x in results) == 1
    assert len(manager.status()["claims"]) == 1


def test_stale_canvas_and_single_claim_per_worker(tmp_path):
    manager = _make(tmp_path, 2)
    old_revision = manager.status()["graph_revision"]
    with ScienceGraphStore(manager.graph_path) as store:
        store.add_hypothesis("H-0003", title="new frontier")
        store.set_frontiers_batch([{"hypothesis_id": "H-0003", "status": "active", "reason": "new"}])
    with pytest.raises(ValueError, match="revision changed"):
        manager.claim("worker-1", expected_revision=old_revision)
    claim = manager.claim("worker-1")
    with pytest.raises(ValueError, match="already has"):
        manager.claim("worker-1", hypothesis_id="H-0002")
    manager.assert_writer("worker-1", claim["claim_id"], claim["fencing_epoch"], "H-0001")


def test_pause_lease_safe_reclaim_and_stale_fencing(tmp_path):
    manager = _make(tmp_path, 1)
    start = utcnow()
    claim = manager.claim("worker-1", now=start)
    manager.update("worker-1", claim["claim_id"], claim["fencing_epoch"], action="pause", now=start)
    with pytest.raises(ValueError, match="paused"):
        manager.assert_writer("worker-1", claim["claim_id"], claim["fencing_epoch"], "H-0001", now=start)
    after = start + timedelta(days=2)
    assert not manager.reclaim("H-0001", now=after)  # expiry alone never proves isolation
    assert manager.reclaim("H-0001", isolated=True, now=after)
    new = manager.claim("worker-2", now=after)
    assert new["fencing_epoch"] > claim["fencing_epoch"]
    with pytest.raises(ValueError, match="stale"):
        manager.assert_writer("worker-1", claim["claim_id"], claim["fencing_epoch"], "H-0001", now=after)
    manager.assert_writer("worker-2", new["claim_id"], new["fencing_epoch"], "H-0001", now=after)
    with pytest.raises(ValueError, match="stale"):
        manager.update("worker-1", claim["claim_id"], claim["fencing_epoch"], action="release")
    manager.update("worker-2", new["claim_id"], new["fencing_epoch"], action="release", now=after)
    assert manager.status()["claims"] == {}


def test_no_hypotheses_means_no_artificial_workers(tmp_path):
    manager = _make(tmp_path, 0)
    assert manager.plan(available_resource_slots=20)["runnable_additional_workers"] == 0


def test_claim_refused_during_existing_single_worker(monkeypatch, tmp_path):
    manager = _make(tmp_path, 5)
    from theory_master import multi_worker
    monkeypatch.setattr(multi_worker, "runtime_health", lambda root: {"active": True})
    assert manager.plan(available_resource_slots=10)["blocked_by_legacy_runtime"]
    assert manager.plan(available_resource_slots=10)["runnable_additional_workers"] == 0
    with pytest.raises(ValueError, match="legacy single-Worker runtime"):
        manager.claim("worker-1")


def test_claims_block_legacy_runtime_start_and_do_not_leak_lock(tmp_path):
    from types import SimpleNamespace
    from theory_master.runtime_manager import foreground_runtime, launch_managed_runtime
    manager = _make(tmp_path, 4)
    manager.claim("worker-1")
    with pytest.raises(RuntimeError, match="branch claims"):
        with foreground_runtime(manager.root, master_id=1, operation="run"):
            pass
    # The failed foreground attempt must release the owner lock.
    with pytest.raises(RuntimeError, match="branch claims"):
        with foreground_runtime(manager.root, master_id=1, operation="run"):
            pass
    with pytest.raises(RuntimeError, match="branch claims"):
        launch_managed_runtime(SimpleNamespace(research_root=manager.root, master_id=1),
                               operation="run", identifier="1")
    assert manager.status()["claims"].get("H-0001") is not None


def test_expired_worker_cannot_heartbeat_or_resume(tmp_path):
    manager = _make(tmp_path, 1)
    now = utcnow()
    claim = manager.claim("worker-1", now=now)
    for operation in ("heartbeat", "resume", "pause"):
        with pytest.raises(ValueError, match="lease expired"):
            manager.update("worker-1", claim["claim_id"], claim["fencing_epoch"],
                           action=operation, now=now + timedelta(days=2))


def test_new_h_uses_cycle_id_across_parallel_workers(tmp_path):
    manager = _make(tmp_path, 2)
    assert reserve_hypothesis_id(manager.root, manager.graph_path, cycle_id="135") == "H-0135"
    assert reserve_hypothesis_id(manager.root, manager.graph_path, cycle_id="134") == "H-0134"
    assert reserve_hypothesis_id(manager.root, manager.graph_path, cycle_id="135") == "H-0135"
    with pytest.raises(ValueError, match="numeric Cycle"):
        reserve_hypothesis_id(manager.root, manager.graph_path, cycle_id="not-a-cycle")
    with ScienceGraphStore(manager.graph_path) as store:
        store.add_hypothesis("H-0136", title="existing H")
    with pytest.raises(ValueError,match="already exists"):
        reserve_hypothesis_id(manager.root, manager.graph_path, cycle_id="136")


def test_running_hypotheses_reach_lead_prompt():
    from theory_master.prompt_stages import stage_context, STAGE_CONTEXT_FIELDS
    incoming = {
        'goal': 'Global physical closure',
        'agent_graph': {'schema': 'theorymaster/agent-graph/v1', 'hypotheses': []},
        'running_hypotheses': [{
            'hypothesis_id': 'H-0090', 'title': 'Negative-valley mechanism',
            'hypothesis': 'The valley is negative.', 'decisive_test': 'Sign of the curvature.',
            'cycle_id': '12', 'worker_id': 'worker-1', 'status': 'claimed',
        }],
        'operator_guidance': 'Prefer a different parent than H-0090.',
    }
    result = stage_context('research_lead', 'frontier_proposal', incoming)
    assert 'canvas_access' not in result
    assert result['running_hypotheses'][0]['hypothesis_id'] == 'H-0090'
    assert 'H-0090' in result['operator_guidance']
    assert 'worker_claimed_frontier_rule' not in result
    assert set(result).issubset(STAGE_CONTEXT_FIELDS[('research_lead','frontier_proposal')])
