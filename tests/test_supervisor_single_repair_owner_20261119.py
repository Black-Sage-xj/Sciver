"""One repair dispatch per continuity action; all model/edit functions mocked."""
from pathlib import Path
from unittest.mock import patch

import pytest

from theory_master import supervisor_continuity as continuity
from theory_master.runtime import write_json
from theory_master.runtime_supervisor_review import review_runtime_failure


@pytest.fixture
def stopped(tmp_path):
    cycle = tmp_path / 'research/1'
    cycle.mkdir(parents=True)
    checkpoint = cycle / 'checkpoint.json'
    checkpoint.write_text('{}')
    state = {'status': 'failed', 'cycle_id': '1', 'phase': 'investigator',
             'protocol_health': 'valid', 'resumable': True,
             'last_safe_checkpoint': str(checkpoint),
             'recovery': {'kind': 'ValueError', 'category': 'runtime',
                          'message': 'invalid Canvas node'}}
    write_json(cycle / 'research_state.json', state)
    return cycle, state


def args(cycle, state):
    return dict(backend=None, research_root=cycle.parent, cycle_dir=cycle, state=state,
                state_path=cycle / 'research_state.json', incident_id='INC-test', scheduler_locked=True)


def test_triage_never_executes_a_repair(stopped):
    cycle, state = stopped
    with patch('theory_master.supervisor_runtime_repair.repair_target', return_value=Path('/sdk/canvas_projection.py')), \
         patch('theory_master.supervisor_runtime_repair.attempt_runtime_repair') as edit:
        result = review_runtime_failure(**args(cycle, state))
    assert result['action'] == 'hold'
    assert result['repair_route'] == 'isolated_runtime'
    edit.assert_not_called()
    assert 'supervisor_runtime_repair_count' not in state


def test_continuity_selects_isolated_repair_once_without_generic_fallback(stopped):
    cycle, state = stopped
    review = {'action': 'hold', 'reason': 'attributed traceback', 'repair_route': 'isolated_runtime'}
    with patch('theory_master.supervisor_incident_session.publish_review_incident', return_value={'id': 'INC-test'}), \
         patch('theory_master.supervisor_runtime_repair.attempt_runtime_repair',
               return_value={'action': 'hold', 'reason': 'scoped patch rejected'}) as isolated, \
         patch('theory_master.supervisor_candidate_repair.attempt_candidate_repair') as generic, \
         patch('theory_master.supervisor_configuration_repair.attempt_configuration_repair') as config:
        outcome = continuity.attempt_engineering_skill(cycle.parent, cycle, state, review)
    isolated.assert_called_once()
    generic.assert_not_called()
    config.assert_not_called()
    assert outcome['repair_route'] == 'isolated_runtime'
    assert outcome['verification'] == 'pending_runtime_recheck'


@pytest.mark.parametrize('guard', ['pause', 'budget', 'not_resumable'])
def test_repair_route_retains_hard_guards(stopped, guard):
    cycle, state = stopped
    if guard == 'pause':
        state['recovery']['action'] = 'pause'
    elif guard == 'budget':
        state['investigator_budget'] = {'exhausted': True}
    else:
        state['resumable'] = False
    with patch('theory_master.supervisor_runtime_repair.attempt_runtime_repair') as edit:
        outcome = continuity.attempt_engineering_skill(cycle.parent, cycle, state,
            {'action': 'hold', 'repair_route': 'isolated_runtime'})
    assert outcome is None
    edit.assert_not_called()


def test_readonly_peer_probe_cannot_dispatch_engineering(stopped):
    cycle, state = stopped
    with patch('theory_master.runtime_supervisor_review.review_runtime_failure',
               return_value={'action': 'hold', 'repair_route': 'isolated_runtime'}), \
         patch.object(continuity, 'attempt_engineering_skill') as edit:
        result = continuity.review_and_repair_runtime_failure(
            **args(cycle, state), readonly_provider_probe_only=True)
    assert result['action'] == 'hold'
    edit.assert_not_called()
