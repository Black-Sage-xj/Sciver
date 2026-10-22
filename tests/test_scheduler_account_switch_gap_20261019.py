"""No real LLM calls: regress Worker scheduler recovery across account-switch gaps."""
import json
from types import SimpleNamespace

from theory_master import worker_runtime
from theory_master.worker_mode import configure_mode


def prepare(tmp_path, monkeypatch, *, prior_status="STALLED"):
    root = tmp_path / "research"
    root.mkdir()
    configure_mode(root, enabled=True, desired_agents=2)
    metadata = root / ".theory-master" / "worker_scheduler.json"
    metadata.write_text(json.dumps({
        "quota_mode": "per_worker",
        "status": prior_status,
        "worker_ids": ["worker-1", "worker-2"],
        "max_cycles_per_worker": 20,
        "cycles_started_by_worker": {"worker-1": 13, "worker-2": 3},
        "resume_batch": True,
    }))
    spec = SimpleNamespace(research_root=root, max_cycles=20)
    from theory_master import specification, worker_supervisor_dispatch
    monkeypatch.setattr(specification.TaskSpec, "from_task_root", classmethod(lambda cls, path: spec))
    monkeypatch.setattr(worker_runtime, "_living_workers", lambda root: [])
    monkeypatch.setattr(worker_runtime, "list_workers", lambda root: [])
    monkeypatch.setattr(worker_supervisor_dispatch, "dispatch_pending", lambda root: False)
    monkeypatch.setattr(worker_runtime.threading.Event, "wait", lambda self, seconds: False)
    return root, metadata, spec


def observe_scheduler_metadata(monkeypatch, metadata):
    snapshots = []
    original = worker_runtime._save_json

    def recording_save(path, payload):
        if path == metadata:
            snapshots.append(dict(payload))
        return original(path, payload)
    monkeypatch.setattr(worker_runtime, "_save_json", recording_save)
    return snapshots


def test_temporary_gap_resumes_and_keeps_allocation_counts(tmp_path, monkeypatch):
    _, metadata, spec = prepare(tmp_path, monkeypatch, prior_status="RUNNING")
    saves = observe_scheduler_metadata(monkeypatch, metadata)
    outcomes = []

    def schedule(spec, **kwargs):
        outcomes.append(kwargs)
        if len(outcomes) == 1:
            return {"started": [], "eligible": 1, "concurrency": 2}
        return {"started": [{"worker_id": "worker-2"}], "eligible": 1, "concurrency": 2}

    monkeypatch.setattr(worker_runtime, "_master_complete", lambda root: len(outcomes) >= 2)
    monkeypatch.setattr(worker_runtime, "schedule_once", schedule)
    assert worker_runtime.scheduler_daemon(spec, 2) == 0
    result = json.loads(metadata.read_text())
    assert result["status"] == "STOPPED"
    assert result["cycles_started_by_worker"] == {"worker-1": 13, "worker-2": 4}
    assert result["resume_batch"] is True
    assert all(item["eligible_worker_ids"] == frozenset({"worker-1", "worker-2"}) for item in outcomes)
    assert any(item["status"] == "RUNNING" and
               item.get("last_no_progress_reason") == "no_worker_started_and_no_live_worker"
               for item in saves)
    assert all(item["status"] != "STALLED" for item in saves)


def test_no_frontier_waits_for_future_candidate_instead_of_stalling(tmp_path, monkeypatch):
    _, metadata, spec = prepare(tmp_path, monkeypatch)
    saves = observe_scheduler_metadata(monkeypatch, metadata)
    calls = []

    def schedule(*args, **kwargs):
        calls.append(1)
        return {"started": [], "eligible": 0, "concurrency": 2}

    monkeypatch.setattr(worker_runtime, "_master_complete", lambda root: len(calls) >= 3)
    monkeypatch.setattr(worker_runtime, "schedule_once", schedule)
    assert worker_runtime.scheduler_daemon(spec, 2) == 0
    waiting = [s for s in saves if s.get("last_no_progress_reason") == "awaiting_eligible_frontier"]
    assert len(waiting) == 3
    assert all(s["status"] == "RUNNING" and
               s["remaining_cycles_by_worker"] == {"worker-1": 7, "worker-2": 17} for s in waiting)
    assert json.loads(metadata.read_text())["cycles_started_by_worker"] == {"worker-1": 13, "worker-2": 3}


def test_paused_claim_retains_capacity_despite_missing_pid(tmp_path, monkeypatch):
    _, metadata, spec = prepare(tmp_path, monkeypatch)
    saves = observe_scheduler_metadata(monkeypatch, metadata)
    calls = []
    monkeypatch.setattr(worker_runtime, "_living_workers", lambda root: [{"worker_id": "worker-2",
                                                                           "claim_id": "pinned"}])
    monkeypatch.setattr(worker_runtime, "schedule_once",
                        lambda *a, **kw: (calls.append(1) or {"started": [], "eligible": 1}))
    monkeypatch.setattr(worker_runtime, "_master_complete", lambda root: len(calls) >= 1)
    assert worker_runtime.scheduler_daemon(spec, 2) == 0
    assert any(s.get("last_no_progress_reason") == "workers_active_or_claimed" for s in saves)
    assert all(s["status"] != "STALLED" for s in saves)
    assert json.loads(metadata.read_text())["cycles_started_by_worker"]["worker-2"] == 3


def test_failed_claim_reports_manual_recovery_without_mislabeling_live_work(tmp_path, monkeypatch):
    _, metadata, spec = prepare(tmp_path, monkeypatch)
    saves = observe_scheduler_metadata(monkeypatch, metadata)
    calls = []
    monkeypatch.setattr(worker_runtime, "_living_workers", lambda root: [{
        "worker_id": "worker-2", "claim_id": "pinned", "status": "FAILED",
        "process_alive": False, "current_cycle": "172",
        "error_kind": "NativeGoalRuntimeFailure",
    }])
    monkeypatch.setattr(worker_runtime, "schedule_once",
                        lambda *a, **kw: (calls.append(1) or {"started": [], "eligible": 1}))
    monkeypatch.setattr(worker_runtime, "_master_complete", lambda root: len(calls) >= 1)

    assert worker_runtime.scheduler_daemon(spec, 2) == 0
    blocked = [s for s in saves if "workers_require_manual_recovery" in
               str(s.get("last_no_progress_reason") or "")]
    assert blocked
    assert "worker-2" in blocked[0]["last_no_progress_reason"]
    assert "Cycle 172" in blocked[0]["last_no_progress_reason"]
    assert "explicit Resume/Release required" in blocked[0]["last_no_progress_reason"]
    assert blocked[0]["workers_requiring_manual_recovery"] == [{
        "worker_id": "worker-2", "status": "FAILED", "cycle": "172",
        "error_kind": "NativeGoalRuntimeFailure",
    }]
    assert all(s.get("last_no_progress_reason") != "workers_active_or_claimed"
               for s in saves)


def test_persistent_block_reports_stalled_without_losing_quota(tmp_path, monkeypatch):
    _, metadata, spec = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(worker_runtime, "SCHEDULER_NO_PROGRESS_GRACE_SECONDS", 0)
    monkeypatch.setattr(worker_runtime, "_master_complete", lambda root: False)
    monkeypatch.setattr(worker_runtime, "schedule_once",
                        lambda *a, **kw: {"started": [], "eligible": 1, "reason": "no eligible idle Worker"})
    assert worker_runtime.scheduler_daemon(spec, 2) == 2
    result = json.loads(metadata.read_text())
    assert result["status"] == "STALLED"
    assert result["stall_reason"] == "no eligible idle Worker"
    assert result["remaining_cycles_by_worker"] == {"worker-1": 7, "worker-2": 17}
    assert result["resume_batch"] is True
