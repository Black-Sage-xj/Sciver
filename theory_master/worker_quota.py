"""Worker quota observation and bounded account recovery."""
from __future__ import annotations
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


def start_worker_quota_watch(spec: Any, worker_id: str) -> dict[str, Any]:
    """Detached task-scoped quota watcher; never starts a new scientific Cycle."""
    from . import multi_worker, runtime_manager, worker_launch, worker_store
    from .quota_auto_adjust import quota_auto_adjust_enabled
    if not quota_auto_adjust_enabled(spec.research_root):
        return {"status": "disabled"}
    root = Path(spec.research_root).resolve()
    directory = worker_store.worker_dir(root, worker_id)
    directory.mkdir(parents=True, exist_ok=True)
    watcher = directory / "quota_watcher.json"
    prior = multi_worker._read_json(watcher)
    if runtime_manager.process_identity_alive(prior.get("pid"), prior.get("pid_start_ticks")):
        return {"status": "already_watching", "pid": prior["pid"]}
    with (directory / "quota_watcher.log").open("a", encoding="utf-8") as output:
        worker_launch._launch_detached_service([sys.executable, "-m", "theory_master.worker_runtime", "quota-watch",
                        "--research-root", str(root), "--worker", worker_id],
                       root=root, env=worker_launch._task_scoped_process_env(root), output=output,
                       kind=f"quota-{worker_id}")
    # The watcher owns PID registration from inside its own process.
    return {"status": "starting", "worker_id": worker_id}


def _repair_missing_quota_watchers(spec: Any) -> list[dict[str, Any]]:
    """Reconcile dead quota watchers without touching a scientific Cycle.

    Detached watchers may disappear after machine restarts or service crashes.
    This scheduler-side check is rate limited so launch failures do not form a
    process storm. Existing Worker claims, model threads and budgets are inert.
    """
    from . import multi_worker, runtime_manager, worker_state, worker_store
    from .quota_auto_adjust import quota_auto_adjust_enabled
    if not quota_auto_adjust_enabled(spec.research_root):
        return []
    root = Path(spec.research_root).resolve()
    claims = worker_store._live_cycle_claims(root)
    repairs: list[dict[str, Any]] = []
    for worker in worker_store.list_workers(root):
        if (worker.get("status") not in {"PAUSED", "NEEDS_ATTENTION"}
                or worker.get("process_alive")
                or runtime_manager.process_identity_alive(worker.get("pid"), worker.get("pid_start_ticks"))):
            continue
        wid = str(worker.get("worker_id") or "")
        cycle = str(worker.get("current_cycle") or "")
        if not (wid.startswith("worker-") and cycle.isascii() and cycle.isdecimal()
                and int(cycle) > 0):
            continue
        claim = next((
            item for item in claims
            if item.get("worker_id") == wid
            and item.get("claim_id") == worker.get("claim_id")
            and item.get("fencing_epoch") == worker.get("epoch")
        ), None)
        state_file = root / cycle / "research_state.json"
        if state_file.is_symlink() or not state_file.is_file():
            continue
        state = multi_worker._read_json(state_file)
        recovery = state.get("recovery")
        paused_quota = (
            worker.get("status") == "PAUSED"
            and state.get("status") == "paused"
            and state.get("phase") == "provider_quota_exhausted"
            and isinstance(recovery, dict)
            and recovery.get("code") == "provider_quota_exhausted"
        )
        review_watch = (
            worker.get("status") == "NEEDS_ATTENTION"
            and _technical_review_quota_watch_eligible(state, wid)
        )
        if (state.get("resumable") is not True
                or state.get("execution_active") is not False
                or not worker_store._checkpoint_matches_claim(state, wid, claim, cycle)
                or not (paused_quota or review_watch)):
            continue
        directory = worker_store.worker_dir(root, wid)
        if review_watch:
            budget = state.get("investigator_budget") or {}
            try:
                review_index = int(budget.get("review_index") or 0)
            except (ValueError, TypeError, OverflowError):
                continue
            if (_budget_review_technical_failures(root / cycle, review_index)
                    >= worker_state.MAX_REPEATED_TECHNICAL_REVIEW_FAILURES):
                continue  # specialized bounded transport recovery owns it
            already = multi_worker._read_json(directory / "budget_review_quota_resume.json")
            key = f"{cycle}:{review_index}:{state['last_safe_checkpoint']}"
            if already.get("key") == key:
                continue  # the existing watcher already consumed this attempt
        prior = multi_worker._read_json(directory / "quota_watcher.json")
        if runtime_manager.process_identity_alive(prior.get("pid"), prior.get("pid_start_ticks")):
            continue
        repair_path = directory / "quota_watch_repair.json"
        previous = multi_worker._read_json(repair_path)
        watch_kind = "budget_review" if review_watch else "provider_quota"
        same = (previous.get("cycle") == cycle
                and previous.get("watch_kind", "provider_quota") == watch_kind)
        try:
            attempts = max(0, int(previous.get("attempts") or 0)) if same else 0
            retry_after = float(previous.get("retry_after_epoch") or 0) if same else 0
        except (TypeError, ValueError, OverflowError):
            # A broken watchdog receipt must not crash the whole scheduler.
            # Reset it to one bounded launch attempt, then overwrite safely.
            attempts, retry_after = 0, 0
        if same and time.time() < retry_after:
            continue
        # Retry in 1, 2, 4, ... minutes (bounded at 15), not once per
        # scheduler tick. Write before launch to survive a crash in-between.
        delay = min(900, 60 * (2 ** min(attempts, 4)))
        attempt = {"schema": "theory-master/quota-watch-repair/v1",
                   "worker_id": wid, "cycle": cycle,
                   "watch_kind": watch_kind,
                   "attempts": attempts + 1,
                   "attempted_at": multi_worker.utcnow().isoformat(),
                   "retry_after_epoch": time.time() + delay}
        multi_worker._save_json(repair_path, attempt)
        try:
            result = start_worker_quota_watch(spec, wid)
            repairs.append({"worker_id": wid, "cycle": cycle,
                            "status": result.get("status"), "attempts": attempts + 1})
        except (OSError, RuntimeError, ValueError) as exc:
            multi_worker._save_json(repair_path, {**attempt, "launch_error": type(exc).__name__})
            repairs.append({"worker_id": wid, "cycle": cycle,
                            "status": "launch_failed",
                            "error_type": type(exc).__name__})
    return repairs


def _technical_review_quota_watch_eligible(state: dict[str, Any], worker_id: str) -> bool:
    """Only genuine, resumable Reviewer delivery faults may have a quota watcher."""
    attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
    budget = state.get("investigator_budget") if isinstance(state.get("investigator_budget"), dict) else {}
    return bool(
        str(state.get("status") or "").lower() == "needs_attention"
        and state.get("phase") == "investigator:budget_review"
        and attention.get("code") == "investigator_budget_review_failed"
        and budget.get("rejected") is not True
        and state.get("worker_id") in (None, worker_id)
        and state.get("resumable") is True
        and state.get("last_safe_checkpoint")
    )


def _budget_review_technical_failures(cycle_dir: Path, review_index: int) -> int:
    """Count persisted failed review sessions at one unresolved budget boundary.

    A different checkpoint must not reset this safety limit. Actual approved
    budget decisions reset the count; temporary model retries within one
    session are not counted separately.
    """
    events = Path(cycle_dir) / "events.jsonl"
    if not events.is_file() or events.is_symlink():
        return 0
    failures = 0
    with events.open(encoding="utf-8") as lines:
        for line in lines:
            try:
                event = json.loads(line)
                payload = event.get("payload") or {}
                if int(payload.get("review_index") or -1) != int(review_index):
                    continue
                if event.get("type") == "investigation.budget_review_decided":
                    failures = 0
                elif event.get("type") == "investigation.budget_review_failed":
                    failures += 1
            except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
                continue
    return failures


def _budget_reviewer_last_technical_error(cycle_dir: Path,
                                          review_index: int) -> tuple[str, str]:
    """Read the latest exact-index technical delivery failure, not a rejection."""
    events = cycle_dir / "events.jsonl"
    if not events.is_file() or events.is_symlink() or events.stat().st_size > 20_000_000:
        return "", ""
    error_type, detail = "", ""
    with events.open(encoding="utf-8") as source:
        for line in source:
            try:
                row = json.loads(line)
                payload = row.get("payload") or {}
                if (row.get("type") == "investigation.budget_review_failed"
                        and int(payload.get("review_index") or 0) == review_index):
                    error_type, detail = (
                        str(payload.get("error_type") or ""),
                        str(payload.get("detail") or "")[:900])
            except (ValueError, TypeError, AttributeError):
                continue
    return error_type, detail


def _dispatch_exhausted_budget_reviewer_transport(spec: Any) -> bool:
    """One extra Reviewer *delivery* attempt after new verified provider health.

    Technical failures do not imply rejection, approval or more Investigator
    budget. A malformed JSON/typed contract is a protocol fault, not a reason
    for endless paid reviews. After two failed sessions, only a recognized
    transient transport error may get ONE same-Worker guarded resume.
    """
    from . import multi_worker, runtime_manager, worker_actions, worker_state, worker_store
    from .investigator_budget import budget_status
    from .observability.incident_registry import register_cycle_incident
    from .codex_accounts import account_rate_limits, quota_available
    root = Path(spec.research_root).resolve()
    for worker in worker_store.list_workers(root):
        if (worker.get("status") != "NEEDS_ATTENTION" or worker.get("process_alive")
                or runtime_manager.process_identity_alive(worker.get("pid"), worker.get("pid_start_ticks"))):
            continue
        wid, cycle = str(worker.get("worker_id") or ""), str(worker.get("current_cycle") or "")
        if not re.fullmatch(r"worker-[1-9][0-9]*", wid):
            continue
        if not cycle.isascii() or not cycle.isdecimal() or int(cycle) < 1:
            continue
        directory = root / cycle
        path = directory / "research_state.json"
        if not path.is_file() or path.is_symlink():
            continue
        state = multi_worker._read_json(path)
        if not _technical_review_quota_watch_eligible(state, wid):
            continue
        budget = state.get("investigator_budget") or {}
        try:
            index = int(budget.get("review_index") or 0)
        except (ValueError, TypeError, OverflowError):
            continue
        failures = _budget_review_technical_failures(directory, index)
        if index < 1 or failures < worker_state.MAX_REPEATED_TECHNICAL_REVIEW_FAILURES:
            continue  # existing quota watcher owns the first bounded attempt
        claim = worker_store._worker_live_claim(multi_worker.CycleClaims(root, worker_store._graph_path(spec)), worker)
        if claim is None or not worker_store._checkpoint_matches_claim(state, wid, claim, cycle):
            continue
        try:
            status = budget_status(directory, root, state)
            if status.get("rejected") is True or status.get("review_index") != index:
                continue
        except (ValueError, TypeError):
            register_cycle_incident(
                directory, severity="important", kind="system_failure",
                source_event="sdk.budget_reviewer_recovery",
                code="budget_reviewer_ledger_integrity_required",
                discriminator=f"{wid}:{cycle}:{index}",
                summary=(f"{wid} · Cycle {cycle} Budget Reviewer 账本验证失败；"
                         "停止自动重试，保留原科研预算和检查点。"),
                fields={"worker_id": wid, "cycle": cycle,
                        "review_index": index, "budget_granted": False},
            )
            continue
        except OSError:
            continue  # transient filesystem errors never authorize a grant
        error_type, detail = _budget_reviewer_last_technical_error(directory, index)
        transport = (error_type in {"RuntimeError", "ConnectionError", "TimeoutError"}
                     and any(token in detail.lower() for token in (
                         "completed without assistant final response",
                         "stream disconnected", "connection reset",
                         "temporarily unavailable", "server overloaded",
                     )))
        if not transport:
            register_cycle_incident(
                directory, severity="important", kind="system_failure",
                source_event="sdk.budget_reviewer_recovery",
                code="budget_reviewer_protocol_repair_required",
                discriminator=f"{wid}:{cycle}:{index}",
                summary=(f"{wid} · Cycle {cycle} 的 Budget Reviewer 连续技术失败，"
                         "未产生有效预算决定。需修复格式/协议/模型配置后显式重试；禁止自动批预算。"),
                fields={"worker_id": wid, "cycle": cycle, "review_index": index,
                        "failed_sessions": failures, "budget_granted": False},
            )
            continue
        receipt = worker_store.worker_dir(root, wid) / f"budget_reviewer_transport_recovery_{cycle}_{index}.json"
        if receipt.exists() or receipt.is_symlink():
            prior = multi_worker._read_json(receipt) if not receipt.is_symlink() else {}
            if (prior.get("cycle") == cycle
                    and prior.get("status") == "resume_dispatched"
                    and failures > int(prior.get("failed_sessions") or 0)):
                register_cycle_incident(
                    directory, severity="important", kind="system_failure",
                    source_event="sdk.budget_reviewer_recovery",
                    code="budget_reviewer_post_transport_recovery_failed",
                    discriminator=f"{wid}:{cycle}:{index}",
                    summary=(f"{wid} · Cycle {cycle} Budget Reviewer 在一次性恢复后"
                             "仍发生技术故障，已停止自动重试；未改变预算。"),
                    fields={"worker_id": wid, "cycle": cycle,
                            "review_index": index, "budget_granted": False},
                )
            continue  # one additional attempt at most
        account = str(worker.get("account") or "")
        if account not in {"1", "2"}:
            continue
        probe_path = worker_store.worker_dir(root, wid) / "budget_reviewer_transport_probe.json"
        previous = multi_worker._read_json(probe_path)
        try:
            next_probe = float(previous.get("next_probe_epoch") or 0)
        except (ValueError, TypeError, OverflowError):
            next_probe = 0
        if (previous.get("cycle") == cycle and previous.get("review_index") == index
                and time.time() < next_probe):
            continue
        multi_worker._save_json(probe_path, {"cycle": cycle, "review_index": index,
                                "next_probe_epoch": time.time() + 60,
                                "at": multi_worker.utcnow().isoformat()})
        try:
            available = quota_available(account_rate_limits(account)) is True
        except (OSError, RuntimeError, ValueError, TimeoutError):
            continue
        if not available:
            continue  # wait for verified quota, no false Supervisor error
        payload = {
            "schema": "theory-master/budget-reviewer-post-transport-recovery/v1",
            "worker_id": wid, "cycle": cycle, "review_index": index,
            "failed_sessions": failures, "account": account,
            "status": "requested_once", "at": multi_worker.utcnow().isoformat(),
        }
        try:
            fd = os.open(receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            launched = worker_actions.worker_action(spec, wid, "resume")
            multi_worker._save_json(receipt, {**payload, "status": "resume_dispatched",
                                 "result_status": launched.get("status")})
        except (OSError, ValueError, RuntimeError) as exc:
            multi_worker._save_json(receipt, {**payload, "status": "resume_blocked",
                                 "error_type": type(exc).__name__})
            register_cycle_incident(
                directory, severity="important", kind="system_failure",
                source_event="sdk.budget_reviewer_recovery",
                code="budget_reviewer_transport_retry_blocked",
                discriminator=f"{wid}:{cycle}:{index}",
                summary=f"{wid} · Cycle {cycle} Budget Reviewer 安全恢复未能启动；预算和 Claim 均保留。",
                fields={"worker_id": wid, "cycle": cycle, "budget_granted": False},
            )
        return True
    return False


def watch_worker_quota(spec: Any, worker_id: str) -> int:
    """Resume one paused quota Cycle on available Codex quota only.

    This is a Worker-specific watchdog: no global selector switching, no
    sibling termination, no new Cycle, and no cross-provider thread continuation.
    """
    from . import multi_worker, runtime_manager, worker_actions, worker_state, worker_store
    from .quota_auto_adjust import quota_auto_adjust_enabled
    from .codex_accounts import account_rate_limits, quota_available
    from .quota_provider_fallback import investigator_native_goal_in_flight
    import time
    root = Path(spec.research_root).resolve()
    gate = worker_store.worker_dir(root, worker_id) / "quota_watcher_owner.lock"
    with gate.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # Another watcher already owns this Worker.
        multi_worker._save_json(worker_store.worker_dir(root, worker_id) / "quota_watcher.json", {
            "pid": os.getpid(), "pid_start_ticks": runtime_manager.process_start_ticks(os.getpid()),
            "started_at": multi_worker.utcnow().isoformat(), "worker_id": worker_id})
        while quota_auto_adjust_enabled(root):
            w = worker_store.read_worker(root, worker_id)
            if w.get("close_requested") or w.get("dismissed"):
                return 0
            status = str(w.get("status") or "")
            if status in {"OVER", "COMPLETE", "RELEASED", "CANCELLED", "STOPPED", "FAILED_START"}:
                return 0
            if status == "RUNNING" or runtime_manager.process_identity_alive(w.get("pid"), w.get("pid_start_ticks")):
                time.sleep(15)
                continue
            if status == "NEEDS_ATTENTION":
                # A Reviewer protocol failure is not a budget rejection.
                # Resume *only the same account and Cycle* once when that
                # account's quota is positively available. No thread migration.
                cycle = str(w.get("current_cycle") or "")
                if not cycle.isascii() or not cycle.isdecimal() or int(cycle) < 1:
                    return 1
                state = multi_worker._read_json(root / cycle / "research_state.json")
                budget = state.get("investigator_budget") if isinstance(state.get("investigator_budget"), dict) else {}
                if not _technical_review_quota_watch_eligible(state, worker_id):
                    return 0
                repeated = _budget_review_technical_failures(
                    root / cycle, int(budget.get("review_index") or 0))
                if repeated >= worker_state.MAX_REPEATED_TECHNICAL_REVIEW_FAILURES:
                    multi_worker._save_json(worker_store.worker_dir(root, worker_id) / "budget_review_recovery_exhausted.json", {
                        "schema": "theory-master/budget-review-recovery-exhausted/v1",
                        "cycle": cycle, "worker_id": worker_id,
                        "review_index": int(budget.get("review_index") or 0),
                        "failed_sessions": repeated,
                        "reason": "repeated_technical_budget_reviewer_failure",
                        "action_required": "manual_review_or_repair_before_explicit_resume",
                        "at": multi_worker.utcnow().isoformat(),
                    })
                    return 0  # Preserve checkpoint and budget; do not replay forever.
                account = str(w.get("account") or "")
                if account not in {"1", "2"}:
                    return 0
                key = f"{cycle}:{budget.get('review_index')}:{state['last_safe_checkpoint']}"
                receipt_path = worker_store.worker_dir(root, worker_id) / "budget_review_quota_resume.json"
                previous = multi_worker._read_json(receipt_path)
                if previous.get("key") == key:
                    return 0  # Durable once-only recovery, even after restart.
                try:
                    quota = account_rate_limits(account)
                except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                    # A failed/unknown quota probe cannot authorize a model
                    # call, but must not kill the only recovery watcher.
                    multi_worker._save_json(worker_store.worker_dir(root, worker_id) / "quota_probe_error.json", {
                        "schema": "theory-master/quota-probe-error/v1",
                        "at": multi_worker.utcnow().isoformat(), "account": account,
                        "cycle": cycle, "error_type": type(exc).__name__,
                        "reason": str(exc)[:240],
                    })
                    time.sleep(60)
                    continue
                if quota_available(quota) is not True:
                    time.sleep(60)
                    continue
                attempt = {"schema": "theory-master/budget-review-quota-resume/v1",
                           "key": key, "cycle": cycle, "worker_id": worker_id,
                           "account": account, "at": multi_worker.utcnow().isoformat(),
                           "status": "attempted"}
                multi_worker._save_json(receipt_path, attempt)  # Commit before resume.
                try:
                    outcome = worker_actions.worker_action(spec, worker_id, "resume")
                except (ValueError, RuntimeError, OSError) as exc:
                    multi_worker._save_json(receipt_path, {**attempt, "status": "blocked",
                                              "error": f"{type(exc).__name__}: {str(exc)[:350]}"})
                    return 1
                multi_worker._save_json(receipt_path, {**attempt, "status": "dispatched", "result": outcome})
                return 0
            if status != "PAUSED":
                return 0  # Other human attention and unrelated failures.
            cycle = str(w.get("current_cycle") or "")
            if not cycle.isascii() or not cycle.isdigit():
                return 1
            state = multi_worker._read_json(root / cycle / "research_state.json")
            # worker_action("resume") re-verifies the exact Cycle Claim fencing.
            if (str(state.get("status") or "").lower() != "paused"
                    or state.get("phase") != "provider_quota_exhausted"
                    or state.get("worker_id") not in (None, worker_id)):
                return 0

            account = str(w.get("account") or "")
            snapshots = {number: account_rate_limits(number) for number in ("1", "2")}
            available = [number for number in ("1", "2")
                         if quota_available(snapshots.get(number) or {}) is True]
            if available:
                candidate = account if account in available else available[0]
                # Never transfer an in-flight Native Goal to another provider,
                # even when Codex quota has recovered.
                if (investigator_native_goal_in_flight(state)
                        and candidate != account):
                    # Preserve uncommitted Native Goals. A terminal Codex
                    # usageLimited Goal is different: the old physical Goal is
                    # already over, and same-account route restoration is safe.
                    time.sleep(60)
                    continue
                try:
                    result = worker_actions.worker_action(
                        spec, worker_id, "resume", account=candidate,
                        provider_fallback=None,
                    )
                    multi_worker._save_json(worker_store.worker_dir(root, worker_id) / "last_quota_recovery.json", {
                        "at": multi_worker.utcnow().isoformat(), "from": account, "to": candidate,
                        "provider_fallback": None,
                        "cycle": cycle, "result": result})
                    time.sleep(15)
                    continue
                except (ValueError, RuntimeError, OSError) as exc:
                    multi_worker._save_json(worker_store.worker_dir(root, worker_id) / "last_quota_recovery.json", {
                        "at": multi_worker.utcnow().isoformat(), "from": account, "to": candidate,
                        "cycle": cycle, "blocked": f"{type(exc).__name__}: {str(exc)[:350]}"})

            # Both Codex identities unavailable: remain paused. The watcher
            # never changes model provider without an explicit Services choice.
            time.sleep(60)
    return 0


def _migrate_paused_worker_account(root: Path, worker: dict[str, Any],
                                   target_account: str) -> dict[str, Any]:
    """Account-only relocation of a PAUSED Cycle at a verified safe boundary.

    Preserve scientific lock, H/claim/Cycle, and historical model transcripts.
    Old native Codex thread IDs are account-owned: archive the checkpoint and
    start fresh model threads in the *same* Cycle after relocation. Never send
    the old thread IDs to the other account, even if the ID happens to resolve.
    """
    from . import multi_worker, runtime_manager, worker_routes, worker_store
    import json
    import os
    from .codex_accounts import account_switch_lock, ensure_layout
    root = Path(root).resolve()
    old_account = str(worker.get("account") or "")
    if target_account not in {"1", "2"} or old_account not in {"1", "2"}:
        raise ValueError("account migration requires two known Codex identities")
    if target_account == old_account:
        return {"status": "unchanged", "account": target_account}
    if runtime_manager.process_identity_alive(worker.get("pid"), worker.get("pid_start_ticks")):
        raise ValueError("cannot migrate account during active scientific Turn")
    cycle = str(worker.get("current_cycle") or "")
    if not cycle.isascii() or not cycle.isdecimal():
        raise ValueError("no valid frozen Cycle for account migration")
    path = root / cycle / "research_state.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Cycle checkpoint is unavailable")
    state = multi_worker._read_json(path)
    # A stale Worker record must never migrate a Cycle claimed by another owner.
    ownership = next((c for c in worker_store._live_cycle_claims(root)
                      if c.get("claim_id") == worker.get("claim_id")), None) or {}
    if (ownership.get("worker_id") != worker.get("worker_id")
            or ownership.get("fencing_epoch") != worker.get("epoch")
            or not worker_store._claim_owns_cycle(ownership, cycle)):
        raise ValueError("Worker no longer owns the frozen science Cycle")
    if (str(state.get("status") or "").lower() != "paused"
            or not worker_store._checkpoint_matches_claim(state, str(worker.get("worker_id") or ""), ownership, cycle)):
        raise ValueError("only matching, safely paused Cycle can migrate")
    if not state.get("resumable"):
        raise ValueError("paused Cycle has no resumable checkpoint")
    # Native Goal may own provider-internal continuation state beyond the
    # ordinary role threads dictionary. Do not risk silently resuming it under
    # a different account/provider. Same-account quota refresh remains allowed.
    from .quota_provider_fallback import investigator_native_goal_in_flight
    if investigator_native_goal_in_flight(state):
        raise ValueError("in-flight Investigator Native Goal requires same-account recovery")
    layout = ensure_layout()
    auth = (layout[target_account] / "auth.json").resolve()
    if not auth.is_file():
        raise ValueError("target Codex account has no valid authentication")
    directory = worker_store.worker_dir(root, str(worker["worker_id"]))
    home = worker_routes._pinned_codex_home(root, str(worker["worker_id"]), old_account)
    link = home / "auth.json"
    with account_switch_lock():
        if not link.is_symlink() or link.resolve() != (layout[old_account] / "auth.json").resolve():
            raise ValueError("Worker private authentication has changed; migration rejected")
        archive = root / cycle / ("research_state.before-account-migration-" +
                                 multi_worker.utcnow().strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        if archive.exists() or archive.is_symlink():
            raise ValueError("migration checkpoint archive collision")
        multi_worker._save_json(archive, state)
        updated = json.loads(json.dumps(state))
        old_threads = list((updated.get("threads") or {}).keys())
        updated["threads"] = {}
        updated["runtime_thread_providers"] = {}
        updated["research_lead_thread_id"] = None
        updated["research_lead_context_chars"] = 0
        updated.setdefault("recovery", {})["allow_thread_fallback"] = True
        migration = {"from": old_account, "to": target_account,
                     "at": multi_worker.utcnow().isoformat(), "before_checkpoint": str(archive),
                     "invalidated_thread_roles": old_threads,
                     "mode": "fresh_identity_threads_same_frozen_cycle"}
        updated.setdefault("account_migration_history", []).append(migration)
        # If this write succeeds but auth rebinding fails, the new state can
        # still safely resume under the old account with fresh threads.
        multi_worker._save_json(path, updated)
        temp = home / (".auth-switch-" + str(os.getpid()))
        try:
            temp.symlink_to(auth)
            os.replace(temp, link)
            worker_store.save_worker(root, str(worker["worker_id"]), account=target_account,
                        last_account_migration=migration)
        except BaseException:
            if temp.is_symlink():
                temp.unlink()
            # Return auth to old identity and restore checkpoint when safe.
            revert = home / (".auth-rollback-" + str(os.getpid()))
            revert.symlink_to((layout[old_account] / "auth.json").resolve())
            os.replace(revert, link)
            multi_worker._save_json(path, state)
            worker_store.save_worker(root, str(worker["worker_id"]), account=old_account)
            raise
    return {"status": "migrated", "account": target_account,
            "old_account": old_account, "cycle": cycle,
            "preserved_science": True, "fresh_model_threads_required": True}


def _verified_codex_quota_wait() -> dict[str, int] | None:
    """Require fresh affirmative exhaustion of both Codex pools, never unknown."""
    from .codex_accounts import account_rate_limits
    now_ts = time.time()
    resets: dict[str, int] = {}
    try:
        for number in ("1", "2"):
            record = account_rate_limits(number)
            if (not isinstance(record, dict) or record.get("ok") is not True
                    or record.get("ordinary_usage_allowed") is not False):
                return None
            stamp = record.get("queried_at")
            if type(stamp) not in (int, float) or not now_ts - 300 <= stamp <= now_ts + 60:
                return None
            exhausted = [w["resets_at"] for w in
                         (record.get("primary"), record.get("secondary"))
                         if isinstance(w, dict)
                         and type(w.get("remaining_percent")) in (int, float)
                         and w["remaining_percent"] <= 0
                         and type(w.get("resets_at")) is int
                         and now_ts < w["resets_at"] < now_ts + 8 * 86400]
            if not exhausted:
                return None
            resets[number] = max(exhausted)
    except (OSError, ValueError, RuntimeError, TimeoutError):
        return None
    return resets


def _verified_pinned_codex_quota_wait(account: str) -> dict[str, int] | None:
    """One exhausted Worker account is enough to wait, even if its peer has quota.

    Native Goal identity forbids an automatic cross-account transfer, so a
    healthy unrelated account does not make this account's Turn possible.
    """
    from .codex_accounts import account_rate_limits
    if account not in {"1", "2"}:
        return None
    now_ts = time.time()
    try:
        record = account_rate_limits(account)
        if (not isinstance(record, dict) or record.get("ok") is not True
                or record.get("ordinary_usage_allowed") is not False):
            return None
        queried = record.get("queried_at")
        if (type(queried) not in (int, float)
                or not now_ts - 300 <= queried <= now_ts + 60):
            return None
        resets = [item["resets_at"] for item in
                  (record.get("primary"), record.get("secondary"))
                  if isinstance(item, dict)
                  and type(item.get("remaining_percent")) in (int, float)
                  and item["remaining_percent"] <= 0
                  and type(item.get("resets_at")) is int
                  and now_ts < item["resets_at"] < now_ts + 8*86400]
        return {account: max(resets)} if resets else None
    except (OSError, ValueError, RuntimeError, TimeoutError):
        return None


def _persist_verified_quota_wait(root: Path, worker_id: str, cycle: str,
                                 claim: dict[str, Any], resets: dict[str, int],
                                 reason: str) -> bool:
    """Keep previous failure provenance while pausing a fenced stopped Cycle."""
    from . import multi_worker, worker_store
    folder = root / cycle
    science_path, run_path = folder / "research_state.json", folder / "run_state.json"
    if (not folder.is_dir() or folder.is_symlink()
            or science_path.is_symlink() or run_path.is_symlink()):
        return False
    science, run = multi_worker._read_json(science_path), multi_worker._read_json(run_path)
    if (not worker_store._checkpoint_matches_claim(science, worker_id, claim, cycle)
            or science.get("resumable") is not True
            or science.get("execution_active") is True
            or str(science.get("status") or "").lower() not in {"paused", "failed"}
            or str(run.get("status") or "").upper() not in {"PAUSED", "FAILED"}):
        return False
    previous = dict(science.get("recovery") or {})
    if previous.get("code") == "provider_quota_exhausted":
        previous = previous.get("previous_failure") or {}
    else:
        previous = {key: previous[key] for key in ("action", "kind", "reason", "message", "at")
                    if previous.get(key)}
    recovery = {"action": "resume", "code": "provider_quota_exhausted",
                "kind": "VerifiedCodexQuotaWait", "reason": reason,
                "previous_failure": previous, "resets_at": resets,
                "resume_when": "fresh quota is positively available",
                "at": multi_worker.utcnow().isoformat()}
    for entry, status in ((science, "paused"), (run, "PAUSED")):
        entry.update(status=status, phase="provider_quota_exhausted",
                     execution_active=False, resumable=True,
                     resource_disposition="provider_quota_exhausted", recovery=recovery)
    multi_worker._save_json(science_path, science)
    multi_worker._save_json(run_path, run)
    return True
