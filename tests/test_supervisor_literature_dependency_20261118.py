"""Offline recovery of failed role delivery; no scientific or model execution."""
from __future__ import annotations

import copy
import json
from unittest.mock import MagicMock, patch

import pytest

from theory_master import supervisor
from theory_master import supervisor_recovery as jobs
from theory_master.observability.incident_routing import classify_incident
from theory_master.runtime import write_json
from theory_master.runtime_manager import load_registry, registry_path, update_registry
from theory_master.runtime_supervisor_review import resume_blocker
from theory_master.supervisor_attention_review import review_supervisor_attention
from theory_master.supervisor_continuity import engineering_skill_eligible
from theory_master.supervisor_intervention import KEY


@pytest.fixture
def literature_dependency(tmp_path):
    root = tmp_path / "research"
    cycle = root / "2"
    (cycle / "checkpoints").mkdir(parents=True)
    (cycle / ".theory-master-cycle").write_text("theory-master/v2\ntest\n")
    checkpoint = cycle / "checkpoints/000001-lead.json"
    checkpoint.write_text(json.dumps({
        "cycle_id": "2", "phase": "research_lead:completed",
        "hypothesis_lock": {"status": "unlocked"},
    }))
    state = {
        "status": "needs_attention", "cycle_id": "2", "phase": "research_lead:completed",
        "protocol_health": "valid", "resumable": True, "execution_active": False,
        "last_safe_checkpoint": str(checkpoint),
        "hypothesis_lock": {"status": "unlocked"},
        "role_outputs": {"research_lead": {"needs_literature": True}},
        "lead_literature_detours": {"frontier_proposal": 1},
        "attention": {
            "code": "lead_literature_unavailable",
            "reason": "Scholarly transport unavailable before the Literature result was delivered.",
            "lead_stage": "frontier_proposal", "source_gap_id": "lead-source-test",
        },
    }
    write_json(cycle / "research_state.json", state)
    return root, cycle, state


@pytest.fixture(params=["lead_literature_unavailable", "contract_repair_failed"])
def delivery_dependency(request, literature_dependency):
    root, cycle, state = literature_dependency
    if request.param == "contract_repair_failed":
        state.update({
            "phase": "judge:default:contract_repair_failed", "protocol_health": "violation",
            "hypothesis_lock": {"status": "locked", "id": "H-frozen"},
            "judge_verdict": None,
            "role_outputs": {"research_lead": {"status": "committed"}},
            "attention": {
                "code": "contract_repair_failed",
                "reason": "The judge:default output still violated its contract after bounded repair.",
            },
            "recovery": {
                "action": "contract_repair_failed", "invocation_id": "INV-failed",
                "role": "judge", "stage": "default", "thread_id": "failed-judge-thread",
            },
        })
        state.pop("lead_literature_detours")
        write_json(cycle / "checkpoints/000001-lead.json", {
            "cycle_id": "2", "phase": "investigator:completed",
            "hypothesis_lock": state["hypothesis_lock"],
        })
        write_json(cycle / "research_state.json", state)
    return root, cycle, state


def test_failed_delivery_hands_off_without_paid_attention_review(delivery_dependency):
    root, cycle, state = delivery_dependency
    original = copy.deepcopy(state)
    backend = MagicMock()
    with patch.dict("os.environ", {KEY: "1"}):
        result = review_supervisor_attention(
            backend=backend, cycle_dir=cycle, state=state,
            goal="g", research_contract="c", final_gate="f",
        )
    assert result["action"] == "hold"
    assert result["next_executor"] == "sdk:supervisor_engineering"
    assert result["recovery_scope"] == "operational_only"
    backend.invoke_attention_review.assert_not_called()
    backend.invoke.assert_not_called()
    assert state == original
    review_path = next((cycle / "attention").glob("sdk_internal_review_*.json"))
    receipt = json.loads(review_path.read_text())
    assert receipt["model"] is None and "exchange" not in receipt
    route = classify_incident(
        severity="attention", kind="needs_attention", code=state["attention"]["code"],
    )
    assert route["owner"] == "supervisor_runtime"
    assert supervisor._exit_disposition(root) == "failed"
    assert engineering_skill_eligible(state, result)
    if state["attention"]["code"] == "contract_repair_failed":
        assert "Judge verdict" in result["reason"]
        assert "Protocol/contract violation" in resume_blocker(state)


def test_real_handler_records_engineering_dependency_without_repeating_paid_turn(delivery_dependency):
    root, cycle, state = delivery_dependency
    original = copy.deepcopy(state)
    backend = MagicMock()
    backend.invoke_runtime_review.return_value = ("fake-runtime-review", json.dumps({
        "action": "hold", "reason": "The scholarly dependency is still unavailable.",
        "diagnosis": "No source result was delivered; the scientific decision remains pending.",
        "repair_suggestion": "Diagnose the scholarly transport or record the exact missing dependency.",
    }))
    validation = {
        "phase": "blocked", "disposition": "needs_dependency",
        "reason": "Scholarly endpoint must become available.", "dependency": "scholarly transport",
    }
    with patch.dict("os.environ", {KEY: "1"}), \
         patch.object(supervisor, "_supervisor_backend", return_value=backend), \
         patch.object(supervisor, "update_registry"), \
         patch("theory_master.supervisor_continuity.attempt_engineering_skill", return_value={
             "status": "engineering_attempted", "validation": validation,
         }) as engineering:
        # Preserve the real scheduler lock, runtime review, engineering routing,
        # owner and persistence. Only model/engineering execution are fake.
        assert not supervisor._handle_failed_state(root, repair_enabled=True)
        assert not supervisor._handle_failed_state(root, repair_enabled=True)
    if state["attention"]["code"] == "contract_repair_failed":
        # The protocol violation is a deterministic plain-resume blocker;
        # engineering still owns diagnosis without a resume-only model turn.
        backend.invoke_runtime_review.assert_not_called()
    else:
        backend.invoke_runtime_review.assert_called_once()
    engineering.assert_called_once()
    job = jobs.read_job(cycle)
    assert job["status"] == "waiting" and job["dependency_wait"]
    assert job["current_action"] == "needs_dependency"
    assert len(job["history"]) == 1 and job["last_verified_progress"] is None
    persisted = json.loads((cycle / "research_state.json").read_text())
    assert {key: value for key, value in persisted.items() if not key.startswith("supervisor_")} == original
    handoff = json.loads((cycle / "run_state.json").read_text())["repair_handoff"]
    assert handoff["target"] == "sdk:runtime_supervisor"
    assert handoff["recovery_job"]["dependency_wait"]


def test_startup_keeps_owner_waiting_then_resumes_same_cycle(literature_dependency):
    root, cycle, state = literature_dependency
    original = (cycle / "research_state.json").read_bytes()
    # A prior qualification waiter may have exited before clearing its field.
    update_registry(root, state="RECOVERY_WAIT", current_cycle=cycle.name, launch_id="offline",
                    qualification_wait={"cycle": cycle.name, "launch_id": "offline"})
    stop = MagicMock()
    stop.is_set.return_value = False
    reviews = []
    child = MagicMock(pid=99999999)

    def review(*args, **kwargs):
        # Isolate launcher/recovery-loop transitions from the runtime handler.
        assert (cycle / "research_state.json").read_bytes() == original
        jobs.begin_action(cycle, state)
        reviews.append(len(reviews) + 1)
        if len(reviews) == 1:
            jobs.finish_action(cycle, state, {
                "action": "hold", "reason": "Scholarly endpoint must become available.",
                "engineering_skill": {"validation": {
                    "disposition": "needs_dependency", "dependency": "scholarly transport",
                }},
            })
            return False
        jobs.finish_action(cycle, state, {"action": "resume", "session_id": "SUP-repaired"})
        return True

    def wait(seconds):
        # The wait leaves the scheduler gate available; unchanged dependencies do not
        # cause another paid turn, even after an arbitrarily long time.
        import fcntl
        with (root / ".theory-master/scheduler.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        registry = load_registry(root)
        assert registry["state"] == "RECOVERY_WAIT" and registry["current_cycle"] == cycle.name
        assert registry["recovery_job"] == jobs.projection(cycle)
        assert registry["recovery_job"]["dependency_wait"]
        assert not jobs.due(cycle, state, at=10**12)
        write_json(root / ".theory-master/web-role-routes.json", {})
        assert jobs.due(cycle, state)

    def runner_exit(**kwargs):
        registry = load_registry(root)
        assert "recovery_job" not in registry
        if child.wait.call_count == 1:
            assert registry["state"] == "RECOVERING"
            (cycle / "supervisor/session.jsonl").touch()
            (cycle / "runtime_events.jsonl").write_text(json.dumps({
                "event": "LLM_MESSAGE", "ts": "2026-11-21T11:00:01+08:00", "thread_id": "fresh-thread",
            }) + "\n")
            raise supervisor.subprocess.TimeoutExpired("fake-runner", kwargs["timeout"])
        assert registry["state"] == "RUNNING"
        if len(reviews) == 2:
            # Fake the next Runner's normal completion; no task is launched.
            write_json(cycle / "research_state.json", {**state, "status": "over"})
        return 0

    stop.wait.side_effect = wait
    child.wait.side_effect = runner_exit
    with patch.dict("os.environ", {KEY: "1"}), \
         patch.object(supervisor.threading, "Thread"), \
         patch.object(supervisor.threading, "Event", return_value=stop), \
         patch.object(supervisor, "update_registry", wraps=update_registry) as registry, \
         patch.object(supervisor, "_stamp", return_value="2026-11-21T11:00:00+08:00"), \
         patch.object(supervisor, "process_identity_alive", side_effect=lambda pid, *args: pid == child.pid), \
         patch.object(supervisor, "_quota_pause_context", return_value=None), \
         patch.object(supervisor, "_runtime_env", return_value={}), \
         patch.object(supervisor, "_runner_command", return_value=["fake-runner"]) as command, \
         patch.object(supervisor.subprocess, "Popen", return_value=child) as spawn, \
         patch.object(supervisor, "_handle_failed_state", side_effect=review), \
         patch.object(supervisor, "_runtime_supervisor_receipt", return_value=(cycle.name, "SUP-repaired")), \
         patch.object(supervisor, "_notify_supervisor_terminal"):
        assert supervisor.main([
            "--task-root", str(root), "--operation", "run", "--launch-id", "offline",
        ]) == 0
    assert spawn.call_count == 1 and reviews == [1, 2]
    assert [call.args[1] for call in command.call_args_list] == ["resume"]
    waits = [call.kwargs for call in registry.call_args_list if call.kwargs.get("state") == "RECOVERY_WAIT"]
    assert len(waits) == 1
    assert waits[0]["recovery_job"]["recovery_owner"] == "sdk:runtime_supervisor"
    assert waits[0]["recovery_job"]["dependency_wait"]
    job = jobs.read_job(cycle)
    assert job["status"] == "activity_verified"
    assert job["last_verified_progress"]["scope"] == "model_or_tool_activity_only"
    assert len(job["history"]) == 2


@pytest.mark.parametrize("transition", [
    {"state": "LAUNCHING"},
    {"current_cycle": "3"},
    {"state": "RECOVERY_WAIT", "qualification_wait": {"cycle": "2", "launch_id": "test"}},
])
def test_recovery_projection_lifetime_is_bound_to_operational_wait(tmp_path, transition):
    wait = {"status": "waiting", "dependency_wait": True}
    update_registry(tmp_path, state="RECOVERY_WAIT", current_cycle="2", recovery_job=wait)
    update_registry(tmp_path, current_cycle="2", heartbeat_at="2026-11-21T11:00:00+08:00")
    assert load_registry(tmp_path)["recovery_job"] == wait

    update_registry(tmp_path, **transition)

    persisted = json.loads(registry_path(tmp_path).read_text())
    assert "recovery_job" not in persisted
    assert all(persisted[key] == value for key, value in transition.items())


def test_existing_active_registry_drops_stale_recovery_projection(tmp_path):
    registry = update_registry(tmp_path, state="RUNNING", current_cycle="14")
    stale = {**registry, "recovery_job": {"status": "waiting", "dependency_wait": True}}
    path = registry_path(tmp_path)
    write_json(path, stale)

    assert load_registry(tmp_path) == registry
    assert json.loads(path.read_text()) == stale
    update_registry(tmp_path, heartbeat_at="2026-11-21T11:00:00+08:00")
    assert "recovery_job" not in json.loads(path.read_text())


@pytest.mark.parametrize("code", [
    "lead_literature_detour_exhausted", "unknown_attention", "no_qualified_candidate",
    "investigator_native_goal_blocked", "cycle_cost_budget_exhausted",
    "cycle_token_budget_exhausted", "investigator_budget_cap_reached",
])
def test_scientific_budget_and_unknown_attention_still_stop(literature_dependency, code):
    root, cycle, state = literature_dependency
    state["attention"]["code"] = code
    write_json(cycle / "research_state.json", state)
    assert not jobs.operational_incident(state)
    assert not jobs.due(cycle, state)
    assert supervisor._exit_disposition(root) == "terminal"


@pytest.mark.parametrize("guard,reason", [
    ({"recovery": {"action": "pause"}}, "Operator pause"),
    ({"recovery": {"action": "interrupt"}}, "Operator pause"),
    ({"abandoned_cycle": True}, "Cycle was abandoned"),
    ({"resumable": False}, "not resumable"),
    ({"protocol_health": "violation"}, "Protocol/contract violation"),
    ({"investigator_budget": {"exhausted": True}}, "Investigator budget guard"),
    ({"investigator_budget": {"rejected": True}}, "Investigator budget guard"),
    ({"last_safe_checkpoint": None, "role_outputs": {}}, "No existing safe checkpoint"),
])
def test_delivery_owner_does_not_waive_resume_guards(delivery_dependency, guard, reason):
    _, cycle, state = delivery_dependency
    # Isolate each guard from the contract fixture's original protocol blocker.
    # The untouched failure's violation is checked by the handoff test above.
    state["protocol_health"] = "valid"
    state.update(guard)
    assert reason in resume_blocker(state)
    if guard.get("recovery") or guard.get("abandoned_cycle"):
        assert not jobs.due(cycle, state)


def test_supervisor_off_does_not_start_delivery_recovery(delivery_dependency):
    root, cycle, _ = delivery_dependency
    stop = MagicMock()
    stop.is_set.return_value = False
    with patch.dict("os.environ", {KEY: "0"}), \
         patch.object(supervisor, "_handle_failed_state") as review, \
         patch.object(supervisor, "update_registry") as registry:
        assert not supervisor._error_repair_enabled(None, root)
        assert not supervisor._recover_failed_state(root, repair_enabled=True, stop=stop)
    review.assert_not_called()
    registry.assert_not_called()
    stop.wait.assert_not_called()
    assert not jobs.read_job(cycle)
