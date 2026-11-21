"""Worker-owned automatic preservation after an SDK-authenticated 3+3 stop.

The scheduler may request preservation once, but only the existing validated
preserve_release path may call the archiver or free a scientific Cycle claim.
All recorded reminders are durable and shown in Important Events.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .observability.incident_registry import register_cycle_incident
from .runtime import now, write_json

_CODES = frozenset({
    "investigator_budget_review_rejected",
    "investigator_budget_supervisor_declined",
    "investigator_budget_cap_reached",
})

SINGLE_BUDGET_AUTHORITY = "sdk_budget_terminal"
_SINGLE_SCHEMA = "theory-master/single-budget-preservation/v1"
_SINGLE_REPLAY_SCHEMA = "theory-master/single-budget-preservation-replay/v1"
_STOPPED_GOALS = {"paused", "blocked", "complete", "budgetLimited", "usageLimited"}


def _single_receipt(cycle: Path) -> Path:
    return cycle / "preservation" / "sdk_budget_terminal.json"


def _single_replay_receipt(cycle: Path) -> Path:
    return cycle / "preservation" / "sdk_budget_cached_replay.json"


def _single_bytes(path: Path) -> bytes:
    from .native_goal import _safe_workspace_bytes

    return _safe_workspace_bytes(Path("/"), path.absolute().relative_to("/"), max_bytes=2_000_000)


def _single_json(path: Path) -> dict[str, Any]:
    value = json.loads(_single_bytes(path))
    if not isinstance(value, dict):
        raise ValueError("Preservation record is not a JSON object")
    return value


def _matching_managed_takeover(cycle: Path, runtime: dict, *, launch_id: str | None = None) -> bool:
    from .runtime_manager import process_identity_alive

    return bool(runtime.get("requested_operation") == "takeover"
                and runtime.get("takeover_cycle") == cycle.name
                and runtime.get("current_cycle") == cycle.name and runtime.get("launch_id")
                and (launch_id is None or runtime["launch_id"] == launch_id)
                and runtime.get("supervisor_pid") != runtime.get("runner_pid")
                and type(runtime.get("supervisor_start_ticks")) is int
                and process_identity_alive(runtime.get("supervisor_pid"), runtime["supervisor_start_ticks"]))


def _single_budget_proof(cycle: Path, state: dict, *, spec: Any,
                         launch_id: str | None = None) -> dict[str, Any]:
    from .goal_terminal import goal_sealed
    from .investigator_budget import BUDGET_POLICY_3_PLUS_3, LEDGER_REL, budget_status
    from .research_preservation import blocked_checkpoint
    from .runtime_manager import latest_cycle_name, load_registry, process_identity_alive, process_start_ticks
    from .science_graph_store import ScienceGraphStore
    from .scientific_objective import frozen_science_digest
    from .supervisor_intervention import supervisor_enabled
    from .worker_store import _live_cycle_claims, list_workers

    root = cycle.parent
    code = (state.get("attention") or {}).get("code")
    marker = {"policy": BUDGET_POLICY_3_PLUS_3, "code": code,
              "source": "sdk_authorized_budget_terminal", "cycle": cycle.name}
    if (not spec.task_mode or Path(spec.research_root) != root or latest_cycle_name(root) != cycle.name
            or not supervisor_enabled(root) or code not in _CODES or state.get("budget_auto_preserve") != marker
            or state.get("investigator_budget_policy") != BUDGET_POLICY_3_PLUS_3):
        raise ValueError("Current Cycle has no exact 3+3 SDK preservation authorization")
    control = cycle / "recovery_control.json"
    recovery = state.get("recovery") or {}
    if (control.exists() or control.is_symlink() or recovery.get("action") in {"pause", "interrupt", "kill", "abandon_cycle"}
            or state.get("abandoned_cycle") or state.get("next_executor") != "investigator"
            or state.get("active_invocation_id") or state.get("worker_id")
            or _live_cycle_claims(root) or any(worker.get("process_alive") for worker in list_workers(root))):
        raise ValueError("Cycle control, Worker ownership, or a later executor still applies")
    runtime = load_registry(root)
    managed = _matching_managed_takeover(cycle, runtime, launch_id=launch_id)
    runner = runtime.get("runner_pid")
    runner_alive = process_identity_alive(runner, runtime.get("runner_start_ticks"))
    own_runner = (managed and runner == os.getpid() and type(runtime.get("runner_start_ticks")) is int
                  and process_start_ticks(runner) == runtime["runner_start_ticks"])
    own_idle_supervisor = (launch_id is None and not runner_alive and runtime.get("current_cycle") == cycle.name
                           and runtime.get("supervisor_pid") == os.getpid()
                           and type(runtime.get("supervisor_start_ticks")) is int
                           and process_start_ticks(os.getpid()) == runtime["supervisor_start_ticks"])
    if ((launch_id is not None and not own_runner)
            or (runner_alive and not own_runner)
            or (process_identity_alive(runtime.get("supervisor_pid"), runtime.get("supervisor_start_ticks"))
                and not (managed or own_idle_supervisor))
            or process_identity_alive(state.get("owner_pid"))):
        raise ValueError("Another runtime owns the stopped Investigator checkpoint")

    if (cycle / "run_state.json").is_symlink():
        raise ValueError("Unsafe Investigator run projection")
    ledger = cycle / LEDGER_REL
    ledger_bytes = _single_bytes(ledger)
    rows = [json.loads(line) for line in ledger_bytes.splitlines() if line.strip()]
    if not rows or any(not isinstance(row, dict) or row.get("cycle_id") != cycle.name for row in rows):
        raise ValueError("Investigator budget ledger is bound to a different Cycle")
    run = blocked_checkpoint(cycle)
    lock = state.get("hypothesis_lock") or {}
    if ((run.get("attention") or {}).get("code") != code
            or (run.get("hypothesis_lock") or {}) != lock
            or str(run.get("cycle_id") or "") != cycle.name
            or Path(run.get("branch_canvas") or "") != Path(spec.branch_canvas)):
        raise ValueError("Budget terminal projection does not match frozen science")
    budget = budget_status(cycle, root, state)
    terminal_key = {"investigator_budget_supervisor_declined": "supervisor_declined",
                    "investigator_budget_cap_reached": "hard_cap_reached",
                    "investigator_budget_review_rejected": "rejected"}[code]
    if (budget.get("policy_version") != BUDGET_POLICY_3_PLUS_3 or budget.get(terminal_key) is not True
            or _single_bytes(ledger) != ledger_bytes
            or (terminal_key == "rejected" and rows[-1].get("decision") != "reject")):
        raise ValueError("Budget ledger does not prove the recorded terminal decision")

    native = state.get("native_goal") or {}
    stop = native.get("stop_confirmation") or {}
    identity = stop.get("goal_identity") or {}
    thread = (state.get("threads") or {}).get("investigator")
    if (native or thread) and (
            native.get("status") != "budgetLimited" or native.get("native_goal_stop_confirmed") is not True
            or stop.get("confirmed") is not True or stop.get("thread_status") != "idle"
            or stop.get("goal_status") not in {None, *_STOPPED_GOALS}
            or (stop.get("goal_status") is not None and identity.get("status") != stop["goal_status"])
            or not isinstance(thread, str) or not thread
            or any(row.get("thread_id") != thread for row in (native, stop, identity))
            or not isinstance(identity.get("created_at"), int)
            or not isinstance(identity.get("objective_sha256"), str)
            or len(identity["objective_sha256"]) != 64):
        raise ValueError("The Investigator Native Goal has no verified stopped identity")

    graph = Path(spec.graph_database)
    if not graph.is_file() or graph.is_symlink() or goal_sealed(root, graph, spec=spec):
        raise ValueError("Canonical Goal is unavailable or has already finished")
    with ScienceGraphStore(graph, readonly=True) as store:
        owner = store.db.execute("SELECT hypothesis_id FROM hypothesis_cycle_owners WHERE cycle_id=?",
                                 (cycle.name,)).fetchone()
        verdict = store.db.execute("SELECT 1 FROM cycle_evaluations WHERE cycle_id=?", (cycle.name,)).fetchone()
        if owner is None or owner["hypothesis_id"] != lock.get("id") or verdict is not None:
            raise ValueError("Canonical Cycle/H identity or scientific verdict forbids preservation")
    return {"cycle": cycle.name, "hypothesis_id": lock["id"], "code": code,
            "frozen_sha256": frozen_science_digest(lock),
            "ledger_sha256": hashlib.sha256(ledger_bytes).hexdigest(), "native_stop": stop}


def single_budget_preservation_preflight(cycle: Path, state: dict | None = None, *,
                                         spec: Any = None) -> dict | None:
    """Read-only classification of one exact single-task 3+3 stop."""
    clear_reason = "The existing 3+3 terminal authorization permits one archival Stage."
    try:
        current = _single_json(cycle / "research_state.json")
        code = (current.get("attention") or {}).get("code")
        if current.get("worker_id") or code not in _CODES or not isinstance(current.get("budget_auto_preserve"), dict):
            return None
        if state is not None and current != state:
            raise ValueError("The stopped Investigator checkpoint changed")
        receipt = _single_receipt(cycle)
        if (receipt.parent.is_symlink() or any(path.exists() or path.is_symlink() for path in (
                _single_replay_receipt(cycle), receipt.with_name("stage_output.json"),
                receipt.with_name("status.json"), receipt.with_name("closure.json")))):
            return {"status": "hold", "code": "sdk_budget_preservation_reconciliation_required",
                    "reason": "A prior preservation attempt requires reconciliation before any further archival work."}
        if spec is None:
            from .specification import TaskSpec
            spec = TaskSpec.from_task_root(cycle.parent)
        proof = _single_budget_proof(cycle, current, spec=spec)
        raw = receipt.with_name("last_model_output.txt")
        cached_only = receipt.exists() or receipt.is_symlink() or raw.exists() or raw.is_symlink()
        if cached_only:
            try:
                _single_cached_display_replay(cycle, proof)
            except (OSError, ValueError, RuntimeError, TypeError, KeyError):
                return {"status": "hold", "code": "sdk_budget_preservation_reconciliation_required",
                        "reason": "The original paid response is unavailable for a verified replay."}
            clear_reason = "The original paid response can be archived from a verified display-only replay."
        return {"status": "ready", "code": proof["code"], "reason": clear_reason, "cached_only": cached_only}
    except (OSError, ValueError, TypeError, RuntimeError, KeyError, AttributeError):
        return {"status": "hold", "code": "sdk_budget_preservation_not_admitted",
                "reason": "The exact budget stop, ownership, or one-shot authority could not be verified."}


def _single_cached_display_replay(cycle: Path, proof: dict) -> tuple[dict, dict[str, str]]:
    """Validate the paid archive and require that only its display text needs repair."""
    from .contract_stages import _preserve_research_structure
    from .invocations import stable_hash
    from .public_submission import strict_json
    from .research_preservation import normalize_preservation_display, verified_sources
    from .runtime_manager import process_identity_alive

    path = _single_receipt(cycle)
    paid_bytes = _single_bytes(path)
    paid = strict_json(paid_bytes)
    if (not isinstance(paid, dict) or paid.get("schema") != _SINGLE_SCHEMA
            or paid.get("status") != "requires_reconciliation" or paid.get("error_type") != "ValueError"
            or paid.get("proof") != proof
            or not isinstance(paid.get("launch_id"), str) or not paid["launch_id"]
            or type(paid.get("runner_pid")) is not int or type(paid.get("runner_start_ticks")) is not int
            or process_identity_alive(paid.get("runner_pid"), paid.get("runner_start_ticks"))):
        raise ValueError("Original paid preservation attempt is unavailable or still owned")
    raw = _single_bytes(path.with_name("last_model_output.txt"))
    original = strict_json(raw)
    if not isinstance(original, dict):
        raise ValueError("Cached preservation output is not an object")
    normalized = normalize_preservation_display(original)
    if normalized == original:
        raise ValueError("Cached preservation failure is outside the display-only replay")
    _preserve_research_structure(normalized, {})
    sources = verified_sources(cycle, normalized)
    return normalized, {"paid_receipt_sha256": hashlib.sha256(paid_bytes).hexdigest(),
                        "raw_output_sha256": hashlib.sha256(raw).hexdigest(),
                        "source_manifest_sha256": stable_hash(sources)}


def _require_idle_budget_goal(cycle: Path, stop: dict) -> None:
    """Re-read the retained operation without attaching, resuming, or sending a Turn."""
    if not stop:
        return  # The stopped Cycle never recorded a Native Goal.
    from openai_codex import Codex, CodexConfig
    from .codex_accounts import runtime_home
    from .codex_sdk_compat import read_goal, read_thread, server_pid
    from .native_goal_recovery import ensure_no_foreign_goal_owner, verify_existing_goal

    home = runtime_home().absolute()
    if home.is_symlink() or not (home / "config.toml").is_file():
        raise ValueError("Original Codex runtime is unavailable for read-only check")
    thread_id = stop["thread_id"]
    expected = stop["goal_identity"]
    workspace = str(cycle / "workspace")
    recorded_status = stop["goal_status"]
    with Codex(CodexConfig(env={"CODEX_HOME": str(home)}, cwd=workspace)) as client:
        before = read_goal(client, thread_id)
        thread = read_thread(client, thread_id, include_turns=True).thread
        status = getattr(getattr(getattr(thread, "status", None), "root", None), "type", None)
        if (thread.id != thread_id
                or not (status == "idle" or (status == "notLoaded" and recorded_status == "paused"))
                or any(getattr(turn.status, "value", turn.status) not in {"completed", "interrupted", "failed"}
                       for turn in thread.turns)):
            raise ValueError("The retained Investigator thread is not idle")
        after = read_goal(client, thread_id)
        ensure_no_foreign_goal_owner({"workspace_path": workspace, "thread_id": thread_id},
                                     current_server_pid=server_pid(client))
        if recorded_status is None and before is None and after is None:
            return
        if (recorded_status not in _STOPPED_GOALS or before is None or after is None
                or any(getattr(goal.status, "value", goal.status) != recorded_status for goal in (before, after))
                or verify_existing_goal(before, expected, previous=expected) != expected
                or verify_existing_goal(after, expected, previous=expected) != expected):
            raise ValueError("The retained Native Goal changed since the confirmed budget stop")


def require_single_budget_preservation_authorization(cycle: Path, hypothesis_id: str) -> None:
    """Recheck the same owned, durable one-shot receipt at each archive step."""
    from .research_preservation import PreservationGuard
    from .runtime_manager import process_start_ticks
    from .specification import TaskSpec

    try:
        path = _single_replay_receipt(cycle)
        replaying = path.exists() or path.is_symlink()
        receipt = _single_json(path if replaying else _single_receipt(cycle))
        launch_id = receipt.get("launch_id")
        if (receipt.get("schema") != (_SINGLE_REPLAY_SCHEMA if replaying else _SINGLE_SCHEMA)
                or receipt.get("status") != "started"
                or not isinstance(launch_id, str) or not launch_id
                or receipt.get("runner_pid") != os.getpid()
                or receipt.get("runner_start_ticks") != process_start_ticks(os.getpid())):
            raise ValueError("No started single-task budget preservation receipt for this owner")
        state = _single_json(cycle / "research_state.json")
        proof = _single_budget_proof(cycle, state, spec=TaskSpec.from_task_root(cycle.parent), launch_id=launch_id)
        if proof != receipt.get("proof") or proof["hypothesis_id"] != hypothesis_id:
            raise ValueError("Frozen budget preservation identity changed")
        if replaying and _single_cached_display_replay(cycle, proof)[1] != receipt.get("inputs"):
            raise ValueError("Cached preservation input changed after replay admission")
    except (OSError, ValueError, TypeError, RuntimeError, KeyError, AttributeError) as exc:
        raise PreservationGuard("No valid single-task SDK budget preservation authorization") from exc


def run_single_budget_preservation(workflow: Any, cycle: Path, *, launch_id: str) -> dict:
    """Archive a managed single-task 3+3 stop once, with no new research Cycle."""
    from .research_preservation import (PreservationGuard, finalize_preserved_cycle,
                                        preserve_blocked_cycle, run_preservation_stage)
    from .runtime_manager import process_start_ticks

    if not isinstance(launch_id, str) or not launch_id:
        raise PreservationGuard("A managed takeover launch identity is required")
    state = _single_json(cycle / "research_state.json")
    spec = workflow.spec
    decision = single_budget_preservation_preflight(cycle, state, spec=spec)
    if not decision or decision["status"] != "ready":
        raise PreservationGuard("Single-task SDK budget preservation is not ready")
    proof = _single_budget_proof(cycle, state, spec=spec, launch_id=launch_id)
    _require_idle_budget_goal(cycle, proof["native_stop"])
    state = _single_json(cycle / "research_state.json")
    if _single_budget_proof(cycle, state, spec=spec, launch_id=launch_id) != proof:
        raise PreservationGuard("Single-task budget authority changed during read-only verification")
    receipt_path = _single_receipt(cycle)
    cached, inputs = None, {}
    if decision["cached_only"]:
        cached, inputs = _single_cached_display_replay(cycle, proof)
        receipt_path = _single_replay_receipt(cycle)
    elif (raw := receipt_path.with_name("last_model_output.txt")).exists() or raw.is_symlink():
        raise PreservationGuard("Cached response arrived after new attempt admission")
    receipt_path.parent.mkdir(exist_ok=True)
    receipt = {"schema": _SINGLE_REPLAY_SCHEMA if cached is not None else _SINGLE_SCHEMA,
               "status": "started", "at": now(), "launch_id": launch_id,
               "runner_pid": os.getpid(), "runner_start_ticks": process_start_ticks(os.getpid()), "proof": proof,
               **({"inputs": inputs} if cached is not None else {})}
    try:
        fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError as exc:
        raise PreservationGuard("Single-task budget preservation was already started") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(receipt_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    try:
        if cached is None:
            workflow._bind_cycle(cycle, create=False)
            workflow._restore_resumed_cycle_routes(state)
            route = workflow.routes["investigator"]
            archived = run_preservation_stage(cycle_dir=cycle, canvas_path=spec.branch_canvas,
                                             backend=workflow.backend, model=route.model, provider=route.provider,
                                             authorization=SINGLE_BUDGET_AUTHORITY)
        else:
            archived = preserve_blocked_cycle(
                cycle_dir=cycle, canvas_path=spec.branch_canvas, stage_output=cached,
                authorization=SINGLE_BUDGET_AUTHORITY)
        closure = finalize_preserved_cycle(cycle_dir=cycle, authorization=SINGLE_BUDGET_AUTHORITY)
    except Exception as exc:
        write_json(receipt_path, {**receipt, "status": "requires_reconciliation", "error_type": type(exc).__name__})
        raise
    write_json(receipt_path, {**receipt, "status": "completed"})
    return {"status": "over", "preservation": archived, "closure": closure}


def budget_preservation_notice(root: Path, worker_id: str, cycle: str, *,
                               success: bool, detail: str = "",
                               hypothesis_id: str = "") -> dict[str, Any]:
    """Per-Cycle reminder, including failed preserves; no secrets or raw paths."""
    root = Path(root).resolve()
    folder = root / cycle
    if not cycle.isascii() or not cycle.isdigit() or not folder.is_dir():
        raise ValueError("invalid preservation Cycle")
    # The registry is idempotent by Cycle/code and remains readable after
    # the Worker starts a different Cycle.
    return register_cycle_incident(
        folder, severity="important",
        kind="budget_preservation",
        source_event="sdk.budget_preservation_" + ("completed" if success else "failed"),
        code="budget_preserved" if success else "budget_preservation_failed",
        discriminator=str(worker_id),
        summary=(f"{worker_id} · Cycle {cycle} "
                 + ("科研成果已保全，前沿已释放；请查看阶段成果与原因"
                    if success else "科研成果保全失败，原检查点与前沿归属已保留；请处理")
                 + (f"；{detail[:250]}" if detail else "")),
        fields={"worker_id": worker_id, "hypothesis_id": str(hypothesis_id)[:100],
                "persistent_reminder": True,
                "outcome": "preserved_released" if success else "failed_claim_retained"},
    )[0]


def _verified_legacy_budget_rejection(folder: Path, scientific: dict[str, Any]) -> bool:
    """Accept only an actual SDK Reviewer denial in an old frozen Cycle.

    Legacy work predates 3+3 auto-preserve markers; do not turn technical
    failures or ordinary need-attention into an authorization to archive.
    """
    from .investigator_budget import budget_status
    if (scientific.get("investigator_budget_policy") == "three_plus_three_v1"
            or scientific.get("phase") != "investigator:budget_denied"
            or (scientific.get("attention") or {}).get("code") !=
            "investigator_budget_review_rejected"):
        return False
    try:
        result = budget_status(folder, folder.parent, scientific)
    except (ValueError, OSError, TypeError):
        return False
    return result.get("policy_version") == "legacy" and result.get("rejected") is True


def _cached_preservation_replay_ready(folder: Path, worker_folder: Path,
                                      cycle: str) -> bool:
    """Permit ONE automatic replay only of validated previously paid output.

    Missing/ambiguous evidence never authorizes another model turn. The
    preserved response stays immutable; structural and source checks are
    performed before the one-shot process is launched.
    """
    from .contract_stages import _preserve_research_structure
    from .research_preservation import PreservationGuard, verified_sources, blocked_checkpoint

    progress = worker_folder / "preservation_result.json"
    cached = folder / "preservation" / "last_model_output.txt"
    closure = folder / "preservation" / "closure.json"
    if (not progress.is_file() or progress.is_symlink()
            or not cached.is_file() or cached.is_symlink()
            or closure.exists() or closure.is_symlink()):
        return False
    try:
        previous = json.loads(progress.read_text(encoding="utf-8"))
        if (previous.get("cycle") != cycle
                or previous.get("status") != "failed_preservation_retained_claim"):
            return False
        blocked_checkpoint(folder)  # no new budget approval or science turn
        output = json.loads(cached.read_text(encoding="utf-8"))
        if not isinstance(output, dict):
            return False
        _preserve_research_structure(output, {})
        verified_sources(folder, output)
        return True
    except (OSError, ValueError, TypeError, KeyError, PreservationGuard):
        return False


def _launch_cached_preservation_once(spec: Any, *, root: Path,
                                     worker_id: str, cycle: str,
                                     original_receipt: dict[str, Any]) -> bool:
    """Exactly one fenced retry of a paid, now verifiable archived response."""
    from .worker_runtime import worker_action, worker_dir
    folder = root / cycle
    work = worker_dir(root, worker_id)
    if (original_receipt.get("schema") !=
            "theory-master/automatic-budget-preservation/v1"
            or original_receipt.get("cycle") != cycle
            or original_receipt.get("worker_id") != worker_id
            or original_receipt.get("status") not in {"launched", "requested_once"}):
        return False
    if not _cached_preservation_replay_ready(folder, work, cycle):
        return False
    # Arm a persistent, content-bound NO-MODEL guard before starting a
    # separate process. If the cached file disappears between verification
    # and dispatch, the preserve stage must fail closed instead of calling
    # Investigator again.
    cached_path = folder / "preservation" / "last_model_output.txt"
    replay_guard = folder / "preservation" / "cached_replay_required.json"
    try:
        expected = hashlib.sha256(cached_path.read_bytes()).hexdigest()
        if replay_guard.exists() or replay_guard.is_symlink():
            existing = json.loads(replay_guard.read_text(encoding="utf-8"))
            if (replay_guard.is_symlink()
                    or existing.get("cycle") != cycle
                    or existing.get("sha256") != expected):
                return False
        else:
            write_json(replay_guard, {
                "schema": "theory-master/preservation-cached-replay/v1",
                "cycle": cycle, "sha256": expected, "at": now(),
            })
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    marker = work / f"budget_cached_replay_{cycle}.json"
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"schema": "theory-master/budget-cached-preservation-replay/v1",
                   "worker_id": worker_id, "cycle": cycle,
                   "status": "requested_once", "at": now()}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        result = worker_action(spec, worker_id, "preserve_release")
        write_json(marker, {
            "schema": "theory-master/budget-cached-preservation-replay/v1",
            "worker_id": worker_id, "cycle": cycle, "status": "launched",
            "result_status": result.get("status"), "at": now(),
        })
    except (ValueError, RuntimeError, OSError) as exc:
        write_json(marker, {
            "schema": "theory-master/budget-cached-preservation-replay/v1",
            "worker_id": worker_id, "cycle": cycle, "status": "blocked",
            "error_type": type(exc).__name__, "at": now(),
        })
        budget_preservation_notice(root, worker_id, cycle, success=False,
                                   detail="已缓存科研保全自动重放失败，需人工审查")
    return True


def dispatch_auto_budget_preservation(spec: Any) -> bool:
    """One-shot scheduler handoff; never resume science or guess a missing guard."""
    from .worker_runtime import (
        list_workers, read_worker, worker_dir, worker_action,
    )
    root = Path(spec.research_root).resolve()
    for item in list_workers(root):
        if item.get("status") != "NEEDS_ATTENTION" or item.get("process_alive"):
            continue
        wid = str(item.get("worker_id") or "")
        cycle = str(item.get("current_cycle") or "")
        if not cycle.isascii() or not cycle.isdigit():
            continue
        folder = root / cycle
        state_file = folder / "research_state.json"
        if state_file.is_symlink() or not state_file.is_file():
            continue
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        marker = state.get("budget_auto_preserve")
        code = str((state.get("attention") or {}).get("code") or "")
        authorized_3plus3 = (
            isinstance(marker, dict)
            and marker.get("policy") == "three_plus_three_v1"
            and state.get("investigator_budget_policy") == "three_plus_three_v1"
            and marker.get("source") == "sdk_authorized_budget_terminal"
            and marker.get("cycle") == cycle and marker.get("code") == code
        )
        authorized_legacy = (
            not isinstance(marker, dict)
            and _verified_legacy_budget_rejection(folder, state)
        )
        if not ((authorized_3plus3 or authorized_legacy)
                and code in _CODES and state.get("status") == "needs_attention"
                and state.get("next_executor") != "judge"
                and state.get("execution_active") is False
                and not state.get("scientific_verdict")):
            continue
        receipt = worker_dir(root, wid) / f"budget_auto_preserve_{cycle}.json"
        if receipt.exists():
            # A failed preservation may have a fully cached, hash-verifiable
            # single paid Stage output. Restarting the original guarded path
            # reuses that immutable output with no fresh model request.
            # Unknown/interrupted paid attempts remain blocked.
            recorded = {}
            if receipt.is_file() and not receipt.is_symlink():
                try:
                    recorded = json.loads(receipt.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            if isinstance(recorded, dict) and _launch_cached_preservation_once(
                    spec, root=root, worker_id=wid, cycle=cycle,
                    original_receipt=recorded):
                return True
            try:
                created = datetime.fromisoformat(
                    str(recorded.get("at") or "").replace("Z", "+00:00")
                ).astimezone(timezone.utc)
                if (recorded.get("status") == "requested_once"
                        and (datetime.now(timezone.utc) - created).total_seconds() > 300):
                    budget_preservation_notice(root, wid, cycle, success=False,
                                               detail="Preserve 启动状态未知，需核对后人工恢复")
            except (OSError, ValueError, AttributeError):
                pass
            continue
        try:
            from .research_preservation import blocked_checkpoint
            blocked_checkpoint(folder)  # validate before irreversible one-shot claim
            try:
                descriptor = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue  # another scheduler won the same Cycle
            # Exclusive durable claim before starting a paid preserve model.
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({
                    "schema": "theory-master/automatic-budget-preservation/v1",
                    "cycle": cycle, "worker_id": wid, "source_attention_code": code,
                    "at": now(), "status": "requested_once",
                }, handle)
                handle.flush()
                os.fsync(handle.fileno())
            result = worker_action(spec, wid, "preserve_release")
            write_json(receipt, {
                "schema": "theory-master/automatic-budget-preservation/v1",
                "cycle": cycle, "worker_id": wid, "source_attention_code": code,
                "at": now(), "status": "launched", "result_status": result.get("status"),
            })
        except (ValueError, RuntimeError, OSError) as exc:
            try:
                write_json(receipt, {
                    "schema": "theory-master/automatic-budget-preservation/v1",
                    "cycle": cycle, "worker_id": wid, "source_attention_code": code,
                    "at": now(), "status": "failed_closed", "error_type": type(exc).__name__,
                })
            except (OSError, ValueError):
                pass  # notice below still attempted when receipt write fails
            try:
                budget_preservation_notice(root, wid, cycle, success=False,
                                           detail=type(exc).__name__)
            except (OSError, ValueError):
                pass  # receipt remains durable even if the notice writer fails
        return True
    return False
