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
from contextlib import nullcontext
from pathlib import Path
from .runtime_manager import HEARTBEAT_INTERVAL_S, load_registry, owner_lock_path, process_identity_alive, process_start_ticks, update_registry, latest_cycle_name, _runtime_env, _repo_root, runtime_python
from .runtime import write_json
from .goal_terminal import recovery_goal_blocker
from .workflow import precheckpoint_recovery_allowed

MAX_CHILD_RECOVERIES = 3


def _active_repair_fields(receipt: dict) -> dict:
    return {"boot_resume": receipt, "boot_resume_outcome": None, "requested_operation": "resume",
            "takeover_cycle": None, "boot_wait": None, "boot_recovery": None, "qualification_wait": None}


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
    if takeover_cycle := getattr(args, "takeover_expected_cycle", None):
        cmd.extend(["--supervisor-takeover-cycle", takeover_cycle])
    if retained_quota := getattr(args, "retained_quota", None):
        cmd.extend(["--retained-quota", json.dumps(retained_quota)])
    if boot_launch := getattr(args, "boot_resume_launch", None):
        cmd.extend(["--boot-resume-launch", boot_launch])
    if retry_launch := getattr(args, "goal_retry_launch", None):
        cmd.extend(["--goal-retry-launch", retry_launch])
    if repair_launch := getattr(args, "runtime_repair_resume_launch", None):
        cmd.extend(["--runtime-repair-resume-launch", repair_launch])
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
    # A restored pre-lock Cycle can complete an auxiliary Supervisor turn
    # without reopening the Investigator. The SDK records this turn in the
    # Supervisor session, outside the Runner's ordinary event collector.
    cycle = Path(cycle_dir)
    turns = {row.get("session_id"): row for row in _tail(cycle / "supervisor" / "session.jsonl")
             if row.get("kind") == "model_output" and row.get("code") == "no_qualified_candidate"
             and row.get("thread_id")}
    for row in reversed(_tail(cycle / "events.jsonl")):
        if row.get("type") != "supervisor.qualification_decided":
            continue
        decision = row.get("payload") or {}
        turn = turns.get(decision.get("session_id"))
        if not turn or decision.get("status") not in {"proposed", "waiting", "delivery_planned"}:
            continue
        try:
            responded = datetime.fromisoformat(str(turn.get("at") or "")).timestamp()
            decided = datetime.fromisoformat(str(row.get("at") or "")).timestamp()
        except ValueError:
            continue
        if boundary < responded <= decided:
            return (f"Supervisor qualification decision {decision['status']} at {row['at']} "
                    f"(thread {turn['thread_id']})")
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
        retained_goal_quota=None,
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
    if investigator_native_goal_in_flight(scientific_state):
        return None  # The wait path probes only the original Goal's private credential.
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
    if candidate:
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
        provider_fallback_blocked=None,
        retained_goal_quota=None,
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
    from .retained_goal_quota import prepare_retained_goal_home, retained_goal_binding
    from .codex_accounts import account_rate_limits

    poll = quota_poll_seconds()
    while quota_auto_adjust_enabled(root):
        if recovery_goal_blocker(root):
            return None
        context = _quota_pause_context(root)
        if context is None:
            return None
        cycle_name, scientific_state, _ = _latest_scientific_state(root)
        retained = investigator_native_goal_in_flight(scientific_state)
        snapshots = {} if retained else _quota_snapshots()
        prior = read_controller_state(root)
        exhausted = prior.get("exhausted_accounts") if isinstance(prior.get("exhausted_accounts"), dict) else {}
        if retained:
            binding = retained_goal_binding(root / cycle_name, scientific_state) if cycle_name else None
            if binding is not None:
                try:
                    home = prepare_retained_goal_home(root, binding, create=True)
                    snapshot = account_rate_limits(home.account, auth_path=home.auth_path)
                    snapshots[home.account] = snapshot
                    auth_sha256 = home.verify_auth()
                    latest_name, latest_state, _ = _latest_scientific_state(root)
                    if (quota_available(snapshot) is True
                            and latest_name == cycle_name and quota_auto_adjust_enabled(root)
                            and retained_goal_binding(root / cycle_name, latest_state) == binding):
                        approval = {"cycle": cycle_name, "binding": binding, "auth_sha256": auth_sha256,
                                    "sqlite_home": str(home.sqlite_home)}
                        write_controller_state(
                            root, status="resuming", selected_account=home.account,
                            selected_provider=binding["provider"], failed_account=None,
                            quota_fallback_for_resume=None, retained_goal_quota=approval,
                            exhausted_accounts=_reconcile_exhausted(exhausted, snapshots),
                            accounts=snapshots, next_check_in_s=None, provider_fallback_blocked=None,
                        )
                        update_registry(root, state="QUOTA_RECOVERING", heartbeat_at=_stamp())
                        return home.account
                except (OSError, ValueError, TypeError) as exc:
                    snapshots[binding["account"]] = {"ok": False, "error": type(exc).__name__}
        else:
            exhausted = _reconcile_exhausted(exhausted, snapshots)
            candidate = _candidate_account(snapshots, exhausted, failed_account=None)
            if candidate:
                latest_context = _quota_pause_context(root)
                if latest_context is None or latest_context.get("cycle_name") != cycle_name:
                    return None
                return _activate_quota_account(root, candidate, snapshots, exhausted)

        write_controller_state(
            root,
            status="waiting_refresh",
            selected_account=selected_account(),
            selected_provider="Codex",
            quota_fallback_for_resume=None,
            retained_goal_quota=None,
            failed_account=context.get("failed_account") or prior.get("failed_account"),
            exhausted_accounts=exhausted,
            accounts=snapshots,
            next_check_in_s=poll,
            provider_fallback_blocked=(
                "in_flight_investigator_native_goal"
                if retained else None
            ),
        )
        update_registry(root, state="WAITING_QUOTA", heartbeat_at=_stamp())
        _notify_verified_quota_wait(
            root, snapshots, exhausted,
            failed_account=str(prior.get("failed_account") or "") or None,
        )
        time.sleep(poll)

    write_controller_state(root, status="disabled", next_check_in_s=None, retained_goal_quota=None)
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
    bound = (prior.get("retained_goal_quota") or {}).get("binding") or {}
    write_controller_state(
        root,
        status="idle",
        selected_account=bound.get("account") or selected_account(),
        failed_account=None,
        next_check_in_s=None,
        last_resume_at=_stamp(),
        quota_fallback_for_resume=None,
        retained_goal_quota=None,
    )


def _retained_quota_attempt_interrupted(root: Path, launch_id: str) -> bool:
    pin = load_registry(root).get("retained_quota_attempt") or {}
    if pin.get("launch_id") != launch_id:
        return False
    name, state, _ = _latest_scientific_state(root)
    return (name == pin.get("cycle") and state.get("status") in {"active", "failed", "interrupted"}
            and _quota_pause_context(root) is None)


def _cycle_was_abandoned(root: Path) -> bool:
    """Only Kill/abandon blocks an explicit Resume, not ordinary Pause."""
    _, state, _ = _latest_scientific_state(root)
    return (str(state.get("status") or "").lower() == "cancelled"
            and bool(state.get("abandoned_cycle")))


def _exit_disposition(root: Path) -> str:
    """Classify what a new supervisor should do after its Runner disappears."""
    # Historical external repair handoff=started is never liveness authority.
    if recovery_goal_blocker(root):
        return "terminal"
    _, state, _ = _latest_scientific_state(root)
    if not state:
        return "unrecoverable"
    status = str(state.get("status") or "").lower()
    from .supervisor_recovery import operational_incident
    if operational_incident(state):
        return "failed"
    from .task_progression import local_replan_reason
    if local_replan_reason(state) == "no_qualified_candidate":
        return "terminal"
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
    if recovery_goal_blocker(root):
        return False
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
    update_registry(root, state="SUPERVISING", current_cycle=cycle_name, heartbeat_at=_stamp())
    from .supervisor_continuity import review_and_repair_runtime_failure
    from .supervisor_runtime_resume import dispatch_owner
    from .worker_store import list_workers
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
        control = cycle_dir / "recovery_control.json"
        if (current_cycle != cycle_name or current_state != state or recovery_goal_blocker(root)
                or control.exists() or control.is_symlink() or not _error_repair_enabled(None, root)):
            return False
        try:
            backend = _supervisor_backend(root)
        except Exception:
            backend = None
        dispatch = dispatch_owner(root, load_registry(root))
        review = review_and_repair_runtime_failure(
            backend=backend, research_root=root, cycle_dir=cycle_dir, state=state,
            state_path=cycle_dir / "research_state.json", incident_id=str(incident["incident_id"]),
            scheduler_locked=True,
            **({"dispatch_owner": dispatch} if dispatch else {}),
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
    if not repair_enabled:
        # Disabling automatic engineering must not launch a reviewer or run
        # another validator. An already validated, unused receipt can authorize
        # exactly one ordinary managed recovery under the same scheduler gate.
        return _consume_existing_repair_credit(root, stop=stop)
    from .supervisor_recovery import due, maintenance_stop, projection, save_job, stop_reason
    from .supervisor_boot_recovery import wait_receipt
    from .supervisor_runtime_resume import current_origin, unclaimed_in
    from .supervisor_runtime_wait import runtime_wait_evidence
    while repair_enabled and not stop.is_set() and _error_repair_enabled(None, root):
        cycle_name, state, _ = _latest_scientific_state(root)
        if cycle_name and maintenance_stop(root / cycle_name, state):
            return False
        blocked = recovery_goal_blocker(root) or stop_reason(state)
        if cycle_name:
            control = root / cycle_name / "recovery_control.json"
            if control.exists() or control.is_symlink():
                blocked = "operator_control_pending"
        if not cycle_name or blocked:
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
                                       next_wakeup_epoch=None, blocking_dependency=blocked)
                            save_job(root / cycle_name, job)
                    except BlockingIOError:
                        pass  # the active controller owns this transition
            return False
        cycle = root / cycle_name
        registry = load_registry(root)
        if unclaimed_in(state, registry):
            return current_origin(root, registry, registry.get("launch_id")) is not None
        if due(cycle, state) and _handle_failed_state(root, repair_enabled=True):
            registry = load_registry(root)
            if (registry.get("boot_wait") or {}).get("qualification_wait", {}).get("kind") == "runtime_recovery":
                update_registry(root, boot_wait=None, boot_recovery=None, qualification_wait=None)
            return True
        registry = load_registry(root)
        evidence = runtime_wait_evidence(root, cycle, state)
        receipt = None if evidence is None else wait_receipt(
            {**registry, "current_cycle": cycle_name},
            {**evidence, "launch_id": registry.get("launch_id"), "cycle": cycle_name},
        )
        update_registry(root, state='RECOVERY_WAIT', current_cycle=cycle_name, heartbeat_at=_stamp(),
                        recovery_job=projection(cycle), qualification_wait=None,
                        **({"boot_wait": receipt} if registry.get("boot_wait") != receipt else {}))
        # No scheduler lock held while waiting; operator controls remain live.
        stop.wait(8)
    return False


def _consume_existing_repair_credit(root: Path, *, stop: threading.Event) -> bool:
    from .runtime_supervisor_review import review_runtime_failure
    from .supervisor_recovery import maintenance_stop, stop_reason
    from .worker_store import list_workers
    if stop.is_set():
        return False
    gate = root / ".theory-master" / "scheduler.lock"
    gate.parent.mkdir(parents=True, exist_ok=True)
    with gate.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        cycle_name, state, _ = _latest_scientific_state(root)
        if (not cycle_name or recovery_goal_blocker(root) or stop_reason(state)
                or maintenance_stop(root / cycle_name, state) or stop.is_set()
                or any(worker.get("process_alive") for worker in list_workers(root))):
            return False
        result = review_runtime_failure(
            backend=None, research_root=root, cycle_dir=root / cycle_name, state=state,
            state_path=root / cycle_name / "research_state.json", incident_id="offline-validated-repair",
            scheduler_locked=True, existing_receipt_only=True,
        )
        return result["action"] == "resume"


def _supervisor_backend(root: Path):
    """Runtime diagnostics do not depend on loading the failed scientific route."""
    from .backends import CodexBackend
    from .backend_config import RoleRoute
    from .supervisor_intervention import supervisor_model, DEFAULT_PROVIDER
    from .task import TASK_CONFIG_NAME, TASK_DIR_NAME, TaskBinding

    binding = (TaskBinding.load(root) if (root / TASK_DIR_NAME / TASK_CONFIG_NAME).is_file() else None)
    return CodexBackend(root, routes={
        'runtime_supervisor': RoleRoute(supervisor_model(root), DEFAULT_PROVIDER)},
        task_config_path=binding.config_path if binding is not None else None)



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


def _matches_retained_dependency_wait(root: Path, registry: dict, target: tuple[Path, dict, str]) -> bool:
    """A restarted service may only wake the same closed dependency it owned."""
    from .public_submission import read_bounded_regular_file, strict_json
    from .supervisor_activation import (_qualification_wait_control, completed_delivery_attempts,
                                       input_support_target)

    wait = registry.get("qualification_wait")
    control = _qualification_wait_control(root)
    if control.exists() or control.is_symlink():
        try:
            request = strict_json(read_bounded_regular_file(control, 64_000))
        except (OSError, ValueError):
            return wait is None
        if not isinstance(request, dict):
            return wait is None
        if request.get("launch_id") == registry.get("launch_id") and request.get("action") == "run":
            return False

    if wait is None:
        return True
    cycle, state, route = target
    if (not isinstance(wait, dict) or registry.get("requested_operation") != "takeover"
            or registry.get("research_root") != str(root)
            or wait.get("launch_id") != registry.get("launch_id")
            or registry.get("takeover_cycle") != cycle.name or registry.get("current_cycle") != cycle.name
            or wait.get("cycle") != cycle.name):
        return False
    if wait.get("kind") == "input_support_unavailable":
        if route not in {"input_support_wait", "input_support"}:
            return False
        current = input_support_target(cycle, state)
        return bool(current and all(wait.get(key) and wait[key] == current.get(key)
                                    for key in ("dependency_identity", "invocation_id")))
    if wait.get("kind") == "runtime_recovery":
        from .supervisor_runtime_wait import runtime_wait_evidence

        if route != "runtime":
            return False
        current = runtime_wait_evidence(root, cycle, state)
        return current is not None and all(wait.get(key) == value for key, value in current.items())
    if wait.get("kind") is not None or route not in {"qualification_wait", "qualification", "goal_reassessment"}:
        return False
    review = state.get("supervisor_qualification")
    if session_id := wait.get("session_id"):
        return (isinstance(review, dict) and review.get("status") in {"waiting", "review_exhausted"}
                and session_id == review.get("session_id"))
    return bool(wait.get("delivery_revision") and any(
        receipt.get("delivery_revision") == wait["delivery_revision"]
        for receipt in completed_delivery_attempts(registry, cycle.name)
    ))


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
    parser.add_argument("--task-root", type=Path, required=True); parser.add_argument("--operation", choices=["run", "resume", "takeover"], required=True)
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
    from .supervisor_boot_recovery import (DURABLE_RUNNER_PROTOCOL, RUNTIME_REPAIR_ACTIVE_RESUME, RUNTIME_REPAIR_RESUME,
                                           claimed_runtime_resume, rebind_runtime_wait, verify_boot_resume, was_claimed_runtime)
    from .supervisor_runtime_resume import boot_origin, child_resume_lock, current_origin, launch_options, unclaimed_in
    from .runtime_admission import admission_lock, assert_no_worker_claims
    from .runtime_manager import host_boot_id
    receipt = registry.get("boot_wait") or {}
    retained_runtime = (isinstance(receipt, dict) and isinstance(receipt.get("qualification_wait"), dict)
                        and receipt["qualification_wait"].get("kind") == "runtime_recovery"
                        and receipt.get("boot_id") == host_boot_id()
                        and registry.get("state") in {"RECOVERY_WAIT", "STARTING"})
    repair_options = launch_options(model=args.model, provider=args.provider, routes=args.route,
                                    cycle_audit=args.cycle_audit, error_repair=args.error_repair,
                                    attention_takeover=args.attention_takeover, check_literature=args.check_literature)
    with admission_lock(root):
        # The service may start after LAUNCHING's grace period. Recheck actual
        # Claim ownership while runner.lock is held before publishing STARTING.
        assert_no_worker_claims(root)
        active_repair = {}
        pending_repair = registry.get("boot_resume") or {}
        first_boot_start = (pending_repair.get("kind") == RUNTIME_REPAIR_ACTIVE_RESUME
                            and registry.get("state") == "LAUNCHING" and not registry.get("supervisor_attempt")
                            and registry.get("supervisor_pid") is None)
        if (not first_boot_start and was_claimed_runtime(root, registry)
                and not process_identity_alive(registry.get("runner_pid"), registry.get("runner_start_ticks"))):
            plan = claimed_runtime_resume(root, registry, launch_id=args.launch_id)
            if plan is None or plan["origin"]["options"] != repair_options:
                update_registry(root, state="STOPPED", supervisor_pid=None, supervisor_start_ticks=None,
                                boot_resume_outcome="checkpoint_changed")
                return 0
            active_repair = _active_repair_fields(plan)
            args.operation, args.identifier = "resume", plan["cycle"]
        queued_runtime = not active_repair and retained_runtime and boot_origin(root, registry) is None
        rebound = rebind_runtime_wait(root, registry) if queued_runtime else None
        if queued_runtime and rebound is None:
            update_registry(root, state="STOPPED", supervisor_pid=None, supervisor_start_ticks=None,
                            qualification_wait=None)
            return 0
        update_registry(root, state="STARTING", supervisor_pid=os.getpid(),
                        supervisor_start_ticks=process_start_ticks(os.getpid()),
                        goal_recovery_blocker=None, runtime_repair_options=repair_options,
                        **(rebound or active_repair))
        if rebound is not None:
            args.identifier = str(registry["master_id"])
    retained_registry = {**registry, **(rebound or active_repair)}
    boot_resume = retained_registry.get("boot_resume")
    boot_runtime = isinstance(boot_resume, dict) and boot_resume.get("kind") == RUNTIME_REPAIR_RESUME
    if boot_runtime:
        boot_resume = None  # A repaired stop uses normal Resume preparation, not active checkpoint replay.
    boot_delivery = isinstance(boot_resume, dict) and boot_resume.get("kind") == "goal_reassessment"
    supervisor_attempt = int(registry.get("supervisor_attempt") or 0) + 1
    supervisor_pid = os.getpid()
    update_registry(root, state="STARTING", supervisor_pid=supervisor_pid, supervisor_start_ticks=process_start_ticks(supervisor_pid), supervisor_attempt=supervisor_attempt, heartbeat_at=_stamp())
    stop = threading.Event(); beat = threading.Thread(target=_heartbeat, args=(root, stop), name="theory-master-heartbeat", daemon=True); beat.start()
    operation = args.operation if supervisor_attempt == 1 else "resume"; recoveries = 0; log_path = root / ".theory-master" / "runner.log"
    pending_runtime_verification: tuple[str, str] | None = None
    advanced_input_support = False
    restored_dependency_wait = False
    goal_retry = None

    from .supervisor_activation import (completed_delivery_attempts, input_support_target, latest_recovery,
                                       same_delivery_attempt, takeover_runner_operation, wait_for_qualification_change)

    def stop_on_goal_authority() -> bool:
        blocker = recovery_goal_blocker(root)
        if not blocker:
            return False
        update_registry(root, state="STOPPED", heartbeat_at=_stamp(), supervisor_pid=None,
                        supervisor_start_ticks=None, goal_recovery_blocker=blocker,
                        recovery_job=None, qualification_wait=None)
        return True

    def recover_claimed_runtime() -> bool | None:
        nonlocal boot_resume, boot_runtime, operation

        with admission_lock(root):
            current = load_registry(root)
            if not was_claimed_runtime(root, current):
                return None
            assert_no_worker_claims(root)
            plan = claimed_runtime_resume(root, current, launch_id=args.launch_id, current_owner=True)
            if plan is None or plan["origin"]["options"] != repair_options:
                update_registry(root, state="STOPPED", boot_resume_outcome="checkpoint_changed", heartbeat_at=_stamp())
                return False
            update_registry(root, state="RECOVERING", heartbeat_at=_stamp(), **_active_repair_fields(plan))
        args.operation = operation = "resume"
        args.identifier = plan["cycle"]
        args.takeover_expected_cycle = None
        args.boot_resume_launch = args.launch_id
        boot_resume = plan
        boot_runtime = False
        return True

    def ready_takeover(target):
        if target[2] in {"input_support", "input_support_wait"}:
            update_registry(root, input_support_takeover_cycle=target[0].name)
        if target[2] not in {"qualification_wait", "input_support_wait"}:
            return target
        if not wait_for_qualification_change(root, target[0], args.launch_id, stop):
            return None
        ready = latest_recovery(root)
        routes = {"input_support"} if target[2] == "input_support_wait" else {"qualification", "goal_reassessment"}
        return ready if ready and ready[0] == target[0] and ready[2] in routes else None

    def terminal_takeover_operation(disposition: str) -> str | None:
        if (operation == "preserve" or disposition != "terminal"
                or not _error_repair_enabled(args.error_repair, root)):
            return None
        target = latest_recovery(root, include_wait=True)
        if target is None:
            return None
        qualification = (target[1].get("supervisor_qualification") or {}).get("status") in {
            "waiting", "review_exhausted"}
        if (target[2] not in {"budget_preserve", "goal_reassessment", "input_support", "input_support_wait"}
                and not (qualification and target[2] in {"qualification", "qualification_wait"})):
            return None
        args.operation = "takeover"
        update_registry(root, requested_operation="takeover", takeover_cycle=target[0].name)
        ready = ready_takeover(target)
        return takeover_runner_operation(ready[2]) if ready is not None else None

    def launching_input_support(registry):
        attempt = registry.get("input_support_attempt")
        if (isinstance(attempt, dict) and attempt.get("status") == "launching"
                and attempt.get("launch_id") == registry.get("launch_id") == args.launch_id
                and registry.get("requested_operation") == "takeover" and registry.get("takeover_cycle")
                and attempt.get("stopped_cycle") == registry["takeover_cycle"]):
            return attempt
        return None

    def record_input_support_exit(attempt, rc=None):
        nonlocal advanced_input_support
        from .specification import cycle_number

        if launching_input_support(load_registry(root)) != attempt:
            return False
        latest = latest_cycle_name(root)
        stopped_ordinal = cycle_number(attempt["stopped_cycle"])
        latest_ordinal = cycle_number(latest) if latest else None
        advanced = (latest_ordinal is not None and stopped_ordinal is not None
                    and latest_ordinal > stopped_ordinal)
        if advanced:
            status = "advanced"
        elif rc is None:
            status = "unconfirmed"
        else:
            status = "failed" if rc else "no_progress"
        receipt = {**attempt, "status": status}
        if rc is not None:
            receipt["runner_exit_code"] = rc
        released = {}
        if advanced:
            receipt["advanced_cycle"] = latest
            released = {"requested_operation": "resume", "takeover_cycle": None,
                        "input_support_takeover_cycle": None, "current_cycle": latest}
            args.operation = "resume"
            args.takeover_expected_cycle = None
            advanced_input_support = True
        update_registry(root, runner_pid=None, runner_start_ticks=None, input_support_attempt=receipt, **released)
        return advanced

    def input_support_resume_allowed():
        cycle, state, _ = _latest_scientific_state(root)
        if not cycle or recovery_goal_blocker(root):
            return False
        control = root / cycle / "recovery_control.json"
        recovery = state.get("recovery") or {}
        return not (control.exists() or control.is_symlink() or state.get("abandoned_cycle")
                    or recovery.get("action") in {"pause", "interrupt", "kill", "abandon_cycle"})

    def record_delivery_completion(attempt, status, rc=None):
        completed = {**attempt, "status": status}
        if status in {"unconfirmed", "failed"}:
            from .supervisor_goal_reassessment import recorded_reassessment

            if recorded := recorded_reassessment(root, attempt):
                completed.update(recorded, status="finished")
        if rc is not None:
            completed["runner_exit_code"] = rc
        history = completed_delivery_attempts(load_registry(root), attempt["stopped_cycle"])
        history = [row for row in history if not same_delivery_attempt(attempt, row)]
        update_registry(root, runner_pid=None, runner_start_ticks=None, runner_operation=None,
                        delivery_reassessment_attempt=completed,
                        delivery_reassessment_history=[*history, completed])

    def prepare_goal_retry() -> bool:
        nonlocal goal_retry
        from .supervisor_reassessment_retry import goal_retry_plan, verify_goal_retry

        if (boot_resume is not None or stop.is_set()
                or not _error_repair_enabled(args.error_repair, root)):
            return False
        goal_retry = verify_goal_retry(root, args.launch_id)
        if goal_retry is None:
            goal_retry = goal_retry_plan(root, args.launch_id)
            if goal_retry is None:
                return False
            update_registry(root, goal_reassessment_retry=goal_retry)
        args.goal_retry_launch = args.launch_id
        return True

    def delivery_followup(attempt):
        if not _error_repair_enabled(None, root):
            return None
        target = latest_recovery(root, include_wait=True)
        if target is None or target[0].name != attempt["stopped_cycle"]:
            return None
        review = target[1].get("supervisor_qualification") or {}
        if (target[2] not in {"qualification_wait", "goal_reassessment"}
                and not (target[2] == "qualification" and isinstance(review, dict)
                         and review.get("status") in {"waiting", "review_exhausted"})):
            return None
        ready = ready_takeover(target)
        return takeover_runner_operation(ready[2]) if ready is not None else None

    try:
        # A restarted owner must honor the persisted failure before its first
        # Runner launch, just as it does after a child exits. Reloading Python
        # modules alone is neither a repair receipt nor permission to retry.
        registry = load_registry(root)
        _, initial_state, _ = _latest_scientific_state(root)
        from .supervisor_recovery import operational_incident
        runner_alive = process_identity_alive(registry.get("runner_pid"), registry.get("runner_start_ticks"))
        previous_delivery = registry.get("delivery_reassessment_attempt") or {}
        previous_input_support = registry.get("input_support_attempt") or {}
        takeover_cycle = registry.get("takeover_cycle")
        initial_runtime = current_origin(root, registry, args.launch_id) if not runner_alive else None
        if boot_runtime:
            if not runner_alive and (initial_runtime is None or args.operation != "takeover"
                                     or args.identifier != str(registry.get("master_id"))):
                update_registry(root, state="STOPPED", heartbeat_at=_stamp(), boot_resume_outcome="checkpoint_changed")
                return 0
            operation = "resume"
        elif boot_resume is not None:
            if not runner_alive:
                ticket = (verify_boot_resume(root, args.launch_id, allow_lost_delivery_watch=True)
                          if boot_delivery else verify_boot_resume(root, args.launch_id))
                if boot_delivery:
                    command_matches = args.operation == "takeover" and args.identifier == str(registry.get("master_id"))
                else:
                    command_matches = args.operation == "resume" and ticket is not None and args.identifier == ticket["cycle"]
                if ticket is None or not command_matches:
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp(),
                                    boot_resume_outcome="checkpoint_changed")
                    return 0
                if boot_delivery and ticket["origin"].get("recovery_pin") is None:
                    # Restore the stopped dependency's owner. The original
                    # attempted revision will be recorded as unconfirmed.
                    status = ticket["origin"]["delivery_attempt"].get("status")
                    update_registry(root, boot_resume=None,
                                    boot_resume_outcome="delivery_unconfirmed" if status == "launching"
                                    else "delivery_wait_restored")
                    boot_resume = None
                    boot_delivery = False
                else:
                    args.boot_resume_launch = args.launch_id
                    operation = "reassess" if boot_delivery else "resume"
        elif (previous_input_support.get("status") == "advanced"
                and previous_input_support.get("launch_id") == args.launch_id
                and registry.get("requested_operation") == "resume" and takeover_cycle is None):
            args.operation = operation = "resume"
            advanced_input_support = True
        elif registry.get("requested_operation") == "takeover":
            # The service command keeps its original Run or Resume after an
            # in-place transition to a Cycle-bound recovery episode.
            args.operation = "takeover"
            restored_dependency_wait = retained_registry.get("qualification_wait") is not None
        if args.operation == "takeover" and not runner_alive and not boot_delivery and initial_runtime is None:
            if (previous_delivery.get("status") == "launching" and previous_delivery.get("stopped_cycle")
                    and previous_delivery["stopped_cycle"] == registry.get("takeover_cycle")):
                if prepare_goal_retry():
                    operation = "reassess"
                else:
                    record_delivery_completion(previous_delivery, "unconfirmed")
            if attempt := launching_input_support(registry):
                if record_input_support_exit(attempt):
                    operation = "resume"
        if not runner_alive and stop_on_goal_authority():
            return 0
        if (args.operation == "takeover" and not runner_alive and not boot_delivery
                and goal_retry is None and initial_runtime is None):
            target = latest_recovery(root, include_wait=True) if _error_repair_enabled(None, root) else None
            if (target is not None and target[0].name == registry.get("takeover_cycle")
                    and _matches_retained_dependency_wait(root, retained_registry, target)):
                target = ready_takeover(target)
            else:
                target = None
            if target is None:
                if not stop_on_goal_authority():
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp(),
                                    supervisor_pid=None, supervisor_start_ticks=None, qualification_wait=None,
                                    **({"takeover_cycle": None} if restored_dependency_wait else {}))
                return 0
            operation = takeover_runner_operation(target[2])
        if not runner_alive and _retained_quota_attempt_interrupted(root, args.launch_id):
            update_registry(root, state="STOPPED", heartbeat_at=_stamp())
            return 0
        if advanced_input_support and not runner_alive:
            disposition = _exit_disposition(root)
            if disposition == "terminal" and _quota_pause_context(root) is None:
                if next_operation := terminal_takeover_operation(disposition):
                    operation = next_operation
                else:
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                    return 0
            elif disposition not in {"terminal", "recoverable", "failed"}:
                update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                return 0
        if not runner_alive and operational_incident(initial_state):
            if initial_runtime is not None:
                pending_runtime_verification = (initial_runtime["cycle"], initial_runtime["authorization"]["session_id"])
            elif unclaimed_in(initial_state, registry) or not _recover_failed_state(
                    root, repair_enabled=_error_repair_enabled(args.error_repair, root), stop=stop):
                if stop_on_goal_authority():
                    return 0
                update_registry(root, state="FAILED", heartbeat_at=_stamp())
                _notify_supervisor_terminal(root)
                return 0
            else:
                pending_runtime_verification = _runtime_supervisor_receipt(root)
            if (retained_registry.get("qualification_wait") or {}).get("kind") == "runtime_recovery":
                restored_dependency_wait = False
            operation = "resume"
            update_registry(root, state="RECOVERING", heartbeat_at=_stamp())
        while True:
            registry = load_registry(root); old_pid = registry.get("runner_pid"); old_ticks = registry.get("runner_start_ticks")
            if process_identity_alive(old_pid, old_ticks):
                update_registry(root, state="RUNNING", heartbeat_at=_stamp())
                while process_identity_alive(old_pid, old_ticks):
                    time.sleep(HEARTBEAT_INTERVAL_S)
                if input_attempt := launching_input_support(registry):
                    if not record_input_support_exit(input_attempt):
                        if next_operation := terminal_takeover_operation("terminal"):
                            operation = next_operation
                            continue
                        update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                        return 0
                reassessment = registry.get("delivery_reassessment_attempt") or {}
                if reassessment.get("launch_id") == args.launch_id and reassessment.get("status") == "launching":
                    if prepare_goal_retry():
                        operation = "reassess"
                        continue
                    record_delivery_completion(reassessment, "unconfirmed")
                    if boot_delivery:
                        update_registry(root, boot_resume=None, boot_resume_outcome="delivery_unconfirmed")
                        args.boot_resume_launch = None
                        boot_resume = None
                        boot_delivery = False
                    if stop_on_goal_authority():
                        return 0
                    if next_operation := delivery_followup(reassessment):
                        operation = next_operation
                        continue
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                    return 0
                if stop_on_goal_authority():
                    return 0
                claimed_recovery = recover_claimed_runtime()
                if claimed_recovery is not None:
                    if claimed_recovery:
                        continue
                    return 0
                if boot_resume is not None and boot_resume.get("kind") == RUNTIME_REPAIR_ACTIVE_RESUME:
                    args.boot_resume_launch = None
                    args.operation, args.identifier = "resume", str(boot_resume["origin"]["master_id"])
                    boot_resume = None
                    update_registry(root, boot_resume=None, boot_resume_outcome="retained_runner_exited",
                                    runner_pid=None, runner_start_ticks=None, runner_operation=None)
                elif boot_resume is not None:
                    # A replacement Supervisor monitored the admitted child.
                    # Its exit cannot authorize another generic Task Resume.
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp(),
                                    runner_pid=None, runner_start_ticks=None, runner_operation=None,
                                    boot_resume_outcome="retained_runner_exited")
                    return 0
                if _retained_quota_attempt_interrupted(root, args.launch_id):
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                    return 0
                disposition = _exit_disposition(root)
                if disposition == "repair_handoff":
                    update_registry(root, state="REPAIR_HANDOFF", heartbeat_at=_stamp())
                    return 0
                if disposition == "terminal" and _quota_pause_context(root) is not None:
                    operation = "resume"
                elif disposition == "failed" and _recover_failed_state(root, repair_enabled=_error_repair_enabled(args.error_repair, root), stop=stop):
                    pending_runtime_verification = _runtime_supervisor_receipt(root)
                    update_registry(root, state="RECOVERING", heartbeat_at=_stamp())
                elif next_operation := terminal_takeover_operation(disposition):
                    operation = next_operation
                    continue
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
            if stop_on_goal_authority():
                return 0
            args.retained_quota = None
            quota_context = _quota_pause_context(root)
            if quota_context is not None:
                if _quota_auto_resume(root):
                    operation = "resume"
                    from .quota_auto_adjust import read_controller_state
                    from .quota_provider_fallback import investigator_native_goal_in_flight
                    from .retained_goal_quota import verify_retained_quota_launch
                    _, quota_state, _ = _latest_scientific_state(root)
                    approval = read_controller_state(root).get("retained_goal_quota")
                    if approval is not None or investigator_native_goal_in_flight(quota_state):
                        try:
                            if not isinstance(approval, dict) or approval.get("cycle") != quota_context.get("cycle_name"):
                                raise ValueError("No verified retained quota authorization for this Cycle")
                            verify_retained_quota_launch(root, approval or {})
                        except (OSError, ValueError, TypeError):
                            update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                            return 0
                        args.retained_quota = approval
                else:
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                    return 0
            if operation == "resume" and _cycle_was_abandoned(root):
                # Ordinary PAUSED/INTERRUPTED must still Resume normally.
                update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                return 0
            if operation == "resume" and advanced_input_support and not input_support_resume_allowed():
                update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                return 0
            args.runtime_repair_resume_launch = None
            repair_dispatch = None
            active_repair_dispatch = bool(boot_resume and boot_resume.get("kind") == RUNTIME_REPAIR_ACTIVE_RESUME)
            if operation == "resume" and pending_runtime_verification is not None and args.retained_quota is None:
                registry = load_registry(root)
                repair_dispatch = current_origin(root, registry, args.launch_id)
                if repair_dispatch is None and unclaimed_in(_latest_scientific_state(root)[1], registry):
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp(), boot_resume_outcome="checkpoint_changed")
                    return 0
            input_attempt = None
            original_identifier = args.identifier
            if repair_dispatch is not None:
                args.runtime_repair_resume_launch = args.launch_id
                args.takeover_expected_cycle = repair_dispatch["cycle"]
                args.identifier = str(repair_dispatch["master_id"])
            elif boot_delivery:
                if operation != "reassess" or verify_boot_resume(root, args.launch_id) is None:
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp(),
                                    boot_resume_outcome="checkpoint_changed")
                    return 0
                args.takeover_expected_cycle = boot_resume["cycle"]
            elif goal_retry is not None:
                from .supervisor_reassessment_retry import verify_goal_retry

                if operation != "reassess" or verify_goal_retry(root, args.launch_id) != goal_retry:
                    record_delivery_completion(goal_retry["attempt"], "unconfirmed")
                    args.goal_retry_launch = None
                    lost_attempt = goal_retry["attempt"]
                    goal_retry = None
                    if next_operation := delivery_followup(lost_attempt):
                        operation = next_operation
                        continue
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                    return 0
                args.takeover_expected_cycle = goal_retry["attempt"]["stopped_cycle"]
            elif args.operation == "takeover":
                target = latest_recovery(root) if _error_repair_enabled(None, root) else None
                if (target is None or takeover_runner_operation(target[2]) != operation
                        or (operation in {"run", "preserve", "reassess"}
                            and target[0].name != registry.get("takeover_cycle"))
                        or (restored_dependency_wait
                            and not _matches_retained_dependency_wait(root, retained_registry, target))):
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp(), qualification_wait=None,
                                    **({"takeover_cycle": None} if restored_dependency_wait else {}))
                    return 0
                args.takeover_expected_cycle = target[0].name
                if target[2] == "input_support":
                    plan = input_support_target(target[0], target[1], registry=registry)
                    if not plan or plan["route"] != "input_support":
                        update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                        return 0
                    prior = registry.get("input_support_attempt") or {}
                    same_support = (prior.get("stopped_cycle") == target[0].name
                                    and prior.get("dependency_identity") == plan["dependency_identity"])
                    attempted = set(prior.get("attempted_input_ids", [])) if same_support else set()
                    added = set(plan["added_input_ids"])
                    if not added - attempted:
                        update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                        return 0
                    input_attempt = {"launch_id": args.launch_id, "stopped_cycle": target[0].name,
                                     "dependency_identity": plan["dependency_identity"],
                                     "wake_identity": plan["wake_identity"], "status": "launching",
                                     "attempted_input_ids": sorted(attempted | added)}
                    update_registry(root, input_support_attempt=input_attempt)
            reassessment = None
            if operation == "reassess":
                from .specification import TaskSpec
                from .supervisor_goal_reassessment import delivery_reassessment_plan, reassessment_launch_identity

                plan = delivery_reassessment_plan(TaskSpec.from_task_root(root))
                if not plan or plan["stopped_cycle"] != args.takeover_expected_cycle:
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                    return 0
                identity = reassessment_launch_identity(plan, args.launch_id)
                prior = registry.get("delivery_reassessment_attempt") or {}
                if boot_delivery:
                    expected_attempt = boot_resume["origin"]["delivery_attempt"]
                    same_boot_attempt = {**expected_attempt, "launch_id": args.launch_id}
                    history = registry.get("delivery_reassessment_history") or []
                    blocked = (prior not in (expected_attempt, same_boot_attempt)
                               or not isinstance(history, list)
                               or any(same_delivery_attempt(plan, row) for row in history))
                elif goal_retry is not None:
                    blocked = prior != goal_retry["attempt"] or prior != {**identity, "status": "launching"}
                else:
                    history = completed_delivery_attempts(registry, plan["stopped_cycle"])
                    blocked = any(same_delivery_attempt(plan, entry) for entry in [prior, *history])
                if blocked:
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                    return 0
                reassessment = {**identity, "status": "launching"}
                retry_fields = ({"goal_reassessment_retry": {**goal_retry, "status": "dispatching"},
                                 "runner_pid": None, "runner_start_ticks": None} if goal_retry is not None else {})
                update_registry(root, state="RECOVERING", delivery_reassessment_attempt=reassessment,
                                runner_operation="reassess", runner_checkpoint_protocol=DURABLE_RUNNER_PROTOCOL,
                                **retry_fields)
            child_env = _runtime_env()
            child_env.pop("THEORYMASTER_QUOTA_PROVIDER_FALLBACK", None)
            if boot_resume is not None and verify_boot_resume(root, args.launch_id) is None:
                update_registry(root, state="STOPPED", heartbeat_at=_stamp(),
                                boot_resume_outcome="checkpoint_changed")
                return 0
            if operation == "resume" and boot_resume is None and repair_dispatch is None:
                from .quota_auto_adjust import read_controller_state
                fallback_target = read_controller_state(root).get("quota_fallback_for_resume")
                if fallback_target == "Codex":
                    child_env["THEORYMASTER_QUOTA_PROVIDER_FALLBACK"] = fallback_target
            with open(log_path, "a", encoding="utf-8") as log:
                if args.retained_quota is not None:
                    from .invocations import stable_hash
                    update_registry(root, retained_quota_attempt={
                        "launch_id": args.launch_id, "cycle": args.retained_quota["cycle"],
                        "approval_sha256": stable_hash(args.retained_quota),
                    })
                with child_resume_lock(root) if repair_dispatch is not None or active_repair_dispatch else nullcontext():
                    if (repair_dispatch is not None
                            and current_origin(root, load_registry(root), args.launch_id) != repair_dispatch):
                        update_registry(root, state="STOPPED", heartbeat_at=_stamp(), boot_resume_outcome="checkpoint_changed")
                        return 0
                    if active_repair_dispatch and verify_boot_resume(root, args.launch_id) != boot_resume:
                        update_registry(root, state="STOPPED", heartbeat_at=_stamp(), boot_resume_outcome="checkpoint_changed")
                        return 0
                    child = subprocess.Popen(_runner_command(args, operation), cwd=_repo_root(), env=child_env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                    update_registry(root, state="RUNNING" if operation == args.operation and recoveries == 0 else "RECOVERING", runner_pid=child.pid, runner_start_ticks=process_start_ticks(child.pid), runner_operation=operation, heartbeat_at=_stamp(), current_cycle=latest_cycle_name(root), recovery_count=recoveries,
                                    runner_checkpoint_protocol=DURABLE_RUNNER_PROTOCOL,
                                    **({"qualification_wait": None} if restored_dependency_wait else {}))
                restored_dependency_wait = False
                if operation == "resume" and boot_resume is None and repair_dispatch is None:
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
                still_unclaimed = False
                if repair_dispatch is not None:
                    _, finished_state, _ = _latest_scientific_state(root)
                    still_unclaimed = (operational_incident(finished_state)
                                       and unclaimed_in(finished_state, load_registry(root)))
                if verification is not None and not still_unclaimed:
                    evidence = _verified_model_activity(root / verification[0], launch_at)
                    _record_runtime_verification(
                        root, verification, verified=bool(evidence),
                        detail=evidence or f"Runner exited (rc={rc}) before confirmed post-resume model activity.",
                    )
            if repair_dispatch is not None:
                args.identifier = original_identifier
                args.takeover_expected_cycle = None
                if still_unclaimed:
                    registry = update_registry(root, runner_pid=None, runner_start_ticks=None,
                                               runner_operation=None, heartbeat_at=_stamp())
                    if current_origin(root, registry, args.launch_id) == repair_dispatch:
                        # A systemd restart retries only this unclaimed dispatch.
                        return 1
                    update_registry(root, state="STOPPED", boot_resume_outcome="checkpoint_changed")
                    return 0
            claimed_recovery = recover_claimed_runtime()
            if claimed_recovery is not None:
                if claimed_recovery:
                    continue
                return 0
            if repair_dispatch is not None and boot_runtime:
                update_registry(root, boot_resume=None, boot_resume_outcome="runtime_repair_dispatched")
                boot_runtime = False
            if reassessment is not None and rc != 0 and goal_retry is None and prepare_goal_retry():
                operation = "reassess"
                continue
            if goal_retry is not None:
                args.goal_retry_launch = None
                goal_retry = None
            update_registry(root, runner_pid=None, runner_start_ticks=None,
                            runner_operation="reassess" if reassessment is not None else None,
                            last_runner_exit_code=rc, current_cycle=latest_cycle_name(root))
            if boot_delivery:
                args.boot_resume_launch = None
                boot_resume = None
                boot_delivery = False
                update_registry(root, boot_resume=None,
                                boot_resume_outcome="delivery_failed" if rc != 0 else "delivery_returned")
            elif boot_resume is not None:
                failed_bound_cycle = (latest_cycle_name(root) == boot_resume["cycle"]
                                      and _exit_disposition(root) in {"failed", "unrecoverable"})
                args.boot_resume_launch = None
                if active_repair_dispatch:
                    args.identifier = str(boot_resume["origin"]["master_id"])
                boot_resume = None
                failed_boot_resume = rc != 0 or failed_bound_cycle
                update_registry(root, boot_resume=None,
                                boot_resume_outcome="runner_failed" if failed_boot_resume else "returned")
                if failed_boot_resume and not (active_repair_dispatch and _exit_disposition(root) == "failed"):
                    update_registry(root, state="FAILED", heartbeat_at=_stamp())
                    _notify_supervisor_terminal(root)
                    return 0
            if input_attempt is not None and not record_input_support_exit(input_attempt, rc):
                if next_operation := terminal_takeover_operation("terminal"):
                    operation = next_operation
                    continue
                update_registry(root, state="STOPPED" if rc == 0 else "FAILED", heartbeat_at=_stamp())
                return 0
            if reassessment is not None:
                record_delivery_completion(reassessment, "finished" if rc == 0 else "failed", rc)
                if stop_on_goal_authority():
                    return 0
                if next_operation := delivery_followup(reassessment):
                    operation = next_operation
                    continue
                update_registry(root, state="STOPPED" if rc == 0 else "FAILED", heartbeat_at=_stamp())
                return 0
            if stop_on_goal_authority():
                return 0
            if _quota_pause_context(root) is not None:
                if args.retained_quota is not None and not _verified_model_activity(
                        root / args.retained_quota["cycle"], launch_at):
                    from .quota_auto_adjust import write_controller_state
                    write_controller_state(root, status="waiting_refresh", next_check_in_s=None,
                                           provider_fallback_blocked="retained_goal_repaused_without_activity")
                    update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                    return 0
                operation = "resume"
                continue
            if _retained_quota_attempt_interrupted(root, args.launch_id):
                update_registry(root, state="STOPPED", heartbeat_at=_stamp())
                return 0  # A failed pinned launch cannot fall back to a shared account.
            disposition = _exit_disposition(root)
            if next_operation := terminal_takeover_operation(disposition):
                operation = next_operation
                continue
            if rc == 0 and disposition != "failed":
                _notify_supervisor_terminal(root)
                update_registry(root, state="STOPPED", heartbeat_at=_stamp()); return 0
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
