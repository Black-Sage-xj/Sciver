"""Bounded preparation repair for a completed slot with older budget generations."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path


def is_preparation_failure(state: dict) -> bool:
    recovery = state.get('recovery') or {}
    return (state.get('status') == 'failed' and recovery.get('kind') == 'NativeGoalRecoveryConflict'
            and recovery.get('message') == 'the original native Goal no longer exists'
            and bool(state.get('supervisor_completed_delivery_audit')))


def budget_generation_origins(cycle: Path, record: dict, identity: dict, inputs: dict) -> list[str]:
    from .invocations import InvocationLedger
    from .operator_completed_delivery import _read
    snapshots = InvocationLedger(cycle / 'invocations.jsonl').snapshots()
    ledger_rows = [json.loads(line) for line in _read(cycle / 'invocations.jsonl', inputs).splitlines()]
    events = [json.loads(line) for line in _read(cycle / 'events.jsonl', inputs).splitlines()]
    result = [record['invocation_id']]
    for row in snapshots:
        if row['invocation_id'] == record['invocation_id']:
            break
        if (row.get('logical_key') != record.get('logical_key') or row.get('role') != 'investigator'
                or row.get('stage') != record.get('stage') or row.get('commit_state') != 'not_committed'
                or row.get('state') not in {'running', 'interrupted', 'superseded'}):
            continue
        previous = row.get('native_goal_identity') or {}
        if (row.get('thread_id') != identity['thread_id'] or row.get('state') != 'interrupted'
                or row.get('reason') != 'live Native Goal token guard exceeded'
                or previous.get('thread_id') != identity['thread_id']
                or previous.get('objective_sha256') != identity['objective_sha256']
                or not isinstance(previous.get('created_at'), int)
                or previous['created_at'] >= identity['created_at']):
            raise ValueError('An older unfinished invocation is not an approved budget generation')
        interruptions = [item for item in ledger_rows if item.get('invocation_id') == row['invocation_id']
                         and item.get('event') == 'invocation.interrupted'
                         and item.get('reason') == 'live Native Goal token guard exceeded']
        if len(interruptions) != 1:
            raise ValueError('No unique original token-interruption boundary')
        ended = datetime.fromisoformat(interruptions[0]['recorded_at']).timestamp()
        approvals = [event for event in events if event.get('type') == 'investigation.budget_review_decided'
                     and (event.get('payload') or {}).get('decision') == 'approve'
                     and ended <= datetime.fromisoformat(event['at']).timestamp() <= identity['created_at']]
        if not approvals:
            raise ValueError('Older token interruption has no subsequent SDK budget approval')
        result.append(row['invocation_id'])
    if len(result) > 8:
        raise ValueError('Too many terminal budget generations for bounded redelivery')
    return result


def check_preparation_retry(cycle: Path, state: dict, *, goal_reader=None) -> tuple[dict, dict]:
    from .invocations import InvocationLedger
    from .operator_completed_delivery import _read, check_authorization, VALIDATOR
    from .runtime_supervisor_review import resume_blocker
    from .scientific_objective import frozen_science_digest
    from .supervisor_repair_receipts import _digest
    if (not is_preparation_failure(state) or state.get('execution_active')
            or state.get('active_invocation_id') is not None or state.get('worker_id')
            or state.get('protocol_health') != 'valid'
            or state.get('scientific_verdict') is not None
            or resume_blocker(state, runtime_revalidated=True) is not None):
        raise ValueError('Not a safely stopped redelivery preparation failure')
    audit = state['supervisor_completed_delivery_audit'][-1]
    ref = audit.get('repair_receipt') or {}
    path = Path(str(ref.get('path') or ''))
    inputs = {}
    if path.parent != cycle / 'supervisor/repairs':
        raise ValueError('Foreign original redelivery receipt')
    receipt = json.loads(_read(path, inputs))
    if (_digest(receipt) != ref.get('sha256') or receipt.get('validator') != VALIDATOR
            or receipt.get('repair_identity') not in state.get('supervisor_completed_delivery_reentries', [])
            or audit.get('frozen_objective_sha256') != frozen_science_digest(state['hypothesis_lock'])
            or audit.get('thread_id') != (state.get('threads') or {}).get('investigator')):
        raise ValueError('Original one-use redelivery authority changed')
    invocation = state.get('last_invocation_id')
    ledger = InvocationLedger(cycle / 'invocations.jsonl')
    failed = ledger.snapshot(invocation) or {}
    if (failed.get('state') != 'failed' or failed.get('error_type') != 'NativeGoalRecoveryConflict'
            or failed.get('native_goal_identity') or failed.get('commit_state') != 'not_committed'
            or failed.get('thread_id') != audit['thread_id']
            or failed.get('role') != 'investigator' or failed.get('stage') != 'investigate'
            or state.get('last_invocation_failure') != {'invocation_id':invocation,
                'role':'investigator','stage':'investigate','failure_phase':'invoke',
                'error_type':'NativeGoalRecoveryConflict'}):
        raise ValueError('A new native Goal may already have started')
    events = [json.loads(line) for line in _read(cycle / 'events.jsonl', inputs).splitlines()]
    starts = [e['at'] for e in events if e.get('type') == 'role.started'
              and (e.get('payload') or {}).get('invocation_id') == invocation]
    if len(starts) != 1:
        raise ValueError('No unique preparation-failure boundary')
    runtime = [json.loads(line) for line in _read(cycle / 'runtime_events.jsonl', inputs).splitlines()]
    if any(row.get('role') == 'investigator' and row.get('ts', '') >= starts[0]
           and row.get('event') in {'LLM_TURN_STARTED', 'LLM_MESSAGE', 'TOOL_ACTIVITY'} for row in runtime):
        raise ValueError('Preparation retry cannot hide an executed model turn')
    # This is a read-only view of the already certified original failure, never
    # a state rewrite. The new receipt binds the actual current preparation error.
    original = copy.deepcopy(state)
    original.update(status='needs_attention', phase='investigator:handoff_invalid', recovery={},
        last_invocation_id=audit['origin_invocation_id'],
        last_invocation_failure=audit.get('origin_failure') or audit['original_failure'],
        attention=audit.get('origin_attention') or audit['original_attention'],
        native_goal=audit.get('origin_native_goal') or audit['original_native_goal'],
        runtime_source_provenance=receipt.get('original_source_provenance'))
    proof, original_inputs = check_authorization(cycle, original, goal_reader=goal_reader)
    proof['preparation_retry_from'] = invocation
    proof['original_redelivery_receipt'] = str(path)
    inputs.update(original_inputs)
    return proof, inputs
