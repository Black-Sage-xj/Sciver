"""Autonomous engineering skill dispatch for the single Supervisor.

Called under either Runner mode's exclusive scheduler lock. An engineering
skill attempt is never, by itself, proof of repair or authorization to resume.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any


def engineering_skill_eligible(state: dict[str, Any], review: dict[str, Any]) -> bool:
    """Do not invoke an edit-capable model for science, quota, or unsafe recovery."""
    if review.get("action") != "hold":
        return False
    if state.get("abandoned_cycle") or state.get("phase") == "abandon_requested":
        return False
    if not state.get("resumable"):
        return False
    if (state.get("recovery") or {}).get("action") in {"pause", "interrupt"}:
        return False
    budget = state.get("investigator_budget") or {}
    if isinstance(budget, dict) and (budget.get("exhausted") or budget.get("rejected")):
        return False
    checkpoint = Path(str(state.get("last_safe_checkpoint") or ""))
    if not ((checkpoint.is_file() and not checkpoint.is_symlink()) or state.get("role_outputs")):
        return False
    from .observability.incident_routing import classify_incident
    from .supervisor_recovery import failure_details
    recovery = failure_details(state)
    route = classify_incident(
        severity="error", kind="system_failure", code="run_failed",
        summary=str(recovery.get("message") or ""),
    )
    if route["nature"] not in {"engineering_failure", "provider_configuration"}:
        return False
    from .provider_capacity import is_model_at_capacity
    if is_model_at_capacity(recovery.get("message")):
        return False  # transient provider recovery is not an edit permission
    return True


def attempt_engineering_skill(root: Path, cycle: Path, state: dict[str, Any],
                              review: dict[str, Any]) -> dict[str, Any] | None:
    """Run engineering skill once; a later triage/verified activity is mandatory."""
    if not engineering_skill_eligible(state, review):
        return None
    try:
        from .supervisor_incident_session import (
            publish_review_incident,
        )
        violation = state.get("protocol_health") == "violation"
        record = publish_review_incident(
            research_root=root, cycle_dir=cycle, state=state,
            trigger_type="protocol_violation" if violation else "hold",
            trigger_event="protocol_health=violation" if violation else "run_failed",
            analysis=None, model_invoked=False,
            system_note="Automated engineering skill dispatch: "
                        + str(review.get("repair_suggestion") or review.get("reason") or "Diagnose unresolved operational failure")[:6000],
            tool_reads=None,
            audit={"source": "supervisor_continuity",
                   "supervisor_session_id": review.get("session_id")},
        )
        if not record:
            return {"status": "engineering_unavailable", "reason": "No incident session"}
        from .supervisor import _supervisor_backend
        from .supervisor_intervention import supervisor_model, DEFAULT_PROVIDER
        from .supervisor_candidate_repair import attempt_candidate_repair
        from .supervisor_configuration_repair import attempt_configuration_repair, configuration_failure
        backend = _supervisor_backend(root)
        repair = attempt_configuration_repair if configuration_failure(state) else attempt_candidate_repair
        outcome = repair(root=root, cycle=cycle, state=state, review=review,
                         backend=backend, model=supervisor_model(root), provider=DEFAULT_PROVIDER)
        if state.get('supervisor_repair_receipt'):
            from .runtime import write_json
            import json
            current = json.loads((cycle / 'research_state.json').read_text())
            if {k: v for k, v in current.items() if not k.startswith('supervisor_')} != {
                    k: v for k, v in state.items() if not k.startswith('supervisor_')}:
                raise ValueError('Cycle changed during repair; receipt not installed')
            write_json(cycle / 'research_state.json', state)
        return {'status': 'engineering_attempted', 'incident_session_id': str(record['id']),
                'validation': outcome, 'repair_phase': outcome['phase'],
                'verification': 'pending_runtime_recheck'}
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        from .supervisor_incident_session import _redact
        return {"status": "engineering_failed", "reason": _redact(f'{type(exc).__name__}: {exc}', 700),
                "verification": "pending_runtime_recheck"}


def engineering_recheck_ready(cycle: Path, state: dict[str, Any]) -> bool:
    """Only an independent validator may advance an engineering reply to resume.

    Additional fault families require their own validators. Model prose and
    successful tool exits are never a substitute for one.
    """
    from .supervisor_repair_receipts import inspect_repair_receipt
    try:
        inspect_repair_receipt(cycle, state)
        return True
    except (OSError, ValueError, TypeError, KeyError):
        pass
    from .budget_reconciliation import inspect_legacy_failure
    try:
        inspect_legacy_failure(cycle, state)
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _review_once(**kwargs: Any) -> dict[str, Any]:
    """One bounded pipeline for both launchers; the caller owns scheduler.lock.

    The caller releases that lock before invoking its official resume adapter.
    Provider peer probes never enter this writable continuation.
    """
    from .runtime_supervisor_review import review_runtime_failure
    from .supervisor_repair_receipts import (
        failure_binding, source_manifest, validate_engineering_changes, validate_existing_method,
    )
    from .supervisor_runtime_repair import repair_target, _log_for_repair
    from .runtime import write_json
    import json

    review = review_runtime_failure(**kwargs)
    if kwargs.get('readonly_provider_probe_only'):
        return review
    root, cycle, state = kwargs['research_root'], kwargs['cycle_dir'], kwargs['state']
    if not engineering_skill_eligible(state, review):
        return review
    if not kwargs.get('scheduler_locked'):
        return {**review, 'reason': 'Engineering requires the exclusive scheduler gate'}
    engine = Path(__file__).resolve().parent
    try:
        binding = failure_binding(cycle, state)
        before = source_manifest(engine)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {**review, 'reason': f'Engineering evidence unavailable: {exc}'}
    from .supervisor_recovery import read_job
    job = read_job(cycle)
    review = {**review, 'repair_suggestion': (
        str(review.get('repair_suggestion') or review.get('reason') or 'Diagnose unresolved failure')
        + '\nRecovery plan revision: ' + str(job.get('plan_revision', 0))
        + '; next action: ' + str(job.get('current_action') or 'replan')
        + '\nPrior attempts and rejected evidence: '
        + json.dumps(job.get('history') or [], ensure_ascii=False)[-7000:]
        + '\nUse the prior failure to select a new action or exact dependency. Do not repeat an unchanged rejected patch.'
    )}
    attempts = int(state.get('supervisor_engineering_attempts') or 0)
    target = repair_target(_log_for_repair(root, log_path=kwargs.get('log_path')), state, engine)
    # Persist intent before the paid call. The durable job supplies action-scoped
    # history and bounded wake cadence across process restarts.
    state['supervisor_engineering_attempts'] = attempts + 1
    state['supervisor_engineering_handoff'] = {
        'phase': 'started', 'failure_binding': binding, 'source_manifest': before,
        'reason': 'Engineering turn started; validation pending'}
    write_json(kwargs['state_path'], state)
    if kwargs.get('log_path'):
        review['log_path'] = str(kwargs['log_path'])
    engineering = attempt_engineering_skill(root, cycle, state, review)
    if engineering is None:
        engineering = {'status': 'engineering_unavailable', 'repair_phase': 'blocked'}
    validation = {'phase': 'blocked', 'reason': engineering.get('reason') or 'No passing objective repair validation'}
    try:
        if json.loads(kwargs['state_path'].read_text()) != state:
            raise ValueError('Persisted Cycle state changed during engineering')
        if engineering_recheck_ready(cycle, state):
            validation = {'phase': 'repair_validated', 'reason': 'Existing SDK evidence revalidated'}
        elif engineering.get('validation'):
            validation = engineering['validation']
        elif engineering.get('status') in {'engineering_failed', 'engineering_unavailable'}:
            pass
        else:
            validation = validate_existing_method(
                cycle, state, engine=engine, log=_log_for_repair(root, log_path=kwargs.get('log_path')))
            if validation is None:
                validation = validate_engineering_changes(
                    cycle, state, engine=engine, target=target, before=before)
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        validation = {'phase': 'blocked', 'reason': f'SDK revalidation failed: {exc}'}
    engineering = {**engineering, 'validation': validation,
                   'repair_phase': validation['phase'], 'failure_binding': binding,
                   'reason': validation.get('reason') or validation.get('tests', '')}
    try:
        engineering['source_manifest'] = source_manifest(engine)
    except (OSError, ValueError):
        engineering['source_manifest'] = before
    # Do not overwrite operator state if it changed while a model was running.
    current = json.loads(kwargs['state_path'].read_text())
    if {k: v for k, v in current.items() if not k.startswith('supervisor_')} != {
            k: v for k, v in state.items() if not k.startswith('supervisor_')}:
        return {**review, 'reason': 'Cycle state changed during engineering; resume denied',
                'engineering_skill': engineering}
    state['supervisor_engineering_handoff'] = engineering
    write_json(kwargs['state_path'], state)
    if engineering.get('incident_session_id'):
        from .supervisor_incident_session import record_engineering_validation
        record_engineering_validation(root, engineering['incident_session_id'], validation,
                                      state.get('supervisor_repair_receipt'))
    if validation['phase'] == 'repair_validated':
        review = review_runtime_failure(**{**kwargs, 'backend': None})
    else:
        review = {**review, 'reason': 'Engineering blocked: ' + str(validation.get('reason'))}
    return {**review, 'engineering_skill': engineering}


def review_and_repair_runtime_failure(**kwargs: Any) -> dict[str, Any]:
    """Execute one due action while retaining durable responsibility on hold."""
    from .supervisor_recovery import (operational_incident, stop_reason, due,
                                     begin_action, finish_action, projection, read_job)
    from .runtime_supervisor_review import review_runtime_failure
    cycle, state = kwargs['cycle_dir'], kwargs['state']
    if kwargs.get('readonly_provider_probe_only'):
        return review_runtime_failure(**kwargs)
    if not kwargs.get('scheduler_locked'):
        return {'action': 'hold', 'reason': 'Engineering requires the exclusive scheduler gate',
                'repair_suggestion': ''}
    if stop_reason(state):
        return review_runtime_failure(**kwargs) if operational_incident(state) else {
            'action': 'hold', 'reason': stop_reason(state), 'repair_suggestion': ''}
    if not due(cycle, state):
        job = read_job(cycle)
        return {'action': 'hold', 'reason': job.get('last_result') or 'Recovery action is waiting for its wake condition',
                'repair_suggestion': '', 'recovery_job': projection(cycle),
                'engineering_skill': state.get('supervisor_engineering_handoff')}
    begin_action(cycle, state)
    try:
        result = _review_once(**kwargs)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        result = {'action': 'hold', 'reason': f'Recovery action failed: {type(exc).__name__}: {str(exc)[:700]}',
                  'repair_suggestion': 'Diagnose the failed recovery tool and replan safely.'}
    import json
    current = json.loads(kwargs['state_path'].read_text())
    if stop_reason(current):
        from .supervisor_recovery import save_job
        job = read_job(cycle)
        job.update(status='stopped', owner_pid=None, current_action='respect_stop',
                   next_wakeup_epoch=None, blocking_dependency=stop_reason(current))
        save_job(cycle, job)
        return {'action': 'hold', 'reason': stop_reason(current), 'recovery_job': projection(cycle)}
    finish_action(cycle, current, result)
    return {**result, 'recovery_job': projection(cycle)}
