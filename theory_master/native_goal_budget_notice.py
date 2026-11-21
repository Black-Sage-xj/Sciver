"""Deliver an already-applied SDK budget decision to the original Goal."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .native_goal_guidance import FILENAME, queue_bound_guidance
from .native_goal_recovery import verify_existing_goal
from .runtime import now, write_json
from .scientific_objective import frozen_science_digest


def approved_review(cycle: Path, thread_id: str, allowance: int) -> dict | None:
    ledger = cycle / "budget/investigator_reviews.jsonl"
    if not ledger.is_file():
        return None
    if ledger.is_symlink():
        raise ValueError("unsafe resource review ledger")
    rows = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    if not rows:
        return None
    row = rows[-1]
    if row.get("investigator_thread_id") != thread_id:
        return None
    index = row.get("review_index")
    if (row.get("decision") != "approve" or row.get("new_allowance") != allowance
            or row.get("cycle_id") != cycle.name or not isinstance(index, int)):
        raise ValueError("resource notice requires the current applied approval")
    path = cycle / "budget" / f"internal_review_{index:04d}.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("resource review decision is unavailable")
    data = path.read_bytes()
    decision = json.loads(data)
    if (decision.get("schema") != "theory-master/internal-budget-review/v1"
            or any(decision.get(key) != row.get(key)
                   for key in ("at", "review_index", "decision", "new_allowance"))):
        raise ValueError("resource approval and applied ledger disagree")
    return {"review_index":index, "allowance_tokens":allowance, "thread_id":thread_id,
            "decision_path":str(path), "decision_sha256":hashlib.sha256(data).hexdigest(),
            "ledger_row_sha256":hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()}


def validate_notice(cycle: Path, record: dict) -> None:
    expected = record["budget_review"]
    current = approved_review(cycle, expected["thread_id"], expected["allowance_tokens"])
    if current != expected:
        raise ValueError("resource approval changed before acknowledged delivery")


def queue_review_notice(cycle: Path, identity: dict, *, allowance: int, observed: int,
                        frozen: str) -> dict | None:
    binding = approved_review(cycle, identity["thread_id"], allowance)
    if binding is None:
        return None
    current = cycle / FILENAME
    history = cycle / "native_goal_guidance_history"
    for path in [current, *history.glob("*.json")]:
        if not path.exists():
            continue
        if path.is_symlink():
            raise ValueError("unsafe resource notice history")
        old = json.loads(path.read_text())
        if path == current and old.get("status") == "pending":
            return None  # Never replace a pending explicit correction.
        previous = old.get("goal_identity") or {}
        if (old.get("status") == "consumed" and old.get("budget_review") == binding
                and all(previous.get(key) == identity.get(key) for key in
                        ("thread_id","created_at","objective_sha256","token_budget"))):
            return None
    text = (
        f"SDK RESOURCE DECISION: the original SDK Budget Reviewer approved review "
        f"{binding['review_index']}; it is already applied in the original ledger. "
        f"The current cumulative allowance is {allowance} tokens; {observed} tokens "
        f"have already been used, leaving {max(0,allowance-observed)}. Previous usage "
        "has NOT been reset. The approval is effective now: do not wait for the "
        "old review again or search for an Investigator review action. Resource "
        "review is SDK-owned; continue the existing frozen work within its actual "
        "allowance and obey subsequent SDK guards. Do not voluntarily block the "
        "Goal for this already-resolved resource dependency. This is operational "
        "authority only, not a Finding, algorithm instruction or scientific verdict. "
        f"Decision provenance: {binding['decision_path']} sha256={binding['decision_sha256']}."
    )
    return queue_bound_guidance(cycle,text,source="sdk_applied_budget_review",identity=identity,
        frozen=frozen,budget={"allowance_tokens":allowance,"observed_tokens":observed},
        extra={"budget_review":binding})


def ensure_review_notice(handoff: Path, origin: dict | None, *, allowance: int | None,
                         observed: int) -> None:
    if origin is None or allowance is None:
        return
    cycle = handoff.parents[2]
    if not (cycle / "budget/investigator_reviews.jsonl").is_file():
        return
    identity = origin.get("persistent_goal")
    if not isinstance(identity,dict):
        raise ValueError("resource notification lacks the original Goal identity")
    queue_review_notice(cycle,identity,allowance=allowance,observed=observed,
                        frozen=origin["frozen_objective_sha256"])


def resolve_review_dependency(owner: Any, *, reason: str, client: Any = None) -> dict:
    """Explicit operator resolution; an approval is never issued here."""
    from .invocations import InvocationLedger
    from .investigator_budget import budget_status
    from .runtime_manager import runtime_health
    from .specification import latest_cycle_dir
    from .supervisor_repair_receipts import failure_binding

    cycle = latest_cycle_dir(owner.spec.research_root,task_mode=owner.spec.task_mode)
    state = json.loads((cycle / "research_state.json").read_text())
    native = state.get("native_goal") or {}
    if (not reason.strip() or state.get("status") != "failed" or state.get("execution_active")
            or runtime_health(cycle.parent).get("runner_alive")
            or native.get("runtime_failure_kind") != "native_goal_dependency_blocked"
            or (state.get("recovery") or {}).get("kind") != "NativeGoalRuntimeFailure"
            or (state.get("recovery") or {}).get("action") != "error"
            or state.get("scientific_verdict") is not None):
        raise ValueError("not an inactive original blocked-Goal dependency failure")
    last = InvocationLedger(cycle / "invocations.jsonl").snapshot(state["last_invocation_id"])
    identity = native.get("persistent_goal") or {}
    if (not last or last.get("state") != "failed" or last.get("role") != "investigator"
            or last.get("thread_id") != identity.get("thread_id")
            or last.get("error_type") != "NativeGoalRuntimeFailure"
            or last.get("commit_state") != "not_committed" or last.get("closure_hash")
            or (state.get("threads") or {}).get("investigator") != identity.get("thread_id")):
        raise ValueError("blocked dependency has no uncommitted owning Investigator")
    budget = budget_status(cycle,cycle.parent,state)
    if budget["exhausted"] or budget.get("rejected"):
        raise ValueError("resource dependency has no usable approved allowance")
    approval = approved_review(cycle,identity["thread_id"],budget["allowance_tokens"])
    if approval is None:
        raise ValueError("resource dependency has no original approval")
    before = failure_binding(cycle,state)
    if client is None:
        from openai_codex import Codex,CodexConfig
        from .codex_accounts import runtime_home
        with Codex(CodexConfig(env={"CODEX_HOME":str(runtime_home())},
                               cwd=str(cycle / "workspace"))) as actual:
            return _resolve_with_client(owner,cycle,state,identity,budget,approval,before,reason,actual)
    return _resolve_with_client(owner,cycle,state,identity,budget,approval,before,reason,client)


def _resolve_with_client(owner,cycle,state,identity,budget,approval,before,reason,client):
    from .codex_sdk_compat import read_goal,goal_client,server_pid
    from .native_goal_recovery import ensure_no_foreign_goal_owner
    from .supervisor_repair_receipts import failure_binding

    origin = {"thread_id":identity["thread_id"],"objective_sha256":identity["objective_sha256"]}
    ensure_no_foreign_goal_owner({**origin,"workspace_path":str(cycle / "workspace")},
                                 current_server_pid=server_pid(client))
    actual = verify_existing_goal(read_goal(client,identity["thread_id"]),origin,previous=identity)
    if actual["status"] not in {"blocked","paused"}:
        raise ValueError("resolved dependency is no longer the original stopped Goal")
    route = (state.get("runtime_role_routes") or {}).get("investigator") or {}
    parameters = {}
    if route.get("model"):
        from .backend_config import ACCOUNT_PROVIDERS
        provider = route.get("provider")
        parameters = {"model":route["model"],"model_provider":
            "openai_https" if provider=="CodexHTTPS" else None if provider in ACCOUNT_PROVIDERS else provider}
    thread = client.thread_resume(identity["thread_id"],**parameters)
    if thread.id != identity["thread_id"]:
        raise ValueError("dependency resolution loaded a foreign thread")
    io = goal_client(thread)
    snapshot = io.thread_read(thread.id,include_turns=True).thread
    if (getattr(snapshot.status.root,"type",None) != "idle"
            or any(getattr(t.status,"value",t.status)=="inProgress" for t in snapshot.turns)):
        raise ValueError("blocked Goal still owns an active physical Turn")
    if failure_binding(cycle,json.loads((cycle / "research_state.json").read_text())) != before:
        raise ValueError("dependency failure changed during validation")
    if approved_review(cycle,thread.id,budget["allowance_tokens"]) != approval:
        raise ValueError("budget review changed during resolution")
    ensure_no_foreign_goal_owner({**origin,"workspace_path":str(cycle / "workspace")},
                                 current_server_pid=server_pid(client))
    actual = verify_existing_goal(io.get_goal(thread.id),origin,previous=actual)
    if actual["status"] not in {"blocked","paused"}:
        raise ValueError("resource dependency ownership changed before pause")
    io.pause_goal(thread.id)  # Status only: preserve original Goal and usage.
    paused = verify_existing_goal(io.get_goal(thread.id),origin,previous=actual)
    if paused["status"] != "paused":
        raise ValueError("original Goal did not acknowledge its operational pause")
    from .native_goal_stop import cancel_owned_goal
    operation = io.register_goal_operation(thread.id)
    try:
        stopped = cancel_owned_goal(io,operation,paused)
    finally:
        io.unregister_goal_operation(operation)
    queued = queue_review_notice(cycle,paused,allowance=budget["allowance_tokens"],
        observed=budget["observed_tokens"],frozen=frozen_science_digest(state["hypothesis_lock"]))
    receipt = {"schema":"theory-master/budget-dependency-resolution/v1","at":now(),
        "reason":reason,"failure_binding":before,"original_failure":state["recovery"],
        "original_native_goal":state["native_goal"],"approved_review":approval,
        "paused_original_goal":paused,"stop_confirmation":stopped,"guidance":queued,"token_grant":False,
        "scientific_verdict":None}
    write_json(cycle / "budget/dependency_resolution.json",receipt)
    owner._bind_cycle(cycle,create=False)
    state["budget_dependency_resolution"] = receipt
    state["native_goal"] = {**state["native_goal"],"status":"paused",
                            "runtime_failure_kind":None,"persistent_goal":paused}
    state.update(status="paused",phase="paused",execution_active=False,resumable=True,
                 next_executor="investigator")
    owner._emit(state,"budget.dependency_resolved",{"review":approval,"token_grant":False})
    owner._persist(state)
    return receipt
