"""Released Worker/Cycle lifecycle reconciliation is operational, never scientific."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master import worker_runtime


def _fixture(tmp_path: Path, *, status: str = "active", run_status: str = "RUNNING"):
    root = tmp_path / "research"
    cycle = root / "1"
    cycle.mkdir(parents=True)
    checkpoint = cycle / "checkpoints" / "safe.json"
    checkpoint.parent.mkdir()
    checkpoint.write_text('{"frozen": true}', encoding="utf-8")
    evidence = cycle / "evidence.json"
    evidence.write_text('{"evidence": "preserve"}', encoding="utf-8")
    frozen = {
        "id": "H-0001",
        "statement": "frozen scientific claim",
        "decisive_test": "unchanged criterion",
    }
    science = {
        "cycle_id": "1",
        "worker_id": "worker-1",
        "cycle_claim_bound": True,
        "status": status,
        "phase": "investigator:running" if status == "active" else status,
        "runtime_state": "running" if status == "active" else status,
        "execution_active": status == "active",
        "resumable": status in {"active", "paused", "failed", "interrupted", "needs_attention"},
        "owner_pid": 900000 if status == "active" else None,
        "current_turn_id": "turn-frozen-1",
        "hypothesis_lock": frozen,
        "findings": [{"statement": "keep the original finding"}],
        "last_safe_checkpoint": str(checkpoint),
        "evidence_refs": [str(evidence)],
        "goal_completion": {"verdict": "not_passed"},
        "recovery": {"action": "prior_recovery", "reason": "keep this history"},
    }
    (cycle / "research_state.json").write_text(json.dumps(science), encoding="utf-8")
    run = {
        "status": run_status,
        "phase": "investigator:running",
        "execution_active": run_status == "RUNNING",
        "resumable": True,
        "owner_pid": 900000 if run_status == "RUNNING" else None,
        "current_agent": "investigator",
        "current_turn_id": "turn-frozen-1",
        "current_thread_id": "thread-frozen-1",
        "current_tool": "read_file",
        "current_activity": "research",
    }
    (cycle / "run_state.json").write_text(json.dumps(run), encoding="utf-8")
    worker_runtime.save_worker(
        root, "worker-1", status="INTERRUPTED", current_cycle="1",
        target_h_id="H-0001", entry_node_id="H-0001",
        claim_id="claim-1", epoch=3, pid=900000, pid_start_ticks=12345,
    )
    return root, cycle, checkpoint, evidence, frozen, science


class _Claims:
    def __init__(self, *args):
        self.released = []

    def update(self, worker_id, claim_id, epoch, *, action):
        self.released.append((worker_id, claim_id, epoch, action))


def test_release_reconciles_dead_worker_cycle_without_fabricating_over(tmp_path, monkeypatch):
    root, cycle, checkpoint, evidence, frozen, before = _fixture(tmp_path)
    claims = _Claims()
    monkeypatch.setattr(worker_runtime, "CycleClaims", lambda *args: claims)
    monkeypatch.setattr(worker_runtime, "process_identity_alive", lambda *_args: False)
    spec = SimpleNamespace(research_root=root, branch_canvas=root / "branch_canvas.json")

    result = worker_runtime.worker_action(spec, "worker-1", "release")

    science = json.loads((cycle / "research_state.json").read_text(encoding="utf-8"))
    run = json.loads((cycle / "run_state.json").read_text(encoding="utf-8"))
    worker = worker_runtime.read_worker(root, "worker-1")
    events = [json.loads(line) for line in (cycle / "worker_lifecycle_events.jsonl").read_text().splitlines()]

    assert result["status"] == "RELEASED"
    assert result["cycle_reconciliation"]["status"] == "interrupted"
    assert claims.released == [("worker-1", "claim-1", 3, "release")]
    assert worker["status"] == "RELEASED"
    assert worker["pid"] is None and worker["pid_start_ticks"] is None

    assert science["status"] == "interrupted"
    assert science["phase"] == "interrupted"
    assert science["execution_active"] is False
    assert science["resumable"] is False
    assert science["owner_pid"] is None
    assert science["hypothesis_lock"] == frozen
    assert science["findings"] == before["findings"]
    assert science["evidence_refs"] == before["evidence_refs"]
    assert science["last_safe_checkpoint"] == before["last_safe_checkpoint"]
    assert checkpoint.read_text(encoding="utf-8") == '{"frozen": true}'
    assert evidence.read_text(encoding="utf-8") == '{"evidence": "preserve"}'
    assert science["goal_completion"] == {"verdict": "not_passed"}
    assert "verdict" not in science and "judge_verdict" not in science

    assert run["status"] == "INTERRUPTED"
    assert run["execution_active"] is False
    assert run["resumable"] is False
    assert run["owner_pid"] is None
    assert run["current_agent"] is None
    assert run["current_turn_id"] is None
    assert events[-1]["event"] == "worker_cycle_reconciled"
    assert events[-1]["scientific_verdict_changed"] is False
    assert events[-1]["evidence_or_checkpoint_deleted"] is False
    science_backup = json.loads((cycle / "research_state.before-worker-release-reconcile.json").read_text(encoding="utf-8"))
    run_backup = json.loads((cycle / "run_state.before-worker-release-reconcile.json").read_text(encoding="utf-8"))
    assert science_backup["status"] == "active" and science_backup["execution_active"] is True
    assert run_backup["status"] == "RUNNING" and run_backup["execution_active"] is True


def test_release_refuses_if_cycle_owner_is_still_live(tmp_path, monkeypatch):
    root, cycle, _checkpoint, _evidence, _frozen, _before = _fixture(tmp_path)
    claims = _Claims()
    monkeypatch.setattr(worker_runtime, "CycleClaims", lambda *args: claims)
    monkeypatch.setattr(
        worker_runtime, "process_identity_alive",
        lambda pid, _ticks=None: pid is not None and int(pid) == 424242,
    )
    science_path = cycle / "research_state.json"
    science = json.loads(science_path.read_text(encoding="utf-8"))
    science["owner_pid"] = 424242
    science_path.write_text(json.dumps(science), encoding="utf-8")
    spec = SimpleNamespace(research_root=root, branch_canvas=root / "branch_canvas.json")

    with pytest.raises(ValueError, match="Cycle owner process is still live"):
        worker_runtime.worker_action(spec, "worker-1", "release")

    assert claims.released == []
    assert json.loads(science_path.read_text(encoding="utf-8"))["status"] == "active"
    assert worker_runtime.read_worker(root, "worker-1")["status"] == "INTERRUPTED"


def test_release_preserves_terminal_scientific_status_and_syncs_stale_run_state(tmp_path, monkeypatch):
    root, cycle, _checkpoint, _evidence, _frozen, _before = _fixture(
        tmp_path, status="over", run_status="RUNNING",
    )
    claims = _Claims()
    monkeypatch.setattr(worker_runtime, "CycleClaims", lambda *args: claims)
    monkeypatch.setattr(worker_runtime, "process_identity_alive", lambda *_args: False)
    science_path = cycle / "research_state.json"
    science = json.loads(science_path.read_text(encoding="utf-8"))
    science["execution_active"] = False
    science["resumable"] = False
    science["goal_completion"] = {"verdict": "passed"}
    science_path.write_text(json.dumps(science), encoding="utf-8")
    spec = SimpleNamespace(research_root=root, branch_canvas=root / "branch_canvas.json")

    worker_runtime.worker_action(spec, "worker-1", "release")

    after = json.loads(science_path.read_text(encoding="utf-8"))
    run = json.loads((cycle / "run_state.json").read_text(encoding="utf-8"))
    assert after["status"] == "over"
    assert after["goal_completion"] == {"verdict": "passed"}
    assert run["status"] == "OVER"
    assert run["execution_active"] is False
