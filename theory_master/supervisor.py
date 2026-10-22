"""Protect one TheoryMaster Master-ID runner and resume it after unexpected child loss."""
from __future__ import annotations
import argparse
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from .runtime_manager import HEARTBEAT_INTERVAL_S, load_registry, owner_lock_path, process_identity_alive, process_start_ticks, update_registry, latest_cycle_name, _runtime_env, _repo_root, runtime_python
from .runtime import write_json
from .workflow import precheckpoint_recovery_allowed

MAX_CHILD_RECOVERIES = 3


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _runner_command(args: argparse.Namespace, operation: str) -> list[str]:
    cmd = [runtime_python(), "-m", "theory_master.runner", "--task-root", str(args.task_root.resolve()), "--operation", operation, "--identifier", str(args.identifier), "--managed-child"]
    if args.model: cmd += ["--model", args.model]
    if args.provider: cmd += ["--provider", args.provider]
    for route in args.route: cmd += ["--route", route]
    if args.cycle_audit is True: cmd.append("--cycle-audit")
    elif args.cycle_audit is False: cmd.append("--no-cycle-audit")
    if args.error_repair is True: cmd.append("--error-repair")
    elif args.error_repair is False: cmd.append("--no-error-repair")
    if args.attention_takeover is True: cmd.append("--attention-takeover")
    elif args.attention_takeover is False: cmd.append("--no-attention-takeover")
    if args.check_literature: cmd.append("--check-literature")
    return cmd


def _runtime_supervisor_receipt(root: Path) -> tuple[str, str] | None:
    """Return the authorized session and Cycle, not a repair-success claim."""
    cycle, state, _ = _latest_scientific_state(root)
    result = state.get("supervisor_runtime_review") or {}
    session_id = str(result.get("session_id") or "")
    if not cycle or not session_id.startswith("SUP-"):
        return None
    return cycle, session_id


def _verified_model_activity(cycle_dir: Path, started_at: str) -> str:
    """Check objective activity AFTER this managed launch; a PID alone fails."""
    from datetime import datetime
    from .supervisor_incident import _tail
    try:
        boundary = datetime.fromisoformat(started_at).timestamp()
    except ValueError:
        return ""
    for row in reversed(_tail(Path(cycle_dir) / "runtime_events.jsonl")):
        if row.get("event") not in {"LLM_MESSAGE", "TOOL_ACTIVITY"}:
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get("ts") or "")).timestamp()
        except ValueError:
            continue
        if stamp > boundary:
            return f"{row['event']} at {row['ts']} (thread {row.get('thread_id') or '?'})"
    return ""


def _record_runtime_verification(root: Path, receipt: tuple[str, str],
                                 *, verified: bool, detail: str) -> None:
    """Append-only status evidence; never mutate the live Runner's scientific state."""
    from .supervisor_intervention import append_supervisor_verification
    cycle, session_id = receipt
    append_supervisor_verification(
        root / cycle, session_id=session_id, verified=verified, detail=detail,
    )


def _heartbeat(root: Path, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_INTERVAL_S):
        update_registry(root, heartbeat_at=_stamp(), current_cycle=latest_cycle_name(root))


def _repair_handoff_started(root: Path) -> bool:
    cycle_name = latest_cycle_name(root)
    if not cycle_name:
        return False
    path = root / cycle_name / "run_state.json"
    if not path.is_file():
        return False
    try:
        import json
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    handoff = state.get("repair_handoff") if isinstance(state, dict) else None
    return isinstance(handoff, dict) and str(handoff.get("status") or "").lower() == "started"


def _latest_scientific_state(root: Path) -> tuple[str | None, dict, Path | None]:
    cycle_name = latest_cycle_name(root)
    if not cycle_name:
        return None, {}, None
    path = root / cycle_name / "research_state.json"
    if not path.is_file():
        return cycle_name, {}, path
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return cycle_name, {}, path
    return cycle_name, state if isinstance(state, dict) else {}, path


def _quota_pause_context(root: Path) -> dict | None:
    """Return persisted quota-pause provenance without changing scientific state."""
    cycle_name, state, _ = _latest_scientific_state(root)
    if not cycle_name or not state:
        return None
    recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else {}
    if str(state.get("status") or "").lower() != "paused":
        return None
    if recovery.get("code") != "provider_quota_exhausted":
        return None
    return {
        "cycle_name": cycle_name,
        "provider": recovery.get("provider"),
        "failed_account": recovery.get("codex_account"),
        "message": recovery.get("message"),
        "checkpoint": recovery.get("checkpoint") or state.get("last_safe_checkpoint"),
    }


def _quota_snapshots() -> dict[str, dict]:
    from .codex_accounts import account_rate_limits
    return {number: account_rate_limits(number) for number in ("1", "2")}


def _reconcile_exhausted(
    exhausted: dict[str, dict],
    snapshots: dict[str, dict],
    *,
    protected: set[str] | None = None,
) -> dict[str, dict]:
    from .codex_accounts import quota_available, quota_reset_at
    protected = protected or set()
    current = {str(key): dict(value) for key, value in exhausted.items() if isinstance(value, dict)}
    for number, snapshot in snapshots.items():
        available = quota_available(snapshot)
        if available is False:
            current[number] = {
                "observed_at": _stamp(),
                "resets_at": quota_reset_at(snapshot),
                "source": "rate_limit_probe",
            }
        elif available is True and number not in protected:
            current.pop(number, None)
    return current


def _candidate_account(
    snapshots: dict[str, dict],
    exhausted: dict[str, dict],
    *,
    failed_account: str | None,
) -> str | None:
    """Prefer proven available accounts; allow one unproven non-exhausted fallback."""
    from .codex_accounts import quota_available, selected_account
    current = selected_account()
    order = [number for number in (current, "1", "2") if number in {"1", "2"}]
    order = list(dict.fromkeys(order))
    candidates = [number for number in order if number != failed_account and number not in exhausted]
    for number in candidates:
        if quota_available(snapshots.get(number) or {}) is True:
            return number
    for number in candidates:
        snapshot = snapshots.get(number) or {}
        if quota_available(snapshot) is None and snapshot.get("error") != "auth_missing":
            return number
    return None


def _activate_quota_account(
    root: Path, account: str, snapshots: dict[str, dict], exhausted: dict[str, dict],
    *, provider_fallback_target: str | None = None,
) -> str:
    from .codex_accounts import account_switch_lock, quota_available, selected_account, use_account
    from .quota_auto_adjust import read_controller_state, write_controller_state

    with account_switch_lock():
        current = selected_account()
        # Another task may have switched the global selector after our probes.
        # If that freshly selected identity is not known exhausted, keep it
        # instead of ping-ponging the host-wide selector back again.
        if (
            current in {"1", "2"}
            and current != account
            and current not in exhausted
            and (
                quota_available(snapshots.get(current) or {}) is True
                if provider_fallback_target == "Codex"
                else quota_available(snapshots.get(current) or {}) is not False
            )
        ):
            account = current
        elif current != account:
            # A managed Runner is about to be recreated, so restarting the daily
            # Codex daemon is unnecessary and would disturb unrelated sessions.
            use_account(account, restart_app=False, _lock_held=True)
    write_controller_state(
        root,
        status="resuming",
        selected_account=account,
        selected_provider=provider_fallback_target or "Codex",
        quota_fallback_for_resume=provider_fallback_target,
        failed_account=None,
        exhausted_accounts=exhausted,
        accounts=snapshots,
        next_check_in_s=None,
    )
    update_registry(root, state="QUOTA_RECOVERING", heartbeat_at=_stamp())
    return account


def _notify_verified_quota_wait(
    root: Path, snapshots: dict[str, dict], exhausted: dict[str, dict],
    *, failed_account: str | None,
) -> None:
    """Send one email only when both Codex identities are genuinely exhausted.

    A failed quota probe is unknown, not proof of exhaustion. The account
    whose native Goal just returned usageLimited is independently proven
    exhausted even if its rate-limit probe fails. A durable cycle incident
    receipt keeps Supervisor polling/restarts from generating repeat mail.
    """
    from .codex_accounts import quota_available, quota_reset_at
    from .quota_auto_adjust import quota_auto_adjust_enabled, quota_poll_seconds

    if not quota_auto_adjust_enabled(root):
        return
    for account in ("1", "2"):
        availability = quota_available(snapshots.get(account) or {})
        if availability is False:
            continue
        # The exact failed account has a native provider error, not just a
        # remembered stale quota probe. Do not trust historical exhaustion
        # for the other identity when fresh verification is unavailable.
        origin = exhausted.get(account) or {}
        if not (availability is None and account == failed_account
                and origin.get("source") == "explicit_provider_error"):
            return

    future_resets: list[float] = []
    now_epoch = time.time()
    for account in ("1", "2"):
        raw = quota_reset_at(snapshots.get(account) or {})
        if raw is None:
            raw = (exhausted.get(account) or {}).get("resets_at")
        try:
            reset = float(raw)
        except (ValueError, TypeError):
            continue
        if 0 < reset < 10**11 and reset > now_epoch:
            future_resets.append(reset)
    next_reset_at = min(future_resets) if future_resets else None

    cycle_name, state, _path = _latest_scientific_state(root)
    if not cycle_name or not state:
        return
    recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else {}
    state = {**state, "run_dir": str(root / cycle_name),
             "cycle_dir": str(root / cycle_name), "cycle_id": cycle_name,
             "research_root": str(root)}
    payload = {
        "pause_kind": "provider_quota_exhausted",
        "quota_wait_confirmed": True,
        "error_kind": "ProviderQuotaExhausted",
        "stage": "provider_quota_exhausted",
        "role": state.get("current_agent") or state.get("active_role"),
        "error": recovery.get("message") or "Both Codex accounts exhausted",
        "checkpoint": recovery.get("checkpoint") or state.get("last_safe_checkpoint"),
        "next_reset_at": next_reset_at,
        "next_check_in_s": quota_poll_seconds(),
    }
    try:
        from .observability.email_notify import notify_cycle_terminal_once
        notify_cycle_terminal_once(
            root / cycle_name, event="run.paused", state=state, payload=payload,
        )
    except Exception as exc:
        print(f"TheoryMaster dual-quota email failed: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)


def _resolve_quota_pause(root: Path, context: dict) -> str | None:
    """Resolve a quota pause to an available Codex identity or wait."""
    from .codex_accounts import quota_available, quota_reset_at, selected_account
    from .quota_auto_adjust import read_controller_state, write_controller_state
    from .backend_config import ACCOUNT_PROVIDERS
    from .quota_provider_fallback import investigator_native_goal_in_flight

    provider = context.get("provider")
    _, scientific_state, _ = _latest_scientific_state(root)
    if provider not in {None, ""} and provider not in ACCOUNT_PROVIDERS:
        write_controller_state(root, status="unsupported_provider", provider=provider,
                               failed_account=context.get("failed_account"))
        return None
    else:
        bound_account = str(scientific_state.get("runtime_codex_account") or "")
        failed = str(bound_account or context.get("failed_account") or selected_account() or "")
        failed = failed if failed in {"1", "2"} else None

    prior = read_controller_state(root)
    exhausted = prior.get("exhausted_accounts") if isinstance(prior.get("exhausted_accounts"), dict) else {}
    exhausted = {str(key): dict(value) for key, value in exhausted.items() if isinstance(value, dict)}
    if failed:
        exhausted[failed] = {
            "observed_at": _stamp(),
            "resets_at": None,
            "source": "explicit_provider_error",
        }

    snapshots = _quota_snapshots()
    exhausted = _reconcile_exhausted(exhausted, snapshots, protected={failed} if failed else set())
    if failed:
        snap = snapshots.get(failed) or {}
        if quota_available(snap) is False:
            exhausted[failed]["resets_at"] = quota_reset_at(snap)

    candidate = _candidate_account(snapshots, exhausted, failed_account=failed)
    if candidate and not investigator_native_goal_in_flight(scientific_state):
        return _activate_quota_account(root, candidate, snapshots, exhausted)

    from .quota_auto_adjust import quota_poll_seconds
    write_controller_state(
        root,
        status="waiting_refresh",
        selected_account=selected_account(),
        selected_provider="Codex",
        quota_fallback_for_resume=None,
        failed_account=failed,
        exhausted_accounts=exhausted,
        accounts=snapshots,
        next_check_in_s=quota_poll_seconds(),
        provider_fallback_blocked=(
            "in_flight_investigator_native_goal"
            if investigator_native_goal_in_flight(scientific_state) else None
        ),
    )
    update_registry(root, state="WAITING_QUOTA", heartbeat_at=_stamp())
    _notify_verified_quota_wait(root, snapshots, exhausted, failed_account=failed)
    return None


def _wait_for_quota(root: Path) -> str | None:
    """Poll Codex quotas and resume only after Codex quota returns."""
    from .codex_accounts import quota_available, selected_account
    from .quota_auto_adjust import (
        quota_auto_adjust_enabled, quota_poll_seconds, read_controller_state, write_controller_state,
    )
    from .quota_provider_fallback import investigator_native_goal_in_flight

    poll = quota_poll_seconds()
    while quota_auto_adjust_enabled(root):
        context = _quota_pause_context(root)
        if context is None:
            return None
        snapshots = _quota_snapshots()
        prior = read_controller_state(root)
        exhausted = prior.get("exhausted_accounts") if isinstance(prior.get("exhausted_accounts"), dict) else {}
        exhausted = _reconcile_exhausted(exhausted, snapshots)
        _, scientific_state, _ = _latest_scientific_state(root)
        provider = context.get("provider")
        candidate = _candidate_account(snapshots, exhausted, failed_account=None)
        bound_account = str(
            scientific_state.get("runtime_codex_account")
            or context.get("failed_account") or prior.get("failed_account") or ""
        )
        if (candidate and investigator_native_goal_in_flight(scientific_state)
                and candidate != bound_account):
            candidate = None
        if candidate:
            if _quota_pause_context(root) is None:
                return None
            return _activate_quota_account(root, candidate, snapshots, exhausted)

        write_controller_state(
            root,
            status="waiting_refresh",
            selected_account=selected_account(),
            selected_provider="Codex",
            quota_fallback_for_resume=None,
            failed_account=prior.get("failed_account"),
            exhausted_accounts=exhausted,
            accounts=snapshots,
            next_check_in_s=poll,
            provider_fallback_blocked=(
                "in_flight_investigator_native_goal"
                if investigator_native_goal_in_flight(scientific_state) else None
            ),
        )
        update_registry(root, state="WAITING_QUOTA", heartbeat_at=_stamp())
        _notify_verified_quota_wait(
            root, snapshots, exhausted,
            failed_account=str(prior.get("failed_account") or "") or None,
        )
        time.sleep(poll)

    write_controller_state(root, status="disabled", next_check_in_s=None)
    return None


def _quota_auto_resume(root: Path) -> bool:
    """Handle a persisted quota PAUSED state; True means launch a resume child."""
    from .quota_auto_adjust import quota_auto_adjust_enabled, write_controller_state

    context = _quota_pause_context(root)
    if context is None:
        return False
    if not quota_auto_adjust_enabled(root):
        write_controller_state(root, status="disabled", failed_account=context.get("failed_account"))
        return False
    from .backend_config import ACCOUNT_PROVIDERS
    provider = context.get("provider")
    if provider not in {None, ""} and provider not in ACCOUNT_PROVIDERS:
        write_controller_state(root, status="unsupported_provider", provider=provider)
        return False
    account = _resolve_quota_pause(root, context)
    if account is None:
        account = _wait_for_quota(root)
    return account is not None and _quota_pause_context(root) is not None


def _quota_resume_started(root: Path) -> None:
    """Clear transient controller status once a resume child has been launched."""
    from .codex_accounts import selected_account
    from .quota_auto_adjust import read_controller_state, write_controller_state

    prior = read_controller_state(root)
    if prior.get("status") != "resuming":
        return
    write_controller_state(
        root,
        status="idle",
        selected_account=selected_account(),
        failed_account=None,
        next_check_in_s=None,
        last_resume_at=_stamp(),
        quota_fallback_for_resume=None,
    )


def _cycle_was_abandoned(root: Path) -> bool:
    """Only Kill/abandon blocks an explicit Resume, not ordinary Pause."""
    _, state, _ = _latest_scientific_state(root)
    return (str(state.get("status") or "").lower() == "cancelled"
            and bool(state.get("abandoned_cycle")))


def _exit_disposition(root: Path) -> str:
    """Classify what a new supervisor should do after its Runner disappears."""
    # Historical external repair handoff=started is never liveness authority.
    _, state, _ = _latest_scientific_state(root)
    if not state:
        return "unrecoverable"
    status = str(state.get("status") or "").lower()
    from .supervisor_recovery import operational_incident
    if operational_incident(state):
        return "failed"
    if status in {"over", "completed", "cancelled", "paused", "interrupted", "needs_attention"}:
        return "terminal"
    if status == "failed":
        return "failed"
    if status not in {"active", "running"}:
        return "unrecoverable"
    owner_pid = state.get("owner_pid")
    if owner_pid and process_identity_alive(owner_pid):
        return "runner_alive"
    if state.get("resumable") or state.get("last_safe_checkpoint") or state.get("role_outputs"):
        return "recoverable"
    if precheckpoint_recovery_allowed(state):
        return "recoverable"
    return "unrecoverable"


def _error_repair_enabled(explicit: bool | None, root: Path | None = None) -> bool:
    if explicit is not None:
        return bool(explicit)
    if root is not None:
        from .supervisor_intervention import supervisor_enabled
        return supervisor_enabled(root)
    raw = os.environ.get("THEORY_MASTER_ERROR_REPAIR")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _handle_failed_state(root: Path, *, repair_enabled: bool) -> bool:
    """Register persisted FAILED and provide the supervisor-level repair fallback."""
    cycle_name, state, _ = _latest_scientific_state(root)
    if not cycle_name or not state:
        return False
    cycle_dir = root / cycle_name
    from .supervisor_recovery import failure_details
    recovery = failure_details(state)
    error = str(recovery.get("message") or state.get("human_attention") or "Persisted TheoryMaster state is FAILED.")
    from .observability.incident_registry import register_cycle_incident
    incident, _ = register_cycle_incident(
        cycle_dir,
        severity="error",
        kind="system_failure",
        source_event="supervisor.failed_fallback",
        summary=error,
        code="run_failed",
        discriminator=f"{state.get('phase') or ''}:run_failed",
        fields={"phase": state.get("phase"), "error_type": recovery.get("kind")},
    )
    # Never consider a stale external repair handoff to own this Runner.
    if not repair_enabled:
        return False
    update_registry(root, state="SUPERVISING", heartbeat_at=_stamp())
    from .supervisor_continuity import review_and_repair_runtime_failure
    from .worker_runtime import list_workers
    gate = root / ".theory-master" / "scheduler.lock"
    gate.parent.mkdir(parents=True, exist_ok=True)
    with gate.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        if any(worker.get("process_alive") for worker in list_workers(root)):
            from .supervisor_recovery import defer_job
            defer_job(cycle_dir, state, dependency='Other Worker is still executing',
                      action='wait_for_safe_engineering_window')
            return False
        # Re-read after ownership acquisition; no stale failure may be repaired.
        current_cycle, current_state, _ = _latest_scientific_state(root)
        if current_cycle != cycle_name or current_state != state:
            return False
        try:
            backend = _supervisor_backend(root)
        except Exception:
            backend = None
        review = review_and_repair_runtime_failure(
            backend=backend, research_root=root, cycle_dir=cycle_dir, state=state,
            state_path=cycle_dir / "research_state.json", incident_id=str(incident["incident_id"]),
            scheduler_locked=True,
        )
    run_path = cycle_dir / "run_state.json"
    run_state: dict = {}
    if run_path.is_file():
        try:
            loaded = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            run_state = loaded
    run_state.update({
        "status": str(state.get("status") or "failed").upper(),
        "phase": state.get("phase") or "failed",
        "run_dir": str(cycle_dir),
        "cycle_dir": str(cycle_dir),
        "cycle_id": cycle_name,
        "run_id": cycle_name,
    })
    resume = review["action"] == "resume"
    run_state["repair_handoff"] = {
        "status": "supervisor_resume" if resume else "sdk_internal_hold",
        "target": "sdk:runtime_supervisor", "session_id": review.get("session_id"),
        "engineering_skill": review.get("engineering_skill"),
        "recovery_job": review.get("recovery_job"),
        "error": None if resume else
        str(review.get("reason") or "FAILED runtime requires verified repair: " + error)[:900],
    }
    write_json(run_path, run_state)
    return resume


def _recover_failed_state(root: Path, *, repair_enabled: bool, stop: threading.Event) -> bool:
    """Keep ownership while an unresolved operational incident waits/replans."""
    from .supervisor_recovery import stop_reason, due, projection, save_job
    while repair_enabled and not stop.is_set() and _error_repair_enabled(None, root):
        cycle_name, state, _ = _latest_scientific_state(root)
        if not cycle_name or stop_reason(state):
            if cycle_name:
                from .supervisor_recovery import read_job
                gate = root / '.theory-master/scheduler.lock'
                gate.parent.mkdir(parents=True, exist_ok=True)
                with gate.open('a+') as lock:
                    try:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        job = read_job(root / cycle_name)
                        if job:
                            job.update(status='stopped', owner_pid=None, current_action='respect_stop',
                                       next_wakeup_epoch=None, blocking_dependency=stop_reason(state))
                            save_job(root / cycle_name, job)
                    except BlockingIOError:
                        pass  # the active controller owns this transition
            return False
        cycle = root / cycle_name
        if due(cycle, state) and _handle_failed_state(root, repair_enabled=True):
            return True
        update_registry(root, state='RECOVERY_WAIT', heartbeat_at=_stamp(),
                        recovery_job=projection(cycle))
        # No scheduler lock held while waiting; operator controls remain live.
        stop.wait(8)
    return False


def _supervisor_backend(root: Path):
    """Runtime diagnostics do not depend on loading the failed scientific route."""
    from .backends import CodexBackend
    from .backend_config import RoleRoute
    from .supervisor_intervention import supervisor_model, DEFAULT_PROVIDER
    return CodexBackend(root, routes={
        'runtime_supervisor': RoleRoute(supervisor_model(root), DEFAULT_PROVIDER)})



def _notify_supervisor_terminal(root: Path) -> None:
    """Fallback notification when the Runner/Collector exited without delivery.

    Durable registry receipt ensures Collector and Supervisor don't double-send.
    Failure of SMTP must not alter the already stopped research state.
    """
    cycle_name, state, _ = _latest_scientific_state(root)
    if not cycle_name or not state:
        return
    status = str(state.get("status") or "").lower()
    if status not in {"needs_attention", "failed"}:
        return
    attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
    recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else {}
    event = "run.needs_attention" if status == "needs_attention" else "run.failed"
    payload = ({
        "attention_code": attention.get("code") or state.get("closure_reason") or "needs_attention",
        "reason": attention.get("reason") or state.get("human_attention") or "SDK cannot continue",
        "stage": state.get("phase"),
    } if status == "needs_attention" else {
        "error": recovery.get("message") or state.get("human_attention") or "Runner failed",
        "stage": state.get("phase"),
    })
    state = {**state, "run_dir": str(root / cycle_name), "cycle_dir": str(root / cycle_name),
             "cycle_id": cycle_name}
    try:
        from .observability.email_notify import notify_cycle_terminal_once
        notify_cycle_terminal_once(root / cycle_name, event=event, state=state, payload=payload)
    except Exception as exc:
        print(f"TheoryMaster terminal email could not be sent: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)


def _mark_unrecoverable(root: Path, *, retry_exhausted: bool = False) -> None:
    cycle_name, state, state_path = _latest_scientific_state(root)
    if not cycle_name or state_path is None or not state:
        return
    reason = ("Supervisor exceeded its bounded child recovery attempts; human runtime inspection is required."
              if retry_exhausted else
              "Runner stopped before a recoverable checkpoint and the persisted state is not safe for automatic replay.")
    code = "runtime_retry_exhausted" if retry_exhausted else "runtime_no_recoverable_checkpoint"
    resumable = bool(retry_exhausted and (state.get("last_safe_checkpoint") or state.get("role_outputs")))
    attention = {
        "code": code,
        "reason": reason,
        "requested_action": "Inspect the interrupted Cycle and decide whether to restart or repair it.",
        "resume_behavior": "Automatic resume is disabled until a safe recovery anchor exists.",
    }
    state.update({
        "status": "needs_attention",
        "phase": f"runtime_failure:{code}",
        "execution_active": False,
        "owner_pid": None,
        "resumable": resumable,
        "human_attention": reason,
        "attention": attention,
    })
    write_json(state_path, state)
    run_path = root / cycle_name / "run_state.json"
    run_state: dict = {}
    if run_path.is_file():
        try:
            loaded = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            run_state = loaded
    run_state.update({
        "status": "NEEDS_ATTENTION",
        "phase": state["phase"],
        "execution_active": False,
        "owner_pid": None,
        "resumable": resumable,
        "current_agent": None,
        "current_thread_id": None,
        "current_turn_id": None,
        "current_tool": None,
        "current_activity": None,
        "human_attention": reason,
        "attention": attention,
        "run_dir": str(root / cycle_name),
        "cycle_dir": str(root / cycle_name),
        "cycle_id": cycle_name,
        "run_id": cycle_name,
    })
    from .observability.incident_registry import register_cycle_incident
    register_cycle_incident(
        root / cycle_name,
        severity="attention",
        kind="needs_attention",
        source_event="supervisor.unrecoverable",
        summary=reason,
        code=attention["code"],
        discriminator=f"{state['phase']}:{attention['code']}",
        fields={"phase": state["phase"], "resumable": resumable, "next_executor": None},
    )
    from .attention_takeover import attention_takeover_enabled
    enabled = attention_takeover_enabled(root)
    run_state["attention_takeover_enabled"] = enabled
    if enabled:
        # A supervisor without a recoverable SDK runner/backend must never
        # spawn an external Codex repair agent or invent a successful recovery.
        run_state['attention_takeover_handoff'] = {
            'status': 'sdk_internal_hold', 'target': None,
            'error': 'No safe owning SDK runner; manual recovery required',
        }
    write_json(run_path, run_state)
    if enabled:
        try:
            from .supervisor_incident_session import publish_review_incident
            publish_review_incident(
                research_root=root, cycle_dir=root / cycle_name, state=state,
                trigger_type="needs_attention", trigger_event=code,
                analysis=None, model_invoked=False, system_note=reason, tool_reads=[],
                audit={"source": "supervisor.unrecoverable", "code": code},
            )
        except Exception as exc:
            print(f"TheoryMaster incident session was not opened: {type(exc).__name__}: {str(exc)[:200]}",
                  file=sys.stderr)
    _notify_supervisor_terminal(root)


def main(argv: list[str] | None = None) -> int:

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-root", type=Path, required=True); parser.add_argument("--operation", choices=["run", "resume"], required=True)
    parser.add_argument("--identifier", default="0"); parser.add_argument("--launch-id", required=True); parser.add_argument("--model"); parser.add_argument("--provider"); parser.add_argument("--route", action="append", default=[])
    parser.add_argument("--cycle-audit", dest="cycle_audit", action="store_true"); parser.add_argument("--no-cycle-audit", dest="cycle_audit", action="store_false")
    parser.add_argument("--error-repair", dest="error_repair", action="store_true"); parser.add_argument("--no-error-repair", dest="error_repair", action="store_false")
    parser.add_argument("--attention-takeover", dest="attention_takeover", action="store_true"); parser.add_argument("--no-attention-takeover", dest="attention_takeover", action="store_false")
    parser.add_argument("--check-literature", action="store_true"); parser.set_defaults(cycle_audit=None, error_repair=None, attention_takeover=None)
    args = parser.parse_args(argv); root = args.task_root.resolve()
    lock_path = owner_lock_path(root); lock_path.parent.mkdir(parents=True, exist_ok=True); lock = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("TheoryMaster supervisor refused duplicate owner: runner.lock is held", file=sys.stderr); return 0
    registry = load_registry(root)
    if registry.get("launch_id") != args.launch_id:
        print("TheoryMaster supervisor launch id is stale; refusing ownership", file=sys.stderr); return 0
    supervisor_attempt = int(registry.get("supervisor_attempt") or 0) + 1
    supervisor_pid = os.getpid()
    update_registry(root, state="STARTING", supervisor_pid=supervisor_pid, supervisor_start_ticks=process_start_ticks(supervisor_pid), supervisor_attempt=supervisor_attempt, heartbeat_at=_stamp())
    stop = threading.Event(); beat = threading.Thread(target=_heartbeat, args=(root, stop), name="theory-master-heartbeat", daemon=True); beat.start()
    operation = args.operation if supervisor_attempt == 1 else "resume"; recoveries = 0; log_path = root / ".theory-master" / "runner.log"
    pending_runtime_verification: tuple[str, str] | None = None
    try:
        while True:
            registry = load_registry(root); old_pid = registry.get("runner_pid"); old_ticks = registry.get("runner_start_ticks")
            if process_identity_alive(old_pid, old_ticks):
                update_registry(root, state="RUNNING", heartbeat_at=_stamp())
                while process_identity_alive(old_pid, old_ticks):
                    time.sleep(HEARTBEAT_INTERVAL_S)
                disposition = _exit_disposition(root)
                if disposition == "repair_handoff":
                    update_registry(root, state="REPAIR_HANDOFF", heartbeat_at=_stamp())
                    return 0
                if disposition == "terminal" and _quota_pause_context(root) is not None:
                    operation = "resume"
                elif disposition == "failed" and _recover_failed_state(root, repair_enabled=_error_repair_enabled(args.error_repair, root), stop=stop):
                    pending_runtime_verification = _runtime_supervisor_receipt(root)
                    update_registry(root, state="RECOVERING", heartbeat_at=_stamp())
                elif disposition in {"terminal", "failed"}:
                    update_registry(root, state="STOPPED" if disposition == "terminal" else "FAILED", heartbeat_at=_stamp())
                    _notify_supervisor_terminal(root)
                    return 0
                if disposition == "unrecoverable":
                    _mark_unrecoverable(root)
                    if _recover_failed_state(root, repair_enabled=_error_repair_enabled(args.error_repair, root), stop=stop):
                        pending_runtime_verification = _runtime_supervisor_receipt(root)
                        operation = "resume"
                        continue
                    _notify_supervisor_terminal(root)
                    update_registry(root, state="FAILED_RECOVERY", heartbeat_at=_stamp())
                    return 0
                operation = "resume"
            if _quota_pause_context(root) is not None:
                if _quota_auto_resume(root):
                    operation = "resume"
                else:
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                    return 0
            if operation == "resume" and _cycle_was_abandoned(root):
                # Ordinary PAUSED/INTERRUPTED must still Resume normally.
                update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                return 0
            child_env = _runtime_env()
            child_env.pop("THEORYMASTER_QUOTA_PROVIDER_FALLBACK", None)
            if operation == "resume":
                from .quota_auto_adjust import read_controller_state
                fallback_target = read_controller_state(root).get("quota_fallback_for_resume")
                if fallback_target == "Codex":
                    child_env["THEORYMASTER_QUOTA_PROVIDER_FALLBACK"] = fallback_target
            with open(log_path, "a", encoding="utf-8") as log:
                child = subprocess.Popen(_runner_command(args, operation), cwd=_repo_root(), env=child_env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                update_registry(root, state="RUNNING" if operation == args.operation and recoveries == 0 else "RECOVERING", runner_pid=child.pid, runner_start_ticks=process_start_ticks(child.pid), heartbeat_at=_stamp(), current_cycle=latest_cycle_name(root), recovery_count=recoveries)
                if operation == "resume":
                    _quota_resume_started(root)
                launch_at = _stamp()
                verification = pending_runtime_verification
                pending_runtime_verification = None
                while True:
                    try:
                        rc = child.wait(timeout=HEARTBEAT_INTERVAL_S)
                        break
                    except subprocess.TimeoutExpired:
                        if verification is not None:
                            evidence = _verified_model_activity(root / verification[0], launch_at)
                            if evidence and process_identity_alive(child.pid):
                                _record_runtime_verification(
                                    root, verification, verified=True, detail=evidence,
                                )
                                verification = None
                                update_registry(root, state="RUNNING", heartbeat_at=_stamp())
                if verification is not None:
                    _record_runtime_verification(
                        root, verification, verified=False,
                        detail=f"Runner exited (rc={rc}) before confirmed post-resume model activity.",
                    )
            update_registry(root, runner_pid=None, runner_start_ticks=None, last_runner_exit_code=rc, current_cycle=latest_cycle_name(root))
            if _quota_pause_context(root) is not None:
                operation = "resume"
                continue
            if rc == 0 and _exit_disposition(root) != "failed":
                _notify_supervisor_terminal(root)
                update_registry(root, state="STOPPED", heartbeat_at=_stamp()); return 0
            disposition = _exit_disposition(root)
            if disposition == "repair_handoff":
                update_registry(root, state="REPAIR_HANDOFF", heartbeat_at=_stamp())
                return 0
            if disposition == "failed" and _recover_failed_state(root, repair_enabled=_error_repair_enabled(args.error_repair, root), stop=stop):
                pending_runtime_verification = _runtime_supervisor_receipt(root)
                recoveries += 1
                operation = "resume"; update_registry(root, state="RECOVERING", recovery_count=recoveries, heartbeat_at=_stamp())
                continue
            if disposition in {"terminal", "failed"}:
                update_registry(root, state="STOPPED" if disposition == "terminal" else "FAILED", heartbeat_at=_stamp())
                _notify_supervisor_terminal(root)
                return 0
            if disposition == "unrecoverable":
                _mark_unrecoverable(root)
                if _recover_failed_state(root, repair_enabled=_error_repair_enabled(args.error_repair, root), stop=stop):
                    pending_runtime_verification = _runtime_supervisor_receipt(root)
                    operation = "resume"
                    continue
                _notify_supervisor_terminal(root)
                update_registry(root, state="FAILED_RECOVERY", heartbeat_at=_stamp())
                return 0
            recoveries += 1
            if recoveries > MAX_CHILD_RECOVERIES:
                _mark_unrecoverable(root, retry_exhausted=True)
                if _recover_failed_state(root, repair_enabled=_error_repair_enabled(args.error_repair, root), stop=stop):
                    pending_runtime_verification = _runtime_supervisor_receipt(root)
                    operation = "resume"
                    continue
                _notify_supervisor_terminal(root)
                update_registry(root, state="FAILED_RECOVERY", recovery_count=recoveries, heartbeat_at=_stamp()); return 0
            operation = "resume"; update_registry(root, state="RECOVERING", recovery_count=recoveries, heartbeat_at=_stamp()); time.sleep(2.0)
    finally:
        stop.set(); beat.join(timeout=1.0)
        try: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally: lock.close()

if __name__ == "__main__":
    raise SystemExit(main())
