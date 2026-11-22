"""Operator-bound rescan or bounded computation after a blocked native Goal.

This validates execution authority and immutable inputs. The Investigator still
owns all scientific claims; an authorization is never a Finding or a verdict.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import stat
from pathlib import Path

SCHEMA = 'theory-master/operator-technical-recovery/v1'
VALIDATOR = 'operator-technical-recovery-reentry/v1'
BOUNDED_COMPUTATION = 'bounded_computation_continuation'
ARTIFACT_RESCAN = 'verified_artifact_rescan'
PREPARATION = 'operator_verified_artifact_new_goal'
AUTHORIZATION_FILE = 'attention/technical-recovery-authorization.json'
AUDIT_FILES = ('events.jsonl', 'runtime_events.jsonl', 'invocations.jsonl')
_SETUP_CONFLICT = 'unfinished native Goal changed thread or contract'


def _read(path: Path, inputs: dict[str, str], limit: int = 128 * 1024 * 1024) -> bytes:
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError('unsafe technical recovery input')
    if path.stat().st_size > limit:
        raise ValueError('technical recovery input exceeds its bound')
    data = path.read_bytes()
    inputs[str(path.resolve())] = hashlib.sha256(data).hexdigest()
    return data


def _dispatcher_lock_snapshot(workspace: Path, values: list[str]) -> dict[str, list[int]]:
    """Probe the declared locks without starting, signalling or clearing work."""
    if (not isinstance(values, list) or len(values) > 64
            or not all(isinstance(value, str) for value in values)
            or len(set(values)) != len(values)):
        raise ValueError('invalid dispatcher lock inventory')
    snapshot = {}
    for value in values:
        path = Path(value)
        if (not path.is_absolute() or path != path.resolve()
                or not path.is_relative_to(workspace)
                or any(part.is_symlink() for part in (path, *path.parents))):
            raise ValueError('unsafe dispatcher lock path')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            details = os.fstat(fd)
            if not stat.S_ISREG(details.st_mode):
                raise ValueError('dispatcher lock is not a regular file')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError(f'compute is still owned: {path.name}') from exc
            snapshot[str(path)] = [details.st_dev, details.st_ino]
        finally:
            os.close(fd)
    return snapshot


def _continuation_limits(authorization: dict) -> dict:
    limits = authorization.get('limits')
    fields = ('max_workers', 'threads_per_worker', 'cpu_cores', 'max_unit_seconds')
    if (not isinstance(limits, dict) or set(limits) != set(fields)
            or any(type(limits[key]) is not int or limits[key] <= 0 for key in fields)
            or limits['max_workers'] * limits['threads_per_worker'] > limits['cpu_cores']
            or not authorization.get('dispatcher_locks')):
        raise ValueError('bounded continuation requires a positive execution envelope and dispatcher locks')
    return limits


def _retired_origins(ledger, failed: dict, blocked: dict) -> list[str]:
    """Exclude the blocked operation and its verified earlier generations."""
    invocation = failed['invocation_id']
    origin = failed.get('native_goal_origin') or {}
    root_id = origin.get('invocation_id') or invocation

    def same_slot(record: dict) -> bool:
        return (bool(failed.get('logical_key')) and record.get('logical_key') == failed['logical_key']
                and record.get('thread_id') == failed['thread_id']
                and record.get('role') == 'investigator' and record.get('stage') == failed['stage']
                and record.get('commit_state') == 'not_committed' and not record.get('closure_hash'))

    if root_id != invocation:
        if (not re.fullmatch(r'inv-[A-Za-z0-9_-]+', str(root_id))
                or not same_slot(ledger.snapshot(root_id) or {})):
            raise ValueError('original blocked invocation lineage changed')

    retired = {invocation, root_id}
    for record in ledger.snapshots():
        if not same_slot(record):
            continue
        identity = record.get('native_goal_identity') or {}
        older = (record.get('state') in {'interrupted', 'superseded'}
                 and identity.get('thread_id') == blocked.get('thread_id')
                 and identity.get('objective_sha256') == blocked.get('objective_sha256')
                 and type(identity.get('created_at')) is int
                 and identity['created_at'] < blocked['created_at'])
        if older or (record.get('native_goal_origin') or {}).get('invocation_id') == root_id:
            retired.add(record['invocation_id'])
    return sorted(retired)


def technical_reentry_key(state: dict) -> str:
    from .supervisor_repair_receipts import _digest
    native = state.get('native_goal') or {}
    return _digest([state.get('last_invocation_id'), native.get('thread_id'), native.get('objective_sha256')])


def _judge_rework_binding(cycle: Path, state: dict, inputs: dict[str, str]) -> dict:
    """Bind an artifact rescan to the existing, unfulfilled Judge request."""
    from .scientific_objective import frozen_science_digest

    authorization = state.get('rework_authorization') or {}
    judge = (state.get('role_outputs') or {}).get('judge') or {}
    attempt = state.get('investigation_attempt')
    record = (state.get('investigation_attempts') or {}).get(f'attempt-{attempt:04d}') if type(attempt) is int else None
    refs = state.get('evidence_refs')
    if (authorization.get('authorized') is not True
            or authorization.get('target_executor') != 'investigator'
            or authorization.get('attempt') != state.get('judge_rework_count')
            or judge.get('assessment_state') != 'rework_requested'
            or judge.get('verdict') is not None
            or authorization.get('judge_request') != judge
            or not isinstance(judge.get('objections'), list) or not judge['objections']
            or not isinstance(record, dict) or record.get('judge_feedback') != judge
            or not isinstance(record.get('objections'), list) or not record['objections']
            or record.get('outputs') != [] or state.get('next_executor') != 'investigator'
            or not isinstance(refs, list) or not refs
            or not all(isinstance(ref, str) and ref for ref in refs)):
        raise ValueError('Judge artifact rescan requires the original unfulfilled rework and Evidence')
    inventory = {}
    for name in ('registry.jsonl', 'registration_batches.jsonl', 'lifecycle.jsonl',
                 'assessments.jsonl', 'criteria.json'):
        path = cycle / 'evidence' / name
        if path.exists() or path.is_symlink():
            _read(path, inputs, 16_000_000)
            inventory[str(path)] = inputs[str(path.resolve())]
        else:
            inventory[str(path)] = None
    registry = cycle / 'evidence/registry.jsonl'
    if inventory[str(registry)] is None:
        raise ValueError('Judge artifact rescan has no registered Evidence')
    rows = [json.loads(line) for line in _read(registry, inputs, 16_000_000).splitlines() if line.strip()]
    if (not all(isinstance(row, dict) for row in rows)
            or not set(refs).issubset({row.get('evidence_id') for row in rows})):
        raise ValueError('Judge artifact rescan Evidence identity changed')
    return {'stage': 'judge_rework',
            'frozen_objective_sha256': frozen_science_digest(state.get('hypothesis_lock') or {}),
            'rework_authorization': copy.deepcopy(authorization), 'judge_output': copy.deepcopy(judge),
            'investigation_attempt': attempt, 'judge_rework_count': state.get('judge_rework_count'),
            'objections': copy.deepcopy(record['objections']), 'evidence_refs': list(refs),
            'evidence_inventory': inventory}


def _recheck_rework(cycle: Path, state: dict, proof: dict, inputs: dict[str, str]) -> None:
    if proof.get('stage', 'investigate') != 'judge_rework':
        return
    if (proof.get('mode') != ARTIFACT_RESCAN
            or _judge_rework_binding(cycle, state, inputs) != proof.get('judge_rework_binding')):
        raise ValueError('Judge artifact rescan changed its original rework or Evidence')


def check_authorization(cycle: Path, state: dict, *, goal_reader=None) -> tuple[dict, dict]:
    """Check a stopped failed slot and operator-only authorization, without models."""
    from .invocations import InvocationLedger
    from .native_goal_recovery import verify_existing_goal
    from .runtime_supervisor_review import resume_blocker
    from .scientific_objective import frozen_science_digest, frozen_science_view
    from .supervisor_native_goal_recovery import read_existing_goal

    cycle = cycle.absolute()
    native, recovery = state.get('native_goal') or {}, state.get('recovery') or {}
    invocation, thread = state.get('last_invocation_id'), native.get('thread_id')
    control = cycle / 'recovery_control.json'
    science = state.get('hypothesis_lock') or {}
    budget = state.get('investigator_budget') or {}
    remaining = budget.get('remaining_tokens')
    if (state.get('status') != 'failed' or state.get('execution_active')
            or state.get('protocol_health') != 'valid' or state.get('scientific_verdict') is not None
            or state.get('active_invocation_id') is not None
            or recovery.get('action') != 'error' or recovery.get('kind') != 'NativeGoalRuntimeFailure'
            or native.get('status') != 'blocked' or native.get('provider_failure')
            or native.get('runtime_failure_kind') != 'native_goal_dependency_blocked'
            or not isinstance(invocation, str) or not re.fullmatch(r'inv-[A-Za-z0-9_-]+', invocation)
            or not thread or (state.get('threads') or {}).get('investigator') != thread
            or science.get('status') != 'locked' or not science.get('id')
            or len(frozen_science_view(science)) != 3 or not state.get('last_safe_checkpoint')
            or budget.get('hard_cap_reached') or type(remaining) is not int or remaining <= 0
            or technical_reentry_key(state) in (state.get('supervisor_technical_operations') or [])
            or resume_blocker(state, for_repair=True, runtime_revalidated=True) is not None
            or control.exists() or control.is_symlink()):
        raise ValueError('not a safely stopped native Goal dependency failure')
    stage = (state.get('last_invocation_failure') or {}).get('stage')
    if stage not in {'investigate', 'judge_rework'}:
        raise ValueError('unsupported technical recovery stage')
    failure = {'invocation_id': invocation, 'role': 'investigator', 'stage': stage,
               'failure_phase': 'invoke', 'error_type': 'NativeGoalRuntimeFailure'}
    if state.get('last_invocation_failure') != failure:
        raise ValueError('failed invocation identity changed')
    inputs = {}
    path = cycle / AUTHORIZATION_FILE
    authorization = json.loads(_read(path, inputs, 1_048_576))
    if (not isinstance(authorization, dict) or authorization.get('schema') != SCHEMA
            or authorization.get('mode') not in {ARTIFACT_RESCAN, BOUNDED_COMPUTATION}
            or authorization.get('authorization_source') not in {'human', 'delegated_operator'}
            or not str(authorization.get('user_instruction') or '').strip()
            or not str(authorization.get('scope') or '').strip()
            or authorization.get('cycle') != str(cycle.resolve())
            or authorization.get('failed_invocation_id') != invocation
            or authorization.get('thread_id') != thread
            or authorization.get('stage', stage) != stage
            or frozen_science_digest(authorization.get('frozen_objective') or {})
                != frozen_science_digest(science)):
        raise ValueError('operator authorization does not bind this frozen failure')
    workspace = cycle / 'workspace'
    if workspace != workspace.resolve() or not workspace.is_dir():
        raise ValueError('unsafe technical recovery workspace')
    mode = authorization['mode']
    rework = None
    if stage == 'judge_rework':
        if mode != ARTIFACT_RESCAN or authorization.get('stage') != stage:
            raise ValueError('Judge rework permits only an explicitly bound artifact rescan')
        rework = _judge_rework_binding(cycle, state, inputs)
        if authorization.get('judge_rework_binding') != rework:
            raise ValueError('operator authorization changed its Judge rework binding')
        persisted = json.loads(_read(cycle / 'research_state.json', {}, 16_000_000))
        if _judge_rework_binding(cycle, persisted, {}) != rework:
            raise ValueError('persisted Judge rework differs from the authorized state')
    limits = _continuation_limits(authorization) if mode == BOUNDED_COMPUTATION else None
    locks = _dispatcher_lock_snapshot(workspace, authorization.get('dispatcher_locks', []))
    for key in ('source_script', 'recovery_script'):
        source = Path(str(authorization.get(key) or ''))
        if not source.is_absolute() or source != source.resolve() or not source.is_relative_to(workspace):
            raise ValueError('technical source is outside the original workspace')
        data = _read(source, inputs)
        if hashlib.sha256(data).hexdigest() != authorization.get(key + '_sha256'):
            raise ValueError('technical source changed after authorization')
    artifacts = authorization.get('artifact_manifest')
    if not isinstance(artifacts, dict) or not 1 <= len(artifacts) <= 64:
        raise ValueError('no bounded computation artifact manifest')
    for name, expected in artifacts.items():
        source = Path(name)
        if (not source.is_absolute() or source != source.resolve() or not source.is_relative_to(workspace)
                or not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected)
                or hashlib.sha256(_read(source, inputs)).hexdigest() != expected):
            raise ValueError('repaired artifact identity changed')
    guidance_path = cycle / 'attention/operator_guidance.json'
    guidance = json.loads(_read(guidance_path, inputs, 1_048_576))
    guidance_text = _read(cycle / 'attention/operator_guidance.md', inputs, 1_048_576).decode().strip()
    if (not isinstance(guidance, dict) or guidance.get('status') != 'pending'
            or guidance.get('attention_code') != 'operator_technical_recovery'
            or guidance.get('text') != guidance_text or str(path) not in guidance_text):
        raise ValueError('the resumed Investigator has no matching operator direction')
    for name in AUDIT_FILES:
        _read(cycle / name, inputs, 16_000_000)
    ledger = InvocationLedger(cycle / 'invocations.jsonl')
    record = ledger.snapshot(invocation) or {}
    if (record.get('state') != 'failed' or record.get('commit_state') != 'not_committed'
            or record.get('role') != 'investigator' or record.get('stage') != stage
            or record.get('error_type') != 'NativeGoalRuntimeFailure'
            or record.get('thread_id') != thread or record.get('closure_hash')
            or any((cycle / folder / (invocation + '.json')).exists()
                   for folder in ('semantic_closure', 'workspace/.tm-handoff'))):
        raise ValueError('the failed invocation already changed or committed')
    previous = record.get('native_goal_identity') or (record.get('native_goal_origin') or {}).get('persistent_goal')
    if not isinstance(previous, dict) or previous.get('thread_id') != thread:
        raise ValueError('the original native Goal has no recorded identity')
    reader = goal_reader or read_existing_goal
    goal = reader(cycle, {'thread_id': thread})
    observed = verify_existing_goal(goal, {'thread_id': thread,
        'objective_sha256': previous['objective_sha256']}, previous=previous)
    if observed['status'] != 'blocked':
        raise ValueError('the original native Goal is no longer terminal blocked')
    return {'resume_preparation': PREPARATION, 'stage': stage,
            **({'judge_rework_binding': rework} if rework is not None else {}),
            'origin_invocation_id': invocation,
            'origin_exclusion_ids': _retired_origins(ledger, record, observed),
            'blocked_thread_id': thread, 'blocked_goal_identity': observed,
            'frozen_objective_sha256': frozen_science_digest(science),
            'operator_authorization': str(path), 'artifact_manifest': artifacts,
            'mode': mode, 'limits': limits, 'dispatcher_locks': locks,
            'scientific_verdict': None, 'new_scientific_cycle': False}, inputs



def _matches_digest(data: bytes, digest: str, *, allow_appended_records: bool) -> bool:
    if hashlib.sha256(data).hexdigest() == digest:
        return True
    if not allow_appended_records:
        return False
    prefix = hashlib.sha256()
    for line in data.splitlines(keepends=True):
        prefix.update(line)
        if prefix.hexdigest() == digest:
            return True
    return False


def _consumed_proof(cycle: Path, state: dict, inputs: dict[str, str]) -> dict:
    from .supervisor_repair_receipts import SCHEMA as RECEIPT_SCHEMA, _digest

    audit = (state.get('supervisor_technical_reentry_audit') or [{}])[-1]
    ref = audit['repair_receipt']
    receipt_path = Path(ref['path'])
    if receipt_path.parent != cycle / 'supervisor/repairs':
        raise ValueError('technical continuation receipt is outside this Cycle')
    receipt = json.loads(_read(receipt_path, inputs, 1_048_576))
    proof = recheck_receipt_inputs(receipt, state=state)
    if (receipt.get('schema') != RECEIPT_SCHEMA or receipt.get('cycle') != str(cycle)
            or receipt.get('phase') != 'repair_validated' or _digest(receipt) != ref.get('sha256')
            or receipt.get('repair_identity') != ref.get('repair_identity')
            or ref['repair_identity'] not in (state.get('supervisor_technical_reentries') or [])
            or ref['repair_identity'] not in (state.get('supervisor_consumed_repairs') or [])
            or proof.get('setup_retry_invocation') or any(audit.get(key) != val for key, val in proof.items())):
        raise ValueError('the original continuation receipt is no longer bound to the stopped Cycle')
    dependencies = receipt.get('extra_inputs') or {}
    appendable = {str(cycle / name) for name in AUDIT_FILES}
    if not appendable.issubset(dependencies) or proof['operator_authorization'] not in dependencies:
        raise ValueError('the original continuation has no immutable evidence binding')
    for filename, expected in dependencies.items():
        allow_append = filename in appendable
        data = _read(Path(filename), inputs, 16_000_000 if allow_append else 128 * 1024 * 1024)
        if not _matches_digest(data, expected, allow_appended_records=allow_append):
            raise ValueError('the original continuation inputs changed')
    return proof


def _require_replacement_goal_absent(cycle: Path, thread: str) -> None:
    from .supervisor_native_goal_recovery import read_existing_goal

    try:
        goal = read_existing_goal(cycle, {'thread_id': thread})
    except ValueError as error:
        provider = error.__cause__
        if provider is None:
            raise
        from openai_codex.errors import InvalidRequestError
        # A setup-only thread can disappear when the local app server exits.
        # Only the exact official missing-thread response proves absence here.
        if (isinstance(provider, InvalidRequestError) and provider.code == -32600
                and provider.message == f'thread not found: {thread}'):
            return
        raise
    if goal is not None:
        raise ValueError('the replacement thread already has a Goal')


def _check_setup_retry(cycle: Path, state: dict) -> tuple[dict, dict]:
    """Retry a consumed continuation once when it failed before creating a Goal."""
    from .invocations import InvocationLedger
    from .native_goal_recovery import verify_existing_goal
    from .runtime_supervisor_review import resume_blocker
    from .scientific_objective import frozen_science_digest
    from .supervisor_native_goal_recovery import read_existing_goal

    native, recovery = state.get('native_goal') or {}, state.get('recovery') or {}
    science, budget = state.get('hypothesis_lock') or {}, state.get('investigator_budget') or {}
    invocation, thread = state.get('last_invocation_id'), (state.get('threads') or {}).get('investigator')
    control = cycle / 'recovery_control.json'
    remaining = budget.get('remaining_tokens')
    if (state.get('status') != 'failed' or state.get('execution_active')
            or state.get('protocol_health') != 'valid' or state.get('scientific_verdict') is not None
            or state.get('active_invocation_id') is not None
            or recovery.get('action') != 'error' or recovery.get('kind') != 'NativeGoalRecoveryConflict'
            or recovery.get('message') != _SETUP_CONFLICT
            or native.get('status') != 'blocked' or native.get('provider_failure')
            or native.get('runtime_failure_kind') != 'native_goal_dependency_blocked'
            or not isinstance(invocation, str) or not re.fullmatch(r'inv-[A-Za-z0-9_-]+', invocation)
            or not thread or thread == native.get('thread_id')
            or science.get('status') != 'locked' or not state.get('last_safe_checkpoint')
            or budget.get('hard_cap_reached') or type(remaining) is not int or remaining <= 0
            or resume_blocker(state, for_repair=True, runtime_revalidated=True) is not None
            or control.exists() or control.is_symlink()):
        raise ValueError('not a safely stopped technical continuation setup')
    stage = (state.get('last_invocation_failure') or {}).get('stage')
    if stage not in {'investigate', 'judge_rework'}:
        raise ValueError('unsupported technical recovery setup stage')
    failure = {'invocation_id': invocation, 'role': 'investigator', 'stage': stage,
               'failure_phase': 'setup', 'error_type': 'NativeGoalRecoveryConflict'}
    if state.get('last_invocation_failure') != failure:
        raise ValueError('failed setup invocation identity changed')

    inputs: dict[str, str] = {}
    proof = _consumed_proof(cycle, state, inputs)
    if proof.get('stage', 'investigate') != stage:
        raise ValueError('technical recovery setup changed its original stage')
    blocked = proof['blocked_goal_identity']
    if (proof['blocked_thread_id'] != native.get('thread_id')
            or proof['frozen_objective_sha256'] != frozen_science_digest(science)):
        raise ValueError('consumed continuation differs from the failed frozen objective')
    ledger = InvocationLedger(cycle / 'invocations.jsonl')
    initial = ledger.snapshot(proof['origin_invocation_id']) or {}
    setup = ledger.snapshot(invocation) or {}
    if (setup.get('logical_key') != initial.get('logical_key') or not setup.get('logical_key')
            or setup.get('thread_id') != thread or setup.get('role') != 'investigator'
            or setup.get('stage') != stage or setup.get('state') != 'failed'
            or setup.get('commit_state') != 'not_committed'
            or setup.get('failure_phase') != 'setup'
            or setup.get('error_type') != 'NativeGoalRecoveryConflict'
            or setup.get('native_goal_identity') or setup.get('native_goal_origin') or setup.get('closure_hash')
            or any((cycle / folder / (invocation + '.json')).exists()
                   for folder in ('semantic_closure', 'workspace/.tm-handoff'))):
        raise ValueError('the replacement setup already started or produced output')
    old = read_existing_goal(cycle, {'thread_id': proof['blocked_thread_id']})
    if verify_existing_goal(old, blocked, previous=blocked)['status'] != 'blocked':
        raise ValueError('the original Goal is no longer blocked')
    _require_replacement_goal_absent(cycle, thread)
    if (_dispatcher_lock_snapshot(cycle / 'workspace', list(proof['dispatcher_locks']))
            != proof['dispatcher_locks']):
        raise ValueError('technical recovery dispatcher ownership changed')
    retired = _retired_origins(ledger, initial, blocked)
    if not set(retired) > set(proof['origin_exclusion_ids']):
        raise ValueError('no verified older native generation caused the setup conflict')
    return {**proof, 'origin_exclusion_ids': retired, 'setup_retry_invocation': invocation,
            'replacement_thread_id': thread}, inputs


def dependency_fingerprint(cycle: Path, state: dict) -> dict | None:
    """Wake SPV when the authorization, guidance or lock availability changes."""
    if (state.get('native_goal') or {}).get('runtime_failure_kind') != 'native_goal_dependency_blocked':
        return None
    inputs: dict[str, str] = {}
    try:
        authorization = json.loads(_read(cycle / AUTHORIZATION_FILE, inputs, 1_048_576))
    except (OSError, ValueError):
        return {'authorization': 'unavailable', 'inputs': inputs}
    for name in ('operator_guidance.json', 'operator_guidance.md'):
        try:
            _read(cycle / 'attention' / name, inputs, 1_048_576)
        except (OSError, ValueError):
            continue
    locks = None
    try:
        locks = _dispatcher_lock_snapshot(cycle / 'workspace', authorization.get('dispatcher_locks', []))
    except (AttributeError, OSError, ValueError):
        pass
    return {'inputs': inputs, 'dispatcher_locks': locks}


def validate_operator_technical_recovery(cycle: Path, state: dict, *, engine: Path) -> dict | None:
    """Bind one resume to an authorized rescan or bounded computation plan."""
    if not (cycle / AUTHORIZATION_FILE).is_file():
        return None
    from .supervisor_repair_receipts import (
        _record_validation, failure_binding, source_manifest,
    )
    try:
        binding, manifest = failure_binding(cycle, state), source_manifest(engine)
        if (state.get('recovery') or {}).get('kind') == 'NativeGoalRecoveryConflict':
            proof, inputs = _check_setup_retry(cycle.absolute(), state)
        else:
            proof, inputs = check_authorization(cycle, state)
        _recheck_rework(cycle, state, proof, inputs)
        if (failure_binding(cycle, state) != binding or source_manifest(engine) != manifest
                or any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != digest
                       for p, digest in inputs.items())):
            raise ValueError('technical recovery evidence changed during validation')
        return _record_validation(cycle, state, engine,
            validator=VALIDATOR,
            module='operator_technical_recovery.py', change_id='operator-authorized-technical-recovery',
            tests=json.dumps(proof, sort_keys=True), manifest=manifest,
            disposition='already_fixed', extra_inputs=inputs)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError):
        return None



def recheck_receipt_inputs(receipt: dict, *, state: dict | None = None) -> dict:
    proof = json.loads(receipt.get('tests') or '{}')
    if (receipt.get('validator') != VALIDATOR or proof.get('resume_preparation') != PREPARATION
            or proof.get('stage', 'investigate') not in {'investigate', 'judge_rework'}
            or proof.get('mode') not in {ARTIFACT_RESCAN, BOUNDED_COMPUTATION}
            or (proof.get('blocked_goal_identity') or {}).get('status') != 'blocked'
            or not proof.get('origin_invocation_id') or not proof.get('origin_exclusion_ids')
            or not proof.get('frozen_objective_sha256')):
        raise ValueError('Invalid technical recovery evidence')
    if state is not None:
        _recheck_rework(Path(receipt['cycle']), state, proof, {})
    elif proof.get('stage') == 'judge_rework':
        raise ValueError('Judge artifact rescan requires current state revalidation')
    return proof


def consume_technical_recovery(cycle: Path, state: dict) -> bool:
    """Retire the original blocked operation for one bounded same-Cycle reentry."""
    from .native_goal_recovery import verify_existing_goal
    from .runtime import now
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_native_goal_recovery import read_existing_goal
    from .supervisor_repair_receipts import inspect_repair_receipt

    native = state.get('native_goal') or {}
    if native.get('runtime_failure_kind') != 'native_goal_dependency_blocked':
        return False
    if not state.get('supervisor_repair_receipt'):
        return False
    receipt = inspect_repair_receipt(cycle, state)
    if receipt.get('validator') != VALIDATOR:
        return False
    proof = recheck_receipt_inputs(receipt, state=state)
    blocked_thread = proof['blocked_thread_id']
    blocked_identity = proof['blocked_goal_identity']
    current = read_existing_goal(cycle, {'thread_id': blocked_thread})
    verified = verify_existing_goal(current, blocked_identity, previous=blocked_identity)
    if verified['status'] != 'blocked':
        raise ValueError('Original native Goal is no longer terminal blocked')
    replacement = proof.get('replacement_thread_id')
    if replacement:
        if (state.get('threads') or {}).get('investigator') != replacement:
            raise ValueError('Replacement thread changed')
        _require_replacement_goal_absent(cycle, replacement)
    if (_dispatcher_lock_snapshot((cycle / 'workspace').resolve(), list(proof['dispatcher_locks']))
            != proof['dispatcher_locks']):
        raise ValueError('Technical recovery dispatcher ownership changed')
    blocker = resume_blocker(state, for_repair=True, runtime_revalidated=True,
                             contract_projection_repair=receipt)
    control = cycle / 'recovery_control.json'
    if blocker or control.exists() or control.is_symlink():
        raise ValueError(blocker or 'Operator control forbids technical recovery')
    operation = technical_reentry_key(state)
    if operation in (state.get('supervisor_technical_operations') or []):
        raise ValueError('Technical recovery opportunity was already consumed')
    key = receipt['repair_identity']
    state.setdefault('supervisor_technical_operations', []).append(operation)
    state.setdefault('supervisor_technical_reentries', []).append(key)
    state.setdefault('supervisor_technical_reentry_audit', []).append({
        **proof, 'at': now(), 'repair_receipt': copy.deepcopy(state['supervisor_repair_receipt']),
        'original_native_goal': copy.deepcopy(native),
        'original_failure': copy.deepcopy(state['last_invocation_failure']),
    })
    if key not in state.setdefault('supervisor_consumed_repairs', []):
        state['supervisor_consumed_repairs'].append(key)
    # The persisted blocked Goal remains on its own thread. A fresh thread can
    # continue at the same frozen checkpoint without clearing that Goal.
    state['threads'].pop('investigator')
    (state.get('runtime_thread_providers') or {}).pop('investigator', None)
    state.pop('attention', None)
    state.pop('human_attention', None)
    return True


def technical_recovery_origins(state: dict) -> set[str]:
    from .scientific_objective import frozen_science_digest
    digest = frozen_science_digest(state.get('hypothesis_lock') or {})
    spent = set(state.get('supervisor_technical_reentries') or [])
    return {
        invocation
        for entry in state.get('supervisor_technical_reentry_audit') or []
        if (entry.get('repair_receipt') or {}).get('repair_identity') in spent
        and entry.get('frozen_objective_sha256') == digest
        and entry.get('resume_preparation') == PREPARATION
        for invocation in entry.get('origin_exclusion_ids') or []
    }
