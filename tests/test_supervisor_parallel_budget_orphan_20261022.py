"""Deterministic fault-injection; never call provider model or real Workers."""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from theory_master import worker_runtime as wr
from theory_master.worker_supervisor_dispatch import (
    _fingerprint, dispatch_pending, review_worker,
)
from theory_master.worker_runtime import save_worker


def _write(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value))


def _supervisor_fixture(tmp_path):
    root = tmp_path / "research"
    cycle = root / "127"
    cycle.mkdir(parents=True)
    state = {
        "status": "failed", "phase": "investigator", "resumable": True,
        "worker_id": "worker-1", "hypothesis_lock": {"id": "H-0127"},
        "role_outputs": {"research_lead": "frozen"},
        "recovery": {"kind": "RuntimeError", "message":
                     "Provider capacity recovery timed out after 1800s; thread must be idle before starting a goal"},
    }
    _write(cycle/"research_state.json", state)
    _write(root/".theory-master/cycle_claims.json", {
        "schema": "theory-master/cycle-claims/v1",
        "epoch": 1, "history": [], "claims": {
            "c1": {"claim_id": "c1", "fencing_epoch": 1,
                   "worker_id": "worker-1", "cycle_id": "127",
                   "hypothesis_id": "H-0127", "status": "claimed"}}})
    save_worker(root, "worker-1", status="FAILED", current_cycle="127",
                claim_id="c1", epoch=1, pid=None, account=None)
    (root/".theory-master/env").write_text("THEORY_MASTER_SUPERVISOR=1\n")
    return root, cycle, state


def test_live_peer_launches_readonly_capacity_probe_without_shared_repair(tmp_path):
    root, cycle, state = _supervisor_fixture(tmp_path)
    records = [
        {"worker_id": "worker-1", "status": "FAILED", "process_alive": False,
         "current_cycle": "127", "claim_id": "c1", "epoch": 1},
        {"worker_id": "worker-2", "status": "RUNNING", "process_alive": True},
    ]
    with (patch("theory_master.worker_supervisor_dispatch.list_workers", return_value=records),
          patch("theory_master.worker_supervisor_dispatch._live_peer_safe_probe_candidate", return_value=True),
          patch("theory_master.worker_supervisor_dispatch.subprocess.Popen",
                return_value=MagicMock(pid=612345)) as launch,
          patch("theory_master.worker_supervisor_dispatch.process_start_ticks", return_value=33),
          patch("theory_master.worker_supervisor_dispatch.process_identity_alive", return_value=False)):
        assert dispatch_pending(root) is True
    launch.assert_called_once()
    record = json.loads((root/".theory-master/workers/worker-1/supervisor_dispatch.json").read_text())
    assert record["read_only_peer_probe"] is True
    assert record["recovery_attempts"] == 0  # no paid engineering retry budget spent


def test_live_peer_capacity_resume_keeps_other_worker_and_never_calls_engineering(tmp_path):
    root, cycle, state = _supervisor_fixture(tmp_path)
    path = root/".theory-master/workers/worker-1/supervisor_dispatch.json"
    _write(path, {"cycle": "127", "worker_id": "worker-1",
                  "fingerprint": _fingerprint(state)})
    records = [
        {"worker_id": "worker-1", "status": "FAILED", "process_alive": False,
         "current_cycle": "127", "claim_id": "c1", "epoch": 1},
        {"worker_id": "worker-2", "status": "RUNNING", "process_alive": True},
    ]
    with (patch("theory_master.worker_supervisor_dispatch.list_workers", return_value=records),
          patch("theory_master.worker_supervisor_dispatch._live_peer_safe_probe_candidate", return_value=True),
          patch("theory_master.runtime_supervisor_review.review_runtime_failure",
                return_value={"action": "resume", "session_id": "read-only",
                              "reason": "Provider's identical pinned thread verified idle"}) as reviewer,
          patch("theory_master.supervisor_continuity.attempt_engineering_skill") as editing,
          patch("theory_master.supervisor._supervisor_backend") as backend,
          patch("theory_master.worker_supervisor_dispatch.worker_action",
                return_value={"pid": 500123}) as resume,
          patch("theory_master.specification.TaskSpec.from_task_root",
                return_value=SimpleNamespace(research_root=root))):
        answer = review_worker(root, "worker-1", "127", _fingerprint(state))
    assert answer["status"] == "resume_launched"
    assert reviewer.call_args.kwargs["readonly_provider_probe_only"] is True
    assert reviewer.call_args.kwargs["backend"] is None
    resume.assert_called_once()
    backend.assert_not_called()
    editing.assert_not_called()


def test_live_peer_capacity_not_verified_remains_deferred_no_engineering(tmp_path):
    root, cycle, state = _supervisor_fixture(tmp_path)
    path = root/".theory-master/workers/worker-1/supervisor_dispatch.json"
    _write(path, {"cycle": "127", "worker_id": "worker-1",
                  "fingerprint": _fingerprint(state)})
    records = [
        {"worker_id": "worker-1", "status": "FAILED", "process_alive": False,
         "current_cycle": "127", "claim_id": "c1", "epoch": 1},
        {"worker_id": "worker-2", "status": "RUNNING", "process_alive": True},
    ]
    with (patch("theory_master.worker_supervisor_dispatch.list_workers", return_value=records),
          patch("theory_master.worker_supervisor_dispatch._live_peer_safe_probe_candidate", return_value=True),
          patch("theory_master.runtime_supervisor_review.review_runtime_failure",
                return_value={"action": "hold", "session_id": "read-only",
                              "reason": "Thread still active"}),
          patch("theory_master.supervisor_continuity.attempt_engineering_skill") as editing,
          patch("theory_master.worker_supervisor_dispatch.worker_action") as resume):
        answer = review_worker(root, "worker-1", "127", _fingerprint(state))
    assert answer["status"] == "deferred_peers_active"
    assert answer["retry_at_epoch"] > time.time()
    assert json.loads(path.read_text())["status"] == "deferred_peers_active"
    editing.assert_not_called()
    resume.assert_not_called()


def _budget_fixture(tmp_path, *, error_type="RuntimeError", detail="turn completed without assistant final response"):
    root = tmp_path/"research"
    cycle = root/"185"
    state = {
        "worker_id": "worker-2", "status": "needs_attention",
        "phase": "investigator:budget_review", "resumable": True,
        "execution_active": False, "last_safe_checkpoint": "checkpoints/frozen.json",
        "attention": {"code": "investigator_budget_review_failed"},
        "investigator_budget": {"review_index": 1, "rejected": False},
    }
    _write(cycle/"research_state.json", state)
    with (cycle/"events.jsonl").open("w") as f:
        for _ in range(3):
            f.write(json.dumps({"type": "investigation.budget_review_failed",
                    "payload": {"review_index": 1,
                                "error_type": error_type, "detail": detail}})+"\n")
    worker = {"worker_id": "worker-2", "status": "NEEDS_ATTENTION",
              "process_alive": False, "pid": None, "current_cycle": "185",
              "claim_id": "c2", "epoch": 2, "account": "2"}
    return root, cycle, worker


def test_budget_transport_one_verified_resume_no_grant_or_repeat(tmp_path):
    root, cycle, worker = _budget_fixture(tmp_path)
    spec = SimpleNamespace(research_root=root)
    with (patch.object(wr, "list_workers", return_value=[worker]),
          patch.object(wr, "process_identity_alive", return_value=False),
          patch.object(wr, "_graph_path", return_value=root/"graph.db"),
          patch.object(wr, "_worker_live_claim", return_value={"cycle_id": "185"}),
          patch.object(wr, "_checkpoint_matches_claim", return_value=True),
          patch("theory_master.investigator_budget.budget_status",
                return_value={"review_index": 1, "rejected": False}),
          patch("theory_master.codex_accounts.account_rate_limits", return_value={"ok": True}),
          patch("theory_master.codex_accounts.quota_available", return_value=True),
          patch.object(wr, "worker_action", return_value={"status": "STARTING"}) as resume):
        assert wr._dispatch_exhausted_budget_reviewer_transport(spec) is True
        assert wr._dispatch_exhausted_budget_reviewer_transport(spec) is False
        resume.assert_called_once_with(spec, "worker-2", "resume")
    receipt = root/".theory-master/workers/worker-2/budget_reviewer_transport_recovery_185_1.json"
    assert json.loads(receipt.read_text())["status"] == "resume_dispatched"


def test_budget_json_failure_never_retried_as_transport(tmp_path):
    root, cycle, worker = _budget_fixture(
        tmp_path, error_type="ValueError",
        detail="Budget Reviewer did not return one valid JSON object")
    spec = SimpleNamespace(research_root=root)
    with (patch.object(wr, "list_workers", return_value=[worker]),
          patch.object(wr, "process_identity_alive", return_value=False),
          patch.object(wr, "_graph_path", return_value=root/"graph.db"),
          patch.object(wr, "_worker_live_claim", return_value={"cycle_id": "185"}),
          patch.object(wr, "_checkpoint_matches_claim", return_value=True),
          patch("theory_master.investigator_budget.budget_status",
                return_value={"review_index": 1, "rejected": False}),
          patch("theory_master.codex_accounts.account_rate_limits") as provider,
          patch.object(wr, "worker_action") as resume):
        assert wr._dispatch_exhausted_budget_reviewer_transport(spec) is False
        assert wr._dispatch_exhausted_budget_reviewer_transport(spec) is False
        provider.assert_not_called()
        resume.assert_not_called()
    messages = [json.loads(t) for t in (cycle/"important_events.jsonl").read_text().splitlines()]
    assert len(messages) == 1
    assert messages[0]["code"] == "budget_reviewer_protocol_repair_required"


def test_orphan_with_authoritative_closed_preservation_releases_without_model(tmp_path):
    root = tmp_path/"research"
    cycle = root/"184"
    old = (datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
    _write(cycle/"research_state.json", {
        "worker_id": "worker-2", "status": "over",
        "closure_reason": "investigator_preservation", "scientific_verdict": None})
    _write(cycle/"preservation/closure.json", {
        "cycle_id": "184", "hypothesis_id": "H-0184", "scientific_verdict": None})
    worker = {"worker_id": "worker-2", "status": "PRESERVING",
              "process_alive": False, "current_cycle": "184",
              "updated_at": old, "preservation_job_pid": 123456,
              "preservation_job_start_ticks": 4,
              "claim_id": "original", "epoch": 5}
    spec = SimpleNamespace(research_root=root)
    with (patch.object(wr, "list_workers", return_value=[worker]),
          patch.object(wr, "process_identity_alive", return_value=False),
          patch.object(wr, "_graph_path", return_value=root/"graph.db"),
          patch.object(wr, "_worker_live_claim", return_value={"cycle_id": "184"}),
          patch.object(wr, "_checkpoint_matches_claim", return_value=True),
          patch("theory_master.research_preservation.finalize_preserved_cycle",
                return_value={"scientific_verdict": None}),
          patch.object(wr, "worker_action", return_value={
              "status": "RELEASED", "model_calls": 0}) as release):
        result = wr._flag_orphaned_preservation_jobs(spec)
        assert result == [{"worker_id": "worker-2", "cycle": "184",
                           "status": "verified_released"}]
        assert wr._flag_orphaned_preservation_jobs(spec) == []  # once only
        release.assert_called_once_with(spec, "worker-2", "preserve_release")
    receipt = root/".theory-master/workers/worker-2/preservation_orphaned_release_184.json"
    assert json.loads(receipt.read_text())["status"] == "verified_released"
    assert not (root/".theory-master/workers/worker-2/preservation_orphaned_184.json").exists()

def test_dead_budget_reviewer_watcher_restarted_only_before_retry_guard(tmp_path):
    root, cycle, worker = _budget_fixture(tmp_path)
    # One technical session: ordinary quota watcher must remain recoverable.
    (cycle/"events.jsonl").write_text(json.dumps({
        "type": "investigation.budget_review_failed",
        "payload": {"review_index": 1, "error_type": "RuntimeError",
                    "detail": "stream disconnected"}})+"\n")
    claim = {"worker_id": "worker-2", "claim_id": "c2",
             "fencing_epoch": 2, "cycle_id": "185"}
    spec = SimpleNamespace(research_root=root)
    with (patch("theory_master.quota_auto_adjust.quota_auto_adjust_enabled", return_value=True),
          patch.object(wr, "list_workers", return_value=[worker]),
          patch.object(wr, "_live_cycle_claims", return_value=[claim]),
          patch.object(wr, "process_identity_alive", return_value=False),
          patch.object(wr, "_checkpoint_matches_claim", return_value=True),
          patch.object(wr, "start_worker_quota_watch",
                       return_value={"status": "starting"}) as watcher):
        result = wr._repair_missing_quota_watchers(spec)
        assert result[0]["status"] == "starting"
        assert watcher.call_count == 1
        assert wr._repair_missing_quota_watchers(spec) == []
        _write(root/".theory-master/workers/worker-2/budget_review_quota_resume.json", {
            "key": "185:1:checkpoints/frozen.json", "status": "attempted"})
        assert wr._repair_missing_quota_watchers(spec) == []
        watcher.assert_called_once()


def test_readonly_supervisor_real_gate_never_calls_model_or_patch(tmp_path):
    from theory_master.runtime_supervisor_review import review_runtime_failure
    root, cycle, state = _supervisor_fixture(tmp_path)
    state_path = cycle/"research_state.json"
    backend = MagicMock()
    with (patch("theory_master.runtime_supervisor_review.SupervisorSession") as session,
          patch("theory_master.runtime_supervisor_review.supervisor_model", return_value="mock"),
          patch("theory_master.supervisor_incident.incident_context", return_value={}),
          patch("theory_master.supervisor_runtime_repair.repair_target", return_value=None),
          patch("theory_master.runtime_supervisor_review._recovered_capacity_thread_is_idle",
                return_value=True),
          patch("theory_master.runtime_supervisor_review._publish_runtime_incident") as publish,
          patch("theory_master.supervisor_runtime_repair.attempt_runtime_repair") as edit):
        session.return_value.session_id = "SUP-safe"
        result = review_runtime_failure(
            backend=backend, research_root=root, cycle_dir=cycle, state=state,
            state_path=state_path, incident_id="INC-fixture",
            readonly_provider_probe_only=True)
    assert result["action"] == "resume"
    assert json.loads(state_path.read_text())["supervisor_runtime_resume_count"] == 1
    backend.invoke.assert_not_called()
    edit.assert_not_called()
    assert publish.call_args.kwargs["model_invoked"] is False if "model_invoked" in publish.call_args.kwargs else publish.call_args.args[6] is False


def test_recovery_state_banners_reveal_engineering_and_budget_technical_wait(tmp_path):
    from tm_observer.supervisor_view import banner_html
    root = tmp_path / "research"
    cycle = root/"127"
    state = {"status": "failed", "worker_id": "worker-1",
             "phase": "reporter", "resumable": True}
    _write(cycle/"research_state.json", state)
    receipt = root/".theory-master/workers/worker-1/supervisor_dispatch.json"
    _write(receipt, {"cycle": "127", "status": "deferred_peers_active"})
    run = {"research_root": str(root), "cycle": "127", "observer_id": "fixture"}
    result = banner_html(run)
    assert "Supervisor 工程恢复等待安全窗口" in result
    assert 'data-recovery-gate="true"' in result
    state.update(status="needs_attention", phase="investigator:budget_review",
                 attention={"code": "investigator_budget_review_failed"},
                 investigator_budget={"review_index": 1})
    _write(cycle/"research_state.json", state)
    result = banner_html(run)
    assert "Budget Reviewer 技术故障" in result
    assert "不会自动批准科研预算" in result
    state.update(status="running", phase="investigator:running")
    _write(cycle/"research_state.json", state)
    assert "data-recovery-gate" not in banner_html(run)

def test_budget_reviewer_post_transport_failure_is_recorded_without_new_spend(tmp_path):
    root, cycle, worker = _budget_fixture(tmp_path)
    _write(root/".theory-master/workers/worker-2/budget_reviewer_transport_recovery_185_1.json", {
        "cycle": "185", "worker_id": "worker-2", "status": "resume_dispatched",
        "failed_sessions": 2, "review_index": 1})
    spec = SimpleNamespace(research_root=root)
    with (patch.object(wr, "list_workers", return_value=[worker]),
          patch.object(wr, "process_identity_alive", return_value=False),
          patch.object(wr, "_graph_path", return_value=root/"graph.db"),
          patch.object(wr, "_worker_live_claim", return_value={"cycle_id": "185"}),
          patch.object(wr, "_checkpoint_matches_claim", return_value=True),
          patch("theory_master.investigator_budget.budget_status",
                return_value={"review_index": 1, "rejected": False}),
          patch.object(wr, "worker_action") as resume):
        assert wr._dispatch_exhausted_budget_reviewer_transport(spec) is False
        resume.assert_not_called()
    events = [json.loads(x) for x in (cycle/"important_events.jsonl").read_text().splitlines()]
    assert events[0]["code"] == "budget_reviewer_post_transport_recovery_failed"


def test_budget_ledger_corruption_is_not_misclassified_as_quota(tmp_path):
    root, cycle, worker = _budget_fixture(tmp_path)
    spec = SimpleNamespace(research_root=root)
    with (patch.object(wr, "list_workers", return_value=[worker]),
          patch.object(wr, "process_identity_alive", return_value=False),
          patch.object(wr, "_graph_path", return_value=root/"graph.db"),
          patch.object(wr, "_worker_live_claim", return_value={"cycle_id": "185"}),
          patch.object(wr, "_checkpoint_matches_claim", return_value=True),
          patch("theory_master.investigator_budget.budget_status",
                side_effect=ValueError("bad ledger")),
          patch.object(wr, "worker_action") as resume):
        assert wr._dispatch_exhausted_budget_reviewer_transport(spec) is False
        resume.assert_not_called()
    events = [json.loads(x) for x in (cycle/"important_events.jsonl").read_text().splitlines()]
    assert events[0]["code"] == "budget_reviewer_ledger_integrity_required"


def test_readonly_supervisor_rejects_engine_patch_even_if_provider_idle(tmp_path):
    from theory_master.runtime_supervisor_review import review_runtime_failure
    root, cycle, state = _supervisor_fixture(tmp_path)
    state_path = cycle/"research_state.json"
    backend = MagicMock()
    with (patch("theory_master.runtime_supervisor_review.SupervisorSession") as session,
          patch("theory_master.runtime_supervisor_review.supervisor_model", return_value="mock"),
          patch("theory_master.supervisor_incident.incident_context", return_value={}),
          patch("theory_master.supervisor_runtime_repair.repair_target",
                return_value=cycle/"engineering.py"),
          patch("theory_master.runtime_supervisor_review._recovered_capacity_thread_is_idle",
                return_value=True) as probe,
          patch("theory_master.runtime_supervisor_review._publish_runtime_incident"),
          patch("theory_master.supervisor_runtime_repair.attempt_runtime_repair") as edit):
        session.return_value.session_id = "SUP-safe"
        result = review_runtime_failure(
            backend=backend, research_root=root, cycle_dir=cycle, state=state,
            state_path=state_path, incident_id="INC-fixture",
            readonly_provider_probe_only=True)
    assert result["action"] == "hold"
    probe.assert_not_called()
    edit.assert_not_called()
    backend.invoke.assert_not_called()
    assert not state_path.exists() or "supervisor_runtime_resume_count" not in json.loads(state_path.read_text())
