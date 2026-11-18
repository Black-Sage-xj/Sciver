"""Offline proof of the old Judge projection delivery defect and its narrow gate."""
import copy
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from theory_master import supervisor_repair_receipts as receipts
from theory_master.contracts import role_contract
from theory_master.invocations import stable_hash
from theory_master.runtime import write_json
from theory_master.runtime_supervisor_review import review_runtime_failure, resume_blocker


@pytest.fixture
def stopped_judge(tmp_path):
    cycle = tmp_path / 'research/5'
    (cycle / 'checkpoints').mkdir(parents=True)
    (cycle / 'contract_repairs').mkdir()
    invocation, thread = 'inv-5-judge-original', 'original-judge-thread'
    contract = role_contract('judge', phase='terminal_assessment')
    old_schema = {**contract.prompt_schema, 'verdict':
        'supported|rejected|milestone_advanced|open for terminal; null only when requesting one bounded rework'}
    projection = json.dumps(old_schema, ensure_ascii=False, separators=(',', ':'))
    artifact = cycle / 'contract_repairs' / (invocation + '.json')
    checkpoint = cycle / 'checkpoints/000013-investigation.json'
    lock = {'id': 'H-5', 'hypothesis': 'A bounded claim.', 'scope': 'Frozen scope.',
            'decisive_test': 'A frozen comparison.'}
    write_json(checkpoint, {'cycle_id': '5', 'hypothesis_lock': lock})
    recovery = {'action': 'contract_repair_failed', 'invocation_id': invocation,
                'role': 'judge', 'stage': 'terminal_assessment', 'thread_id': thread,
                'artifact': str(artifact), 'repair_prompt': 'Required stage contract: ' + projection,
                'at': 'original-occurrence'}
    state = {'status': 'needs_attention', 'cycle_id': '5', 'resumable': True,
             'phase': 'judge:terminal_assessment:contract_repair_failed',
             'protocol_health': 'violation', 'execution_active': False,
             'scientific_verdict': None, 'hypothesis_lock': lock,
             'last_safe_checkpoint': str(checkpoint),
             'attention': {'code': 'contract_repair_failed'}, 'recovery': recovery}
    write_json(artifact, {**recovery, 'raw_outputs': [json.dumps({
        'verdict': 'open for terminal', 'rationale': 'The recorded scientific rationale.'})],
        'validation_errors': [{'path': '/', 'code': 'contract_violation',
            'expected': contract.ref, 'actual': 'object', 'message': 'judge verdict is invalid'}]})
    rows = [{
        'invocation_id': invocation, 'event': 'invocation.prepared', 'state': 'prepared',
        'role': 'judge', 'stage': 'terminal_assessment', 'commit_state': 'not_committed',
        'contract_ref': {**contract.descriptor(), 'schema_hash': stable_hash(old_schema)},
    }, {'invocation_id': invocation, 'state': 'failed', 'thread_id': thread,
        'event': 'invocation.failed', 'error_type': 'ContractRepairFailed'}]
    (cycle / 'invocations.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    event_rows = [{
        'event': 'AGENT_INPUT', 'role': 'judge', 'stage': 'terminal_assessment',
        'input_kind': 'task', 'content': 'exact stage contract: ' + projection}, {
        'event': 'LLM_MESSAGE', 'role': 'judge', 'thread_id': thread, 'message_role': 'assistant',
        'content': json.loads(artifact.read_text())['raw_outputs'][0]}]
    (cycle / 'runtime_events.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in event_rows))
    write_json(cycle / 'research_state.json', state)
    return cycle, state


def validate(stopped_judge):
    cycle, state = stopped_judge
    return receipts.validate_existing_judge_projection(
        cycle, state, engine=Path(receipts.__file__).resolve().parent)


def test_fresh_contract_receipt_keeps_science_and_historical_violation(stopped_judge):
    cycle, state = stopped_judge
    original = copy.deepcopy(state)
    artifact_bytes = Path(state['recovery']['artifact']).read_bytes()
    record = validate(stopped_judge)
    assert record['validator'] == receipts.CONTRACT_PROJECTION_VALIDATOR
    assert record['disposition'] == 'already_fixed'
    assert record['runtime_verification'] == 'pending' and not record['resume_performed']
    assert receipts.inspect_repair_receipt(cycle, state) == record
    assert resume_blocker(state, runtime_revalidated=True).startswith('Protocol/contract')
    assert resume_blocker(state, runtime_revalidated=True, contract_projection_repair=record) is None
    assert {k: v for k, v in state.items() if not k.startswith('supervisor_')} == original
    assert Path(state['recovery']['artifact']).read_bytes() == artifact_bytes


@pytest.mark.parametrize('mutation', [
    'other_role', 'other_stage', 'other_failure', 'other_error', 'other_schema',
    'no_prompt_evidence', 'committed_answer', 'scientific_verdict', 'valid_answer',
    'other_answer_defect', 'wrong_thread', 'pause', 'budget', 'active', 'checkpoint',
])
def test_unrelated_failure_or_independent_guard_never_receives_projection_receipt(stopped_judge, mutation):
    cycle, state = stopped_judge
    artifact = Path(state['recovery']['artifact'])
    saved = json.loads(artifact.read_text())
    if mutation == 'other_role':
        state['recovery']['role'] = 'investigator'
    elif mutation == 'other_stage':
        state['recovery']['stage'] = 'goal_completion'
    elif mutation == 'other_failure':
        state['recovery']['action'] = 'runtime_failure'
    elif mutation == 'other_error':
        saved['validation_errors'][0]['message'] = 'missing rationale'
    elif mutation in {'other_schema', 'committed_answer'}:
        path = cycle / 'invocations.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if mutation == 'other_schema':
            rows[0]['contract_ref']['schema_hash'] = 'different-schema'
        else:
            rows[-1]['state'] = 'committed'
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    elif mutation == 'no_prompt_evidence':
        (cycle / 'runtime_events.jsonl').write_text('')
    elif mutation == 'wrong_thread':
        path = cycle / 'runtime_events.jsonl'
        path.write_text(path.read_text().replace('original-judge-thread', 'unrelated-judge-thread'))
    elif mutation == 'scientific_verdict':
        state['scientific_verdict'] = 'supported'
    elif mutation in {'valid_answer', 'other_answer_defect'}:
        answer = json.loads(saved['raw_outputs'][0])
        if mutation == 'valid_answer':
            answer['verdict'] = 'open'
        else:
            answer['objection'] = {'requested_resolution': 'New work.'}
        saved['raw_outputs'] = [json.dumps(answer)]
    elif mutation == 'pause':
        state['recovery']['action'] = 'pause'
    elif mutation == 'budget':
        state['investigator_budget'] = {'exhausted': True}
    elif mutation == 'active':
        state['execution_active'] = True
    else:
        Path(state['last_safe_checkpoint']).unlink()
    write_json(artifact, saved)
    write_json(cycle / 'research_state.json', state)
    assert validate(stopped_judge) is None
    assert receipts.RECEIPT_KEY not in state


@pytest.mark.parametrize('mutation', ['artifact', 'events', 'ledger', 'new_occurrence', 'fake_validator'])
def test_receipt_rechecks_occurrence_and_all_original_evidence(stopped_judge, mutation):
    cycle, state = stopped_judge
    record = validate(stopped_judge)
    if mutation in {'events', 'ledger', 'artifact'}:
        path = {'artifact': Path(state['recovery']['artifact']), 'events': cycle / 'runtime_events.jsonl',
                'ledger': cycle / 'invocations.jsonl'}[mutation]
        path.write_text(path.read_text() + '\n')
    elif mutation == 'new_occurrence':
        state['recovery']['at'] = 'new-occurrence'
    else:
        record = {**record, 'validator': receipts.CANDIDATE_VALIDATOR}
    assert resume_blocker(state, runtime_revalidated=True, contract_projection_repair=record).startswith('Protocol/contract')


def test_runtime_review_authorizes_only_validated_projection_without_model(stopped_judge):
    cycle, state = stopped_judge
    original = copy.deepcopy(state)
    backend = MagicMock()
    with patch('theory_master.supervisor_runtime_repair.attempt_runtime_repair') as engineering:
        result = review_runtime_failure(backend=backend, research_root=cycle.parent, cycle_dir=cycle,
            state=state, state_path=cycle / 'research_state.json', incident_id='INC-offline')
    assert result['action'] == 'resume'
    assert state['supervisor_runtime_resume_count'] == 1
    assert len(state['supervisor_consumed_repairs']) == 1
    assert {k: v for k, v in state.items() if not k.startswith('supervisor_')} == original
    assert state['protocol_health'] == 'violation'
    backend.invoke_runtime_review.assert_not_called()
    backend.invoke.assert_not_called()
    engineering.assert_not_called()


@pytest.mark.parametrize('fresh_thread', [True, False])
def test_fresh_judge_invocation_uses_current_contract_instead_of_saved_repair_prompt(tmp_path, fresh_thread):
    from theory_master.core import FakeBackend, TaskSpec, Workflow
    from theory_master.task import bootstrap

    class CaptureJudge(FakeBackend):
        def __init__(self):
            super().__init__(reworks=0)
            self.prompts = []

        def invoke(self, role, prompt, thread_id, model, provider):
            self.prompts.append(prompt)
            return self.ensure_thread(role, thread_id, model, provider), json.dumps({
                'verdict': 'open', 'rationale': 'The bounded comparison remains uncertain.'})

    plan = tmp_path / 'research_plan.md'
    plan.write_text('# Goal\ng\n\n# Research Contract\nc\n\n# Final Gate\nf\n\n# Required Context\n- r\n')
    spec = TaskSpec.from_config(bootstrap(plan).config_path)
    backend = CaptureJudge()
    workflow = Workflow(spec, backend)
    workflow._ensure_cycle()
    state = workflow._new_state()
    state['recovery'] = {'action': 'resume', 'repair_prompt': 'OLD-FAILED-REPAIR-SENTINEL'}
    if not fresh_thread:
        state['threads']['judge'] = 'original-judge-thread'
    output = workflow._call(state, 'judge', {
        'hypothesis_lock': {'hypothesis': 'H', 'scope': 'S', 'decisive_test': 'D'},
        'investigation_result': {'result': 'Uncertain bounded result.'},
    }, fresh_thread=fresh_thread)
    assert output['verdict'] == 'open'
    assert len(backend.prompts) == 1
    assert 'supported|rejected|milestone_advanced|open|null' in backend.prompts[0]
    assert 'OLD-FAILED-REPAIR-SENTINEL' not in backend.prompts[0]
    assert 'open for terminal' not in backend.prompts[0]
