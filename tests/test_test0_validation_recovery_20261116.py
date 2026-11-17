"""Offline regressions for validation identity and blocked recovery; no provider calls."""
import copy
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from theory_master import supervisor_candidate_repair as candidates
from theory_master import supervisor_recovery as jobs
from theory_master.evidence import EvidenceRegistry
from theory_master.contract_closure import close_agent_output
from theory_master.prompt_stages import build_initial_prompt, stage_context
from theory_master.runtime_supervisor_review import resume_blocker
from theory_master.skills import skills_root
from theory_master.supervisor_incident import engineering_evidence
from theory_master.workflow import Workflow
from test_supervisor_repair_receipts_20261022 import stopped


def test_ordinary_validation_stays_adjudicable_without_certifying_blind_identity(tmp_path):
    cycle = tmp_path / '1'
    workspace = cycle / 'workspace'
    workspace.mkdir(parents=True)
    registry = EvidenceRegistry(cycle, workspace)
    statement = ('Prespecified train estimator gives D=0.37; the disjoint validation estimate '
                 '0.36 lies in the train-derived interval. No protected blind identity is claimed.')
    output = close_agent_output('investigator', 'investigate',
                                {'findings': [{'statement': statement}]}, {}, {})
    registry.register_role_output('investigator', output,
        epoch='post_lock_test', producer_invocation={'role': 'investigator',
        'stage': 'investigate', 'invocation_id': 'inv-current'})
    payload = json.loads(registry.manifest_path.read_text())
    assert payload['records'][0]['adjudicable']
    assert payload['records'][0]['freshness'] == 'origin_unverified'
    runner = Workflow.__new__(Workflow)
    runner.cycle_dir = cycle
    result = runner._materialize_investigation_result({}, {}, {'evidence_manifest': {'payload': payload}})
    ordinary = {'hypothesis': 'Train and validation estimates agree', 'scope': 'Disjoint trajectories',
                'decisive_test': 'Fix method on train, then compare validation independently.'}
    strict = {**ordinary, 'decisive_test': 'Freeze the prediction before any protected blind holdout exposure.'}
    for lock in (ordinary, strict):
        context = stage_context('judge', 'terminal_assessment',
                                {'hypothesis_lock': lock, 'investigation_result': result})
        prompt = build_initial_prompt(prefix='C1', role='judge', stage='terminal_assessment',
            contract_ref='judge/v8', contract_schema='{}', context=context, write_rule='Read only')
        received = json.loads(prompt.split('Context:\n', 1)[1])
        assert received['hypothesis_lock']['decisive_test'] == lock['decisive_test']
        assert received['investigation_result']['findings'][0]['statement'] == statement
        assert 'protected_identity_verified' not in json.dumps(received)
        assert 'missing stronger SDK attestation or origin_unverified alone' in prompt
        assert 'Ground each objection in a specific necessary frozen requirement' in prompt
    # Source declarations still cannot upgrade post-hoc data into blind evidence.
    with pytest.raises(ValueError, match='agent-generated validation cannot claim holdout'):
        registry.register_role_output('investigator', {'evidence': [{'summary': statement,
            'provenance': {'source': 'agent_generated', 'split': 'holdout'}}]},
            epoch='post_lock_test', producer_invocation={'role': 'investigator', 'invocation_id': 'forged'})


@pytest.mark.parametrize('kind', ['native_goal_dependency_blocked', 'repeated_unexpected_block'])
def test_native_block_requires_validated_repair_before_plain_resume(stopped, kind):
    _, _, state, _ = stopped
    state['native_goal'] = {'runtime_failure_kind': kind, 'status': 'blocked'}
    assert 'dependency or contract diagnosis' in resume_blocker(state)
    assert resume_blocker(state, runtime_revalidated=True) is None
    state['investigator_budget'] = {'exhausted': True}
    assert 'budget guard' in resume_blocker(state, runtime_revalidated=True)
    state['recovery']['action'] = 'pause'
    assert 'Operator pause' in resume_blocker(state, runtime_revalidated=True)


@pytest.mark.parametrize('disposition', ['needs_dependency', 'needs_rule_revision', 'needs_configuration'])
def test_nonpatch_diagnosis_is_retained_without_installation(stopped, disposition):
    engine, cycle, state, _ = stopped
    original = copy.deepcopy(state)
    def diagnose(package, model, prompt):
        context = json.loads(prompt.split('\n', 1)[1])
        assert context['incident_evidence']['schema'] == 'theory-master/engineering-evidence/v1'
        (package / 'repair_plan.json').write_text(json.dumps({
            'disposition': disposition, 'reason': 'A reviewed contract change is required',
            'dependency': 'Review the frozen test and loaded rule; do not remove budget guards'}))
        return True, 'exit_code=0\nBearer TEST_ONLY_CREDENTIAL'
    backend = MagicMock()
    with patch.object(candidates, '__file__', str(engine / 'supervisor_candidate_repair.py')), \
         patch.object(candidates, '_invoke_codex', side_effect=diagnose), \
         patch('theory_master.supervisor_runtime_repair._register_auto_change', return_value='TMC-fake'), \
         patch.object(candidates, 'promote_candidate') as install:
        result = candidates.attempt_candidate_repair(root=cycle.parent, cycle=cycle, state=state,
                     review={}, backend=backend, model='Fake', provider='Fake')
    assert result['phase'] == 'blocked'
    assert result['disposition'] == disposition
    assert result['engineering_turn']['exit_success']
    evidence = Path(result['evidence_path']).read_text()
    assert 'TEST_ONLY_CREDENTIAL' not in evidence and '[redacted]' in evidence
    install.assert_not_called()
    backend.invoke_runtime_review.assert_not_called()
    assert state == original


@pytest.mark.parametrize('exit_success', [True, False])
def test_missing_plan_keeps_engineering_output_even_on_successful_exit(stopped, exit_success):
    engine, cycle, state, _ = stopped
    with patch.object(candidates, '__file__', str(engine / 'supervisor_candidate_repair.py')), \
         patch.object(candidates, '_invoke_codex', return_value=(exit_success, 'No legal patch. Bearer TEST_ONLY_CREDENTIAL')), \
         patch('theory_master.supervisor_runtime_repair._register_auto_change', return_value='TMC-fake'):
        result = candidates.attempt_candidate_repair(root=cycle.parent, cycle=cycle, state=state,
            review={}, backend=MagicMock(), model='Fake', provider='Fake')
    retained = Path(result['evidence_path']).read_text()
    assert 'No legal patch.' in retained and 'TEST_ONLY_CREDENTIAL' not in retained
    assert result['phase'] == 'blocked'
    if exit_success:
        assert result['disposition'] == 'needs_dependency'
        assert 'did not deliver' in result['reason']


def test_diagnostic_plan_cannot_smuggle_source_changes(tmp_path):
    baseline, candidate = tmp_path / 'old', tmp_path / 'new'
    baseline.mkdir()
    candidate.mkdir()
    (baseline / 'engine.py').write_text('budget_guard = True')
    (candidate / 'engine.py').write_text('budget_guard = False')
    result = candidates.diagnostic_plan({'disposition': 'needs_rule_revision',
        'reason': 'Need a rule review', 'dependency': 'Review rule'}, baseline, candidate)
    assert result['phase'] == 'blocked' and 'changed source' in result['reason']
    assert 'disposition' not in result


def test_dependency_wait_is_cheap_and_observed_skill_change_wakes_it(stopped, tmp_path):
    engine, cycle, state, _ = stopped
    skill = tmp_path / 'numerical-investigation/SKILL.md'
    skill.parent.mkdir()
    skill.write_text('ordinary validation rule v1')
    with patch.object(jobs, '__file__', str(engine / 'supervisor_recovery.py')), \
         patch('theory_master.supervisor_incident.loaded_rule_paths', return_value=[skill]):
        jobs.begin_action(cycle, state)
        validation = {'phase': 'blocked', 'disposition': 'needs_rule_revision',
                      'reason': 'Need reviewed rule', 'dependency': 'Loaded numerical rule revision'}
        job = jobs.finish_action(cycle, state, {'action': 'hold', 'reason': 'Rule review required',
                                               'engineering_skill': {'validation': validation}})
        assert job['dependency_wait'] and job['recovery_owner']
        assert not jobs.due(cycle, state, at=job['next_wakeup_epoch'] + 10**9)
        from theory_master.supervisor_continuity import review_and_repair_runtime_failure
        with patch('theory_master.supervisor_continuity._review_once') as paid_action:
            outcome = review_and_repair_runtime_failure(backend=MagicMock(), research_root=cycle.parent,
                cycle_dir=cycle, state=state, state_path=cycle / 'research_state.json',
                incident_id='same-incident', scheduler_locked=True)
        paid_action.assert_not_called()
        assert outcome['action'] == 'hold' and outcome['recovery_job']['dependency_wait']
        skill.write_text('ordinary validation rule v2')
        assert jobs.due(cycle, state)
        state['recovery']['action'] = 'pause'
        assert not jobs.due(cycle, state)


def test_engineering_bundle_binds_public_outputs_to_failed_invocation(stopped):
    _, cycle, state, _ = stopped
    state['hypothesis_lock'].update(hypothesis='H', scope='S', decisive_test='Train then validation')
    state['native_goal'] = {'thread_id': 'thread-failed'}
    state['recovery'].update(role='investigator', thread_id='thread-failed', invocation_id='inv-failed')
    def event(kind, **payload):
        return {'type': kind, 'at': '2026-11-16T15:00:00+08:00', 'payload': payload}
    rows = [event('role.completed', role='challenger', output={'reason': '150/50 disjoint'}),
            event('role.completed', role='investigator', output={'statement': 'D=0.37'}),
            event('role.completed', role='judge', output={'objection': 'Needs certificate'}),
            event('role.started', role='investigator', invocation_id='inv-failed', stage='judge_rework'),
            event('role.started', role='judge', invocation_id='later'),
            event('role.completed', role='judge', output={'reason': 'Unrelated later result'})]
    (cycle / 'events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    bundle = engineering_evidence(cycle, state)
    assert bundle['incident']['invocation_id'] == 'inv-failed'
    assert bundle['frozen_science']['decisive_test'] == 'Train then validation'
    assert '150/50 disjoint' in bundle['latest_bound_outputs']['challenger']
    assert 'Needs certificate' in bundle['latest_bound_outputs']['judge']
    assert 'Unrelated later result' not in json.dumps(bundle)
    assert len(bundle['frozen_science_sha256']) == 64


def test_logged_skill_path_cannot_grant_arbitrary_file_reads(stopped):
    from theory_master.supervisor_incident import loaded_rule_paths
    _, cycle, state, _ = stopped
    state['native_goal'] = {'thread_id': 'original-thread'}
    rows = [{'event': 'SKILL_LOADED', 'role': 'investigator', 'thread_id': 'original-thread',
             'skill': 'numerical-investigation', 'skill_path': '/not-authorized/secrets'},
            {'event': 'SKILL_LOADED', 'role': 'investigator', 'thread_id': 'other-thread',
             'skill': 'plotting', 'skill_path': str(skills_root() / 'plotting/SKILL.md')},
            {'event': 'SKILL_LOADED', 'role': 'investigator', 'thread_id': 'original-thread',
             'skill': 'unlisted-skill', 'skill_path': '/not-authorized/other'}]
    (cycle / 'runtime_events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    assert loaded_rule_paths(cycle, state) == [skills_root() / 'numerical-investigation/SKILL.md']


def test_real_invocation_path_preserves_blocked_thread_without_scientific_submission(tmp_path):
    from test_native_goal_quota_auto_adjust_20261010 import GoalQuotaBackend, NativeGoalQuotaTests
    from theory_master.supervisor_incident import NativeGoalRuntimeFailure
    class BlockedBackend(GoalQuotaBackend):
        def invoke_goal(self, role, *, thread_id, **kwargs):
            self.started.append(thread_id)
            self.last_native_goal = {'status': 'blocked', 'thread_id': thread_id,
                                    'runtime_failure_kind': 'native_goal_dependency_blocked'}
            raise NativeGoalRuntimeFailure(thread_id=thread_id, error='Need dependency or contract diagnosis')
    spec = NativeGoalQuotaTests().make_spec(tmp_path)
    backend = BlockedBackend('blocked')
    workflow = Workflow(spec, backend)
    with pytest.raises(NativeGoalRuntimeFailure):
        workflow.run()
    state = json.loads((workflow.cycle_dir / 'research_state.json').read_text())
    assert len(backend.started) == 1
    assert state['status'] == 'failed' and state['resumable']
    assert state['native_goal']['runtime_failure_kind'] == 'native_goal_dependency_blocked'
    assert state['threads']['investigator'] == backend.started[0]
    assert not state.get('scientific_verdict')
    assert not (state.get('role_outputs') or {}).get('judge')
    assert Path(state['last_safe_checkpoint']).is_file()


@pytest.mark.parametrize('timeout', [False, True])
def test_engineering_subprocess_keeps_redacted_output_on_exit_or_timeout(tmp_path, timeout):
    import subprocess
    from theory_master.supervisor_runtime_repair import _invoke_codex
    class Child:
        pid = 123456789
        returncode = 0
        def __init__(self, command, **kwargs):
            self.output = kwargs['stdout']
        def communicate(self, **kwargs):
            self.output.write(b'Engineer explains missing dependency. Bearer TEST_ONLY_CREDENTIAL\n')
            self.output.flush()
            if timeout:
                raise subprocess.TimeoutExpired('mock-codex', 1)
        def wait(self):
            self.returncode = -9
    with patch('theory_master.supervisor_runtime_repair.shutil.which', return_value='/mock/codex'), \
         patch('theory_master.supervisor_runtime_repair.subprocess.Popen', side_effect=Child), \
         patch('theory_master.supervisor_runtime_repair.os.killpg') as kill:
        success, detail = _invoke_codex(tmp_path, 'Fake', 'diagnose')
    assert success is (not timeout)
    assert 'Engineer explains missing dependency' in detail
    assert '[redacted]' in detail and 'TEST_ONLY_CREDENTIAL' not in detail
    assert ('timed out' in detail) is timeout
    assert kill.called is timeout
