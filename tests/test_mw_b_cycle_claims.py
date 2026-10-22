"""MW-B CycleClaims: I-04 uniqueness, fencing, reissue (I-10), G5 scope, I-12."""
from __future__ import annotations

import os
import subprocess
import sys
import types
from datetime import timedelta

import pytest

from theory_master.multi_worker import (
    CycleClaims, attach_owner, finish_owned_claim, frontier_snapshot, utcnow,
)
from theory_master.runtime_manager import process_start_ticks
from theory_master.science_graph_store import ScienceGraphStore


class GoalSealedStub(Exception):
    pass


@pytest.fixture
def goal_stub(monkeypatch):
    state = {"sealed": False}

    def assert_goal_open(research_root, graph_path):
        if state["sealed"]:
            raise GoalSealedStub("goal sealed")

    module = types.ModuleType("theory_master.goal_terminal")
    module.assert_goal_open = assert_goal_open
    module.GoalSealedError = GoalSealedStub
    monkeypatch.setitem(sys.modules, "theory_master.goal_terminal", module)
    return state


def _token(claim):
    return claim["worker_id"], claim["claim_id"], claim["fencing_epoch"]


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    return proc.pid


def test_claim_without_active_h_and_one_live_claim_per_worker(tmp_path):
    claims = CycleClaims(tmp_path)
    first = claims.claim_cycle("worker-1")
    assert first["cycle_id"] is None and first["hypothesis_id"] is None
    with pytest.raises(ValueError, match="already has"):
        claims.claim_cycle("worker-1")
    with pytest.raises(ValueError, match="worker-N"):
        claims.claim_cycle("single")
    claims.update(*_token(first), action="wait_direction")
    second = claims.claim_cycle("worker-1")
    assert second["fencing_epoch"] > first["fencing_epoch"]
    assert claims.history()[0]["status"] == "waiting_direction"


def test_goal_sealed_refuses_claim_cycle(tmp_path, goal_stub):
    claims = CycleClaims(tmp_path, tmp_path / "science_graph.sqlite3")
    claims.claim_cycle("worker-1")
    goal_stub["sealed"] = True
    with pytest.raises(GoalSealedStub):
        claims.claim_cycle("worker-2")
    assert [c["worker_id"] for c in claims.list_claims()] == ["worker-1"]


def test_cycle_and_hypothesis_uniqueness(tmp_path):
    claims = CycleClaims(tmp_path)
    a = claims.claim_cycle("worker-1")
    b = claims.claim_cycle("worker-2")
    with pytest.raises(ValueError, match="bind the Cycle"):
        claims.bind_hypothesis(*_token(a), "H-0007")
    claims.bind_cycle(*_token(a), "7")
    assert claims.bind_cycle(*_token(a), "007")["cycle_id"] == "7"  # idempotent
    with pytest.raises(ValueError, match="I-04"):
        claims.bind_cycle(*_token(b), "7")
    with pytest.raises(ValueError, match="rebind"):
        claims.bind_cycle(*_token(a), "8")
    claims.bind_cycle(*_token(b), "8")
    claims.bind_hypothesis(*_token(a), "H-0007")
    with pytest.raises(ValueError, match="I-04"):
        claims.bind_hypothesis(*_token(b), "H-0007")
    # Terminal (non-released) claims keep their identity.
    claims.update(*_token(a), action="stopped_goal")
    with pytest.raises(ValueError, match="I-04"):
        claims.bind_hypothesis(*_token(b), "H-0007")
    claims.bind_hypothesis(*_token(b), "H-0008")


def test_fencing_rejects_stale_paused_expired_and_mismatch(tmp_path):
    claims = CycleClaims(tmp_path)
    start = utcnow()
    c = claims.claim_cycle("worker-1", now=start)
    claims.bind_cycle(*_token(c), "3", now=start)
    claims.bind_hypothesis(*_token(c), "H-0003", now=start)
    claims.assert_writer(*_token(c), cycle_id="3", hypothesis_id="H-0003", now=start)
    claims.assert_writer(*_token(c), cycle_id=3, now=start)
    with pytest.raises(ValueError, match="Cycle"):
        claims.assert_writer(*_token(c), cycle_id="4", now=start)
    with pytest.raises(ValueError, match="Hypothesis"):
        claims.assert_writer(*_token(c), cycle_id="3", hypothesis_id="H-0009", now=start)
    with pytest.raises(ValueError, match="stale"):
        claims.assert_writer("worker-1", c["claim_id"], c["fencing_epoch"] + 1, cycle_id="3")
    with pytest.raises(ValueError, match="expired"):
        claims.assert_writer(*_token(c), cycle_id="3", now=start + timedelta(days=2))
    claims.update(*_token(c), action="pause", now=start)
    with pytest.raises(ValueError, match="paused"):
        claims.assert_writer(*_token(c), cycle_id="3", now=start)
    claims.update(*_token(c), action="resume", now=start)
    claims.assert_writer(*_token(c), cycle_id="3", now=start)
    with pytest.raises(ValueError, match="expired"):
        claims.update(*_token(c), action="resume", now=start + timedelta(days=2))


def test_release_refused_while_owner_alive_and_terminal_is_archived(tmp_path):
    claims = CycleClaims(tmp_path)
    c = claims.claim_cycle("worker-1")
    attach_owner(claims, worker_id="worker-1", claim_id=c["claim_id"], epoch=c["fencing_epoch"],
                 owner_pid=os.getpid(), owner_start_ticks=process_start_ticks(os.getpid()))
    with pytest.raises(ValueError, match="alive"):
        claims.update(*_token(c), action="release")
    claims.bind_cycle(*_token(c), "1")
    finish_owned_claim(claims, worker_id="worker-1", claim_id=c["claim_id"],
                       epoch=c["fencing_epoch"], current_pid=os.getpid())
    assert claims.list_claims() == []
    assert claims.history()[-1]["status"] == "finished"
    with pytest.raises(ValueError, match="stale"):
        claims.assert_writer(*_token(c), cycle_id="1")
    d = claims.claim_cycle("worker-2")
    with pytest.raises(ValueError, match="original"):
        claims.finish(*_token(d), current_pid=os.getpid())
    claims.update(*_token(d), action="release")
    assert claims.history()[-1]["status"] == "released"


def test_reissue_preserves_cycle_and_h_with_new_token(tmp_path):
    claims = CycleClaims(tmp_path)
    c = claims.claim_cycle("worker-1")
    claims.attach_owner(*_token(c), owner_pid=os.getpid(), owner_start_ticks=None)
    claims.bind_cycle(*_token(c), "12")
    claims.bind_hypothesis(*_token(c), "H-0012")
    with pytest.raises(ValueError, match="alive"):
        claims.reissue_for_resume(*_token(c))
    with claims._locked() as state:  # simulate a crashed owner process
        state["claims"][c["claim_id"]]["owner_pid"] = _dead_pid()
        from theory_master.multi_worker import _save_json
        _save_json(claims.path, state)
    new = claims.reissue_for_resume(*_token(c))
    assert (new["cycle_id"], new["hypothesis_id"]) == ("12", "H-0012")
    assert new["claim_id"] != c["claim_id"] and new["fencing_epoch"] > c["fencing_epoch"]
    assert new["reissued_from_claim_id"] == c["claim_id"]
    with pytest.raises(ValueError, match="stale"):
        claims.assert_writer(*_token(c), cycle_id="12", hypothesis_id="H-0012")
    claims.assert_writer(*_token(new), cycle_id="12", hypothesis_id="H-0012")
    assert claims.history()[-1]["status"] == "superseded"
    assert claims.running_hypotheses() == [
        {"worker_id": "worker-1", "cycle_id": "12", "hypothesis_id": "H-0012", "status": "claimed"}]


def test_running_hypotheses_scope(tmp_path):
    claims = CycleClaims(tmp_path)
    a, b, c, d = (claims.claim_cycle(f"worker-{n}") for n in range(1, 5))
    for claim, n in ((a, 1), (b, 2), (c, 3)):
        claims.bind_cycle(*_token(claim), str(n))
        claims.bind_hypothesis(*_token(claim), f"H-000{n}")
    claims.bind_cycle(*_token(d), "4")  # no H yet: not running a hypothesis
    claims.update(*_token(b), action="pause")
    claims.update(*_token(c), action="stopped_goal")
    assert claims.running_hypotheses() == [
        {"worker_id": "worker-1", "cycle_id": "1", "hypothesis_id": "H-0001", "status": "claimed"},
        {"worker_id": "worker-2", "cycle_id": "2", "hypothesis_id": "H-0002", "status": "paused"},
    ]


def test_preferred_does_not_order_frontier_snapshot(tmp_path):
    db = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(db) as store:
        for n in (3, 1, 2):
            store.add_hypothesis(f"H-000{n}", title=f"H {n}")
        store.set_frontiers_batch([
            {"hypothesis_id": "H-0003", "status": "active", "reason": "preferred", "preferred": True},
            {"hypothesis_id": "H-0001", "status": "active", "reason": "x"},
            {"hypothesis_id": "H-0002", "status": "active", "reason": "x"},
        ])
    rows = frontier_snapshot(db)[1]
    assert [r["hypothesis_id"] for r in rows] == ["H-0001", "H-0002", "H-0003"]
    assert rows[2]["priority"] == "preferred"  # display data only
