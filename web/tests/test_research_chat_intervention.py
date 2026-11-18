"""Deterministic human-direction persistence; no model or runtime is launched."""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path

import pytest

from theory_master.goal_terminal import GoalSealedError
from theory_master.operator_direction import requested_conjecture, claim_conjecture, list_conjectures
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.task import bootstrap
from tm_observer import research_chat_intervention as intervention
from tm_observer.research_plan_editor import ResearchPlanError, read_plan, save_section


PLAN = (
    "# Goal\n\nOriginal scientific goal\n\n"
    "# Research Contract\n\nKeep independent validation\n\n"
    "# Final Gate\n\nJudge decides completion\n\n"
    "# Required Context\n\nExisting context anchor\n\n"
    "# Scientific Model\n\nExisting model\n"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    search_root = tmp_path / "workspace"
    task_root = search_root / "project"
    task_root.mkdir(parents=True)
    plan_path = task_root / "research_plan.md"
    plan_path.write_text(PLAN, encoding="utf-8")
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    binding = bootstrap(plan_path, allocation_root=search_root)
    runtime = binding.research_root
    database = Path(binding.config.get("graph_database") or
                    Path(binding.config["branch_canvas"]).with_name("science_graph.sqlite3"))
    task = {"observer_id": hashlib.sha256(str(runtime).encode()).hexdigest()[:16],
            "task_name": "synthetic project", "task_root": str(task_root),
            "research_root": str(runtime), "_fixture_plan": plan_path.read_text(encoding="utf-8")}
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Existing qualified hypothesis")
    return search_root, task, plan_path, database


def _request(project, **overrides):
    search_root, task, _, _ = project
    section = next(row for row in read_plan(search_root, task)["sections"]
                   if row["name"] == "Required Context")
    payload = {"hypothesis": "The specified mechanism predicts a positive slope.",
               "decisive_test": "Hold out independent samples and compare the slope sign.",
               "reason": "用户希望研究这个机制", "expected_revision": section["revision"],
               "source_session": "synthetic-chat-session"}
    return {**payload, **overrides}


def _external_count(database):
    with ScienceGraphStore(database, readonly=True) as store:
        return store.db.execute("SELECT COUNT(*) FROM external_records").fetchone()[0]


def test_records_exact_human_node_preserving_plan_and_frozen_cycle(project):
    search_root, task, plan, database = project
    before = plan.read_bytes()
    state = Path(task["research_root"]) / "research_state.json"
    state.write_text('{"hypothesis_lock":{"status":"locked","hypothesis":"current H"}}')
    frozen = state.read_bytes()
    hypothesis = "原始用户猜想\n# Goal\nKeep <this> exact."
    request = _request(project, hypothesis=hypothesis)
    result = intervention.record_conjecture(search_root, task, **request)
    assert result["status"] == "recorded"
    assert result["plan_saved"] is False and result["plan_unchanged"] is True
    assert result["node_recorded"] is True and result["execution_started"] is False
    assert result["qualification_required"] and result["formal_hypothesis_id"] is None
    assert result["delivery_status"] == "pending" and result["queue_position"] == 1
    assert result["effective_at"] == "next_fresh_frontier_proposal"
    assert plan.read_bytes() == before and state.read_bytes() == frozen
    with ScienceGraphStore(database, readonly=True) as store:
        record = store.db.execute("SELECT * FROM external_records").fetchone()
        assert record["source"] == "human" and record["content_type"] == "ResearchFocus"
        content = requested_conjecture(record["content"])
        assert content["hypothesis"] == hypothesis
        assert store.db.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 1
        assert store.db.execute("SELECT verdict FROM outcomes").fetchone()[0] == "open"
    assert result["external_id"] in (Path(task["task_root"]) / "exploration.canvas").read_text()


def test_stale_context_and_context_race_create_no_graph_node(project, monkeypatch):
    search_root, task, plan, database = project
    request = _request(project)
    save_section(search_root, task, {"section": "Required Context", "text": "\nChanged context\n",
                                   "expected_revision": request["expected_revision"], "change_reason": "edit"})
    with pytest.raises(ResearchPlanError, match="已变化"):
        intervention.record_conjecture(search_root, task, **request)
    fresh = _request(project)
    original = intervention._canonical_graph
    def race(*args):
        result = original(*args)
        plan.write_text(plan.read_text().replace("Changed context", "Racing context"))
        return result
    monkeypatch.setattr(intervention, "_canonical_graph", race)
    with pytest.raises(ResearchPlanError, match="已变化"):
        intervention.record_conjecture(search_root, task, **fresh)
    assert _external_count(database) == 0


def test_graph_transaction_failure_does_not_change_plan_or_leave_pending(project, monkeypatch):
    search_root, task, plan, database = project
    before = plan.read_bytes()
    original = ScienceGraphStore._event
    def fail(store, action, subject, detail):
        if action == "external.human_conjecture.pending":
            raise RuntimeError("synthetic graph failure")
        return original(store, action, subject, detail)
    monkeypatch.setattr(ScienceGraphStore, "_event", fail)
    with pytest.raises(RuntimeError, match="synthetic graph failure"):
        intervention.record_conjecture(search_root, task, **_request(project))
    assert plan.read_bytes() == before and _external_count(database) == 0


def test_projection_failure_receipt_preserves_durable_pending(project, monkeypatch):
    search_root, task, plan, database = project
    monkeypatch.setattr(intervention, "_publish_projection", lambda *args: (_ for _ in ()).throw(OSError("render")))
    result = intervention.record_conjecture(search_root, task, **_request(project))
    assert result["status"] == "partial" and result["node_recorded"] is True
    assert result["plan_unchanged"] and not result["projection_refreshed"]
    assert result["delivery_status"] == "pending" and _external_count(database) == 1


def test_default_can_register_during_live_worker_but_immediate_requires_actual_stop(project, monkeypatch):
    search_root, task, plan, database = project
    monkeypatch.setattr("theory_master.worker_runtime.list_workers", lambda root: [
        {"worker_id": "worker-1", "status": "PAUSE_REQUESTED", "process_alive": True},
    ])
    result = intervention.record_conjecture(search_root, task, **_request(project))
    assert result["runtime"]["idle_verified"] is False and not result["execution_started"]
    with pytest.raises(intervention.ResearchInterventionError, match="已请求暂停不代表已停止"):
        intervention.record_conjecture(search_root, task, **_request(project), timing="immediate")
    assert _external_count(database) == 1


def test_immediate_honors_allocation_lock_and_never_starts_runtime(project):
    search_root, task, _, _ = project
    metadata = Path(task["research_root"]) / ".theory-master"
    metadata.mkdir(exist_ok=True)
    with (metadata / "scheduler.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with pytest.raises(intervention.ResearchInterventionError, match="分配 Worker"):
            intervention.record_conjecture(search_root, task, **_request(project), timing="immediate")
    result = intervention.record_conjecture(search_root, task, **_request(project), timing="immediate")
    assert result["runtime"]["idle_verified"] and not result["execution_started"]


def test_fifo_replace_revoke_and_claimed_direction_cannot_be_changed(project):
    search_root, task, plan, database = project
    first = intervention.record_conjecture(search_root, task, **_request(project))
    second = intervention.record_conjecture(search_root, task, **_request(project, hypothesis="Second"))
    assert second["queue_position"] == 2
    assert second["effective_at"] == "fresh_frontier_proposal_in_fifo_order"
    replacement = intervention.record_conjecture(search_root, task, **_request(project, hypothesis="Replacement"),
                                                replace_external_id=first["external_id"])
    assert replacement["queue_position"] == 2
    revoked = intervention.revoke_conjecture(search_root, task, external_id=second["external_id"], reason="withdraw")
    assert revoked["delivery_status"] == "revoked" and revoked["plan_unchanged"]
    claimed = claim_conjecture(database, cycle_id="2")
    assert claimed["record"]["external_id"] == replacement["external_id"]
    with pytest.raises(intervention.ResearchInterventionError, match="已领取"):
        intervention.revoke_conjecture(search_root, task, external_id=replacement["external_id"], reason="too late")
    with pytest.raises(intervention.ResearchInterventionError, match="已领取"):
        intervention.record_conjecture(search_root, task, **_request(project), replace_external_id=replacement["external_id"])
    assert _external_count(database) == 3
    assert list_conjectures(database) == []
    assert plan.read_text() == task["_fixture_plan"]


def test_goal_seal_rejects_registration_and_revoke(project):
    search_root, task, plan, database = project
    recorded = intervention.record_conjecture(search_root, task, **_request(project))
    with ScienceGraphStore(database) as store:
        with store.transaction():
            store.db.execute("INSERT INTO task_goal_terminal VALUES (?,?,?,?,?,?,?)",
                             ("synthetic-goal", "passed", "1", "2026-11-18T00:00:00Z",
                              "0" * 64, "synthetic-receipt", "{}"))
    with pytest.raises(GoalSealedError):
        intervention.record_conjecture(search_root, task, **_request(project))
    with pytest.raises(GoalSealedError):
        intervention.revoke_conjecture(search_root, task, external_id=recorded["external_id"], reason="sealed")
    assert plan.read_text() == task["_fixture_plan"] and _external_count(database) == 1


def test_missing_canonical_graph_is_not_created(project):
    search_root, task, plan, database = project
    request = _request(project)
    database.unlink()
    with pytest.raises(intervention.ResearchInterventionError, match="SQLite"):
        intervention.record_conjecture(search_root, task, **request)
    assert not database.exists() and plan.read_text() == task["_fixture_plan"]


def test_registration_verifies_sources_and_keeps_authoritative_snapshot(project):
    from tm_observer.research_chat_sources import prepare_source

    search_root, task, plan, database = project
    source = prepare_source(search_root, {"runs": [task]}, task["observer_id"],
                            {"kind": "node", "id": "H-0001", "quote": "Existing qualified hypothesis"})
    result = intervention.record_conjecture(search_root, task, **_request(project), source_references=[source])
    assert result["source_references"][0]["hypothesis_id"] == "H-0001"
    claimed = claim_conjecture(database, cycle_id="2")
    assert claimed["source_references"][0]["quote"] == source["quote"]
    assert claimed["source_references"][0]["source_version"] == source["source_version"]
    with ScienceGraphStore(database) as store:
        with store.transaction():
            store.db.execute("UPDATE hypotheses SET title='Changed source' WHERE id='H-0001'")
    with pytest.raises(intervention.ResearchInterventionError) as stale:
        intervention.record_conjecture(search_root, task, **_request(project), source_references=[source])
    assert stale.value.status == 409 and _external_count(database) == 1
    current = prepare_source(search_root, {"runs": [task]}, task["observer_id"], {"kind": "node", "id": "H-0001"})
    current["quote"] = "Invented evidence"
    with pytest.raises(intervention.ResearchInterventionError) as forged:
        intervention.record_conjecture(search_root, task, **_request(project), source_references=[current])
    assert forged.value.status == 400 and _external_count(database) == 1


@pytest.mark.parametrize("field,value", [("hypothesis", ""), ("decisive_test", "x" * 4001),
                                       ("reason", " "), ("source_session", "\x00")])
def test_invalid_conjecture_does_not_write(project, field, value):
    search_root, task, plan, database = project
    with pytest.raises(intervention.ResearchInterventionError):
        intervention.record_conjecture(search_root, task, **_request(project, **{field: value}))
    assert plan.read_text() == task["_fixture_plan"] and _external_count(database) == 0
