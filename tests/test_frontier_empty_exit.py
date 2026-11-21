"""Offline explicit-empty qualification and evidence-bound recovery guards."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from theory_master import supervisor_frontier_recovery as recovery
from theory_master.backend_fakes import FakeBackend
from theory_master.contracts import role_contract
from theory_master.contract_closure import close_agent_output
from theory_master.invocations import stable_hash
from theory_master.runtime import write_json
from theory_master.runtime_supervisor_review import resume_blocker
from theory_master.supervisor_repair_receipts import inspect_repair_receipt, failure_binding
from theory_master.workflow import Workflow
from theory_master.qualification_session import QualificationSession
from theory_master.workflow_qualification import NoQualifiedCandidate

ANSWER = {'candidates': [], 'blocking_reason': 'The stipulated fresh observations are unavailable; acquisition must be resolved.'}


def test_explicit_empty_is_valid_and_closes_without_new_identity():
    contract = role_contract('research_lead', phase='frontier_proposal')
    contract.validate(ANSWER, {})
    assert close_agent_output('research_lead', 'frontier_proposal', ANSWER, {}, {}) == ANSWER
    recovery._probe(Path(recovery.__file__).resolve().parent, ANSWER)


@pytest.mark.parametrize('answer', [
    {'candidates': []}, {'candidates': [], 'blocking_reason': ''},
    {'candidates': [], 'blocking_reason': '   '},
    {**ANSWER, 'needs_literature': True}, {**ANSWER, 'literature_request': {}},
    {**ANSWER, 'candidates': [{}]}, {**ANSWER, 'verdict': 'rejected'},
    {**ANSWER, 'blocking_reason': 42}, {'candidates': [], 'contract_conflict': 'Legacy field'},
])
def test_incomplete_or_mixed_exits_fail(answer):
    with pytest.raises(ValueError):
        role_contract('research_lead', phase='frontier_proposal').validate(answer, {})


def test_qualification_exit_does_not_retry_lock_or_call_challenger(tmp_path):
    owner = Mock()
    owner.supervisor_enabled.return_value = False
    state = {'qualification_proposal_rounds': 1, 'role_outputs': {}}
    session = QualificationSession(owner, state, '1', {})
    session.proposal._load_model_and_feedback = Mock()
    session.proposal._recover_legacy_checkpoint = Mock()
    def propose(): session.frontier_output = copy.deepcopy(ANSWER)
    session.proposal._propose_frontier = Mock(side_effect=propose)
    session.recovery._restore_working_set = Mock()
    session.lock._commit_hypothesis_lock = Mock()
    with pytest.raises(NoQualifiedCandidate): session.run()
    assert state['status'] == 'no_qualified_candidate'
    assert state['hypothesis_locked'] is False
    assert state['qualification_attempts'][-1]['reason'] == ANSWER['blocking_reason']
    assert state['frontier_review']['generated_candidates'] == []
    assert 'scientific_verdict' not in state
    session.recovery._restore_working_set.assert_not_called()
    session.lock._commit_hypothesis_lock.assert_not_called()
    assert not owner._call.called


@pytest.fixture
def stopped_lead(tmp_path, monkeypatch):
    from tests.test_core import TheoryMasterTest
    spec, _ = TheoryMasterTest().make(tmp_path, reworks=0)
    workflow = Workflow(spec, FakeBackend(reworks=0))
    cycle = spec.research_root / '1'
    workflow._bind_cycle(cycle, create=True)
    state = workflow._new_state()
    invocation, thread = 'inv-1-research_lead-empty', 'stopped-lead-thread'
    artifact = cycle / 'contract_repairs' / (invocation + '.json')
    fault = {'action': 'contract_repair_failed', 'invocation_id': invocation,
             'role': 'research_lead', 'stage': 'frontier_proposal', 'thread_id': thread,
             'artifact': str(artifact), 'repair_prompt': 'Original bounded repair', 'at': 'original-time'}
    raw = [json.dumps({'candidates': [], 'needs_literature': False}),
           'Cannot fabricate a scientifically qualified candidate.', json.dumps(ANSWER)]
    state.update(status='needs_attention', phase=recovery._PHASE, protocol_health='violation',
                 execution_active=False, resumable=True, recovery=fault,
                 attention={'code': 'contract_repair_failed'}, threads={'research_lead': thread},
                 qualification_status='pending', hypothesis_lock={'status': 'unlocked'},
                 role_outputs={'literature': {'remaining_scientific_question': 'A fresh input is missing.'}})
    write_json(artifact, {**fault, 'raw_outputs': raw, 'validation_errors': [
        {'path': '/', 'code': 'contract_violation',
         'expected': 'theory-master/role/research-lead/frontier-proposal/v15',
         'actual': 'object', 'message': recovery._ERROR}]})
    rows = [{'invocation_id': invocation, 'event': 'invocation.prepared', 'state': 'prepared',
             'role': 'research_lead', 'stage': 'frontier_proposal', 'commit_state': 'not_committed',
             'logical_key': stable_hash('empty-slot')},
            {'invocation_id': invocation, 'event': 'invocation.failed', 'state': 'failed',
             'thread_id': thread, 'error_type': 'ContractRepairFailed'}]
    (cycle / 'invocations.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    (cycle / 'runtime_events.jsonl').write_text(''.join(json.dumps({
        'event': 'LLM_MESSAGE', 'thread_id': thread, 'message_role': 'assistant', 'content': r}) + '\n' for r in raw))
    write_json(cycle / 'research_state.json', state)
    monkeypatch.setattr(recovery, '_old_source', lambda *_: '0' * 64)
    return cycle, state


def validate(fixture):
    cycle, state = fixture
    return recovery.validate_empty_frontier_reentry(cycle, state, engine=Path(recovery.__file__).resolve().parent)


def test_verified_resume_is_single_use_and_preserves_old_outputs(stopped_lead):
    cycle, state = stopped_lead
    original = copy.deepcopy(state)
    artifact = Path(state['recovery']['artifact']).read_bytes()
    receipt = validate(stopped_lead)
    assert receipt['validator'] == recovery.VALIDATOR
    assert inspect_repair_receipt(cycle, state) == receipt
    assert resume_blocker(state, contract_projection_repair=receipt) is None
    assert state['recovery'] == original['recovery']
    assert recovery.consume_frontier_reentry(cycle, state)
    assert state['protocol_health'] == 'valid'
    assert state['supervisor_frontier_reentry_audit'][0]['original_failure'] == original['recovery']
    assert Path(original['recovery']['artifact']).read_bytes() == artifact
    assert state['role_outputs'] == original['role_outputs']
    assert state['scientific_verdict'] is None


@pytest.mark.parametrize('patch', [
    {'execution_active': True}, {'resumable': False}, {'hypothesis_locked': True},
    {'scientific_verdict': 'supported'}, {'active_invocation_id': 'live-call'},
    {'hypothesis_lock': {'status': 'locked', 'id': 'H-0001'}},
    {'investigator_budget': {'exhausted': True}}, {'native_goal': {'status': 'blocked'}},
    {'phase': 'judge:terminal_assessment:contract_repair_failed'},
    {'recovery': {'action': 'pause'}}, {'recovery': {'action': 'interrupt'}},
])
def test_invalid_operational_or_scientific_states_are_inert(stopped_lead, patch):
    _, state = stopped_lead
    state.update(patch)
    assert validate(stopped_lead) is None


def test_plain_resume_and_tampered_receipt_are_rejected(stopped_lead):
    cycle, state = stopped_lead
    assert 'verified repair' in resume_blocker(state)
    validate(stopped_lead)
    with open(cycle / 'runtime_events.jsonl', 'a') as stream: stream.write('{}\n')
    with pytest.raises(ValueError): inspect_repair_receipt(cycle, state)


def test_altered_delivery_or_ledger_does_not_get_a_receipt(stopped_lead):
    cycle, _ = stopped_lead
    (cycle / 'runtime_events.jsonl').write_text('')
    assert validate(stopped_lead) is None


def set_historical_outputs(stopped_lead, *outputs):
    cycle, state = stopped_lead
    artifact = Path(state['recovery']['artifact'])
    saved = json.loads(artifact.read_text())
    raw = [json.dumps(output) if isinstance(output, dict) else output for output in outputs]
    write_json(artifact, {**saved, 'raw_outputs': raw})
    (cycle / 'runtime_events.jsonl').write_text(''.join(json.dumps({
        'event': 'LLM_MESSAGE', 'thread_id': saved['thread_id'],
        'message_role': 'assistant', 'content': output,
    }) + '\n' for output in raw))


@pytest.mark.parametrize('outputs, reason_index', [
    ([{'candidates': [], 'needs_literature': False}, ANSWER, {'candidates': [], 'needs_literature': False}], 1),
    ([{'candidates': [], 'needs_literature': False},
      {'candidates': [], 'needs_literature': False, 'contract_conflict': ANSWER['blocking_reason']}], 1),
])
def test_legacy_explanation_only_authorizes_fresh_delivery(stopped_lead, outputs, reason_index):
    set_historical_outputs(stopped_lead, *outputs)
    cycle, state = stopped_lead
    artifact = Path(state['recovery']['artifact'])
    original = artifact.read_bytes()
    receipt = validate(stopped_lead)
    evidence = json.loads(receipt['tests'])
    assert evidence['reason_output_index'] == reason_index
    assert evidence['resume_preparation'] == 'new_lead_delivery'
    assert inspect_repair_receipt(cycle, state) == receipt
    assert recovery.consume_frontier_reentry(cycle, state)
    assert artifact.read_bytes() == original
    assert 'research_lead_frontier' not in state['role_outputs']
    assert state['scientific_verdict'] is None


@pytest.mark.parametrize('outputs', [
    [{'candidates': []}, {'candidates': [], 'needs_literature': False}],
    [ANSWER, {'candidates': [], 'contract_conflict': '   '}],
    [ANSWER, {**ANSWER, 'contract_conflict': 'Conflicting field'}],
    [ANSWER, {'candidates': [], 'needs_literature': True}],
    [ANSWER, {'candidates': [{'hypothesis': 'Old candidate'}]}],
    [ANSWER, 'Unparseable final delivery'],
])
def test_historical_fallback_requires_consistent_empty_deliveries(stopped_lead, outputs):
    set_historical_outputs(stopped_lead, *outputs)
    assert validate(stopped_lead) is None


def test_fresh_probe_failure_leaves_incident_held(stopped_lead, monkeypatch):
    def failed_probe(*_args):
        raise recovery.subprocess.CalledProcessError(1, ['python'])

    monkeypatch.setattr(recovery, '_probe', failed_probe)
    assert validate(stopped_lead) is None
    _, state = stopped_lead
    assert state['protocol_health'] == 'violation'
    assert 'supervisor_repair_receipt' not in state


def test_operator_pause_after_validation_is_preserved(stopped_lead):
    cycle, state = stopped_lead
    validate(stopped_lead)
    write_json(cycle / 'recovery_control.json', {'action': 'pause'})
    with pytest.raises(ValueError, match='operator control'): recovery.consume_frontier_reentry(cycle, state)
    assert state['protocol_health'] == 'violation'


def test_old_source_requires_exact_recorded_v15_hash(tmp_path, monkeypatch):
    old = b'"theory-master/role/research-lead/frontier-proposal", 15,' + recovery._ERROR.encode()
    monkeypatch.setattr(recovery.subprocess, 'run', lambda *_a, **_k: Mock(stdout=old))
    source = {'source_coherent': True, 'sdk_git_sha': '1' * 40,
              'module_sha256': {'contract_stages.py': hashlib.sha256(old).hexdigest()}}
    assert recovery._old_source(tmp_path, {'runtime_source_provenance': source}) == hashlib.sha256(old).hexdigest()
    source['module_sha256']['contract_stages.py'] = 'f' * 64
    with pytest.raises(ValueError): recovery._old_source(tmp_path, {'runtime_source_provenance': source})


def test_next_lead_feedback_requires_a_committed_source_bound_exit(stopped_lead):
    from theory_master.qualification_stop_history import recent_qualification_stops
    cycle, state = stopped_lead
    state.update(status='no_qualified_candidate', protocol_health='valid')
    write_json(cycle / 'research_state.json', state)
    assert recent_qualification_stops(cycle.parent, current_cycle='2') == []
    invocation = 'inv-1-research_lead-empty-stop'
    closure = {'role': 'research_lead', 'stage': 'frontier_proposal', 'closed_output': ANSWER}
    write_json(cycle / 'semantic_closure' / (invocation + '.json'), closure)
    (cycle / 'invocations.jsonl').write_text(json.dumps({'invocation_id': invocation,
        'state': 'committed', 'commit_state': 'committed', 'stage': 'frontier_proposal',
        'role': 'research_lead', 'closure_hash': stable_hash(closure)}) + '\n')
    rows = recent_qualification_stops(cycle.parent, current_cycle='2')
    assert rows[0]['blocking_reason'] == ANSWER['blocking_reason']
    assert 'frozen_objective' not in rows[0] and 'review' not in rows[0]
    closure['closed_output'] = {**ANSWER, 'blocking_reason': 'Altered later'}
    write_json(cycle / 'semantic_closure' / (invocation + '.json'), closure)
    assert recent_qualification_stops(cycle.parent, current_cycle='2') == []


def test_real_workflow_stops_empty_qualification_without_protocol_fault(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_SUPERVISOR", "0")
    from unittest.mock import patch
    from tests.test_core import TheoryMasterTest
    spec, _ = TheoryMasterTest().make(tmp_path, reworks=0)
    workflow = Workflow(spec, FakeBackend(reworks=0))
    workflow.set_max_cycles(1)
    with patch.object(workflow, '_call', return_value=copy.deepcopy(ANSWER)) as call:
        result = workflow.run()
    assert result['status'] == 'no_qualified_candidate'
    assert result['protocol_health'] == 'valid'
    assert result['hypothesis_lock'] == {'status': 'unlocked'}
    assert result['scientific_verdict'] is None
    assert [args.args[1] for args in call.call_args_list] == ['research_lead']
