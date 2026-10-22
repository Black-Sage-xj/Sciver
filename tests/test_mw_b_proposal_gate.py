"""MW-B ProposalGate: cross-process exclusion, FIFO, recovery, Goal seal (stubbed)."""
from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from theory_master.multi_worker import (
    CycleClaims, ProposalGate, ProposalGateBusy, ProposalGateError,
)
from theory_master.runtime_manager import process_start_ticks


class GoalSealedStub(Exception):
    pass


@pytest.fixture
def goal_stub(monkeypatch):
    state = {"sealed": False, "calls": 0}

    def assert_goal_open(research_root, graph_path):
        state["calls"] += 1
        if state["sealed"]:
            raise GoalSealedStub("goal sealed")

    module = types.ModuleType("theory_master.goal_terminal")
    module.assert_goal_open = assert_goal_open
    module.GoalSealedError = GoalSealedStub
    monkeypatch.setitem(sys.modules, "theory_master.goal_terminal", module)
    return state


def _me() -> dict:
    return {"owner_pid": os.getpid(), "owner_start_ticks": process_start_ticks(os.getpid())}


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    return proc.pid


def _critical_section(root: str, worker: str, claim_id: str, epoch: int, log: str) -> None:
    gate = ProposalGate(Path(root))
    with gate.holding(worker_id=worker, claim_id=claim_id, epoch=epoch,
                      poll_seconds=0.01, timeout=30, **_me()) as token:
        with open(log, "a", encoding="utf-8") as stream:
            stream.write(f"enter {worker}\n")
        time.sleep(0.05)
        gate.assert_holder(token)
        with open(log, "a", encoding="utf-8") as stream:
            stream.write(f"exit {worker}\n")


def test_gate_mutual_exclusion_across_processes(tmp_path):
    claims = CycleClaims(tmp_path)
    tokens = [claims.claim_cycle(f"worker-{n}") for n in range(1, 7)]
    log = tmp_path / "critical.log"
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_critical_section, args=(
        str(tmp_path), t["worker_id"], t["claim_id"], t["fencing_epoch"], str(log))) for t in tokens]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 12
    for enter, leave in zip(lines[::2], lines[1::2]):
        assert enter.startswith("enter ") and leave == "exit " + enter.split()[1]
    status = ProposalGate(tmp_path).status()
    assert status["holder"] is None and status["queue"] == []
    events = [json.loads(x)["event"] for x in
              (tmp_path / ".theory-master" / "proposal_gate_events.jsonl").read_text().splitlines()]
    assert events.count("acquire") == 6 and events.count("release") == 6


def test_fifo_order_and_idempotent_release(tmp_path):
    gate = ProposalGate(tmp_path)
    first = gate.acquire(worker_id="single", claim_id=None, epoch=None, **_me())
    claims = CycleClaims(tmp_path)
    b = claims.claim_cycle("worker-1")
    c = claims.claim_cycle("worker-2")
    order: list[str] = []

    def wait_for(claim):
        token = gate.acquire(worker_id=claim["worker_id"], claim_id=claim["claim_id"],
                             epoch=claim["fencing_epoch"], poll_seconds=0.01, timeout=20, **_me())
        order.append(claim["worker_id"])
        gate.release(token, reason="test")

    threads = []
    for claim, depth in ((b, 1), (c, 2)):
        thread = threading.Thread(target=wait_for, args=(claim,))
        thread.start()
        threads.append(thread)
        deadline = time.monotonic() + 10
        while len(gate.status()["queue"]) < depth:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    assert [w["worker_id"] for w in gate.status()["queue"]] == ["worker-1", "worker-2"]
    with pytest.raises(ProposalGateBusy):
        gate.acquire(worker_id="worker-3", claim_id=claims.claim_cycle("worker-3")["claim_id"],
                     epoch=claims.list_claims()[-1]["fencing_epoch"], wait=False, **_me())
    assert gate.release(first, reason="hypothesis_frozen") is True
    assert gate.release(first, reason="again") is False
    for thread in threads:
        thread.join(30)
    assert order == ["worker-1", "worker-2"]
    assert gate.status()["last_release"]["worker_id"] == "worker-2"
    with pytest.raises(ProposalGateError):
        gate.assert_holder(first)


def test_holding_releases_on_exception_and_rejects_stale_claim(tmp_path):
    gate = ProposalGate(tmp_path)
    with pytest.raises(RuntimeError, match="boom"):
        with gate.holding(worker_id="single", claim_id=None, epoch=None, **_me()):
            raise RuntimeError("boom")
    status = gate.status()
    assert status["holder"] is None
    assert status["last_release"]["reason"] == "exception:RuntimeError"
    claims = CycleClaims(tmp_path)
    claim = claims.claim_cycle("worker-1")
    claims.update("worker-1", claim["claim_id"], claim["fencing_epoch"], action="pause")
    with pytest.raises(ValueError, match="paused"):
        gate.acquire(worker_id="worker-1", claim_id=claim["claim_id"], epoch=claim["fencing_epoch"], **_me())
    with pytest.raises(ValueError, match="stale"):
        gate.acquire(worker_id="worker-1", claim_id=claim["claim_id"], epoch=0, **_me())
    with pytest.raises(ValueError, match="token"):
        gate.acquire(worker_id="worker-1", claim_id=None, epoch=None, **_me())


def test_dead_holder_recovered_live_holder_never_revoked(tmp_path):
    gate = ProposalGate(tmp_path)
    live = gate.acquire(worker_id="single", claim_id=None, epoch=None, **_me())
    assert gate.recover_dead_holder() is None
    gate.assert_holder(live)
    gate.release(live, reason="done")

    dead = gate.acquire(worker_id="single", claim_id=None, epoch=None,
                        owner_pid=_dead_pid(), owner_start_ticks=None)
    recovered = gate.recover_dead_holder()
    assert recovered["gate_token"] == dead
    assert gate.status()["last_release"]["reason"] == "dead_holder_recovered"
    assert gate.release(dead, reason="late") is False

    gate.acquire(worker_id="single", claim_id=None, epoch=None, owner_pid=_dead_pid(), owner_start_ticks=None)
    claim = CycleClaims(tmp_path).claim_cycle("worker-1")
    token = gate.acquire(worker_id="worker-1", claim_id=claim["claim_id"],
                         epoch=claim["fencing_epoch"], wait=False, **_me())
    gate.assert_holder(token)
    assert gate.status()["last_release"]["reason"] == "dead_holder_recovered_by_waiter"


def test_goal_sealed_refuses_acquire_and_dequeues(tmp_path, goal_stub):
    graph = tmp_path / "science_graph.sqlite3"
    gate = ProposalGate(tmp_path, graph)
    token = gate.acquire(worker_id="single", claim_id=None, epoch=None, **_me())
    gate.release(token, reason="done")
    assert goal_stub["calls"] == 1
    goal_stub["sealed"] = True
    with pytest.raises(GoalSealedStub):
        gate.acquire(worker_id="single", claim_id=None, epoch=None, **_me())
    assert gate.status()["holder"] is None and gate.status()["queue"] == []


def test_goal_sealed_while_waiting_leaves_queue(tmp_path, goal_stub):
    graph = tmp_path / "science_graph.sqlite3"
    gate = ProposalGate(tmp_path, graph)
    holder = gate.acquire(worker_id="single", claim_id=None, epoch=None, **_me())
    claim = CycleClaims(tmp_path).claim_cycle("worker-1")
    errors: list[BaseException] = []

    def waiter():
        try:
            gate.acquire(worker_id="worker-1", claim_id=claim["claim_id"], epoch=claim["fencing_epoch"],
                         poll_seconds=0.01, timeout=20, **_me())
        except BaseException as exc:  # noqa: BLE001 - surfaced through assertion
            errors.append(exc)

    thread = threading.Thread(target=waiter)
    thread.start()
    deadline = time.monotonic() + 10
    while not gate.status()["queue"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    goal_stub["sealed"] = True
    thread.join(20)
    assert len(errors) == 1 and isinstance(errors[0], GoalSealedStub)
    assert gate.status()["queue"] == []
    gate.assert_holder(holder)
