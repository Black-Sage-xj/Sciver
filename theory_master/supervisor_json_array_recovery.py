"""Evidence-bound reentry after the lossless root-array syntax repair."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

VALIDATOR = 'json-root-array-reentry/v1'
PHASE = 'research_lead:frontier_proposal:contract_repair_failed'


def is_untouched_json_array_failure(cycle: Path, state: dict) -> bool:
    recovery = state.get('recovery') or {}
    return (state.get('status')=='needs_attention' and state.get('phase')==PHASE
        and state.get('protocol_health')=='violation' and state.get('resumable') is True
        and not state.get('execution_active') and state.get('active_invocation_id') is None
        and not state.get('last_safe_checkpoint') and not state.get('role_outputs')
        and not state.get('agent_outputs') and not state.get('hypothesis_locked')
        and state.get('hypothesis_lock')=={'status':'unlocked'} and not state.get('native_goal')
        and state.get('scientific_verdict') is None and not state.get('candidates')
        and not state.get('branches') and not state.get('active_branch_id')
        and state.get('qualification_status','pending')=='pending'
        and state.get('last_invocation_id')==recovery.get('invocation_id')
        and str(state.get('cycle_id'))==cycle.name
        and recovery.get('action')=='contract_repair_failed' and recovery.get('role')=='research_lead'
        and recovery.get('stage')=='frontier_proposal'
        and (state.get('attention') or {}).get('code')=='contract_repair_failed')


def has_verified_json_array_reentry(state: dict, *, cycle: Path | None = None) -> bool:
    from .supervisor_repair_receipts import inspect_repair_receipt
    try:
        if cycle is None:cycle=Path(state['supervisor_repair_receipt']['path']).parents[2]
        if not is_untouched_json_array_failure(cycle,state):return False
        receipt=inspect_repair_receipt(cycle,state)
        return (receipt.get('validator')==VALIDATOR and receipt.get('repair_identity')
                not in (state.get('supervisor_json_array_reentries') or []))
    except (OSError,ValueError,KeyError,TypeError,IndexError):return False


def _read(path: Path, inputs: dict) -> bytes:
    if (any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file()
            or path.stat().st_size > 16_000_000):
        raise ValueError('Unsafe JSON-repair evidence')
    raw = path.read_bytes();inputs[str(path.absolute())] = hashlib.sha256(raw).hexdigest()
    return raw


def _original_source(engine: Path, state: dict, inputs: dict | None = None) -> str:
    import ast
    source=state.get('runtime_source_provenance') or {};head=source.get('sdk_git_sha','')
    hashes=source.get('module_sha256') or {};expected=hashes.get('output_normalization.py')
    if (not re.fullmatch('[0-9a-f]{40}',head) or source.get('source_coherent') is not True
            or source.get('source_coherence_errors')):
        raise ValueError('Original JSON source identity is missing')
    def git_file(name):
        return subprocess.run(['git','show',head+':theory_master/'+name],cwd=engine.parent,
                              capture_output=True,check=True,timeout=10).stdout
    old=git_file('output_normalization.py');digest=hashlib.sha256(old).hexdigest()
    if b'def _close_array_before_root_field(' in old:
        raise ValueError('Original SDK already contained the missing repair')
    root=Path(str(source.get('source_root') or ''))
    if not root.is_absolute() or source.get('sdk_git_root')!=str(root):
        raise ValueError('Original pinned SDK tree is unavailable')
    bound=inputs if inputs is not None else {}
    if hashlib.sha256(_read(root/'theory_master/output_normalization.py',bound)).hexdigest()!=digest:
        raise ValueError('Original pinned normalizer changed from its recorded commit')
    if expected is not None:
        if expected!=digest:raise ValueError('Original normalizer hash differs from its recorded module')
    else:
        # Older provenance omitted this dependency. Bind the captured importer
        # and the still-pinned tree; do not claim a historical dependency hash.
        name='invocation_output.py';loader=git_file(name)
        if (hashes.get(name)!=hashlib.sha256(loader).hexdigest()
                or (source.get('module_paths') or {}).get(name)!=str(root/'theory_master'/name)
                or not any(isinstance(node,ast.ImportFrom) and node.level==1
                    and node.module=='output_normalization'
                    and any(alias.name=='normalize_agent_output' for alias in node.names)
                    for node in ast.walk(ast.parse(loader)))):
            raise ValueError('Recorded invocation importer does not bind this dependency')
        actual=subprocess.run(['git','--work-tree='+str(root),'rev-parse','HEAD'],cwd=root,
                              capture_output=True,text=True,check=True,timeout=10).stdout.strip()
        if actual!=head:raise ValueError('Original SDK tree moved from its recorded commit')
        _read(root/'theory_master'/name,bound)
    return digest


def _probe(engine: Path, raw: str) -> None:
    script = '''import json,sys
def offline(event,args):
    if event in {'socket.connect','socket.getaddrinfo','subprocess.Popen','os.system','os.posix_spawn','ctypes.dlopen'} or event.startswith('os.exec'):
        raise RuntimeError('Offline syntax probe forbids network/process execution')
sys.addaudithook(offline)
sys.path.insert(0,sys.argv[1])
from theory_master.contracts import role_contract
from theory_master.output_normalization import normalize_agent_output,_close_array_before_root_field
raw=sys.stdin.read();schema=role_contract('research_lead',phase='frontier_proposal').prompt_schema
fixed,repair=_close_array_before_root_field(raw,schema)
assert repair and fixed==raw[:repair['offset']]+']'+raw[repair['offset']:]
result=normalize_agent_output(raw,schema)
assert result.value==json.loads(fixed)
assert list(result.repairs)==[repair]
assert isinstance(result.value.get('candidates'),list) and result.value['candidates']
print('exact one-bracket normalization passed')
'''
    result = subprocess.run([sys.executable,'-I','-c',script,str(engine.parent)],input=raw,
                            text=True,capture_output=True,check=True,timeout=20)
    if result.stdout.strip() != 'exact one-bracket normalization passed':
        raise ValueError('Fresh JSON repair probe failed')


def validate_json_array_reentry(cycle: Path, state: dict, *, engine: Path) -> dict | None:
    from .contracts import role_contract
    from .invocations import InvocationLedger
    from .output_normalization import _close_array_before_root_field
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_repair_receipts import _digest,_record_validation,source_manifest
    recovery = state.get('recovery') or {}
    blocker=resume_blocker({**state,'protocol_health':'valid'},for_repair=True)
    uncommitted=(blocker=='No existing safe checkpoint or committed role boundary exists for managed resume.'
                 and is_untouched_json_array_failure(cycle,state))
    if (state.get('status') != 'needs_attention' or state.get('phase') != PHASE
            or state.get('protocol_health') != 'violation' or state.get('execution_active')
            or state.get('scientific_verdict') is not None or state.get('hypothesis_locked')
            or state.get('hypothesis_lock') != {'status':'unlocked'} or state.get('native_goal')
            or state.get('active_invocation_id') or recovery.get('action') != 'contract_repair_failed'
            or recovery.get('role') != 'research_lead' or recovery.get('stage') != 'frontier_proposal'
            or (state.get('attention') or {}).get('code') != 'contract_repair_failed'
            or (blocker is not None and not uncommitted)):
        return None
    invocation,thread = recovery.get('invocation_id'),recovery.get('thread_id')
    if (not isinstance(invocation,str) or not invocation or Path(invocation).name != invocation
            or not thread or (state.get('threads') or {}).get('research_lead') != thread):
        return None
    artifact = cycle/'contract_repairs'/(invocation+'.json')
    if recovery.get('artifact') != str(artifact):return None
    inputs={};saved=json.loads(_read(artifact,inputs))
    if any(saved.get(k) != recovery.get(k) for k in ('invocation_id','role','stage','thread_id','repair_prompt','at')):
        return None
    errors=saved.get('validation_errors')
    if (not isinstance(errors,list) or not errors
            or any(e.get('code')!='json_syntax_error' or e.get('actual')!='syntax_damage' for e in errors)):
        return None
    raw=saved.get('raw_outputs')
    if not isinstance(raw,list) or not 1<=len(raw)<=8:return None
    schema=role_contract('research_lead',phase='frontier_proposal').prompt_schema
    choices=[value for value in raw if isinstance(value,str) and _close_array_before_root_field(value,schema)[1]]
    if not choices:return None
    ledger=cycle/'invocations.jsonl';_read(ledger,inputs)
    record=InvocationLedger(ledger).snapshot(invocation) or {}
    if (record.get('state')!='failed' or record.get('commit_state')!='not_committed'
            or record.get('error_type')!='ContractRepairFailed' or record.get('role')!='research_lead'
            or record.get('stage')!='frontier_proposal' or record.get('thread_id')!=thread):return None
    rows=[json.loads(line) for line in _read(cycle/'runtime_events.jsonl',inputs).splitlines()]
    delivered=[r.get('content') for r in rows if r.get('event')=='LLM_MESSAGE'
               and r.get('message_role')=='assistant' and r.get('thread_id')==thread]
    if delivered[-len(raw):]!=raw:return None
    original=_original_source(engine,state,inputs);manifest=source_manifest(engine);_probe(engine,choices[0])
    proof={'fresh_exact_bracket_probe':'passed','original_normalizer_sha256':original,
           'invocation_id':invocation,'thread_id':thread,'resume_preparation':'new_lead_delivery',
           'raw_sha256':hashlib.sha256(choices[0].encode()).hexdigest(),
           'source_evidence':('recorded_normalizer_hash' if (state.get('runtime_source_provenance') or {}).get('module_sha256',{}).get('output_normalization.py')
                              else 'pinned_tree_and_recorded_importer; dependency hash not captured historically')}
    return _record_validation(cycle,state,engine,validator=VALIDATOR,module='output_normalization.py',
        change_id='existing-source:'+_digest(manifest),tests=json.dumps(proof,sort_keys=True),
        manifest=manifest,disposition='already_fixed',extra_inputs=inputs)


def recheck_receipt_inputs(receipt: dict) -> None:
    proof=json.loads(receipt.get('tests') or '{}')
    if (receipt.get('validator')!=VALIDATOR or proof.get('fresh_exact_bracket_probe')!='passed'
            or not proof.get('original_normalizer_sha256') or not proof.get('invocation_id')
            or proof.get('resume_preparation')!='new_lead_delivery'):
        raise ValueError('Invalid JSON repair evidence')


def consume_json_array_reentry(cycle: Path,state: dict) -> bool:
    from .runtime import now
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_repair_receipts import inspect_repair_receipt
    if (state.get('phase')!=PHASE or state.get('protocol_health')!='violation'
            or not state.get('supervisor_repair_receipt')):return False
    receipt=inspect_repair_receipt(cycle,state)
    if receipt.get('validator')!=VALIDATOR:return False
    key=receipt['repair_identity']
    if key in (state.get('supervisor_json_array_reentries') or []):
        raise ValueError('JSON preparation reentry was already consumed')
    blocker=resume_blocker(state,for_repair=True,runtime_revalidated=True,contract_projection_repair=receipt)
    control=cycle/'recovery_control.json'
    if blocker or control.exists() or control.is_symlink():
        raise ValueError(blocker or 'Operator control forbids JSON preparation reentry')
    state.setdefault('supervisor_json_array_reentries',[]).append(key)
    if key not in state.setdefault('supervisor_consumed_repairs',[]):state['supervisor_consumed_repairs'].append(key)
    state.setdefault('supervisor_json_array_audit',[]).append({'at':now(),
        'receipt':copy.deepcopy(state['supervisor_repair_receipt']),
        'original_failure':copy.deepcopy(state['recovery']),
        'original_attention':copy.deepcopy(state.get('attention')),'original_protocol_health':state['protocol_health']})
    state['protocol_health']='valid';state.pop('attention',None);state.pop('human_attention',None)
    return True
