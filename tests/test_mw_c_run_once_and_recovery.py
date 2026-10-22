"""MW-C Worker process: _run_cycle status -> Cycle Claim mapping, G10, resume, dispatch (offline)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master import worker_runtime as wr
from theory_master import worker_supervisor_dispatch as dispatch
from theory_master.goal_terminal import GoalSealedError
from theory_master.multi_worker import CycleClaims, reserve_cycle_number
from theory_master.science_graph_store import ScienceGraphStore


def task(tmp_path: Path):
    root = tmp_path / "research"
    root.mkdir()
    cv = tmp_path / "exploration.canvas"
    cv.write_text('{"nodes": [], "edges": []}', encoding="utf-8")
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as store:
        store.add_hypothesis("H-0001", title="Bound H")
        store.add_hypothesis("H-0002", title="Parent H")
        store.set_frontiers_batch([{"hypothesis_id": h, "status": "active", "reason": "fixture"}
                                   for h in ("H-0001", "H-0002")])
    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal", encoding="utf-8")
    return SimpleNamespace(research_root=root, branch_canvas=cv, document=plan), root


def run_with(monkeypatch, spec, root, outcome):
    """Run run_once against a fake Workflow returning ``outcome`` (dict or exception)."""
    from theory_master import backends as backends_mod, workflow as workflow_mod
    claims = CycleClaims(root, tmp_graph(spec))
    claim = claims.claim_cycle("worker-1")
    seen = {}

    class Backend:
        def __init__(self, root, routes): pass
        def preflight_runtime(self): pass

    class Workflow:
        def __init__(self, *a, **k): self.cycle_dir = None
        def _run_cycle(self):
            seen.update(worker_id=self.worker_id, claim_id=self.worker_claim_id,
                        epoch=self.worker_claim_epoch, claims=self.worker_claims,
                        gate=self.worker_proposal_gate,
                        focus=hasattr(self, "worker_focus_id") or hasattr(self, "worker_focus_node_id"))
            number = reserve_cycle_number(root, "g")
            self.worker_claims.bind_cycle(self.worker_id, self.worker_claim_id, self.worker_claim_epoch, number)
            self.cycle_dir = root / number
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    monkeypatch.setattr(backends_mod, "CodexBackend", Backend)
    monkeypatch.setattr(workflow_mod, "Workflow", Workflow)
    code = wr.run_once(spec, "worker-1", claim["claim_id"], claim["fencing_epoch"])
    return code, claims, claim, seen


def tmp_graph(spec):
    return Path(spec.branch_canvas).with_name("science_graph.sqlite3")


@pytest.mark.parametrize("status,archived,worker_status,code", [
    ("over", "finished", "OVER", 0),
    ("cancelled", "finished", "CANCELLED", 0),
])
def test_terminal_cycle_finishes_claim(tmp_path, monkeypatch, status, archived, worker_status, code):
    spec, root = task(tmp_path)
    result, claims, _claim, seen = run_with(monkeypatch, spec, root, {"status": status})
    assert result == code and claims.list_claims() == []
    assert claims.history()[-1]["status"] == archived
    assert wr.read_worker(root, "worker-1")["status"] == worker_status
    assert seen["focus"] is False and isinstance(seen["claims"], CycleClaims)
    assert seen["worker_id"] == "worker-1"
    assert type(seen["gate"]).__name__ == "ProposalGate"


@pytest.mark.parametrize("status", ["paused", "needs_attention", "interrupted"])
def test_resumable_cycle_pauses_claim(tmp_path, monkeypatch, status):
    spec, root = task(tmp_path)
    result, claims, claim, _seen = run_with(monkeypatch, spec, root, {"status": status})
    assert result == 2
    [live] = claims.list_claims()
    assert live["claim_id"] == claim["claim_id"] and live["status"] == "paused"
    assert wr.read_worker(root, "worker-1")["status"] == status.upper()


def test_no_qualified_candidate_waits_for_direction(tmp_path, monkeypatch):
    spec, root = task(tmp_path)
    offered = {"committed_outcome_count": 0, "committed_outcome_ids_sha256": "x",
               "scientific_model_version": 3, "research_plan_sha256": "y", "extra": 1}
    result, claims, claim, _seen = run_with(monkeypatch, spec, root,
                                            {"status": "NO_QUALIFIED_CANDIDATE", "wake_fingerprint": offered})
    assert result == 0 and claims.list_claims() == []
    assert claims.history()[-1]["status"] == "waiting_direction"
    worker = wr.read_worker(root, "worker-1")
    assert worker["status"] == wr.WAITING_RESEARCH_DIRECTION
    assert worker["claim_id"] is None and worker["last_claim_id"] == claim["claim_id"]
    assert wr._direction_values(worker["wake_fingerprint"]) == (0, "x", 3, "y")
    assert worker["workflow_wake_fingerprint"] == offered


def test_no_qualified_candidate_without_fingerprint_computes_one(tmp_path, monkeypatch):
    spec, root = task(tmp_path)
    run_with(monkeypatch, spec, root, {"status": "NO_QUALIFIED_CANDIDATE"})
    worker = wr.read_worker(root, "worker-1")
    assert worker["wake_fingerprint"] == wr.research_direction_fingerprint(spec)


@pytest.mark.parametrize("outcome", [{"status": "STOPPED_GOAL_COMPLETED"},
                                     GoalSealedError("Goal sealed by Cycle 9")])
def test_goal_stop_is_never_failure(tmp_path, monkeypatch, outcome):
    spec, root = task(tmp_path)
    result, claims, _claim, _seen = run_with(monkeypatch, spec, root, outcome)
    assert result == 0 and claims.list_claims() == []
    assert claims.history()[-1]["status"] == "stopped_goal"
    worker = wr.read_worker(root, "worker-1")
    assert worker["status"] == wr.STOPPED_GOAL_COMPLETED and worker["error_kind"] is None
    assert worker["goal_stop"]["at"]


def test_unexpected_exception_is_failed_and_pauses_claim(tmp_path, monkeypatch):
    spec, root = task(tmp_path)
    with pytest.raises(RuntimeError):
        run_with(monkeypatch, spec, root, RuntimeError("boom"))
    worker = wr.read_worker(root, "worker-1")
    assert worker["status"] == "FAILED" and worker["error_kind"] == "RuntimeError"
    assert CycleClaims(root).list_claims()[0]["status"] == "paused"


def test_g10_only_plan_change_or_own_h_deleted_or_closed(tmp_path):
    import hashlib
    spec, _root = task(tmp_path)
    digest = hashlib.sha256(spec.document.read_bytes()).hexdigest()
    check = lambda **kw: wr.detect_critical_knowledge_change(spec, plan_digest=digest, **kw)
    assert check(hypothesis_id="H-0001", cycle_id="5") is None
    with ScienceGraphStore(tmp_graph(spec)) as store:  # parent/other H closed: ordinary science
        store.set_frontiers_batch([{"hypothesis_id": "H-0002", "status": "closed", "reason": "refuted"}])
    assert check(hypothesis_id="H-0001", cycle_id="5") is None
    assert check(hypothesis_id="H-0009", cycle_id="5")["reason"] == "own_hypothesis_deleted"
    with ScienceGraphStore(tmp_graph(spec)) as store:
        store.set_frontiers_batch([{"hypothesis_id": "H-0001", "status": "closed", "reason": "operator"}])
    assert check(hypothesis_id="H-0001", cycle_id="5")["reason"] == "own_hypothesis_closed"
    import sqlite3
    with sqlite3.connect(tmp_graph(spec)) as db:  # closed by this Cycle's own Outcome: no notice
        db.execute("INSERT INTO cycle_evaluations VALUES (?,?,?,?,?,?)",
                   ("e5", "H-0001", "5", "supported", "{}", "2026-10-20T00:00:00+00:00"))
    assert check(hypothesis_id="H-0001", cycle_id="5") is None
    assert check(hypothesis_id="H-0001", cycle_id="6")["reason"] == "own_hypothesis_closed"
    spec.document.write_text("# Goal changed", encoding="utf-8")
    assert check(hypothesis_id=None)["reason"] == "research_plan_changed"


def paused_cycle(tmp_path: Path, *, state_status: str = "paused", worker_status: str = "PAUSED"):
    spec, root = task(tmp_path)
    claims = CycleClaims(root, tmp_graph(spec))
    claim = claims.claim_cycle("worker-1")
    cycle = reserve_cycle_number(root, "g")
    claims.bind_cycle("worker-1", claim["claim_id"], claim["fencing_epoch"], cycle)
    claims.bind_hypothesis("worker-1", claim["claim_id"], claim["fencing_epoch"], "H-0001")
    claims.update("worker-1", claim["claim_id"], claim["fencing_epoch"], action="pause")
    (root / cycle / "research_state.json").write_text(json.dumps({
        "worker_id": "worker-1", "status": state_status, "resumable": True,
        "hypothesis_lock": {"id": "H-0001"},
        "recovery": {"kind": "RuntimeError", "message": "Traceback transient", "at": "t"}}))
    wr.save_worker(root, "worker-1", status=worker_status, current_cycle=cycle, account=None,
                   claim_id=claim["claim_id"], epoch=claim["fencing_epoch"], pid=None)
    return spec, root, claims, claim, cycle


def test_resume_reissues_claim_for_same_cycle_and_h(tmp_path, monkeypatch):
    spec, root, claims, claim, cycle = paused_cycle(tmp_path)
    launched = []
    monkeypatch.setattr(wr, "_launch_detached_service", lambda args, **kw: launched.append(args))
    result = wr.worker_action(spec, "worker-1", "resume")
    [live] = claims.list_claims()
    assert (result["cycle"], result["hypothesis_id"]) == (cycle, "H-0001")
    assert (live["cycle_id"], live["hypothesis_id"]) == (cycle, "H-0001")
    assert live["claim_id"] != claim["claim_id"] and live["fencing_epoch"] > claim["fencing_epoch"]
    assert live["reissued_from_claim_id"] == claim["claim_id"]
    assert claims.history()[-1]["status"] == "superseded"
    args = launched[0]
    assert args[args.index("--resume-cycle") + 1] == cycle
    assert args[args.index("--claim-id") + 1] == live["claim_id"]
    worker = wr.read_worker(root, "worker-1")
    assert (worker["claim_id"], worker["epoch"]) == (live["claim_id"], live["fencing_epoch"])


def test_resume_after_goal_seal_is_refused_without_launch(tmp_path, monkeypatch):
    spec, root, claims, claim, _cycle = paused_cycle(tmp_path)
    with ScienceGraphStore(tmp_graph(spec)) as store:
        store.seal_goal(goal_id="g", source_cycle="9", research_plan_sha256="0" * 64,
                        judge_receipt_ref=None, detail={})
    monkeypatch.setattr(wr, "_launch_detached_service", lambda *a, **k: pytest.fail("launched"))
    with pytest.raises(ValueError, match="Goal already completed"):
        wr.worker_action(spec, "worker-1", "resume")
    assert claims.list_claims()[0]["claim_id"] == claim["claim_id"]


def test_dismiss_refuses_live_cycle_claim_and_allows_goal_stopped(tmp_path):
    spec, root, claims, claim, _cycle = paused_cycle(tmp_path)
    wr.save_worker(root, "worker-2", status="OVER")
    wr.save_worker(root, "worker-1", status=wr.STOPPED_GOAL_COMPLETED)
    with pytest.raises(ValueError, match="still owns a research claim"):
        wr.worker_action(spec, "worker-1", "dismiss")
    claims.update("worker-1", claim["claim_id"], claim["fencing_epoch"], action="stopped_goal")
    wr.save_worker(root, "worker-1", claim_id=None, epoch=None)
    assert wr.worker_action(spec, "worker-1", "dismiss")["status"] == "hidden"


def test_dispatch_treats_waiting_and_goal_stop_as_non_incidents(tmp_path):
    spec, root, _claims, _claim, cycle = paused_cycle(tmp_path, state_status="failed",
                                                      worker_status="FAILED")
    worker = wr.read_worker(root, "worker-1")
    assert dispatch._eligible(root, {**worker, "process_alive": False})[0] == root / cycle
    for status in wr.NON_INCIDENT_STOPS:
        assert dispatch._eligible(root, {**worker, "status": status, "process_alive": False}) is None


def test_dispatch_requires_live_cycle_claim_for_exact_cycle(tmp_path):
    spec, root, claims, claim, cycle = paused_cycle(tmp_path, state_status="failed",
                                                    worker_status="FAILED")
    worker = {**wr.read_worker(root, "worker-1"), "process_alive": False}
    assert dispatch._eligible(root, {**worker, "claim_id": "stale"}) is None
    assert dispatch._eligible(root, {**worker, "current_cycle": str(int(cycle) + 1)}) is None
    claims.update("worker-1", claim["claim_id"], claim["fencing_epoch"], action="release")
    assert dispatch._eligible(root, worker) is None
