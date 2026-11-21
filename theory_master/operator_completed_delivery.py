"""Operator-bound normal redelivery after a completed Goal omitted its handoff.

The old scientific document is evidence for the Investigator to inspect, never
an SDK-authored Finding. The completed Goal and interrupted invocation survive.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

SCHEMA = 'theory-master/operator-completed-delivery/v1'
VALIDATOR = 'operator-completed-delivery-reentry/v1'
PHASE = 'investigator:handoff_invalid'
REASON = 'explicit handoff file does not exist or is a symlink'


def _read(path: Path, inputs: dict, limit: int = 16_000_000) -> bytes:
    if (any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file()
            or path.stat().st_size > limit):
        raise ValueError('Unsafe completed-delivery evidence')
    data = path.read_bytes()
    inputs[str(path.resolve())] = hashlib.sha256(data).hexdigest()
    return data


def is_completed_delivery_failure(state: dict) -> bool:
    native, attention = state.get('native_goal') or {}, state.get('attention') or {}
    return (state.get('status') == 'needs_attention' and state.get('phase') == PHASE
            and native.get('status') == 'complete' and attention.get('code') == 'investigator_handoff_invalid'
            and attention.get('reason') == REASON)


def check_authorization(cycle: Path, state: dict, *, goal_reader=None) -> tuple[dict, dict]:
    from .invocations import InvocationLedger
    from .native_goal_recovery import verify_existing_goal
    from .runtime_supervisor_review import resume_blocker
    from .scientific_objective import frozen_science_digest
    from .supervisor_native_goal_recovery import read_existing_goal
    cycle = cycle.absolute()
    from .completed_delivery_preparation import is_preparation_failure, check_preparation_retry
    if is_preparation_failure(state):
        return check_preparation_retry(cycle, state, goal_reader=goal_reader)
    native = state.get('native_goal') or {}
    invocation, thread = state.get('last_invocation_id'), native.get('thread_id')
    if (not is_completed_delivery_failure(state) or state.get('execution_active')
            or state.get('active_invocation_id') is not None or state.get('worker_id')
            or state.get('protocol_health') != 'valid' or state.get('scientific_verdict') is not None
            or (state.get('hypothesis_lock') or {}).get('status') != 'locked'
            or not isinstance(invocation, str) or not re.fullmatch(r'inv-[A-Za-z0-9_-]+', invocation)
            or not thread or (state.get('threads') or {}).get('investigator') != thread
            or native.get('provider_failure') or native.get('runtime_failure_kind')
            or resume_blocker(state, runtime_revalidated=True) is not None
            or (cycle / 'recovery_control.json').exists() or (cycle / 'recovery_control.json').is_symlink()):
        raise ValueError('Not a safely stopped completed-Goal handoff failure')
    failure = {'invocation_id': invocation, 'role': 'investigator', 'stage': 'investigate',
               'failure_phase': 'invoke', 'error_type': 'RecoveryStop'}
    if state.get('last_invocation_failure') != failure:
        raise ValueError('Completed-delivery failure identity changed')
    inputs: dict[str, str] = {}
    path = cycle / 'attention/completed-delivery-authorization.json'
    auth = json.loads(_read(path, inputs, 1_048_576))
    frozen = frozen_science_digest(state['hypothesis_lock'])
    if (not isinstance(auth, dict) or auth.get('schema') != SCHEMA
            or auth.get('authorization_source') not in {'human', 'delegated_operator'}
            or not str(auth.get('user_instruction') or '').strip() or not str(auth.get('scope') or '').strip()
            or auth.get('cycle') != str(cycle.resolve()) or auth.get('invocation_id') != invocation
            or auth.get('thread_id') != thread
            or frozen_science_digest(auth.get('frozen_objective') or {}) != frozen):
        raise ValueError('Operator authorization does not bind the original completed delivery')
    ledger = cycle / 'invocations.jsonl'
    _read(ledger, inputs)
    record = InvocationLedger(ledger).snapshot(invocation) or {}
    if (record.get('state') != 'interrupted' or record.get('commit_state') != 'not_committed'
            or record.get('thread_id') != thread or record.get('role') != 'investigator'
            or record.get('stage') != 'investigate' or record.get('failure_phase') != 'invoke'
            or record.get('reason') != 'SDK handoff invalid: ' + REASON or record.get('closure_hash')
            or (cycle / 'semantic_closure' / (invocation + '.json')).exists()):
        raise ValueError('Original completed delivery already changed or committed')
    manifest_path = cycle / 'invocation_artifacts' / (invocation + '.json')
    if record.get('invocation_artifact_manifest') != str(manifest_path):
        raise ValueError('Original SDK capture manifest is missing')
    capture = json.loads(_read(manifest_path, inputs))
    if (capture.get('invocation_id') != invocation or capture.get('status') != 'interrupted'
            or capture.get('role') != 'investigator' or capture.get('stage') != 'investigate'
            or capture.get('workspace') != str(cycle / 'workspace')):
        raise ValueError('Foreign SDK capture manifest')
    captured = {item.get('path'): item.get('sha256') for item in
                [*(capture.get('created') or []), *(capture.get('modified') or [])] if isinstance(item, dict)}
    artifacts = auth.get('artifact_manifest')
    if not isinstance(artifacts, dict) or not 1 <= len(artifacts) <= 32:
        raise ValueError('No bounded Investigator-authored artifact set')
    for name, expected in artifacts.items():
        artifact = Path(name)
        if (not artifact.is_absolute() or not artifact.resolve().is_relative_to((cycle / 'workspace').resolve())
                or captured.get(name) != expected or not isinstance(expected, str)
                or not re.fullmatch('[0-9a-f]{64}', expected)
                or hashlib.sha256(_read(artifact, inputs)).hexdigest() != expected):
            raise ValueError('Investigator artifact does not match its original SDK capture')
    handoff = cycle / 'workspace/.tm-handoff' / (invocation + '.json')
    if handoff.exists() or handoff.is_symlink():
        raise ValueError('Original handoff now exists; use the original delivery path')
    runtime = [json.loads(line) for line in _read(cycle / 'runtime_events.jsonl', inputs).splitlines()]
    if not any(row.get('event') == 'AGENT_INPUT' and row.get('role') == 'investigator'
               and row.get('input_kind') == 'native_goal_setup_turn'
               and str(handoff) in str(row.get('content') or '') for row in runtime):
        raise ValueError('Original Setup did not name the missing handoff')
    _read(cycle / 'events.jsonl', inputs)
    guidance = json.loads(_read(cycle / 'attention/operator_guidance.json', inputs, 1_048_576))
    _read(cycle / 'attention/operator_guidance.md', inputs, 1_048_576)
    if (guidance.get('status') != 'pending' or guidance.get('attention_code') != 'operator_completed_delivery'
            or str(path) not in str(guidance.get('text') or '')):
        raise ValueError('No matching operator direction for the new normal delivery')
    previous = record.get('native_goal_identity')
    if not isinstance(previous, dict) or previous.get('thread_id') != thread:
        raise ValueError('Original completed Goal identity was not recorded')
    goal = (goal_reader or read_existing_goal)(cycle, {'thread_id': thread})
    observation = 'live_get'
    if goal is None:
        from .completed_goal_archive import read_cleared_completion
        goal = read_cleared_completion(cycle, state, auth, previous, inputs)
        observation = 'official_archived_completion_after_verified_get_absence'
    identity = verify_existing_goal(goal, {'thread_id': thread,
        'objective_sha256': previous['objective_sha256']}, previous=previous)
    if identity['status'] != 'complete':
        raise ValueError('Original Goal is no longer complete')
    from .completed_delivery_preparation import budget_generation_origins
    origins = budget_generation_origins(cycle, record, identity, inputs)
    return {'origin_invocation_id': invocation, 'origin_exclusion_ids': origins, 'thread_id': thread, 'completed_goal_identity': identity,
            'frozen_objective_sha256': frozen, 'artifact_manifest': artifacts, 'goal_observation': observation,
            'origin_failure': failure, 'origin_attention': copy.deepcopy(state.get('attention')),
            'origin_native_goal': copy.deepcopy(native),
            'resume_preparation': 'investigator_authors_new_delivery', 'scientific_verdict': None}, inputs


def validate_completed_delivery(cycle: Path, state: dict, *, engine: Path) -> dict:
    from .supervisor_repair_receipts import _record_validation, source_manifest
    proof, inputs = check_authorization(cycle, state)
    return _record_validation(cycle, state, engine, validator=VALIDATOR,
        module='operator_completed_delivery.py', change_id='operator-authorized-normal-redelivery',
        tests=json.dumps(proof, sort_keys=True), manifest=source_manifest(engine),
        disposition='already_fixed', extra_inputs=inputs)


def recheck_receipt_inputs(receipt: dict) -> None:
    proof = json.loads(receipt.get('tests') or '{}')
    if (receipt.get('validator') != VALIDATOR
            or proof.get('resume_preparation') != 'investigator_authors_new_delivery'
            or proof.get('completed_goal_identity', {}).get('status') != 'complete'
            or not proof.get('origin_invocation_id') or not proof.get('frozen_objective_sha256')):
        raise ValueError('Invalid completed-delivery repair evidence')


def consume_completed_delivery(cycle: Path, state: dict) -> bool:
    from .runtime import now
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_repair_receipts import inspect_repair_receipt
    from .completed_delivery_preparation import is_preparation_failure
    if not (is_completed_delivery_failure(state) or is_preparation_failure(state)):
        return False
    receipt = inspect_repair_receipt(cycle, state)
    if receipt.get('validator') != VALIDATOR:
        raise ValueError('Completed-Goal redelivery needs its own verified operator receipt')
    proof = json.loads(receipt['tests'])
    from .supervisor_native_goal_recovery import read_existing_goal
    from .native_goal_recovery import verify_existing_goal
    current = read_existing_goal(cycle, {'thread_id': proof['thread_id']})
    if current is None:
        if proof.get('goal_observation') != 'official_archived_completion_after_verified_get_absence':
            raise ValueError('Original completed Goal disappeared after validation')
    else:
        identity = verify_existing_goal(current, proof['completed_goal_identity'], previous=proof['completed_goal_identity'])
        if identity['status'] != 'complete':
            raise ValueError('Goal slot changed after completion validation')
    key = receipt['repair_identity']
    if key in (state.get('supervisor_completed_delivery_reentries') or []):
        raise ValueError('Completed-Goal redelivery opportunity was already consumed')
    blocker = resume_blocker(state, for_repair=True, runtime_revalidated=True,
                             contract_projection_repair=receipt)
    control = cycle / 'recovery_control.json'
    if blocker or control.exists() or control.is_symlink():
        raise ValueError(blocker or 'Operator control forbids completed-Goal redelivery')
    state.setdefault('supervisor_completed_delivery_reentries', []).append(key)
    state.setdefault('supervisor_completed_delivery_audit', []).append({
        'at': now(), 'repair_receipt': copy.deepcopy(state['supervisor_repair_receipt']),
        'original_native_goal': copy.deepcopy(state['native_goal']),
        'original_failure': copy.deepcopy(state['last_invocation_failure']),
        'original_attention': copy.deepcopy(state.get('attention')), **proof})
    if key not in state.setdefault('supervisor_consumed_repairs', []):
        state['supervisor_consumed_repairs'].append(key)
    state.pop('attention', None)
    state.pop('human_attention', None)
    return True


def completed_delivery_origins(state: dict, thread: str) -> set[str]:
    """Exclude only the original terminal operation; new interrupted Goals attach normally."""
    from .scientific_objective import frozen_science_digest
    frozen = frozen_science_digest(state.get('hypothesis_lock') or {})
    result = set()
    for row in state.get('supervisor_completed_delivery_audit') or []:
        key = (row.get('repair_receipt') or {}).get('repair_identity')
        if (row.get('thread_id') == thread and row.get('frozen_objective_sha256') == frozen
                and row.get('completed_goal_identity', {}).get('status') == 'complete'
                and key in (state.get('supervisor_completed_delivery_reentries') or [])
                and key in (state.get('supervisor_consumed_repairs') or [])
                and row.get('resume_preparation') == 'investigator_authors_new_delivery'):
            result.update(row.get('origin_exclusion_ids') or [row['origin_invocation_id']])
    return result
