"""Deterministic preservation flow: budget reject -> provenance summary -> release."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from theory_master.investigator_budget import apply_review
from theory_master.research_preservation import (
    blocked_checkpoint, preserve_blocked_cycle, finalize_preserved_cycle,
    PreservationGuard,
)
from theory_master.science_graph_store import ScienceGraphStore
from theory_master import worker_runtime as worker


def stage():
    return {
        "observations": [{"statement": "Fixed physical scaffolding was not discriminating",
                          "conditions": "Existing 52 graphs and 208 roots",
                          "limitations": "Does not falsify a scaffold effect in other ensembles",
                          "evidence_paths": ["workspace/result.md"]}],
        "methods_exhausted": [{"method": "Prespecified topology prescreen",
                               "limitation": "No qualifying B/S structural contrast"}],
        "remaining_gap": "No independent qualifying graph-level contrast",
        "unexecuted_test": "Independent held-out energy-law discrimination unavailable",
        "canvas_title": "C155 阶段性研究保全",
        "canvas_summary": "已有网络的骨架占比差异不足以识别独立预测增益，原 Judge 返工未完成。",
    }


def budget_fixture(tmp_path):
    root = tmp_path / "research"
    cycle = root / "155"
    cycle.mkdir(parents=True)
    canvas = tmp_path / "notes" / "exploration.canvas"
    canvas.parent.mkdir()
    canvas.write_text(json.dumps({
        "nodes": [{"id": "H-0155", "text": "type: Hypothesis\nfrontier_status: active\n# Test H",
                   "x": 5, "y": 5, "width": 550, "height": 250}],
        "edges": [],
    }))
    state = {
        "status": "needs_attention", "phase": "judge:rework_blocked",
        "execution_active": False, "resumable": True, "cycle_id": "155",
        "run_id": "155", "research_root": str(root), "branch_canvas": str(canvas),
        "worker_id": "worker-1", "claimed_frontier_id": "H-0090",
        "attention": {"code": "investigator_budget_review_required", "reason": "Budget review"},
        "investigator_budget": {"rejected": False},
        "hypothesis_lock": {"id": "H-0155", "status": "locked",
                            "hypothesis": "Test physical proposition",
                            "scope": "Existing networks only",
                            "decisive_test": "Independent contrast"},
        "agents": {"investigator": {"tokens_total": 50_100_000}},
        "investigation_attempts": {
            "attempt-0001": {"judge_decision": {"assessment_state": "rework_requested",
                                                "missing_evidence": "Independent contrast unavailable"},
                             "outputs": [{"executor_role": "investigator",
                                          "output": {"findings": [{"statement": "Historic null screen"}]}}]},
            "attempt-0002": {"judge_decision": None, "outputs": []},
        },
        "role_outputs": {"judge": {"assessment_state": "rework_requested"}},
        "assessment_state": "rework_requested",
        "scientific_verdict": None,
    }
    for name in ("run_state.json", "research_state.json"):
        (cycle / name).write_text(json.dumps(state))
    registry = cycle / "evidence"
    registry.mkdir()
    (registry / "registry.jsonl").write_text(json.dumps(
        {"evidence_id": "E-155", "summary": "Topological scans are exhausted"}) + "\n")
    (cycle / "workspace").mkdir()
    (cycle / "workspace" / "result.md").write_text("Response blind prescreen had no contrast.")
    apply_review(
        cycle, decision="reject",
        reason="No new independent topology contrast justifies another resource tranche.",
        next_test="Only search a named verified existing input source with a bounded prescreen.",
        evidence_ids=["E-155"], progress_refs=[],
    )
    for name in ("run_state.json", "research_state.json"):
        data = json.loads((cycle / name).read_text())
        data["attention"] = {"code": "investigator_budget_review_rejected", "reason": "No more spend"}
        data["investigator_budget"]["rejected"] = True
        (cycle / name).write_text(json.dumps(data))
    graph_path = canvas.with_name("science_graph.sqlite3")
    with ScienceGraphStore(graph_path) as store:
        store.add_hypothesis("H-0155", title="Test H")
        store.register_cycle_hypothesis(cycle_id="155", hypothesis_id="H-0155")
    return cycle, canvas, graph_path


def test_budget_denied_preserve_writes_note_and_v2_external_with_judge_history(tmp_path):
    cycle, canvas, graph = budget_fixture(tmp_path)
    scoped = blocked_checkpoint(cycle)
    assert scoped["preservation_blockage"]["code"] == "investigator_budget_review_rejected"
    result = preserve_blocked_cycle(
        cycle_dir=cycle, canvas_path=canvas, stage_output=stage(),
        authorization="operator_approved")
    assert result["node_id"] == "H-0155"
    assert result["presentation"] == "hypothesis_outcome"
    with ScienceGraphStore(graph, readonly=True) as store:
        view = store.graph_view()
        assert not any(n["type"] == "External" and "preservation" in n["id"]
                       for n in view["nodes"])
        h = next(n for n in view["nodes"] if n["id"] == "H-0155")
        outcome = next(o for o in h["cycle_outcomes"] if o["cycle_id"] == "155")
        assert outcome["verdict"] == "unadjudicated"
        assert outcome["execution_state"] == "unfinished"
        assert outcome["termination_reason"] == "investigator_budget_review_rejected"
        assert "Independent contrast unavailable" in outcome["prior_judge_objection"]
        assert not store.cycle_history("H-0155")  # Never fabricate a Judge Outcome.
        assert store.frontier_view()["H-0155"]["frontier_status"] == "parked"
    note = canvas.parent / "notes/scientific_reports/Cycle155-preserve_research.md"
    assert "Independent contrast unavailable" in note.read_text()
    receipt = finalize_preserved_cycle(
        cycle_dir=cycle, authorization="operator_approved")
    assert receipt["status"] == "OVER"
    assert receipt["scientific_verdict"] is None
    assert receipt["source_attention_code"] == "investigator_budget_review_rejected"
    assert json.loads((cycle / "research_state.json").read_text())["role_outputs"]["judge"][
        "assessment_state"] == "rework_requested"


def test_preservation_closure_survives_v2_canvas_projection_rebuild(tmp_path):
    cycle, canvas, graph = budget_fixture(tmp_path)
    result = preserve_blocked_cycle(
        cycle_dir=cycle, canvas_path=canvas, stage_output=stage(),
        authorization="operator_approved")
    assert result["presentation"] == "hypothesis_outcome"
    model = json.loads(canvas.read_text())
    h = next(n for n in model["nodes"] if n["id"] == "H-0155")
    # The V2 materializer reconstructs Canvas from canonical graph and
    # drops custom legacy node fields. The textual Outcome and SQLite record
    # remain; closure verification must use both, not a transient sidecar.
    h.pop("preserved_cycle_outcomes", None)
    h["text"] = (h["text"].split("## Cycle 155")[0]
                 + "## Cycle 155 · Outcome · unadjudicated\n"
                 + "保全已完成；[[notes/scientific_reports/Cycle155-preserve_research.md]]")
    canvas.write_text(json.dumps(model, ensure_ascii=False))
    receipt = finalize_preserved_cycle(cycle_dir=cycle, authorization="operator_approved")
    assert receipt["status"] == "OVER"
    assert receipt["scientific_verdict"] is None


def test_budget_preservation_refuses_unrecorded_denial_and_failure_does_not_close(tmp_path):
    cycle, canvas, graph = budget_fixture(tmp_path)
    run = json.loads((cycle / "run_state.json").read_text())
    run["attention"]["code"] = "investigator_budget_review_failed"
    (cycle / "run_state.json").write_text(json.dumps(run))
    with pytest.raises(PreservationGuard):
        preserve_blocked_cycle(cycle_dir=cycle, canvas_path=canvas,
                               stage_output=stage(), authorization="operator_approved")
    assert not (cycle / "preservation/status.json").exists()
    assert not (cycle / "preservation/closure.json").exists()


def test_preserve_request_reserves_claim_and_enqueues_only(monkeypatch, tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    worker_state = {"status": "NEEDS_ATTENTION", "current_cycle": "155",
                    "worker_id": "worker-1", "claim_id": "id", "epoch": 4,
                    "entry_node_id": "H-0090", "pid": None, "account": None}
    monkeypatch.setattr(worker, "read_worker", lambda *args: dict(worker_state))
    monkeypatch.setattr(worker, "save_worker", lambda *args, **kw: worker_state.update(kw) or dict(worker_state))
    monkeypatch.setattr(worker, "process_identity_alive", lambda *args: False)
    monkeypatch.setattr(worker, "_launch_detached_service", MagicMock())
    import theory_master.research_preservation as rp
    # Production run_state.json deliberately omits the Worker owner fields.
    # Preserve MUST consult research_state.json, not the Web projection.
    (research / "155").mkdir()
    (research / "155/research_state.json").write_text(json.dumps({
        "worker_id": "worker-1", "claimed_frontier_id": "H-0090",
        "resumable": True, "status": "needs_attention",
    }))
    monkeypatch.setattr(rp, "blocked_checkpoint", lambda _: {
        "resumable": True, "status": "NEEDS_ATTENTION"})
    class FakeClaims:
        def __init__(self, *_): pass
        def list_claims(self):
            return [{"worker_id": "worker-1", "claim_id": "id", "fencing_epoch": 4,
                     "cycle_id": "155", "hypothesis_id": None}]
    monkeypatch.setattr(worker, "CycleClaims", FakeClaims)
    spec = SimpleNamespace(research_root=research, branch_canvas=tmp_path / "exploration.canvas")
    result = worker.request_preserve_release(spec, "worker-1")
    assert result["status"] == worker_state["status"] == "PRESERVING"
    assert result["claim_preserved"]
    worker._launch_detached_service.assert_called_once()


def test_preserve_rejects_authoritative_worker_mismatch(monkeypatch, tmp_path):
    research = tmp_path / "research"
    cycle = research / "155"
    cycle.mkdir(parents=True)
    (cycle / "research_state.json").write_text(json.dumps({
        "worker_id": "worker-2", "claimed_frontier_id": "H-0090",
        "resumable": True,
    }))
    monkeypatch.setattr(worker, "read_worker", lambda *_: {
        "status": "NEEDS_ATTENTION", "current_cycle": "155", "worker_id": "worker-1",
        "entry_node_id": "H-0090", "claim_id": "claim", "epoch": 17, "pid": None})
    monkeypatch.setattr(worker, "process_identity_alive", lambda *_: False)
    monkeypatch.setattr(worker, "save_worker", MagicMock())
    monkeypatch.setattr(worker, "_launch_detached_service", MagicMock())
    import theory_master.research_preservation as rp
    monkeypatch.setattr(rp, "blocked_checkpoint", lambda _: {"resumable": True})
    spec = SimpleNamespace(research_root=research, branch_canvas=tmp_path / "canvas")
    with pytest.raises(ValueError, match="authoritative frozen Cycle"):
        worker.request_preserve_release(spec, "worker-1")
    worker.save_worker.assert_not_called()
    worker._launch_detached_service.assert_not_called()


def test_archive_job_failure_never_releases_claim(monkeypatch, tmp_path):
    root = tmp_path / "research"
    cycle = root / "155"
    cycle.mkdir(parents=True)
    worker_state = {"status": "PRESERVING", "current_cycle": "155"}
    monkeypatch.setattr(worker, "read_worker", lambda *args: dict(worker_state))
    monkeypatch.setattr(worker, "save_worker", lambda *args, **kw: worker_state.update(kw) or dict(worker_state))
    monkeypatch.setattr(worker, "worker_action", MagicMock())
    monkeypatch.setattr(worker.subprocess, "run",
                        MagicMock(return_value=SimpleNamespace(returncode=7)))
    spec = SimpleNamespace(research_root=root)
    result = worker.execute_preserve_release_job(spec, "worker-1")
    assert result == 1 and worker_state["status"] == "NEEDS_ATTENTION"
    worker.worker_action.assert_not_called()
    assert not (cycle / "preservation/closure.json").exists()


def test_ui_action_names_and_validation():
    from tm_observer.run_views import _worker_summary_controls
    from tm_observer.run_control import _validated_value
    page = _worker_summary_controls(
        {"state": "NEEDS_ATTENTION", "cycle": "155"}, "worker-1")
    assert 'data-worker-action="preserve_release"' in page
    assert "保全成果并结束本轮" in page
    assert _validated_value("worker_control", {"worker_id": "worker-1",
                                "operation": "preserve_release"})["operation"] == "preserve_release"


def test_archive_job_success_releases_only_after_verified_closure(monkeypatch, tmp_path):
    root = tmp_path / "research"
    cycle = root / "155"
    (cycle / "preservation").mkdir(parents=True)
    (cycle / "preservation/closure.json").write_text(json.dumps({
        "cycle_id": "155", "scientific_verdict": None,
        "canvas_node_id": "external-c155-preservation",
    }))
    (cycle / "research_state.json").write_text(json.dumps({
        "status": "over", "closure_reason": "investigator_preservation",
        "scientific_verdict": None,
    }))
    worker_state = {"status": "PRESERVING", "current_cycle": "155"}
    monkeypatch.setattr(worker, "read_worker", lambda *args: dict(worker_state))
    def release_action(*_args):
        assert (cycle / "preservation/closure.json").exists()
        assert json.loads((cycle / "research_state.json").read_text())["status"] == "over"
        worker_state["status"] = "RELEASED"
        return {"status": "RELEASED"}
    release = MagicMock(side_effect=release_action)
    monkeypatch.setattr(worker, "worker_action", release)
    monkeypatch.setattr(worker.subprocess, "run",
                        MagicMock(return_value=SimpleNamespace(returncode=0)))
    import theory_master.worker_mode as wm
    monkeypatch.setattr(wm, "read_mode", lambda _: {"enabled": True, "desired_agents": 1})
    schedule = MagicMock(return_value={"status": "already_running"})
    monkeypatch.setattr(worker, "start_scheduler", schedule)
    spec = SimpleNamespace(research_root=root)
    assert worker.execute_preserve_release_job(spec, "worker-1") == 0
    command = worker.subprocess.run.call_args.args[0]
    assert command[command.index("--task-root") + 1] == str(root)
    assert command[command.index("--identifier") + 1] == "155"
    # Multi-Worker claim is still held until archive commits; runner's
    # legacy foreground_runtime refuses claims unless managed-child is set.
    assert "--managed-child" in command
    release.assert_called_once()
    schedule.assert_called_once()
    assert json.loads((root / ".theory-master/workers/worker-1/preservation_result.json").read_text())[
        "status"] == "preserved_released"


def test_budget_preservation_refuses_judge_terminal_or_wrong_science_identity(tmp_path):
    cycle, canvas, _ = budget_fixture(tmp_path)
    science = json.loads((cycle / "research_state.json").read_text())
    science["scientific_verdict"] = "supported"
    (cycle / "research_state.json").write_text(json.dumps(science))
    with pytest.raises(PreservationGuard, match="scientific verdict"):
        blocked_checkpoint(cycle)
    science["scientific_verdict"] = None
    science["hypothesis_lock"]["id"] = "H-9999"
    (cycle / "research_state.json").write_text(json.dumps(science))
    with pytest.raises(PreservationGuard, match="identities differ"):
        blocked_checkpoint(cycle)


def test_archived_cycle_recovery_releases_without_new_model(monkeypatch, tmp_path):
    root = tmp_path / "research"
    cycle = root / "155"
    cycle.mkdir(parents=True)
    (cycle / "research_state.json").write_text(json.dumps({"status": "over"}))
    worker_state = {"status": "NEEDS_ATTENTION", "worker_id": "worker-1",
                    "current_cycle": "155", "claim_id": "claim-1",
                    "epoch": 17, "pid": None, "entry_node_id": "H-0090"}
    monkeypatch.setattr(worker, "read_worker", lambda *_: dict(worker_state))
    monkeypatch.setattr(worker, "save_worker",
                        lambda *args, **kw: worker_state.update(kw) or dict(worker_state))
    monkeypatch.setattr(worker, "process_identity_alive", lambda *_: False)
    released = []
    class FakeClaims:
        def __init__(self, *_): pass
        def update(self, wid, claim, epoch, action):
            released.append((wid, claim, epoch, action))
    monkeypatch.setattr(worker, "CycleClaims", FakeClaims)
    monkeypatch.setattr(worker, "reconcile_released_worker_cycle",
                        lambda *_: {"reconciled": True})
    import theory_master.research_preservation as rp
    closure = MagicMock(return_value={"cycle_id": "155", "scientific_verdict": None})
    monkeypatch.setattr(rp, "finalize_preserved_cycle", closure)
    spec = SimpleNamespace(research_root=root, branch_canvas=tmp_path / "canvas")
    result = worker.request_preserve_release(spec, "worker-1")
    assert result["status"] == "RELEASED"
    assert result["reused_verified_preservation"] is True
    assert released == [("worker-1", "claim-1", 17, "release")]
    assert worker_state["status"] == "RELEASED"
    closure.assert_called_once()
