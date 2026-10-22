"""MW-C scheduler: Cycle Claims, G6 waiting/waking, Goal seal, gate recovery (offline)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master import worker_runtime as wr
from theory_master.multi_worker import BranchClaims, CycleClaims, ProposalGate
from theory_master.science_graph_store import ScienceGraphStore


def task(tmp_path: Path, hypotheses: int = 0):
    root = tmp_path / "research"
    root.mkdir()
    cv = tmp_path / "exploration.canvas"
    cv.write_text('{"nodes": [], "edges": []}', encoding="utf-8")
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as store:
        for i in range(1, hypotheses + 1):
            store.add_hypothesis(f"H-{i:04d}", title=f"H {i}")
    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal v1", encoding="utf-8")
    return SimpleNamespace(research_root=root, branch_canvas=cv, document=plan), root


@pytest.fixture
def launches(monkeypatch):
    calls = []
    monkeypatch.setattr(wr, "_route_capacity", lambda spec, n: (n, [None] * n))
    monkeypatch.setattr(wr, "_launch_detached_service", lambda args, **kw: calls.append(args))
    return calls


def test_allocation_needs_no_active_hypothesis_and_ignores_preferred(tmp_path, launches):
    spec, root = task(tmp_path, hypotheses=0)
    outcome = wr.schedule_once(spec, resource_slots=3, preferred_hypothesis="H-0001")
    assert [w["worker_id"] for w in outcome["started"]] == ["worker-1", "worker-2", "worker-3"]
    assert outcome["preferred_hypothesis_ignored"]["value"] == "H-0001"
    assert "I-12" in outcome["preferred_hypothesis_ignored"]["deprecation"]
    claims = CycleClaims(root).list_claims()
    assert len(claims) == 3 and all(c["cycle_id"] is None and c["hypothesis_id"] is None for c in claims)
    assert all("--claim-id" in args and "H-0001" not in args for args in launches)
    for name in ("worker-1", "worker-2", "worker-3"):
        record = wr.read_worker(root, name)
        assert record["target_h_id"] is None and record["entry_node_id"] is None


def test_capacity_subtracts_living_claim_owners(tmp_path, launches):
    spec, root = task(tmp_path)
    registry = CycleClaims(root)
    held = registry.claim_cycle("worker-1")
    registry.update("worker-1", held["claim_id"], held["fencing_epoch"], action="pause")
    wr.save_worker(root, "worker-1", status="PAUSED", claim_id=held["claim_id"],
                   epoch=held["fencing_epoch"])
    outcome = wr.schedule_once(spec, resource_slots=2)
    assert outcome["concurrency"] == 1
    assert [w["worker_id"] for w in outcome["started"]] == ["worker-2"]


def _seal(tmp_path: Path):
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as store:
        store.seal_goal(goal_id="goal-1", source_cycle="9", research_plan_sha256="0" * 64,
                        judge_receipt_ref=None, detail={"verdict": "passed"})


def test_sealed_goal_refuses_allocation_with_master_complete(tmp_path, launches):
    spec, root = task(tmp_path, hypotheses=1)
    _seal(tmp_path)
    outcome = wr.schedule_once(spec, resource_slots=2)
    assert outcome["started"] == [] and outcome["reason"] == "master_complete"
    assert launches == [] and CycleClaims(root).list_claims() == []
    assert wr._master_complete(root, spec=spec) is True


def _waiting(root: Path, worker: str, fingerprint):
    wr.save_worker(root, worker, status=wr.WAITING_RESEARCH_DIRECTION, claim_id=None, epoch=None,
                   wake_fingerprint=fingerprint, waiting_since="2026-10-20T00:00:00+00:00", pid=None)


def test_waiting_worker_is_dormant_until_fingerprint_changes(tmp_path, launches):
    spec, root = task(tmp_path)
    _waiting(root, "worker-1", wr.research_direction_fingerprint(spec))
    for _ in range(3):  # repeated ticks: no busy loop, no allocation
        outcome = wr.schedule_once(spec, resource_slots=1)
        assert outcome["started"] == [] and outcome["waiting_workers"] == ["worker-1"]
    assert launches == []
    spec.document.write_text("# Goal v2", encoding="utf-8")
    outcome = wr.schedule_once(spec, resource_slots=1)
    assert [(w["worker_id"], w["wake_reason"]) for w in outcome["started"]] == [
        ("worker-1", "direction_fingerprint_changed")]
    record = wr.read_worker(root, "worker-1")
    assert record["wake_fingerprint"] is None and record["last_wake_reason"] == "direction_fingerprint_changed"


def test_unknown_fingerprint_never_wakes(tmp_path, launches):
    spec, root = task(tmp_path)
    _waiting(root, "worker-1", None)
    spec.document.write_text("# changed", encoding="utf-8")
    assert wr.schedule_once(spec, resource_slots=1)["started"] == []


def test_committed_outcome_changes_fingerprint(tmp_path):
    spec, _root = task(tmp_path, hypotheses=1)
    before = wr.research_direction_fingerprint(spec)
    assert set(wr.DIRECTION_FINGERPRINT_FIELDS) <= set(before)
    import sqlite3
    with sqlite3.connect(tmp_path / "science_graph.sqlite3") as db:
        db.execute("INSERT INTO cycle_evaluations VALUES (?,?,?,?,?,?)",
                   ("eval-1", "H-0001", "1", "supported", "{}", "2026-10-20T00:00:00+00:00"))
    after = wr.research_direction_fingerprint(spec)
    assert after["committed_outcome_count"] == before["committed_outcome_count"] + 1
    assert wr._direction_values(after) != wr._direction_values(before)


def test_retry_direction_request_wakes_once_and_is_consumed(tmp_path, launches):
    spec, root = task(tmp_path)
    _waiting(root, "worker-1", wr.research_direction_fingerprint(spec))
    wr.save_worker(root, "worker-2", status="OVER")
    with pytest.raises(ValueError, match="waiting for research direction"):
        wr.worker_action(spec, "worker-2", "retry_direction")
    assert wr.worker_action(spec, "worker-1", "retry_direction")["status"] == "RETRY_REQUESTED"
    outcome = wr.schedule_once(spec, resource_slots=2)
    started = {w["worker_id"]: w["wake_reason"] for w in outcome["started"]}
    assert started["worker-1"] == "retry_direction"
    request = json.loads(wr._retry_direction_path(root, "worker-1").read_text())
    assert request["status"] == "consumed" and request["consumed_by_claim_id"]


def test_all_waiting_registers_one_deduplicated_important_event(tmp_path, launches):
    spec, root = task(tmp_path)
    fingerprint = wr.research_direction_fingerprint(spec)
    _waiting(root, "worker-1", fingerprint)
    _waiting(root, "worker-2", fingerprint)
    first = wr.schedule_once(spec, resource_slots=2)["all_waiting_notice"]
    second = wr.schedule_once(spec, resource_slots=2)["all_waiting_notice"]
    assert first["created"] is True and second["created"] is False
    assert first["incident_id"] == second["incident_id"]
    assert first["worker_ids"] == ["worker-1", "worker-2"]
    events = [json.loads(line) for line in (root / "important_events.jsonl").read_text().splitlines()]
    assert sum(e.get("kind") == "all_workers_waiting_research_direction" for e in events) == 1


def test_scheduler_tick_recovers_dead_gate_holder(tmp_path, launches, monkeypatch):
    spec, root = task(tmp_path)
    gate = ProposalGate(root)
    with gate._locked() as state:
        state["holder"] = {"worker_id": "worker-9", "claim_id": "dead", "fencing_epoch": 1,
                           "gate_token": "tok", "owner_pid": 2 ** 22 + 12345,
                           "owner_start_ticks": 1, "acquired_at": "2026-10-20T00:00:00+00:00"}
        from theory_master.multi_worker import _save_json
        _save_json(gate.path, state)
    outcome = wr.schedule_once(spec, resource_slots=1)
    assert outcome["proposal_gate_recovered"]["worker_id"] == "worker-9"
    assert gate.status()["holder"] is None
    assert "proposal_gate_recovered" not in wr.schedule_once(spec, resource_slots=1)


def test_live_gate_holder_is_never_recovered(tmp_path, launches):
    spec, root = task(tmp_path)
    gate = ProposalGate(root)
    with gate._locked() as state:
        state["holder"] = {"worker_id": "worker-9", "claim_id": "live", "fencing_epoch": 1,
                           "gate_token": "tok", "owner_pid": os.getpid(),
                           "owner_start_ticks": wr.process_start_ticks(os.getpid()),
                           "acquired_at": "2026-10-20T00:00:00+00:00"}
        from theory_master.multi_worker import _save_json
        _save_json(gate.path, state)
    assert "proposal_gate_recovered" not in wr.schedule_once(spec, resource_slots=1)
    assert gate.status()["holder"]["worker_id"] == "worker-9"


def _legacy_branch_claim(root: Path, *, worker: str, h: str | None, claim_id: str = "legacy-1"):
    path = root / ".theory-master" / "branch_claims.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"worker_id": worker, "claim_id": claim_id, "fencing_epoch": 5, "status": "paused"}
    if h:
        entry["hypothesis_id"] = h
    path.write_text(json.dumps({"schema": "theory-master/branch-claims/v1", "epoch": 5,
                                "claims": {h or "H-0000": entry}}), encoding="utf-8")


def test_worker_action_migrates_legacy_branch_claim_and_repoints_worker(tmp_path):
    spec, root = task(tmp_path, hypotheses=1)
    (root / "3").mkdir()
    (root / "3" / "research_state.json").write_text(json.dumps(
        {"worker_id": "worker-1", "status": "paused", "hypothesis_lock": {"id": "H-0001"}}))
    _legacy_branch_claim(root, worker="worker-1", h="H-0001")
    wr.save_worker(root, "worker-1", status="PAUSED", current_cycle="3",
                   claim_id="legacy-1", epoch=5, pid=None)
    with pytest.raises(ValueError):  # no Worker-2 record; migration still ran first
        wr.worker_action(spec, "worker-2", "pause")
    live = CycleClaims(root).list_claims()
    assert len(live) == 1 and live[0]["migrated_from"]["claim_id"] == "legacy-1"
    assert live[0]["hypothesis_id"] == "H-0001"
    record = wr.read_worker(root, "worker-1")
    assert (record["claim_id"], record["epoch"]) == (live[0]["claim_id"], live[0]["fencing_epoch"])
    assert record["legacy_claim_id"] == "legacy-1"
    assert wr._ensure_legacy_claims_migrated(root) is None  # idempotent
    assert BranchClaims(root, tmp_path / "science_graph.sqlite3").status()["claims"]  # source untouched


def test_legacy_migration_fails_closed_on_skipped_entry(tmp_path, launches):
    spec, root = task(tmp_path)
    _legacy_branch_claim(root, worker="worker-1", h=None)
    with pytest.raises(RuntimeError, match="manual review required"):
        wr.schedule_once(spec, resource_slots=1)
    assert launches == [] and CycleClaims(root).list_claims() == []


def test_daemon_ticks_recover_gate_and_ignore_preferred(tmp_path, monkeypatch):
    root = tmp_path / "research"
    root.mkdir()
    from theory_master import specification, worker_supervisor_dispatch
    spec = SimpleNamespace(research_root=root, max_cycles=1)
    monkeypatch.setattr(specification.TaskSpec, "from_task_root", classmethod(lambda cls, path: spec))
    monkeypatch.setattr(wr, "_master_complete", lambda root, **kw: False)
    monkeypatch.setattr(worker_supervisor_dispatch, "dispatch_pending", lambda root: False)
    monkeypatch.setattr(wr.threading.Event, "wait", lambda self, n: False)
    ticks = []
    monkeypatch.setattr(wr, "_recover_proposal_gate", lambda root: ticks.append(1) or None)
    seen = []
    def fake_schedule(spec, *, resource_slots, eligible_worker_ids=None, **kwargs):
        seen.append(kwargs)
        return {"started": [{"worker_id": n} for n in sorted(eligible_worker_ids)]}
    monkeypatch.setattr(wr, "schedule_once", fake_schedule)
    assert wr.scheduler_daemon(spec, 2, preferred="H-0007") == 0
    data = json.loads((root / ".theory-master" / "worker_scheduler.json").read_text())
    assert seen == [{}] and ticks
    assert data["preferred_hypothesis_ignored"] == "H-0007"
    assert data["stop_reason"] == "cycle_budget_exhausted"
    assert data["cycles_started_by_worker"] == {"worker-1": 1, "worker-2": 1}


def test_daemon_stops_with_goal_completed_reason(tmp_path, monkeypatch):
    root = tmp_path / "research"
    root.mkdir()
    spec = SimpleNamespace(research_root=root, max_cycles=1)
    monkeypatch.setattr(wr, "_master_complete", lambda root, **kw: True)
    monkeypatch.setattr(wr, "schedule_once", lambda *a, **k: pytest.fail("no allocation after seal"))
    assert wr.scheduler_daemon(spec, 1) == 0
    data = json.loads((root / ".theory-master" / "worker_scheduler.json").read_text())
    assert data["status"] == "STOPPED" and data["stop_reason"] == "goal_completed"
