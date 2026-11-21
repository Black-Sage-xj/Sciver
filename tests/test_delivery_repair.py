from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from theory_master.core import FakeBackend, TaskSpec, Workflow
from theory_master.delivery_repair import repair_delivery, science_authority_digest
from theory_master.goal_terminal import GoalSealedError
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.specification import list_cycle_dirs
from theory_master.task import bootstrap


class CountingGoalBackend(FakeBackend):
    def __init__(self):
        super().__init__(reworks=0)
        self.goal_calls = 0
        self.pass_goal = False
        self.goal_context = {}

    def invoke(self, role, prompt, thread_id, model, provider):
        if role == "judge" and "Current task: goal_completion " in prompt:
            self.goal_calls += 1
            self.goal_context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
            if self.pass_goal:
                return str(thread_id), json.dumps({"verdict": "passed", "rationale": "The repaired deliverable satisfies the fixture Final Gate."})
        return super().invoke(role, prompt, thread_id, model, provider)


def _completed(tmp_path: Path, *, max_cycles: int = 1, backend=None):
    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal\n\nBounded fixture.\n\n# Research Contract\n\nLocal inputs.\n\n# Final Gate\n\nResult and reproducible report.\n\n# Required Context\n\n- local fixture\n")
    binding = bootstrap(plan)
    config = json.loads(binding.config_path.read_text())
    config["max_cycles"] = max_cycles
    binding.config_path.write_text(json.dumps(config))
    spec = TaskSpec.from_config(binding.config_path)
    backend = backend or CountingGoalBackend()
    workflow = Workflow(spec, backend)
    state = workflow.run()
    assert state["goal_completion"]["verdict"] == "not_passed"
    cycle = list_cycle_dirs(spec.research_root, task_mode=True)[0]
    return spec, workflow, backend, cycle


def _report(spec, cycle):
    pointer = json.loads((cycle / "delivery_revision.json").read_text())
    frozen = json.loads(Path(pointer["revision_path"]).read_text())
    return Path(frozen["report_versions"][0]["path"])


def test_unchanged_revision_reuses_assessment_without_new_science(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    before = science_authority_digest(spec, spec.graph_database)
    initial = json.loads((cycle / "delivery_revision.json").read_text())
    first = repair_delivery(workflow)
    second = repair_delivery(workflow, cycle=1, reassess=True)
    assert first["delivery_revision"] == second["delivery_revision"] == initial["delivery_revision"]
    assert backend.goal_calls == 1
    assert list_cycle_dirs(spec.research_root, task_mode=True) == [cycle]
    assert science_authority_digest(spec, spec.graph_database) == before
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert len(store.goal_gate_history(cycle.name)) == 1
    access = backend.goal_context["delivery_material_access"]
    assert access["delivery_revision"] == initial["delivery_revision"]
    assert Path(access["revision_path"]).is_relative_to(cycle / "goal_snapshots")
    assert json.loads(Path(access["revision_path"]).read_text())["science_digest"] == before


def test_report_repair_creates_new_revision_and_preserves_old_assessment(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    old = json.loads((cycle / "goal_completion_out.json").read_text())
    report = _report(spec, cycle)
    report.write_text(report.read_text() + "\nReproduction navigation repaired.\n")
    rebuilt = repair_delivery(workflow)
    assert rebuilt["delivery_revision"] != old["delivery_revision"]
    assert backend.goal_calls == 1
    result = repair_delivery(workflow, reassess=True)
    assert result["assessment"]["verdict"] == "not_passed"
    assert backend.goal_calls == 2
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        history = store.goal_gate_history(cycle.name)
        assert len(history) == 2
        assert history[-1]["supersedes"] == history[0]["assessment_id"]
        assert Path(history[0]["receipt_path"]).is_file()
    assert json.loads(Path(old["revision_path"]).read_text())["report_versions"]
    assert len(list_cycle_dirs(spec.research_root, task_mode=True)) == 1


def test_repair_rejects_science_change_and_closed_guard(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    state_path = cycle / "research_state.json"
    original = json.loads(state_path.read_text())
    changed = json.loads(state_path.read_text())
    changed["hypothesis_lock"]["scope"] = "changed scientific scope"
    state_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="scientific authority"):
        repair_delivery(workflow, reassess=True)
    state_path.write_text(json.dumps({**original, "status": "running"}))
    with pytest.raises(ValueError, match="closed Cycle"):
        repair_delivery(workflow, cycle=1)
    assert backend.goal_calls == 1


def test_repaired_revision_can_pass_and_sealed_goal_refuses_repair(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    report = _report(spec, cycle)
    report.write_text(report.read_text() + "\nCompleted delivery index.\n")
    backend.pass_goal = True
    result = repair_delivery(workflow, reassess=True)
    assert result["assessment"]["verdict"] == "passed"
    assert backend.goal_calls == 2
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert [row["verdict"] for row in store.goal_gate_history(cycle.name)] == ["not_passed", "passed"]
    with pytest.raises(GoalSealedError, match="sealed Goal"):
        repair_delivery(workflow)


@pytest.mark.parametrize("verdict", ["rejected", "open"])
def test_delivery_repair_after_negative_or_inconclusive_science_keeps_source_verdict(tmp_path, verdict):
    class TerminalBackend(CountingGoalBackend):
        def invoke(self, role, prompt, thread_id, model, provider):
            if role == "judge" and "Current task: terminal_assessment " in prompt:
                return str(thread_id), json.dumps({"verdict": verdict, "rationale": "The frozen test is scientifically resolved"})
            return super().invoke(role, prompt, thread_id, model, provider)

    spec, workflow, backend, cycle = _completed(tmp_path, backend=TerminalBackend())
    before = science_authority_digest(spec, spec.graph_database)
    report = _report(spec, cycle)
    report.write_text(report.read_text() + "\nRequired delivery corrected.\n")
    repair_delivery(workflow)
    assert science_authority_digest(spec, spec.graph_database) == before
    original_lock = json.loads((cycle / "research_state.json").read_text())["hypothesis_lock"]
    backend.pass_goal = True
    result = repair_delivery(workflow, reassess=True)
    assert result["assessment"]["verdict"] == "passed"
    assert json.loads((cycle / "research_state.json").read_text())["hypothesis_lock"] == original_lock
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert store.db.execute("SELECT verdict FROM cycle_evaluations").fetchone()[0] == verdict
        assert store.goal_terminal()["detail"]["source_verdict"] == verdict
    assert len(list_cycle_dirs(spec.research_root, task_mode=True)) == 1


def test_cli_default_rebuild_has_no_real_backend(tmp_path):
    spec, _, _, _ = _completed(tmp_path)
    from theory_master.__main__ import main
    with patch("sys.argv", ["sciver", "repair-delivery", "fixture", "--cycle", "1"]), \
         patch("theory_master.__main__.spec_from_task", return_value=spec), \
         patch("theory_master.__main__.CodexBackend", side_effect=AssertionError("unexpected model backend")):
        assert main() == 0


def test_interrupted_assessment_commit_reuses_completed_goal_invocation(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    report = _report(spec, cycle)
    report.write_text(report.read_text() + "\nRepaired delivery navigation.\n")
    with patch.object(ScienceGraphStore, "record_goal_gate_not_passed", side_effect=OSError("interrupted commit")):
        with pytest.raises(OSError, match="interrupted commit"):
            repair_delivery(workflow, reassess=True)
    assert backend.goal_calls == 2
    result = repair_delivery(Workflow(spec, backend), reassess=True)
    assert result["assessment"]["verdict"] == "not_passed"
    assert backend.goal_calls == 2
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert len(store.goal_gate_history(cycle.name)) == 2


def test_hard_interruption_and_runtime_failed_wrapper_preserve_closed_goal_replay(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    report = _report(spec, cycle)
    report.write_text(report.read_text() + "\nRepaired delivery navigation.\n")

    class HardInterruption(BaseException):
        pass

    with patch.object(ScienceGraphStore, "record_goal_gate_not_passed", side_effect=HardInterruption()):
        with pytest.raises(HardInterruption):
            repair_delivery(workflow, reassess=True)
    state_path = cycle / "research_state.json"
    interrupted = json.loads(state_path.read_text())
    assert interrupted["goal_delivery_pending"]["closed_status"] == "over"
    # A global runtime failure wrapper may change execution status, without
    # invalidating the already committed scientific Cycle.
    interrupted.update(status="failed", phase="failed")
    state_path.write_text(json.dumps(interrupted))
    result = repair_delivery(Workflow(spec, backend), reassess=True)
    assert result["assessment"]["verdict"] == "not_passed"
    assert backend.goal_calls == 2
    restored = json.loads(state_path.read_text())
    assert restored["status"] == "over"
    assert "goal_delivery_pending" not in restored


def test_material_change_during_goal_call_cannot_seal_stale_revision(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    report = _report(spec, cycle)
    report.write_text(report.read_text() + "\nDelivery correction before assessment.\n")
    original_invoke = backend.invoke

    def mutate_during_goal(role, prompt, thread_id, model, provider):
        if role == "judge" and "Current task: goal_completion " in prompt:
            report.write_text(report.read_text() + "\nChanged during assessment.\n")
        return original_invoke(role, prompt, thread_id, model, provider)

    backend.pass_goal = True
    backend.invoke = mutate_during_goal
    stale = repair_delivery(workflow, reassess=True)
    assert stale["assessment"]["assessment_status"] == "stale"
    assert "changed during" in stale["assessment"]["reason"]
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert store.goal_terminal() is None
        assert [row["verdict"] for row in store.goal_gate_history(cycle.name)] == ["not_passed"]
    receipt = json.loads((cycle / "goal_completion_out.json").read_text())
    assert receipt["verdict"] == "not_passed"


def test_missing_mutable_pointer_cannot_bypass_science_preflight(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path)
    (cycle / "delivery_revision.json").unlink()
    path = cycle / "research_state.json"
    state = json.loads(path.read_text())
    state["hypothesis_lock"]["scope"] = "changed scientific scope"
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="scientific authority"):
        repair_delivery(workflow, reassess=True)
    assert backend.goal_calls == 1


def test_default_selects_latest_supported_closed_cycle(tmp_path):
    spec, workflow, backend, cycle = _completed(tmp_path, max_cycles=2)
    assert cycle.name == "2"
    result = repair_delivery(workflow)
    assert result["cycle_id"] == "2"
    assert backend.goal_calls == 2


def _qualification_stop(spec, cycle_name):
    from theory_master.specification import CYCLE_MARKER

    cycle = spec.research_root / cycle_name
    cycle.mkdir()
    (cycle / CYCLE_MARKER).write_text("fixture")
    (cycle / "research_state.json").write_text(json.dumps({
        "status": "no_qualified_candidate",
        "phase": "qualification",
        "qualification_status": "no_qualified_candidate",
        "protocol_health": "valid",
        "frontier_review": {"status": "unqualified"},
        "hypothesis_lock": {"status": "unlocked"},
        "qualification_attempts": [{"blocking_reason": "No further test"}],
        "attention": {"code": "no_qualified_candidate"},
    }))
    return cycle


def test_supervisor_reassesses_changed_delivery_once_without_new_science(tmp_path):
    from theory_master.supervisor_goal_reassessment import goal_reassessment_plan, run_goal_reassessment

    spec, workflow, backend, anchor = _completed(tmp_path)
    stopped = _qualification_stop(spec, "2")
    original_stop = (stopped / "research_state.json").read_bytes()

    # Another unresolved Cycle and source-version/projection drift alone are
    # insufficient. An available registered material actually has to change.
    assert goal_reassessment_plan(workflow) is None
    _report(spec, anchor).open("a").close()
    assert goal_reassessment_plan(workflow) is None
    report = _report(spec, anchor)
    report.write_text(report.read_text() + "\nReproduction conditions clarified.\n")

    plan = goal_reassessment_plan(workflow)
    assert plan["anchor_cycle"] == anchor.name
    assert plan["stopped_cycle"] == stopped.name
    assert plan["changed_material_refs"]
    assert plan["committed_outcomes"] == []

    result = run_goal_reassessment(workflow, plan)
    assert result["assessment"]["verdict"] == "not_passed"
    assert backend.goal_calls == 2
    assert goal_reassessment_plan(workflow) is None
    assert run_goal_reassessment(workflow, plan) is None
    assert backend.goal_calls == 2
    assert (stopped / "research_state.json").read_bytes() == original_stop
    assert list_cycle_dirs(spec.research_root, task_mode=True) == [stopped, anchor]


def test_valid_new_registered_submission_wakes_wait_without_a_new_direction(tmp_path, monkeypatch):
    from dataclasses import replace
    import hashlib

    from theory_master.delivery_repair import current_delivery_payload
    from theory_master.public_submission import SCHEMA
    from theory_master.supervisor_activation import latest_recovery, takeover_runner_operation
    from theory_master.supervisor_goal_reassessment import delivery_reassessment_plan
    from theory_master.worker_direction import research_direction_fingerprint

    spec, _, _, anchor = _completed(tmp_path)
    stopped = _qualification_stop(spec, "2")
    state_file = stopped / "research_state.json"
    state = json.loads(state_file.read_text())
    previous = research_direction_fingerprint(spec)
    state["supervisor_qualification"] = {"status": "waiting", "direction_fingerprint": previous}
    state_file.write_text(json.dumps(state))
    current = current_delivery_payload(spec, spec.graph_database, anchor)
    spec = replace(spec, submission_contract_ref="benchmark/submission.json")
    current["submission_contract"] = {
        "schema": SCHEMA, "sha256": "c" * 64,
        "json_schema": {"type": "object", "required": ["prediction"]},
    }
    monkeypatch.setattr("theory_master.supervisor_goal_reassessment.current_delivery_payload",
                        lambda *_: current)
    monkeypatch.setattr(TaskSpec, "from_task_root", lambda *_: spec)
    assert latest_recovery(spec.research_root, include_wait=True)[2] == "qualification_wait"

    output = anchor / "workspace" / "submission.json"
    output.write_text('{"prediction": [1, 2, 3]}')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    added = {"path": str(output), "reference": "workspace/submission.json",
             "status": "available", "sha256": digest, "role": "output"}
    current["goal_material_sources"].append(added)
    assert delivery_reassessment_plan(spec) is None
    current["material_index"]["materials"].append({
        "assets": [added], "evidence_states": {"E-approved": {"lifecycle_status": "active"}},
    })
    assert research_direction_fingerprint(spec) == previous

    plan = delivery_reassessment_plan(spec)
    assert plan["anchor_cycle"] == anchor.name and plan["stopped_cycle"] == stopped.name
    assert plan["changed_material_refs"] == [added["reference"]]
    assert plan["committed_outcomes"] == []
    ready = latest_recovery(spec.research_root)
    assert ready[0] == stopped and takeover_runner_operation(ready[2]) == "reassess"

    output.write_text('{"prediction": [4, 5, 6]}')
    assert delivery_reassessment_plan(spec) is None
    assert latest_recovery(spec.research_root, include_wait=True)[2] == "qualification_wait"


def test_operator_python_intake_reassesses_exact_delivery_without_science_writes(tmp_path):
    from theory_master.__main__ import main
    from theory_master.delivery_materials import EXECUTION_RELATION
    from theory_master.delivery_repair import current_delivery_payload
    from theory_master.operator_public_submission import attach_public_submission
    from theory_master.public_submission import PYTHON_SCHEMA, check_current_submission
    from theory_master.supervisor_goal_reassessment import delivery_reassessment_plan, run_goal_reassessment

    spec, _, backend, anchor = _completed(tmp_path)
    stop = _qualification_stop(spec, "2")
    config = json.loads(spec.config_path.read_text())
    config["submission_contract"] = "benchmark/submission.json"
    spec.benchmark_dir.mkdir(parents=True, exist_ok=True)
    filename = "solution#public%20v1.py"
    (spec.benchmark_dir / "submission.json").write_text(json.dumps({
        "schema": PYTHON_SCHEMA, "filename": filename,
        "callable": {"name": "solve", "parameters": ["x", "y"]}, "allowed_imports": [],
    }))
    spec.config_path.write_text(json.dumps(config))
    spec = TaskSpec.from_config(spec.config_path)
    before = science_authority_digest(spec, spec.graph_database)
    source = tmp_path / filename
    raw = b"raise RuntimeError('the SDK must not execute this')\ndef solve(x, y):\n    return x + y\n"
    source.write_bytes(raw)
    sidecar = tmp_path / "proof.json"
    sidecar.write_text(json.dumps({"solution_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="different solution SHA-256"):
        attach_public_submission(spec, cycle_id=1, source=source, verification_receipt=sidecar)
    sidecar.write_bytes(b'{"provenance": "operator verification"}\n')
    control = stop / "recovery_control.json"
    control.write_text('{"action": "pause"}')
    with pytest.raises(ValueError, match="operator control"):
        attach_public_submission(spec, cycle_id=1, source=source, verification_receipt=sidecar)
    control.unlink()
    with patch("theory_master.runtime_manager.runtime_health", return_value={"active": True, "runner_alive": True}):
        with pytest.raises(ValueError, match="active or unverified Runner"):
            attach_public_submission(spec, cycle_id=1, source=source, verification_receipt=sidecar)
    with patch("theory_master.worker_store.list_workers", return_value=[{"process_alive": True}]):
        with pytest.raises(ValueError, match="live Worker"):
            attach_public_submission(spec, cycle_id=1, source=source, verification_receipt=sidecar)
    assert delivery_reassessment_plan(spec) is None
    waiting = {"active": True, "state": "RECOVERY_WAIT", "supervisor_alive": True,
               "registry": {"requested_operation": "takeover", "current_cycle": stop.name,
                            "launch_id": "waiting", "qualification_wait": {"launch_id": "waiting", "cycle": stop.name}}}
    with patch("sys.argv", ["sciver", "repair-delivery", "fixture", "--cycle", "1",
                            "--attach-public-submission", str(source), "--verification-receipt", str(sidecar)]), \
         patch("theory_master.__main__.spec_from_task", return_value=spec), \
         patch("theory_master.runtime_manager.runtime_health", return_value=waiting), \
         patch("theory_master.__main__.CodexBackend", side_effect=AssertionError("unexpected model backend")):
        assert main() == 0

    current = current_delivery_payload(spec, spec.graph_database, anchor)
    attached = current["operator_public_submission"]
    assert attached["origin"] == "operator_supplied"
    assert attached["outcome_id"] == "cycle-outcome-1"
    assert Path(attached["verification"]["path"]).read_bytes() == sidecar.read_bytes()
    assert attach_public_submission(spec, cycle_id=1, source=source, verification_receipt=sidecar) == attached
    selection = Path(attached["receipt"]["path"]).parent.parent / "selected.json"
    selection.write_text("{invalid JSON")
    assert current_delivery_payload(spec, spec.graph_database, anchor)["operator_public_submission"]["status"] == "unavailable"
    assert attach_public_submission(spec, cycle_id=1, source=source, verification_receipt=sidecar) == attached
    assert science_authority_digest(spec, spec.graph_database) == before and backend.goal_calls == 1
    known = {
        "origin": "operator_supplied", "sha256": hashlib.sha256(raw).hexdigest(),
        "path": attached["submission"]["path"],
    }
    assert check_current_submission(current)["submission"] == known
    investigator = {"path": str(source), "status": "available", "role": "output",
                    "sha256": hashlib.sha256(raw).hexdigest()}
    current["material_index"]["materials"].append({
        "assets": [investigator], "relation": EXECUTION_RELATION, "integrity": {"status": "verified"},
        "evidence_states": {"E-existing": {"lifecycle_status": "active"}},
        "producer_invocation": {"role": "investigator", "invocation_id": "retained"},
        "manifest_sha256": "b" * 64,
    })
    current["goal_material_sources"].append(investigator)
    assert check_current_submission(current)["submission"] == known
    without_attachment = {key: value for key, value in current.items() if key != "operator_public_submission"}
    assert check_current_submission(without_attachment)["submission"]["path"] == str(source)

    plan = delivery_reassessment_plan(spec)
    assert plan["anchor_cycle"] == anchor.name and plan["stopped_cycle"] == stop.name
    assert plan["reassessment_of"] == attached["goal_assessment"] and plan["committed_outcomes"] == []
    with patch.object(ScienceGraphStore, "record_goal_gate_not_passed", side_effect=OSError("lost Goal commit")):
        with pytest.raises(OSError, match="lost Goal commit"):
            run_goal_reassessment(Workflow(spec, backend), plan, watch_stopped=True)
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert store.latest_goal_gate_assessment()["assessment_id"] == attached["goal_assessment"]
    changed = tmp_path / "replacement.py"
    replacement = raw + b"# an explicitly selected replacement\n"
    changed.write_bytes(replacement)
    sidecar.write_text(json.dumps({"solution_sha256": hashlib.sha256(replacement).hexdigest()}))
    selected = attach_public_submission(spec, cycle_id=1, source=changed, verification_receipt=sidecar)
    assert selected["goal_assessment"] == attached["goal_assessment"]
    assert Path(attached["submission"]["path"]).read_bytes() == raw
    new_plan = delivery_reassessment_plan(spec)
    assert new_plan["delivery_revision"] != plan["delivery_revision"]
    assert run_goal_reassessment(Workflow(spec, backend), plan) is None
    result = run_goal_reassessment(Workflow(spec, backend), new_plan)
    assert result["assessment"]["verdict"] == "not_passed" and backend.goal_calls == 3
    access = backend.goal_context["delivery_material_access"]
    frozen_path = Path(access["revision_path"])
    frozen = json.loads(frozen_path.read_text())["operator_public_submission"]
    assert frozen["origin"] == "operator_supplied"
    for key in ("submission", "receipt", "verification"):
        assert Path(frozen[key]["path"]).is_relative_to(frozen_path.parent)
        assert Path(frozen[key]["path"]).read_bytes() == Path(selected[key]["path"]).read_bytes()
    assert science_authority_digest(spec, spec.graph_database) == before
    assert delivery_reassessment_plan(spec) is None
    assert list_cycle_dirs(spec.research_root, task_mode=True) == [stop, anchor]

    delivery = Path(selected["submission"]["path"])
    delivery.chmod(0o600)
    delivery.write_bytes(replacement + b"# drifted\n")
    unavailable = current_delivery_payload(spec, spec.graph_database, anchor)["operator_public_submission"]
    assert unavailable["status"] == "unavailable"
    invalid = check_current_submission({**current, "operator_public_submission": unavailable})
    assert invalid["status"] == "invalid" and invalid["matching_artifacts"] == []
    assert delivery_reassessment_plan(spec) is None


@pytest.mark.parametrize("launch_matches", ["wrong_owner", "wrong_delivery", "exact"])
def test_managed_delivery_only_runner_never_falls_through_to_research(tmp_path, monkeypatch, launch_matches):
    import os
    from types import SimpleNamespace
    from unittest.mock import Mock

    from theory_master import runner
    from theory_master.supervisor_goal_reassessment import reassessment_launch_identity

    cycle = tmp_path / "2"
    spec = SimpleNamespace(research_root=tmp_path)
    workflow = Mock()
    plan = {"stopped_cycle": cycle.name, "anchor_cycle": "1", "reassessment_of": "goal-1",
            "delivery_revision": "a" * 64}
    assess = Mock(return_value={"assessment": {"verdict": "not_passed"}})
    attempt = {**reassessment_launch_identity(plan, "launch"), "status": "launching"}
    if launch_matches == "wrong_delivery":
        attempt["delivery_revision"] = "b" * 64
    registry = {"requested_operation": "takeover", "takeover_cycle": cycle.name, "launch_id": "launch",
                "current_cycle": cycle.name,
                "supervisor_pid": -1 if launch_matches == "wrong_owner" else os.getppid(),
                "supervisor_start_ticks": 1, "delivery_reassessment_attempt": attempt}
    monkeypatch.setattr(runner.TaskBinding, "load", lambda *_: SimpleNamespace(task_root=tmp_path))
    monkeypatch.setattr(runner.TaskSpec, "from_task_root", lambda *_: spec)
    monkeypatch.setattr(runner, "resolve_routes", lambda **_: {})
    monkeypatch.setattr(runner, "model_route_preflight_lines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "CodexBackend", Mock())
    monkeypatch.setattr(runner, "Collector", Mock())
    monkeypatch.setattr(runner, "Workflow", lambda *_args, **_kwargs: workflow)
    monkeypatch.setattr("theory_master.supervisor_activation.latest_recovery",
                        lambda *_: (cycle, {}, "goal_reassessment"))
    monkeypatch.setattr("theory_master.runtime_manager.load_registry", lambda *_: registry)
    monkeypatch.setattr("theory_master.runtime_manager.process_identity_alive", lambda *_: True)
    monkeypatch.setattr("theory_master.supervisor_goal_reassessment.delivery_reassessment_plan", lambda *_: plan)
    monkeypatch.setattr("theory_master.supervisor_goal_reassessment.run_goal_reassessment", assess)

    result = runner.main(["--task-root", str(tmp_path), "--operation", "reassess",
                          "--managed-child", "--supervisor-takeover-cycle", cycle.name])

    assert result == (0 if launch_matches == "exact" else 1)
    if launch_matches == "exact":
        assess.assert_called_once_with(workflow, plan, watch_stopped=True, recovery_pin=None)
    else:
        assess.assert_not_called()
    workflow.run.assert_not_called()
    workflow.resume.assert_not_called()


@pytest.mark.parametrize("pass_goal", [False, True])
def test_supervisor_can_send_new_committed_open_outcome_to_goal_judge(tmp_path, pass_goal):
    from theory_master.supervisor_goal_reassessment import goal_reassessment_plan, run_goal_reassessment

    spec, workflow, backend, anchor = _completed(tmp_path)
    before = (anchor / "goal_completion_out.json").read_bytes()
    _qualification_stop(spec, "2")
    stopped = _qualification_stop(spec, "3")

    with ScienceGraphStore(spec.graph_database) as store:
        store.add_hypothesis("H-9999", title="New boundary evidence")
        store.register_cycle_hypothesis(cycle_id="2", hypothesis_id="H-9999")
        store.append_outcome(outcome_id="cycle-outcome-2", hypothesis_id="H-9999",
                             cycle_id="2", verdict="open", summary="The old interpretation has new limits")
        old = store.latest_goal_gate_assessment()
    plan = goal_reassessment_plan(workflow)
    assert plan["committed_outcomes"] == [{"id": "cycle-outcome-2", "cycle_id": "2", "verdict": "open"}]
    assert plan["reassessment_of"] == old["assessment_id"]

    backend.pass_goal = pass_goal
    result = run_goal_reassessment(workflow, plan)
    assert result["assessment"]["verdict"] == ("passed" if pass_goal else "not_passed")
    assert backend.goal_calls == 2
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        history = store.goal_gate_history(anchor.name)
        assert history[0] == old
        assert history[-1]["supersedes"] == old["assessment_id"]
        assert history[-1]["science_digest"] != old["science_digest"]
        assert store.db.execute(
            "SELECT verdict FROM cycle_evaluations WHERE id='cycle-outcome-2'"
        ).fetchone()[0] == "open"
        assert bool(store.goal_terminal()) == pass_goal
        if pass_goal:
            assert store.goal_terminal()["detail"]["trigger"] == "supervisor_reassessment"
    assert Path(old["receipt_path"]).read_bytes() == before
    assert list_cycle_dirs(spec.research_root, task_mode=True)[0] == stopped
    assert goal_reassessment_plan(workflow) is None


def test_reassessment_revalidates_stop_after_goal_turn(tmp_path):
    from theory_master.supervisor_goal_reassessment import goal_reassessment_plan, run_goal_reassessment

    spec, workflow, backend, anchor = _completed(tmp_path)
    stopped = _qualification_stop(spec, "2")
    report = _report(spec, anchor)
    report.write_text(report.read_text() + "\nNew registered report content.\n")
    plan = goal_reassessment_plan(workflow)
    original = backend.invoke

    def invoke(role, prompt, thread_id, model, provider):
        if role == "judge" and "Current task: goal_completion " in prompt:
            (stopped / "recovery_control.json").write_text('{"action":"pause"}')
        return original(role, prompt, thread_id, model, provider)

    backend.invoke = invoke
    backend.pass_goal = True
    result = run_goal_reassessment(workflow, plan)
    assert result["assessment"]["assessment_status"] == "stale"
    assert backend.goal_calls == 2
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert store.goal_terminal() is None
        assert len(store.goal_gate_history(anchor.name)) == 1


def test_delivery_only_judge_watches_the_stopped_cycle_without_pausing_the_anchor(tmp_path):
    from theory_master.supervisor_goal_reassessment import goal_reassessment_plan, run_goal_reassessment
    from theory_master.workflow_support import RecoveryStop

    spec, workflow, backend, anchor = _completed(tmp_path)
    stopped = _qualification_stop(spec, "2")
    report = _report(spec, anchor)
    report.write_text(report.read_text() + "\nNew registered delivery.\n")
    plan = goal_reassessment_plan(workflow)
    original = backend.invoke

    def interrupt(role, prompt, thread_id, model, provider):
        if role == "judge" and "Current task: goal_completion " in prompt:
            (stopped / "recovery_control.json").write_text('{"action":"pause"}')
        return original(role, prompt, thread_id, model, provider)

    backend.invoke = interrupt
    with pytest.raises(RecoveryStop, match="withdrew its delivery"):
        run_goal_reassessment(workflow, plan, watch_stopped=True)
    assert not hasattr(workflow, "_delivery_reassessment_control")
    assert json.loads((anchor / "research_state.json").read_text())["status"] == "over"
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert len(store.goal_gate_history(anchor.name)) == 1


def test_delivery_only_invalid_judge_output_keeps_scientific_cycle_closed(tmp_path):
    from theory_master.supervisor_goal_reassessment import goal_reassessment_plan, run_goal_reassessment
    from theory_master.workflow_support import RecoveryStop

    spec, workflow, backend, anchor = _completed(tmp_path)
    _qualification_stop(spec, "2")
    state_path = anchor / "research_state.json"
    before = json.loads(state_path.read_text())
    report = _report(spec, anchor)
    report.write_text(report.read_text() + "\nNew registered delivery.\n")
    plan = goal_reassessment_plan(workflow)
    original = backend.invoke

    def malformed(role, prompt, thread_id, model, provider):
        if role == "judge":
            return str(thread_id), '{"invalid": true}'
        return original(role, prompt, thread_id, model, provider)

    backend.invoke = malformed
    with pytest.raises(RecoveryStop, match="delivery reassessment contract repair failed"):
        run_goal_reassessment(workflow, plan, watch_stopped=True)
    after = json.loads(state_path.read_text())
    fields = ("status", "phase", "execution_active", "resumable", "active_invocation_id",
              "protocol_health", "closure_reason", "attention", "human_attention", "recovery")
    assert {key: after.get(key) for key in fields} == {key: before.get(key) for key in fields}
    assert list((anchor / "contract_repairs").glob("*judge*.json"))
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert len(store.goal_gate_history(anchor.name)) == 1
        assert store.goal_terminal() is None


def test_interrupted_supervisor_reassessment_reuses_frozen_snapshot(tmp_path):
    from theory_master.supervisor_goal_reassessment import goal_reassessment_plan, run_goal_reassessment

    spec, workflow, backend, anchor = _completed(tmp_path)
    _qualification_stop(spec, "2")
    report = _report(spec, anchor)
    report.write_text(report.read_text() + "\nNew registered report content.\n")
    plan = goal_reassessment_plan(workflow)
    original = backend.invoke

    def interrupt(role, prompt, thread_id, model, provider):
        if role == "judge" and "Current task: goal_completion " in prompt:
            raise RuntimeError("offline Goal transport interrupted")
        return original(role, prompt, thread_id, model, provider)

    backend.invoke = interrupt
    with pytest.raises(RuntimeError, match="offline Goal transport"):
        run_goal_reassessment(workflow, plan)
    state = json.loads((anchor / "research_state.json").read_text())
    pending = state["goal_delivery_pending"]
    assert pending["delivery_revision"] == plan["delivery_revision"]
    assert state["status"] == "over"
    assert Path(pending["snapshot_manifest"]).is_file()
    assert goal_reassessment_plan(workflow) == plan

    backend.invoke = original
    result = run_goal_reassessment(workflow, plan)
    assert result["assessment"]["verdict"] == "not_passed"
    receipt = json.loads((anchor / "goal_completion_out.json").read_text())
    assert receipt["snapshot_manifest_sha256"] == pending["snapshot_manifest_sha256"]
    assert goal_reassessment_plan(workflow) is None


def _delivery_projection_fixture(tmp_path):
    from dataclasses import replace
    from theory_master.cycle_replay import _fingerprint_tree
    from theory_master.goal_materials import material_sha256
    from theory_master.invocation_artifacts import MANIFEST_SCHEMA
    from theory_master.invocations import InvocationLedger
    from theory_master.specification import CYCLE_MARKER

    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal\n\nAdd label to public table.\n\n# Research Contract\n\nPublic inputs.\n\n"
                    "# Final Gate\n\nProvide both label tables.\n\n# Required Context\n\n"
                    "- benchmark/test.csv\n- benchmark/manifest.json\n")
    binding = bootstrap(plan)
    benchmark = tmp_path / "benchmark"
    benchmark.mkdir()
    spec = replace(TaskSpec.from_config(binding.config_path), benchmark_dir=benchmark)
    backend = CountingGoalBackend()
    workflow = Workflow(spec, backend)
    table = benchmark / "test.csv"
    table.write_text('index,descriptor\n0,"a,b"\n1,c\n')
    declaration = benchmark / "manifest.json"
    declaration.write_text(json.dumps({"files": [{"path": "benchmark/test.csv", "role": "public_input",
                                                  "sha256": material_sha256(table)}]}))
    cycle = spec.research_root / "1"
    workspace = cycle / "workspace"
    workspace.mkdir(parents=True)
    (cycle / CYCLE_MARKER).touch()
    (cycle / "research_state.json").write_text(json.dumps({
        "status": "over", "phase": "complete", "scientific_verdict": "supported",
        "execution_active": False, "cycle_id": "1", "goal_id": spec.goal_id,
        "hypothesis_lock": {"status": "locked", "id": "H-0001"},
    }))
    predictions = workspace / "prediction.csv"
    predictions.write_text("id,model_a,model_b\n0,yes,no\n1,no,no\n")
    producer = {"invocation_id": "inv-1-investigator-test", "role": "investigator",
                "stage": "investigate", "thread_id": "test"}
    manifest = cycle / "invocation_artifacts" / (producer["invocation_id"] + ".json")
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({
        "schema": MANIFEST_SCHEMA, "status": "completed", **producer, "workspace": str(workspace),
        "created": [{"path": str(predictions), "relative_path": "prediction.csv",
                     "sha256": material_sha256(predictions)}],
    }))
    InvocationLedger(cycle / "invocations.jsonl").append({
        **producer, "state": "committed", "commit_state": "committed",
        "invocation_artifact_manifest": str(manifest),
    })
    snap = cycle / "replay_checkpoints"
    snap_workspace = snap / "judge_start.inputs/workspace"
    snap_workspace.mkdir(parents=True)
    (snap_workspace / predictions.name).write_bytes(predictions.read_bytes())
    (snap / "judge_start.json").write_text(json.dumps({
        "logical_cycle": 1, "inputs_dir": "judge_start.inputs",
        "boundary": "after_complete_findings_before_judge", "workspace_sha256": _fingerprint_tree(snap_workspace),
    }))
    evidence_dir = cycle / "evidence"
    evidence_dir.mkdir()
    batch = {"evidence_batch_id": "batch1", "evidence_batch_hash": "bound-fixture",
             "producer_invocation": producer}
    (evidence_dir / "registry.jsonl").write_text(json.dumps({
        **batch, "evidence_id": "E-1", "result": {"artifacts": ["prediction.csv"]},
        "provenance": {"declaration_ref": str(declaration),
                       "declaration_sha256": material_sha256(declaration)},
    }) + "\n")
    (evidence_dir / "registration_batches.jsonl").write_text(json.dumps({
        **batch, "event": "batch_committed",
        "schema": "theory-master/evidence-registration-batch-commit/v1", "evidence_ids": ["E-1"],
    }) + "\n")

    with ScienceGraphStore(spec.graph_database) as store:
        store.add_hypothesis("H-0001")
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
        store.append_outcome(outcome_id="cycle-outcome-1", hypothesis_id="H-0001",
                             cycle_id="1", verdict="supported", evidence_refs=["E-1"])
        store.record_goal_gate_not_passed(cycle_id="1", rationale="Label tables missing.")
    _qualification_stop(spec, "2")
    request = {"operation": "csv_label_projection", "input": "benchmark/test.csv",
               "predictions": "prediction.csv", "input_key": "index", "prediction_key": "id",
               "label_column": "label", "allowed_labels": ["yes", "no"],
               "outputs": {"model_a": "a.csv", "model_b": "b.csv"}}
    return spec, workflow, backend, cycle, request


def test_supervisor_projects_adjudicated_csv_and_goal_reads_only_verified_bytes(tmp_path):
    from theory_master.delivery_repair import current_delivery_payload
    from theory_master.supervisor_delivery import delivery_repair_context, materialize_supervisor_delivery

    spec, workflow, backend, cycle, request = _delivery_projection_fixture(tmp_path)
    assert delivery_repair_context(workflow)["predictions"] == [
        {"reference": "prediction.csv", "columns": ["id", "model_a", "model_b"]}]
    before = science_authority_digest(spec, spec.graph_database)
    receipt = materialize_supervisor_delivery(workflow, request)
    assert receipt["cycle_id"] == cycle.name
    assert materialize_supervisor_delivery(workflow, request) == receipt
    assert [Path(row["path"]).read_text() for row in receipt["assets"]] == [
        'index,descriptor,label\n0,"a,b",yes\n1,c,no\n',
        'index,descriptor,label\n0,"a,b",no\n1,c,no\n',
    ]
    payload = current_delivery_payload(spec, spec.graph_database, cycle)
    assert receipt in payload["supervisor_deliveries"]
    assert all(asset in [{"path": row.get("path"), "sha256": row.get("sha256")}
                         for row in payload["goal_material_sources"]]
               for asset in receipt["assets"])
    assert science_authority_digest(spec, spec.graph_database) == before
    assert backend.goal_calls == 0

    assessment = repair_delivery(workflow, cycle=cycle.name, reassess=True)
    assert assessment["assessment"]["verdict"] == "not_passed"
    assert backend.goal_calls == 1
    frozen = json.loads(Path(backend.goal_context["delivery_material_access"]["revision_path"]).read_text())
    assert all(Path(asset["path"]).is_file() and Path(asset["path"]).is_relative_to(cycle / "goal_snapshots")
               for asset in frozen["supervisor_deliveries"][0]["assets"])
    assert science_authority_digest(spec, spec.graph_database) == before


def test_supervisor_rejects_unbound_predictions_or_unsafe_output(tmp_path):
    from theory_master.supervisor_delivery import materialize_supervisor_delivery

    _, workflow, _, cycle, request = _delivery_projection_fixture(tmp_path)
    for bad in ({"input": "benchmark/../test.csv"}, {"allowed_labels": ["no", "maybe"]},
                {"outputs": {"model_a": "../elsewhere.csv"}}, {"prediction_key": "model_a"}):
        with pytest.raises(ValueError):
            materialize_supervisor_delivery(workflow, {**request, **bad})
    assert not (cycle / "supervisor_delivery").exists()

    # Altering both the working bytes and their loose captured manifest still
    # cannot replace the original frozen pre-Judge prediction.
    from theory_master.goal_materials import material_sha256
    prediction = cycle / "workspace/prediction.csv"
    prediction.write_text("id,model_a,model_b\n0,no,no\n1,no,no\n")
    manifest = next((cycle / "invocation_artifacts").glob("*.json"))
    captured = json.loads(manifest.read_text())
    captured["created"][0]["sha256"] = material_sha256(prediction)
    manifest.write_text(json.dumps(captured))
    with pytest.raises(ValueError, match="No verified public input"):
        materialize_supervisor_delivery(workflow, request)
