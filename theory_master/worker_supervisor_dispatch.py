"""Worker-scoped Supervisor recovery for an *exited* FAILED research Worker.

A daemon only schedules an isolated helper. The helper checks Worker ownership,
keeps all other Workers quiescent during code repair, and delegates changes to
independently validated candidate pipelines. Waiting incidents retain ownership.
No registration, notification, or UI projection invokes a model implicitly.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .multi_worker import _read_json, _save_json
from .observability.incident_routing import classify_incident
from .runtime_manager import (
    process_identity_alive, process_start_ticks, runtime_health,
    runtime_source_provenance,
)
from .supervisor_intervention import supervisor_enabled, append_supervisor_verification
from .worker_runtime import (
    NON_INCIDENT_STOPS, _checkpoint_matches_claim, _live_cycle_claims, _pinned_codex_home,
    _task_scoped_process_env, list_workers, read_worker, worker_action, worker_dir,
)


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


MAX_ENGINEERING_RECOVERY_ATTEMPTS = 3


def _fingerprint(state: dict[str, Any]) -> str:
    """Stable identity for one failure; timestamps are evidence, not identity."""
    recovery = state.get('recovery') if isinstance(state.get('recovery'), dict) else {}
    data = [state.get('status'), state.get('phase'), recovery.get('kind'),
            recovery.get('message')]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()[:20]


def _legacy_fingerprint(state: dict[str, Any]) -> str:
    """Read old receipts written before failure identity stopped including time."""
    recovery = state.get('recovery') if isinstance(state.get('recovery'), dict) else {}
    data = [state.get('status'), state.get('phase'), recovery.get('kind'),
            recovery.get('message'), recovery.get('at')]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()[:20]


def _receipt_path(root: Path, worker_id: str) -> Path:
    return worker_dir(root, worker_id) / 'supervisor_dispatch.json'


def _transient_quota_failure(reason: Any) -> bool:
    """Recognize provider-account throttling, not scientific/budget failures."""
    value = str(reason or '').lower()
    return any(marker in value for marker in (
        'providerquotaexhausted', 'rate limit exceeded', 'too many requests',
        'retry after', 'usage limit reached', 'resource_exhausted',
    ))


def _worker_cycle_claim(root: Path, worker: dict[str, Any]) -> dict[str, Any] | None:
    for claim in _live_cycle_claims(root):
        if (claim.get('worker_id') == worker.get('worker_id')
                and claim.get('claim_id') == worker.get('claim_id')
                and claim.get('fencing_epoch') == worker.get('epoch')):
            return claim
    return None


def _eligible(root: Path, worker: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
    from .supervisor_recovery import operational_incident
    if worker.get('status') not in {'FAILED', 'NEEDS_ATTENTION'} or worker.get('process_alive'):
        return None
    cycle = str(worker.get('current_cycle') or '')
    if not cycle.isdigit() or not cycle:
        return None
    directory = root / cycle
    if not directory.is_dir() or directory.is_symlink():
        return None
    state = _read_json(directory / 'research_state.json')
    # Recovery resumes the SAME Cycle/H via reissue_for_resume (I-10): only a
    # live Cycle Claim for this exact Cycle justifies a Supervisor model call.
    if (not operational_incident(state)
            or state.get('abandoned_cycle') or state.get('phase') == 'abandon_requested'
            or not _checkpoint_matches_claim(state, str(worker.get('worker_id') or ''),
                                             _worker_cycle_claim(root, worker), cycle)):
        return None
    from .supervisor_recovery import failure_details
    recovery = failure_details(state)
    route = classify_incident(severity='error', kind='system_failure',
                              code='run_failed', summary=str(recovery.get('message') or ''))
    if route['nature'] not in {'engineering_failure', 'provider_configuration'}:
        return None  # actual quota and budget guards belong to their controllers
    return directory, state


def _verify_launched(root: Path, worker: dict[str, Any], receipt: dict[str, Any]) -> None:
    if receipt.get('status') != 'resume_launched' or receipt.get('verification_recorded'):
        return
    cycle = root / str(receipt.get('cycle') or '')
    if not cycle.is_dir() or cycle.is_symlink():
        return
    from .supervisor import _verified_model_activity
    activity = _verified_model_activity(cycle, str(receipt.get('resume_requested_at') or ''))
    if activity:
        receipt.update(status='runtime_activity_verified', verification_recorded=True,
                       verification=activity, verified_at=_stamp())
        _save_json(_receipt_path(root, worker['worker_id']), receipt)
        append_supervisor_verification(cycle, session_id=str(receipt.get('session_id') or ''),
                                       verified=True, detail=activity)
        from .observability.incident_registry import record_incident_verification
        record_incident_verification(cycle, incident_id=str(receipt.get('incident_id') or ''),
                                     status='runtime_activity_verified', detail=activity)
    elif (worker.get('status') in {'FAILED', 'OVER', 'COMPLETE', 'NEEDS_ATTENTION', *NON_INCIDENT_STOPS}
          and not worker.get('process_alive')):
        receipt.update(status='unverified', verification_recorded=True,
                       verified_at=_stamp(), verification='No post-resume model/tool activity')
        _save_json(_receipt_path(root, worker['worker_id']), receipt)
        append_supervisor_verification(cycle, session_id=str(receipt.get('session_id') or ''),
                                       verified=False, detail=receipt['verification'])


def _live_peer_safe_probe_candidate(state: dict[str, Any]) -> bool:
    """Only a capacity-stuck, otherwise resumable *provider* Goal is probeable.

    Engineering/configuration failures must wait for an exclusive deployment
    window. This gate does not authorize a model call or a scientific resume.
    """
    from .provider_capacity import is_model_at_capacity
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_recovery import failure_details
    recovery = failure_details(state)
    return bool(resume_blocker(state) is None
                and is_model_at_capacity(str(recovery.get("message") or "")))


def _defer_engineering_for_live_peer(root: Path, worker: dict[str, Any],
                                    cycle: Path, fingerprint: str,
                                    prior: dict[str, Any], *,
                                    reason: str) -> None:
    """Make cross-Worker source-repair waiting visible, not a silent hold."""
    from .observability.incident_registry import register_cycle_incident
    from .supervisor_recovery import defer_job
    defer_job(cycle, _read_json(cycle / "research_state.json"), dependency=reason,
              action="wait_for_safe_engineering_window")
    wid = str(worker.get("worker_id") or "")
    path = _receipt_path(root, wid)
    if (prior.get("status") != "deferred_peers_active"
            or prior.get("fingerprint") != fingerprint):
        register_cycle_incident(
            cycle, severity="important", kind="system_failure",
            source_event="sdk.worker_supervisor_dispatch",
            code="supervisor_engineering_waits_for_peer",
            discriminator=f"{wid}:{fingerprint}",
            summary=(f"{wid} · Cycle {cycle.name} 的共享引擎修复需安全窗口；"
                     "其他科研 Worker 仍在执行。保留原 Cycle 与 Claim，不热更新源代码。"),
            fields={"worker_id": wid, "cycle": cycle.name,
                    "requires_safe_engineering_window": True},
        )
    _save_json(path, {**prior, "cycle": cycle.name, "worker_id": wid,
                       "fingerprint": fingerprint,
                       "failure_signature": fingerprint,
                       "status": "deferred_peers_active",
                       "reason": reason[:600], "updated_at": _stamp()})


def dispatch_pending(root: Path) -> bool:
    """Non-blocking scheduler hook. True means recovery occupies the scheduling gate."""
    root = Path(root).resolve()
    if not supervisor_enabled(root) or runtime_health(root).get('active'):
        return False
    workers = list_workers(root)
    for worker in workers:
        _verify_launched(root, worker, _read_json(_receipt_path(root, worker['worker_id'])))
    for worker in workers:
        identity = _eligible(root, worker)
        if identity is None:
            continue
        cycle, state = identity
        fingerprint = _fingerprint(state)
        receipt_path = _receipt_path(root, worker['worker_id'])
        prior = _read_json(receipt_path)
        same_cycle = prior.get('cycle') == cycle.name
        same_fingerprint = same_cycle and prior.get('fingerprint') == fingerprint
        if same_cycle and prior.get('fingerprint') and not same_fingerprint:
            prior_signature = prior.get('failure_signature')
            if prior_signature:
                # A changed stable signature is positive evidence of a new failure.
                same_fingerprint = prior_signature == fingerprint
            elif prior.get('fingerprint') == _legacy_fingerprint(state):
                # One-way migration for receipts written with the old timestamp-bound hash.
                prior = {**prior, 'fingerprint': fingerprint, 'failure_signature': fingerprint}
                _save_json(receipt_path, prior)
                same_fingerprint = True
            else:
                # Fresh claim/checkpoint eligibility supplies current ownership;
                # preserve legacy evidence and counters, then diagnose this failure.
                prior = {**prior, 'previous_fingerprint': prior.get('fingerprint'),
                         'fingerprint': fingerprint, 'failure_signature': fingerprint,
                         'status': 'hold'}
                _save_json(receipt_path, prior)
                same_fingerprint = True
        from .supervisor_recovery import due, read_job, ensure_job
        job = read_job(cycle)
        job_due = bool(job and due(cycle, state))
        if not job:
            ensure_job(cycle, state)
        if same_cycle and same_fingerprint:
            prior.setdefault('failure_signature', fingerprint)
            if prior.get('status') in {'starting', 'reviewing'}:
                if process_identity_alive(prior.get('pid'), prior.get('pid_start_ticks')):
                    return True
                # A killed Supervisor process cannot silently look pending forever.
                prior = {**prior, 'status': 'hold', 'failure_signature': fingerprint,
                         'reason': 'Supervisor helper exited without a verified decision'}
                _save_json(receipt_path, prior)
                from .supervisor_recovery import finish_action
                finish_action(cycle, state, {'action': 'hold', 'reason': prior['reason']})
                job_due = False
            if prior.get('status') == 'retry_wait':
                retry_at = float(prior.get('retry_at_epoch') or 0)
                if time.time() < retry_at and not job_due:
                    return True  # preserve scheduler recovery ownership during backoff
                quota_like = _transient_quota_failure(prior.get('reason'))
                attempts = max(0, int(prior.get('recovery_attempts') or 0))
            elif prior.get('status') in {'hold', 'unverified', 'manual_review_required'} and not job_due:
                attempts = max(0, int(prior.get('recovery_attempts') or 0))
                quota_like = _transient_quota_failure(prior.get('reason'))
                if quota_like:
                    quota_attempts = max(0, int(prior.get('quota_retry_attempts') or 0))
                    delay = min(3_600, 900 * (2 ** min(quota_attempts, 6)))
                    updated_attempts = attempts
                    updated_quota_attempts = quota_attempts + 1
                else:
                    delay = min(21_600, 300 * (2 ** min(attempts // MAX_ENGINEERING_RECOVERY_ATTEMPTS, 6)))
                    updated_attempts = attempts
                    updated_quota_attempts = int(prior.get('quota_retry_attempts') or 0)
                from .supervisor_recovery import save_job
                waiting = ensure_job(cycle, state)
                if waiting.get('status') == 'pending':
                    from .supervisor_recovery import inputs
                    waiting.update(status='waiting', current_action='replan',
                        input_digest=inputs(root, state), next_wakeup_epoch=time.time() + delay,
                        blocking_dependency=prior.get('reason') or 'Previous helper ended without verification')
                    save_job(cycle, waiting)
                _save_json(receipt_path, {**prior, 'status': 'retry_wait',
                    'failure_signature': fingerprint,
                    'recovery_attempts': updated_attempts,
                    'quota_retry_attempts': updated_quota_attempts,
                    'retry_at_epoch': time.time() + delay,
                    'retry_delay_seconds': delay,
                    'reason': str(prior.get('reason') or '')[:900],
                    'updated_at': _stamp()})
                return True
            elif prior.get('status') == 'deferred_peers_active':
                # Read-only provider capacity probes can recur at a bounded
                # cadence; engineering repair waits for a peer-free window.
                if (any(w['process_alive'] for w in workers)
                        and (not _live_peer_safe_probe_candidate(state)
                             or time.time() < float(prior.get('retry_at_epoch') or 0))):
                    continue
            elif prior.get('status') not in {'deferred', 'hold', 'unverified', 'manual_review_required', 'runtime_activity_verified'}:
                continue
        # A receipt remains bound to this exact failure fingerprint. A new
        # fingerprint resets cadence without rewriting scientific state.
        ensure_job(cycle, state)
        if not due(cycle, state):
            return True  # durable owner waits; no repeated model call
        peer_live = any(w['process_alive'] for w in workers)
        if peer_live and not _live_peer_safe_probe_candidate(state):
            _defer_engineering_for_live_peer(
                root, worker, cycle, fingerprint, prior,
                reason="Shared-code and configuration repairs require a peer-free engineering window.")
            continue
        env = _task_scoped_process_env(root)
        if worker.get('account'):
            env['THEORYMASTER_WORKER_CODEX_HOME'] = str(
                _pinned_codex_home(root, worker['worker_id'], str(worker['account'])))
            env['THEORYMASTER_WORKER_PINNED_ACCOUNT'] = str(worker['account'])
        attempts_before = max(0, int(prior.get('recovery_attempts') or 0)) if same_fingerprint else 0
        _save_json(receipt_path, {'cycle': cycle.name, 'worker_id': worker['worker_id'],
                                  'fingerprint': fingerprint, 'failure_signature': fingerprint,
                                  'runtime_source_provenance': runtime_source_provenance(),
                                  'status': 'starting',
                                  'read_only_peer_probe': peer_live,
                                  'peer_probe_count': (int(prior.get('peer_probe_count') or 0) + 1)
                                      if peer_live and same_fingerprint else (1 if peer_live else 0),
                                  'quota_retry_attempts': int(prior.get('quota_retry_attempts') or 0)
                                      if same_fingerprint else 0,
                                  'recovery_attempts': attempts_before + (0 if peer_live else 1),
                                  'created_at': _stamp()})
        try:
            with (worker_dir(root, worker['worker_id']) / 'supervisor.log').open('a', encoding='utf-8') as output:
                child = subprocess.Popen([
                    sys.executable, '-m', 'theory_master.worker_supervisor_dispatch',
                    '--research-root', str(root), '--worker', worker['worker_id'],
                    '--cycle', cycle.name, '--fingerprint', fingerprint,
                ], cwd=root, env=env, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            _save_json(receipt_path, {**_read_json(receipt_path), 'pid': child.pid,
                                       'pid_start_ticks': process_start_ticks(child.pid)})
        except OSError as exc:
            _save_json(receipt_path, {**_read_json(receipt_path), 'status': 'hold',
                                       'reason': f'Supervisor launch failed: {type(exc).__name__}'})
            continue
        return True
    return False


def review_worker(root: Path, worker_id: str, cycle_name: str, fingerprint: str) -> dict[str, Any]:
    """Review and patch only with an exclusive scheduler gate and fresh ownership checks."""
    root = Path(root).resolve()
    path = _receipt_path(root, worker_id)
    receipt = _read_json(path)
    if (receipt.get('cycle') != cycle_name or receipt.get('fingerprint') != fingerprint
            or not supervisor_enabled(root)):
        return {'status': 'hold', 'reason': 'Supervisor identity, configuration or incident changed'}
    gate = root / '.theory-master' / 'scheduler.lock'
    gate.parent.mkdir(parents=True, exist_ok=True)
    # Suppress new sibling launches and exact-Worker controls during repair.
    with gate.open('a+', encoding='utf-8') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        workers = list_workers(root)
        worker = next((w for w in workers if w['worker_id'] == worker_id), None)
        eligible = _eligible(root, worker) if worker else None
        if (eligible is None or eligible[0].name != cycle_name
                or _fingerprint(eligible[1]) != fingerprint):
            result = {'status': 'hold', 'reason': 'FAILED checkpoint / Worker owner changed'}
            _save_json(path, {**receipt, **result})
            return result
        peer_live = any(w['process_alive'] for w in workers)
        if runtime_health(root).get('active') or (
                peer_live and not _live_peer_safe_probe_candidate(eligible[1])):
            result = {'status': 'deferred_peers_active',
                      'reason': 'Another Runner is active; shared engine repair forbidden',
                      'retry_at_epoch': time.time() + 120}
            _save_json(path, {**receipt, **result})
            return result
        cycle, state = eligible
        from .observability.incident_registry import register_cycle_incident
        incident, _ = register_cycle_incident(
            cycle, severity='error', kind='system_failure',
            source_event='worker_supervisor.dispatch', code='run_failed',
            discriminator=f"{state.get('phase') or ''}:run_failed",
            summary=str((state.get('recovery') or {}).get('message') or 'FAILED Worker'),
            fields={'phase': state.get('phase'), 'worker_id': worker_id},
        )
        from .observability.incident_registry import record_repair_handoff
        record_repair_handoff(cycle, incident=incident, status='started',
                              target=f'sdk:worker_supervisor:{worker_id}', policy_version=2)
        receipt.update(status='reviewing', incident_id=incident['incident_id'])
        _save_json(path, receipt)
        from .supervisor import _supervisor_backend
        from .supervisor_continuity import review_and_repair_runtime_failure
        if peer_live:
            backend = None  # no paid Supervisor model call during parallel read-only probe
        else:
            try:
                backend = _supervisor_backend(root)
            except Exception:
                backend = None  # reviewer deterministically holds; never fabricate success
        review = review_and_repair_runtime_failure(
            backend=backend, research_root=root, cycle_dir=cycle, state=state,
            state_path=cycle / 'research_state.json', incident_id=incident['incident_id'],
            scheduler_locked=True,
            log_path=worker_dir(root, worker_id) / 'runner.log',
            **({'readonly_provider_probe_only': True} if peer_live else {}),
        )
        receipt.update(session_id=review.get('session_id'), action=review.get('action'))
        if review.get('engineering_skill') is not None:
            receipt['engineering_skill'] = review['engineering_skill']
        receipt['recovery_job'] = review.get('recovery_job')
        _save_json(path, receipt)
    # worker_action owns scheduler.lock itself: release above before resuming.
    if review.get('action') != 'resume':
        result = {'status': 'deferred_peers_active' if peer_live else 'hold',
                  'reason': str(review.get('reason') or 'Supervisor held')[:900]}
        if peer_live:
            result['retry_at_epoch'] = time.time() + 120
    else:
        try:
            from .specification import TaskSpec
            # worker_action rechecks claim fencing, checkpoint identity, account
            # allocation and no-live-owner before launching a new child.
            requested_at = _stamp()
            launched = worker_action(TaskSpec.from_task_root(root), worker_id, 'resume')
            result = {'status': 'resume_launched', 'resume_requested_at': requested_at,
                      'worker_pid': launched.get('pid'), 'reason': 'Awaiting real model/tool activity'}
        except (OSError, ValueError, RuntimeError) as exc:
            result = {'status': 'hold', 'reason': f'Worker-scoped resume blocked: {type(exc).__name__}: {exc}'[:900]}
            append_supervisor_verification(root / cycle_name, session_id=str(review.get('session_id') or ''),
                                           verified=False, detail=result['reason'])
    _save_json(path, {**_read_json(path), **result, 'updated_at': _stamp()})
    record_repair_handoff(
        root / cycle_name, incident=incident,
        status='resume_authorized' if result['status'] == 'resume_launched' else 'blocked',
        target=f'sdk:worker_supervisor:{worker_id}',
        error=None if result['status'] == 'resume_launched' else result['reason'],
        policy_version=2, review_ref=str(review.get('session_id') or ''),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research-root', type=Path, required=True)
    parser.add_argument('--worker', required=True)
    parser.add_argument('--cycle', required=True)
    parser.add_argument('--fingerprint', required=True)
    args = parser.parse_args(argv)
    # The parent writes the PID immediately after Popen. A fast helper may not
    # update the same JSON receipt until that write is visible.
    for _ in range(50):
        if _read_json(_receipt_path(args.research_root, args.worker)).get('pid'):
            break
        time.sleep(0.04)
    try:
        if not _read_json(_receipt_path(args.research_root, args.worker)).get('pid'):
            raise RuntimeError('Supervisor launcher did not establish process ownership')
        result = review_worker(args.research_root, args.worker, args.cycle, args.fingerprint)
    except Exception as exc:
        # Preserve the original failed Cycle and handoff failure independently.
        result = {'status': 'hold', 'reason':
                  f'Supervisor helper failed safely: {type(exc).__name__}: {str(exc)[:450]}'}
        path = _receipt_path(args.research_root, args.worker)
        _save_json(path, {**_read_json(path), **result, 'updated_at': _stamp()})
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('status') == 'resume_launched' else 1


if __name__ == '__main__':
    raise SystemExit(main())
