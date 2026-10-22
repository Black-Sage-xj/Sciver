"""Deterministic guardrails for TMA-0082 runtime recovery and provenance."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master import worker_runtime
from theory_master.runtime_manager import runtime_source_provenance
from theory_master.workflow import Workflow
from theory_master.worker_runtime import read_worker, save_worker


def test_dead_scheduler_pid_reconciles_stale_running_to_failed(tmp_path, monkeypatch):
    root = tmp_path / "research"
    meta = root / ".theory-master" / "worker_scheduler.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"status": "RUNNING", "pid": 4321,
                                "start_ticks": 99, "cycles_started": 7}))
    monkeypatch.setattr(worker_runtime, "process_identity_alive", lambda pid, ticks: False)

    status = worker_runtime.scheduler_status(root)

    assert status["status"] == "FAILED"
    assert status["process_alive"] is False
    assert status["last_pid"] == 4321
    assert status["error_kind"] == "SchedulerProcessExited"
    persisted = json.loads(meta.read_text())
    assert persisted["status"] == "FAILED"
    assert persisted["pid"] is None
    assert persisted["cycles_started"] == 7
    assert persisted["runtime_source_provenance"]["sdk_git_sha"]


def test_terminal_scheduler_state_is_not_rewritten(tmp_path, monkeypatch):
    root = tmp_path / "research"
    meta = root / ".theory-master" / "worker_scheduler.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"status": "STOPPED", "pid": 4321,
                                "start_ticks": 99}))
    monkeypatch.setattr(worker_runtime, "process_identity_alive", lambda pid, ticks: False)

    status = worker_runtime.scheduler_status(root)

    assert status["status"] == "STOPPED"
    assert status["process_alive"] is False


def test_scheduler_skill_preflight_rejects_missing_configured_skill(monkeypatch, tmp_path):
    from theory_master import skills

    config = tmp_path / "theory-master.config.json"
    config.write_text("{}")
    spec = SimpleNamespace(config_path=config)
    calls = []

    def fake_role_skills(role, task_config_path=None, **kwargs):
        calls.append(role)
        if role == "research_lead":
            raise FileNotFoundError("missing skill directory: canvas-maintenance")
        return ()

    monkeypatch.setattr(skills, "role_skills", fake_role_skills)
    with pytest.raises(FileNotFoundError, match="canvas-maintenance"):
        worker_runtime._validate_scheduler_skills(spec)
    assert calls == ["semantic_annotator", "budget_reviewer", "attention_reviewer",
                     "runtime_supervisor", "incident_supervisor", "research_chat",
                     "research_lead"]


def test_scheduler_preflight_rejects_mixed_sdk_source_roots(monkeypatch, tmp_path):
    config = tmp_path / "theory-master.config.json"
    config.write_text("{}")
    spec = SimpleNamespace(config_path=config)
    monkeypatch.setattr(worker_runtime, "runtime_source_provenance", lambda: {
        "source_coherent": False,
        "source_coherence_errors": ["workflow_qualification.py: /old/sdk/theory_master/workflow_qualification.py"],
    })
    with pytest.raises(RuntimeError, match="Mixed TheoryMaster source roots"):
        worker_runtime._validate_scheduler_skills(spec)


def test_scheduler_start_failure_is_durably_recorded(tmp_path):
    root = tmp_path / "research"
    worker_runtime._record_scheduler_start_failure(
        root, FileNotFoundError("missing skill directory: canvas-maintenance"))
    meta = json.loads((root / ".theory-master" / "worker_scheduler.json").read_text())
    assert meta["status"] == "FAILED"
    assert meta["error_kind"] == "FileNotFoundError"
    assert "canvas-maintenance" in meta["error_detail"]
    assert meta["runtime_source_provenance"]["source_root"]

def test_worker_runtime_records_sdk_revision_and_module_hashes(tmp_path):
    root = tmp_path / "research"
    save_worker(root, "worker-1", status="RESERVED")
    record = read_worker(root, "worker-1")
    provenance = record["runtime_source_provenance"]
    assert Path(provenance["source_root"]).is_absolute()
    assert len(provenance["module_sha256"]["workflow_qualification.py"]) == 64
    assert len(provenance["module_sha256"]["canvas_projection.py"]) == 64
    assert record["runtime_source_history"][-1]["schema"] == provenance["schema"]


def test_source_provenance_helper_reports_loaded_sdk_tree():
    provenance = runtime_source_provenance()
    assert provenance["schema"] == "theory-master/runtime-source-provenance/v1"
    assert Path(provenance["source_root"]).is_absolute()
    assert {"workflow.py", "workflow_qualification.py", "canvas_projection.py"}.issubset(
        provenance["module_sha256"])
    assert {"workflow.py", "workflow_qualification.py", "canvas_projection.py"}.issubset(
        provenance["module_paths"])
    assert Path(provenance["module_paths"]["workflow.py"]).is_absolute()
    assert provenance["source_coherent"] is True
    assert provenance["source_coherence_errors"] == []
    assert all(len(value) == 64 for value in provenance["module_sha256"].values())


def test_legacy_hypothesis_projection_method_delegates_to_canonical_sqlite_method():
    workflow = Workflow.__new__(Workflow)
    # The bridge test never opens a graph database or touches scientific files.
    workflow._ensure_locked_hypothesis = lambda state, branch, selected: "H-0099"
    assert workflow._ensure_locked_hypothesis_projection({}, {}, {}) == "H-0099"
