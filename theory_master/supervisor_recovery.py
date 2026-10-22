"""Durable operational recovery ownership, separate from scientific state.

Action execution requires scheduler.lock; dispatcher scheduling and session-bound
activity projection use atomic records. Retry windows limit individual actions;
they never dispose of an unresolved incident or grant a scientific resume.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .runtime import now, write_json

SCHEMA = 'theory-master/supervisor-recovery/v1'
OPERATIONAL_ATTENTION = frozenset({
    'runtime_failure', 'supervisor_unrecoverable', 'provider_model_capacity_timeout',
    'investigator_native_goal_unexpected_blocked', 'runtime_retry_exhausted',
    'runtime_no_recoverable_checkpoint',
})
RETRY_WINDOW = 3
BASE_DELAY = 300
MAX_DELAY = 21600


def failure_details(state: dict) -> dict:
    recovery = dict(state.get('recovery') or {})
    attention = state.get('attention') or {}
    if attention.get('code') in OPERATIONAL_ATTENTION and not recovery.get('message'):
        recovery.update(kind=recovery.get('kind') or 'RuntimeError',
                        message=attention.get('reason') or attention['code'], category='runtime')
    return recovery


def operational_incident(state: dict) -> bool:
    return (str(state.get('status') or '').lower() == 'failed' or
            (str(state.get('status') or '').lower() == 'needs_attention'
             and (state.get('attention') or {}).get('code') in OPERATIONAL_ATTENTION))


def stop_reason(state: dict) -> str | None:
    if state.get('abandoned_cycle') or state.get('phase') == 'abandon_requested':
        return 'abandoned_by_operator'
    if (state.get('recovery') or {}).get('action') in {'pause', 'interrupt'}:
        return 'operator_pause'
    if not operational_incident(state):
        return 'not_an_operational_incident'
    return None


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def incident_key(state: dict) -> str:
    error = failure_details(state)
    return digest([state.get('cycle_id'), state.get('worker_id'), state.get('hypothesis_lock'),
                   state.get('phase'), error.get('kind'), error.get('message'), state.get('attention')])


def inputs(root: Path, state: dict) -> str:
    """Cheap wake evidence: code, configuration, checkpoints, guards and controls."""
    engine = Path(__file__).resolve().parent
    paths = list(engine.rglob('*.py')) + list((engine / 'resources/identities').glob('*.md'))
    paths += [root / '.theory-master/env', root / 'config.toml',
              root / '.theory-master/web-role-routes.json', root / 'theory-master.config.json']
    # The canonical Task Home may be outside research_root.
    from .supervisor_incident_session import _incident_chat_config_path
    try:
        canonical = _incident_chat_config_path(root)
        if canonical:
            paths.append(canonical)
    except (OSError, ValueError, KeyError):
        pass
    checkpoint = state.get('last_safe_checkpoint')
    if checkpoint:
        paths.append(Path(str(checkpoint)))
    paths.append(root / str(state.get('cycle_id') or '') / 'recovery_control.json')
    hashes = {}
    for path in paths:
        if '__pycache__' in path.parts:
            continue
        try:
            hashes[str(path)] = (hashlib.sha256(path.read_bytes()).hexdigest()
                                 if not path.is_symlink() else 'symlink')
        except OSError:
            hashes[str(path)] = 'missing'
    return digest([hashes, state.get('protocol_health'), state.get('investigator_budget'),
                   state.get('resumable'), state.get('recovery'), state.get('attention')])


def path_for(cycle: Path) -> Path:
    return cycle / 'supervisor/recovery.json'


def read_job(cycle: Path) -> dict:
    path = path_for(cycle)
    if any(p.is_symlink() for p in (cycle, path.parent, path)):
        raise ValueError('Unsafe Supervisor recovery record')
    if not path.exists():
        return {}
    job = json.loads(path.read_text())
    if not isinstance(job, dict) or job.get('schema') != SCHEMA:
        raise ValueError('Invalid Supervisor recovery record')
    return job


def save_job(cycle: Path, job: dict) -> dict:
    if cycle.is_symlink() or (cycle / 'supervisor').is_symlink() or path_for(cycle).is_symlink():
        raise ValueError('Unsafe Supervisor recovery directory')
    job['updated_at'] = now()
    write_json(path_for(cycle), job)
    return job


def ensure_job(cycle: Path, state: dict) -> dict:
    old = read_job(cycle)
    key = incident_key(state)
    if old.get('incident_key') == key:
        return old
    if old:
        archive = cycle / 'supervisor' / ('recovery-' + digest(old) + '.json')
        if not archive.exists():
            write_json(archive, old)
    return save_job(cycle, {
        'schema': SCHEMA, 'incident_key': key, 'cycle': cycle.name,
        'recovery_owner': 'sdk:worker_supervisor:' + str(state['worker_id']) if state.get('worker_id')
                          else 'sdk:runtime_supervisor',
        'status': 'pending', 'current_action': 'diagnose', 'plan_revision': 0,
        'action_attempt': 0, 'next_wakeup_epoch': 0, 'blocking_dependency': None,
        'last_verified_progress': None, 'owner_pid': None,
        'failure': failure_details(state), 'history': [],
    })


def defer_job(cycle: Path, state: dict, *, dependency: str, action: str, delay: int = 120) -> dict:
    """Record a scheduling prerequisite without pretending a model action ran."""
    job = ensure_job(cycle, state)
    if job.get('status') == 'working':
        return job
    if job.get('blocking_dependency') == dependency and job.get('current_action') == action:
        return job
    job.update(status='waiting', owner_pid=None, current_action=action,
               blocking_dependency=dependency, next_wakeup_epoch=time.time() + delay,
               input_digest=inputs(cycle.parent, state))
    return save_job(cycle, job)


def due(cycle: Path, state: dict, *, at: float | None = None) -> bool:
    if stop_reason(state):
        return False
    job = read_job(cycle)
    if not job or job.get('incident_key') != incident_key(state):
        return True
    if job.get('status') == 'working':
        from .runtime_manager import process_identity_alive
        return not process_identity_alive(job.get('owner_pid'), job.get('owner_start_ticks'))
    if job.get('status') in {'stopped', 'activity_verified'}:
        # A fresh recorded failure occurrence still needs a new review.
        return job.get('input_digest') != inputs(cycle.parent, state)
    return ((time.time() if at is None else at) >= float(job.get('next_wakeup_epoch') or 0)
            or job.get('input_digest') != inputs(cycle.parent, state))


def begin_action(cycle: Path, state: dict) -> dict:
    from .runtime_manager import process_start_ticks
    job = ensure_job(cycle, state)
    job['owner_start_ticks'] = process_start_ticks(os.getpid())
    job.update(status='working', owner_pid=os.getpid(), input_digest=inputs(cycle.parent, state),
               action_attempt=int(job.get('action_attempt') or 0) + 1,
               action_id=digest([job['incident_key'], job.get('plan_revision'),
                                 job.get('current_action'), job.get('action_attempt'), time.time_ns()]),
               next_wakeup_epoch=time.time() + MAX_DELAY)
    return save_job(cycle, job)


def finish_action(cycle: Path, state: dict, result: dict) -> dict:
    job = ensure_job(cycle, state)
    reason = str(result.get('reason') or result.get('diagnosis') or 'Independent verification pending')[:2000]
    phase = 'resume_authorized' if result.get('action') == 'resume' else 'waiting'
    revision = int(job.get('plan_revision') or 0)
    if phase == 'resume_authorized':
        action, dependency, delay = 'verify_runtime_activity', 'official_runner_launch', BASE_DELAY
    else:
        validation = (result.get('engineering_skill') or {}).get('validation') or {}
        action = 'prepare_validation' if validation.get('validator') is None and 'validator' in reason.lower() else 'replan'
        dependency = reason
        delay = min(MAX_DELAY, BASE_DELAY * 2 ** min(revision // RETRY_WINDOW, 6))
        revision += 1
    history = list(job.get('history') or [])
    history.append({'at': now(), 'action_id': job.get('action_id'),
                    'action': job.get('current_action'), 'result': reason,
                    'session_id': result.get('session_id'),
                    'validation': {key: value for key, value in ((result.get('engineering_skill') or {}).get('validation') or {}).items()
                                   if key in {'phase', 'validator', 'reason', 'evidence_path'}}})
    job.update(status=phase, owner_pid=None, current_action=action,
               plan_revision=revision, blocking_dependency=dependency,
               next_wakeup_epoch=time.time() + delay, input_digest=inputs(cycle.parent, state),
               history=history[-12:], last_result=reason, resume_session_id=result.get('session_id') if phase == 'resume_authorized' else None)
    return save_job(cycle, job)


def record_activity(cycle: Path, *, verified: bool, detail: str, session_id: str) -> None:
    job = read_job(cycle)
    if not job or job.get('resume_session_id') != session_id:
        return
    job.update(status='activity_verified' if verified else 'waiting', owner_pid=None,
               current_action='monitor_runtime' if verified else 'replan',
               next_wakeup_epoch=time.time() + BASE_DELAY,
               blocking_dependency=None if verified else detail)
    if verified:
        job['last_verified_progress'] = {'at': now(), 'detail': detail, 'scope': 'model_or_tool_activity_only'}
    save_job(cycle, job)


def projection(cycle: Path) -> dict:
    job = read_job(cycle)
    if not job:
        return {}
    from .runtime_manager import process_identity_alive
    return {key: job.get(key) for key in (
        'recovery_owner', 'status', 'current_action', 'next_wakeup_epoch',
        'blocking_dependency', 'last_verified_progress', 'plan_revision', 'owner_pid')
    } | {'executing': bool(job.get('status') == 'working' and process_identity_alive(job.get('owner_pid'), job.get('owner_start_ticks')))}
