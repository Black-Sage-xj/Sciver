"""Exercise archived delivery reassessment with the local FakeBackend only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_delivery_repair import _completed, _qualification_stop, _report
from theory_master.delivery_repair import current_delivery_payload, science_authority_digest
from theory_master.goal_snapshot import GoalAuthoritySnapshot
from theory_master.runtime import write_json
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.specification import list_cycle_dirs
from theory_master.supervisor_activation import latest_recovery, takeover_runner_operation
from theory_master.supervisor_goal_reassessment import (
    delivery_reassessment_plan,
    goal_reassessment_plan,
    run_goal_reassessment,
)
from theory_master.supervisor_intervention import write_supervisor_setting
from theory_master.worker_direction import research_direction_fingerprint


@pytest.fixture
def archive(tmp_path):
    spec, workflow, backend, anchor = _completed(tmp_path)
    stopped = _qualification_stop(spec, "2")
    write_supervisor_setting(spec.research_root, True)
    stop_state = stopped / "research_state.json"
    state = json.loads(stop_state.read_bytes())
    state.update(master_id=spec.master_id, cycle_id=stopped.name, execution_active=False,
                 supervisor_qualification={"status": "waiting", "direction_fingerprint": research_direction_fingerprint(spec)})
    write_json(stop_state, state)

    graph = spec.graph_database
    with ScienceGraphStore(graph, readonly=True) as store:
        assessment = store.latest_goal_gate_assessment()
        assert assessment["verdict"] == "not_passed"
    old_receipt = spec.research_root / assessment["receipt_path"]
    old_revision = spec.research_root / "delivery_revisions" / f"{assessment['delivery_revision']}.json"
    frozen = json.loads(old_revision.read_bytes())
    report = _report(spec, anchor)
    source = next(row for row in frozen["goal_material_sources"]
                  if row["status"] == "available" and row["path"] == str(report))
    synthesis = spec.task_home / "notes" / "archived goal synthesis.md"
    synthesis.parent.mkdir(parents=True, exist_ok=True)
    synthesis.write_text("# Archived check\n\nThe original comparison ran, and its inference remains bounded.\n")
    data = synthesis.read_bytes()
    selector = spec.research_root / ".theory-master" / "archival-goal-delivery.json"
    record = {
        "schema": "theory-master/archival-goal-delivery/v1",
        "master_id": spec.master_id,
        "stopped_cycle": stopped.name,
        "anchor_cycle": anchor.name,
        "goal_assessment": assessment["assessment_id"],
        "prior_delivery_revision": assessment["delivery_revision"],
        "material": {"path": synthesis.relative_to(spec.task_home).as_posix(), "size": len(data),
                     "sha256": hashlib.sha256(data).hexdigest()},
        "outcome_ids": [assessment["outcome_id"]],
        "sources": [{"reference": source["reference"], "sha256": source["sha256"]}],
    }
    return SimpleNamespace(spec=spec, workflow=workflow, backend=backend, anchor=anchor, stopped=stopped,
                           graph=graph, old_receipt=old_receipt, old_revision=old_revision,
                           assessment=assessment, report=report, synthesis=synthesis, selector=selector, record=record)


def test_verified_archive_wakes_only_goal_judge_and_preserves_science(archive):
    spec = archive.spec
    assert spec.submission_contract_ref is None
    before = science_authority_digest(spec, archive.graph)
    assert archive.assessment["science_digest"] != before  # A later stopped Cycle has a new unlocked projection.
    old_revision = archive.old_revision.read_bytes()
    old_receipt = archive.old_receipt.read_bytes()
    old = json.loads(old_receipt)
    old_snapshot = GoalAuthoritySnapshot.restore(Path(old["snapshot_manifest"]), old["snapshot_manifest_sha256"])
    assert "archival_goal_delivery" not in old_snapshot.access
    stopped_state = archive.stopped / "research_state.json"
    stopped_bytes = stopped_state.read_bytes()
    assert "archival_goal_delivery" not in current_delivery_payload(spec, archive.graph, archive.anchor)
    assert delivery_reassessment_plan(spec) is None
    assert latest_recovery(spec.research_root, include_wait=True)[2] == "qualification_wait"

    write_json(archive.selector, archive.record)
    current = current_delivery_payload(spec, archive.graph, archive.anchor)
    archived = current["archival_goal_delivery"]
    assert archived["selection_id"] == hashlib.sha256(archive.selector.read_bytes()).hexdigest()
    assert archived["outcome_ids"] == archive.record["outcome_ids"]
    assert archived["material"]["path"] == str(archive.synthesis)
    assert current["science_digest"] == before
    assert "submission_contract" not in current
    plan = delivery_reassessment_plan(spec)
    assert plan == goal_reassessment_plan(archive.workflow)
    assert (plan["anchor_cycle"], plan["stopped_cycle"]) == (archive.anchor.name, archive.stopped.name)
    assert plan["reassessment_of"] == archive.assessment["assessment_id"]
    assert plan["committed_outcomes"] == []
    assert str(archive.synthesis) in plan["changed_material_refs"]
    ready = latest_recovery(spec.research_root)
    assert ready[0] == archive.stopped and ready[2] == "goal_reassessment"
    assert takeover_runner_operation(ready[2]) == "reassess"

    result = run_goal_reassessment(archive.workflow, plan, watch_stopped=True)
    assert result["assessment"]["verdict"] == "not_passed"
    assert archive.backend.goal_calls == 2
    access = archive.backend.goal_context["delivery_material_access"]
    frozen_path = Path(access["revision_path"])
    frozen = json.loads(frozen_path.read_bytes())["archival_goal_delivery"]
    frozen_material = Path(frozen["material"]["path"])
    assert access["archival_goal_delivery"]["selection_id"] == archived["selection_id"]
    assert Path(access["archival_goal_delivery"]["material"]["path"]) == frozen_material
    assert frozen_material.is_relative_to(frozen_path.parent)
    assert frozen_material.read_bytes() == archive.synthesis.read_bytes()
    assert archive.old_revision.read_bytes() == old_revision and archive.old_receipt.read_bytes() == old_receipt
    assert stopped_state.read_bytes() == stopped_bytes
    assert science_authority_digest(spec, archive.graph) == before
    assert list_cycle_dirs(spec.research_root, task_mode=True) == [archive.stopped, archive.anchor]
    with ScienceGraphStore(archive.graph, readonly=True) as store:
        history = store.goal_gate_history(archive.anchor.name)
        assert [row["verdict"] for row in history] == ["not_passed", "not_passed"]
        assert history[-1]["supersedes"] == archive.assessment["assessment_id"]
    assert delivery_reassessment_plan(spec) is None
    assert run_goal_reassessment(archive.workflow, plan, watch_stopped=True) is None
    assert archive.backend.goal_calls == 2


@pytest.mark.parametrize("failure", ["different-master", "source-drift", "markdown-drift", "wrong-outcome",
                                     "stopped-control", "stopped-action", "anchor-control",
                                     "later-stopped-cycle", "sealed-goal"])
def test_archival_selection_loses_authority_after_drift_or_operator_stop(archive, failure):
    write_json(archive.selector, archive.record)
    plan = delivery_reassessment_plan(archive.spec)
    assert plan is not None
    expected_judge_calls = 1
    if failure == "different-master":
        write_json(archive.selector, {**archive.record, "master_id": archive.spec.master_id + 1})
    elif failure == "source-drift":
        archive.report.write_text(archive.report.read_text() + "\nPrior source drifted.\n")
    elif failure == "markdown-drift":
        archive.synthesis.write_text("# Changed synthesis\n")
    elif failure == "wrong-outcome":
        write_json(archive.selector, {**archive.record, "outcome_ids": ["cycle-outcome-2"]})
    elif failure in {"stopped-control", "anchor-control"}:
        cycle = archive.stopped if failure == "stopped-control" else archive.anchor
        write_json(cycle / "recovery_control.json", {"action": "pause"})
    elif failure == "stopped-action":
        path = archive.stopped / "research_state.json"
        state = json.loads(path.read_bytes())
        write_json(path, {**state, "recovery": {"action": "pause"}})
    elif failure == "later-stopped-cycle":
        _qualification_stop(archive.spec, "3")
    else:
        archive.backend.pass_goal = True
        sealed = run_goal_reassessment(archive.workflow, plan, watch_stopped=True)
        assert sealed["assessment"]["verdict"] == "passed"
        expected_judge_calls = 2
    assert delivery_reassessment_plan(archive.spec) is None
    assert run_goal_reassessment(archive.workflow, plan, watch_stopped=True) is None
    assert archive.backend.goal_calls == expected_judge_calls


def test_selected_archive_changed_during_judge_cannot_commit_stale_result(archive, monkeypatch):
    write_json(archive.selector, archive.record)
    plan = delivery_reassessment_plan(archive.spec)
    assert plan is not None
    original_invoke = archive.backend.invoke
    archive.backend.pass_goal = True

    def replaced_after_snapshot(role, prompt, thread_id, model, provider):
        if role == "judge" and "Current task: goal_completion " in prompt:
            archive.synthesis.write_text("# Archive changed during assessment\n")
        return original_invoke(role, prompt, thread_id, model, provider)

    monkeypatch.setattr(archive.backend, "invoke", replaced_after_snapshot)
    result = run_goal_reassessment(archive.workflow, plan, watch_stopped=True)
    assert result["assessment"]["assessment_status"] == "stale"
    assert archive.backend.goal_calls == 2
    with ScienceGraphStore(archive.graph, readonly=True) as store:
        assert store.goal_terminal() is None
        assert [row["assessment_id"] for row in store.goal_gate_history(archive.anchor.name)] == [
            archive.assessment["assessment_id"],
        ]


def test_archive_drift_during_snapshot_copy_blocks_judge(archive, monkeypatch):
    from theory_master.goal_snapshot import materialize_goal_snapshot

    write_json(archive.selector, archive.record)
    plan = delivery_reassessment_plan(archive.spec)
    assert plan is not None

    def replace_before_copy(*args):
        archive.synthesis.write_text("# Unverified archive bytes\n")
        return materialize_goal_snapshot(*args)

    monkeypatch.setattr("theory_master.goal_gate.materialize_goal_snapshot", replace_before_copy)
    with pytest.raises(ValueError, match="source changed before snapshot capture"):
        run_goal_reassessment(archive.workflow, plan, watch_stopped=True)
    assert archive.backend.goal_calls == 1


def test_attach_requires_exact_idle_archive_and_is_idempotent(archive):
    from theory_master.supervisor_goal_reassessment import attach_archival_goal_delivery

    root = archive.spec.research_root
    before = science_authority_digest(archive.spec, archive.graph)
    write_supervisor_setting(root, False)
    with pytest.raises(ValueError):
        attach_archival_goal_delivery(archive.spec, archive.record)
    assert not archive.selector.exists()
    write_supervisor_setting(root, True)

    unverified = {**archive.record, "material": {**archive.record["material"], "sha256": "0" * 64}}
    with pytest.raises(ValueError):
        attach_archival_goal_delivery(archive.spec, unverified)
    assert not archive.selector.exists()

    control = archive.stopped / "recovery_control.json"
    write_json(control, {"action": "pause"})
    with pytest.raises(ValueError):
        attach_archival_goal_delivery(archive.spec, archive.record)
    assert not archive.selector.exists()
    control.unlink()

    plan = attach_archival_goal_delivery(archive.spec, archive.record)
    assert plan == delivery_reassessment_plan(archive.spec)
    receipt = archive.selector.read_bytes()
    assert json.loads(receipt) == archive.record
    assert attach_archival_goal_delivery(archive.spec, archive.record) == plan

    replacement = archive.synthesis.with_name("different synthesis.md")
    replacement.write_bytes(archive.synthesis.read_bytes() + b"\nA different delivery.\n")
    different = {"path": replacement.relative_to(archive.spec.task_home).as_posix(),
                 "size": replacement.stat().st_size, "sha256": hashlib.sha256(replacement.read_bytes()).hexdigest()}
    with pytest.raises(ValueError):
        attach_archival_goal_delivery(archive.spec, {**archive.record, "material": different})
    assert archive.selector.read_bytes() == receipt
    assert science_authority_digest(archive.spec, archive.graph) == before
    assert archive.backend.goal_calls == 1
