"""One evidence-bound Lead delivery after the empty-frontier contract fix.

A receipt authorizes a new normal proposal, never reprocesses an old answer or
certifies the Lead's scientific assertion that no suitable candidate exists.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

VALIDATOR = 'lead-empty-frontier-reentry/v1'
_PHASE = 'research_lead:frontier_proposal:contract_repair_failed'
_ERROR = 'frontier_proposal requires 1..3 candidates; use the smallest sufficient candidate set'


def _read(path: Path, inputs: dict) -> bytes:
    if (any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file()
            or path.stat().st_size > 16_000_000):
        raise ValueError('Unsafe empty-frontier evidence')
    data = path.read_bytes()
    inputs[str(path.absolute())] = hashlib.sha256(data).hexdigest()
    return data


def _old_source(engine: Path, state: dict) -> str:
    source = state.get('runtime_source_provenance') or {}
    head = source.get('sdk_git_sha', '')
    expected = (source.get('module_sha256') or {}).get('contract_stages.py')
    if (source.get('source_coherent') is not True or source.get('source_coherence_errors')
            or not re.fullmatch('[0-9a-f]{40}', head) or not expected):
        raise ValueError('Original coherent contract source is missing')
    result = subprocess.run(['git', 'show', head + ':theory_master/contract_stages.py'],
                            cwd=engine.parent, capture_output=True, timeout=10, check=True)
    if (hashlib.sha256(result.stdout).hexdigest() != expected
            or _ERROR.encode() not in result.stdout
            or b'"theory-master/role/research-lead/frontier-proposal", 15,' not in result.stdout):
        raise ValueError('Original source does not prove the v15 empty-frontier fault')
    return expected


def _probe(engine: Path, answer: dict) -> None:
    script = '''import json,sys
def offline(event,args):
    if event in {'socket.connect','socket.getaddrinfo','subprocess.Popen','os.system','os.posix_spawn','ctypes.dlopen'} or event.startswith('os.exec'):
        raise RuntimeError('Offline contract probe forbids network/process execution')
sys.addaudithook(offline)
sys.path.insert(0,sys.argv[1])
from theory_master.contracts import role_contract
from theory_master.contract_closure import close_agent_output
c=role_contract('research_lead',phase='frontier_proposal')
assert c.version == 18
answer=json.loads(sys.stdin.read())
c.validate(answer,{})
closed=close_agent_output('research_lead','frontier_proposal',answer,{}, {})
assert closed == {'candidates':[], 'blocking_reason':answer['blocking_reason'].strip()}
for bad in ({'candidates':[]}, {'candidates':[], 'blocking_reason':''}, {'candidates':[], 'blocking_reason':'missing', 'needs_literature':True}, {'candidates':[{}], 'blocking_reason':'missing'}):
    try: c.validate(bad,{})
    except ValueError: pass
    else: raise AssertionError('Invalid empty-frontier exit accepted')
print('empty-frontier contract and closure passed')
'''
    result = subprocess.run([sys.executable, '-I', '-c', script, str(engine.parent)],
                            input=json.dumps(answer), capture_output=True, text=True,
                            timeout=20, check=True)
    if result.stdout.strip() != 'empty-frontier contract and closure passed':
        raise ValueError('Fresh contract probe failed')


def validate_empty_frontier_reentry(cycle: Path, state: dict, *, engine: Path) -> dict | None:
    from .invocations import InvocationLedger
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_repair_receipts import _digest, _record_validation, source_manifest
    recovery = state.get('recovery') or {}
    if (state.get('status') != 'needs_attention' or state.get('phase') != _PHASE
            or state.get('protocol_health') != 'violation' or state.get('execution_active')
            or state.get('scientific_verdict') is not None or state.get('hypothesis_locked')
            or state.get('hypothesis_lock') != {'status': 'unlocked'} or state.get('native_goal')
            or (state.get('attention') or {}).get('code') != 'contract_repair_failed'
            or recovery.get('action') != 'contract_repair_failed'
            or recovery.get('role') != 'research_lead' or recovery.get('stage') != 'frontier_proposal'
            or state.get('active_invocation_id')
            or resume_blocker({**state, 'protocol_health': 'valid'}, for_repair=True) is not None):
        return None
    invocation, thread = recovery.get('invocation_id'), recovery.get('thread_id')
    if (not isinstance(invocation, str) or not invocation or Path(invocation).name != invocation
            or not thread or (state.get('threads') or {}).get('research_lead') != thread):
        return None
    artifact = cycle / 'contract_repairs' / (invocation + '.json')
    if recovery.get('artifact') != str(artifact):
        return None
    inputs: dict[str, str] = {}
    saved = json.loads(_read(artifact, inputs))
    if any(saved.get(k) != recovery.get(k) for k in
           ('invocation_id', 'role', 'stage', 'thread_id', 'repair_prompt', 'at')):
        return None
    if saved.get('validation_errors') != [{'path': '/', 'code': 'contract_violation',
            'expected': 'theory-master/role/research-lead/frontier-proposal/v15',
            'actual': 'object', 'message': _ERROR}]:
        return None
    raw = saved.get('raw_outputs')
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        return None
    answer = None
    reason_output_index = None
    reason_fields = {'blocking_reason', 'contract_conflict'}
    for index, value in enumerate(raw):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            if index == len(raw) - 1:
                return None
            continue  # A historical JSON-repair explanation is not a proposal.
        if (not isinstance(parsed, dict) or parsed.get('candidates') != []
                or parsed.get('needs_literature', False) is not False
                or set(parsed) - {'candidates', 'needs_literature'} - reason_fields):
            return None
        supplied_fields = reason_fields.intersection(parsed)
        if not supplied_fields:
            continue
        if len(supplied_fields) != 1:
            return None
        reason = parsed[next(iter(supplied_fields))]
        if not isinstance(reason, str) or not reason.strip():
            return None
        # Legacy repair could remove the explanation while keeping the same
        # empty list. Use its last explicit reason only for the offline probe.
        answer = {'candidates': [], 'blocking_reason': reason}
        reason_output_index = index
    if answer is None:
        return None
    ledger, events = cycle / 'invocations.jsonl', cycle / 'runtime_events.jsonl'
    _read(ledger, inputs)
    rows = [json.loads(line) for line in _read(events, inputs).decode().splitlines()]
    delivered = [r.get('content') for r in rows if r.get('event') == 'LLM_MESSAGE'
                 and r.get('thread_id') == thread and r.get('message_role') == 'assistant']
    if delivered[-len(raw):] != raw:
        return None
    failed = InvocationLedger(ledger).snapshot(invocation) or {}
    if (failed.get('state') != 'failed' or failed.get('error_type') != 'ContractRepairFailed'
            or failed.get('commit_state') != 'not_committed' or failed.get('role') != 'research_lead'
            or failed.get('stage') != 'frontier_proposal' or failed.get('thread_id') != thread):
        return None
    try:
        original_sha = _old_source(engine, state)
        manifest = source_manifest(engine)
        _probe(engine, answer)
    except subprocess.SubprocessError:
        return None
    evidence = {'fresh_empty_frontier_probe': 'passed', 'original_contract_sha256': original_sha,
                'invocation_id': invocation, 'thread_id': thread, 'resume_preparation': 'new_lead_delivery',
                'reason_output_index': reason_output_index}
    return _record_validation(cycle, state, engine, validator=VALIDATOR,
        module='contract_stages.py', change_id='existing-source:' + _digest(manifest),
        tests=json.dumps(evidence, sort_keys=True), manifest=manifest,
        disposition='already_fixed', extra_inputs=inputs)


def recheck_receipt_inputs(receipt: dict) -> None:
    evidence = json.loads(receipt.get('tests') or '{}')
    if (receipt.get('validator') != VALIDATOR or evidence.get('fresh_empty_frontier_probe') != 'passed'
            or not evidence.get('original_contract_sha256') or not evidence.get('invocation_id')
            or not evidence.get('thread_id') or evidence.get('resume_preparation') != 'new_lead_delivery'):
        raise ValueError('Invalid empty-frontier repair evidence')


def consume_frontier_reentry(cycle: Path, state: dict) -> bool:
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_repair_receipts import inspect_repair_receipt
    from .runtime import now
    ref = state.get('supervisor_repair_receipt')
    if state.get('phase') != _PHASE or state.get('protocol_health') != 'violation' or not ref:
        return False
    receipt = inspect_repair_receipt(cycle, state)
    if receipt.get('validator') != VALIDATOR:
        return False
    key = receipt['repair_identity']
    if key in (state.get('supervisor_frontier_reentries') or []):
        raise ValueError('Empty-frontier reentry was already consumed')
    blocker = resume_blocker(state, for_repair=True, runtime_revalidated=True,
                             contract_projection_repair=receipt)
    control = cycle / 'recovery_control.json'
    if blocker or control.exists() or control.is_symlink():
        raise ValueError(blocker or 'Pending operator control forbids frontier reentry')
    state.setdefault('supervisor_frontier_reentries', []).append(key)
    if key not in state.setdefault('supervisor_consumed_repairs', []):
        state['supervisor_consumed_repairs'].append(key)
    state.setdefault('supervisor_frontier_reentry_audit', []).append({
        'at': now(), 'repair_receipt': copy.deepcopy(ref),
        'original_failure': copy.deepcopy(state['recovery']),
        'original_attention': copy.deepcopy(state.get('attention')),
        'original_protocol_health': state['protocol_health'], 'scientific_outputs_committed': False})
    state['protocol_health'] = 'valid'
    state.pop('attention', None)
    state.pop('human_attention', None)
    return True
