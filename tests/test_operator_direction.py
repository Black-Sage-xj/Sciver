"""Offline delivery and qualification boundaries, without model calls."""
from dataclasses import replace
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from theory_master.operator_direction import (
    format_requested_conjecture, qualification_guidance, requested_conjecture,
    claim_conjecture, consume_conjecture, list_conjectures,
)
from theory_master.prompt_stages import stage_context
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.workflow import Workflow, RecoveryStop
from tests.test_repair_verification_state_machine_20261022 import RepairFixtureBackend, make_spec


def direction(**overrides):
    return dict(external_id='human-conjecture-' + 'a' * 32,
                hypothesis='  The fixed statistic transfers.\n# A user heading --> ',
                decisive_test='Compare α≤δ\n with  two spaces.',
                reason='用户要求测试新猜想', source_session='b' * 32, **overrides)


def test_plan_marker_preserves_exact_science_and_cannot_add_sections():
    record = direction()
    block = format_requested_conjecture(**record)
    assert len(block.splitlines()) == 1
    assert block.count('-->') == 1
    assert requested_conjecture('other context\n' + block) == record
    projected = stage_context('research_lead', 'frontier_proposal', {
        'agent_graph': {}, 'operator_guidance': qualification_guidance(block, 'Earlier guidance'),
    })
    assert record['external_id'] in projected['operator_guidance']
    assert 'Earlier guidance' in projected['operator_guidance']
    assert 'Challenger qualification' in projected['operator_guidance']
    assert 'not evidence or a scientific verdict' in projected['operator_guidance']


def test_latest_current_plan_direction_replaces_old_subject_and_absence_preserves_guidance():
    old = format_requested_conjecture(**direction())
    new = dict(direction(), hypothesis='A different proposition')
    assert requested_conjecture(old + '\n' + format_requested_conjecture(**new)) == new
    assert requested_conjecture('ordinary research context') is None
    assert qualification_guidance('ordinary research context', 'existing') == 'existing'


@pytest.mark.parametrize('block', [
    '<!-- sciver:human-conjecture:v1 broken -->',
    '<!-- sciver:human-conjecture:v1 {} -->',
    '<!-- sciver:human-conjecture:v1 incomplete',
])
def test_corrupt_saved_direction_is_not_silently_ignored(block):
    with pytest.raises(ValueError):
        requested_conjecture(block)
    with pytest.raises(ValueError):
        requested_conjecture(format_requested_conjecture(**direction()) + '\n' + block)


def test_unicode_line_separators_remain_inside_single_plan_marker():
    record = dict(direction(), hypothesis='A\u0085B\u2028C\u2029D')
    block = format_requested_conjecture(**record)
    assert len(block.splitlines()) == 1
    assert requested_conjecture(block) == record


def test_fresh_qualification_delivers_direction_before_any_backend_call(tmp_path):
    workflow = Workflow(make_spec(tmp_path), RepairFixtureBackend(direct=True))
    state = workflow.initialize()
    add_direction(workflow._graph_database_path(), direction())
    state['operator_guidance_pending'] = True
    state['operator_guidance'] = {'text': 'Retain the fixed sample definition.'}
    captured = []
    class AtLead(Exception):
        pass
    def capture(_state, role, context, **kwargs):
        captured.append((role, context))
        raise AtLead()
    workflow._call = capture
    with pytest.raises(AtLead):
        workflow._qualify_and_lock(state, '1', {})
    assert captured[0][0] == 'research_lead'
    received = captured[0][1]['operator_guidance']
    assert direction()['external_id'] in received
    assert 'Retain the fixed sample definition.' in received
    assert not state.get('hypothesis_locked')
    assert state.get('hypothesis_lock', {}).get('status') != 'locked'


def test_corrupt_direction_stops_fresh_qualification_before_model(tmp_path):
    workflow = Workflow(make_spec(tmp_path), RepairFixtureBackend(direct=True))
    state = workflow.initialize()
    workflow.spec.document.write_text(workflow.spec.document.read_text().replace('- offline fixture', '<!-- sciver:human-conjecture:v1 {} -->'))
    workflow._call = Mock(side_effect=AssertionError('No model invocation permitted'))
    with pytest.raises(RecoveryStop, match='Invalid human conjecture'):
        workflow._qualify_and_lock(state, '1', {})
    workflow._call.assert_not_called()


def add_direction(database, record, *, pending=True):
    with ScienceGraphStore(database) as store:
        with store.transaction():
            store.db.execute("INSERT INTO external_records VALUES (?,?,?,?,?,?)",
                             (record['external_id'], 'ResearchFocus', 'human', 'Proposed direction',
                              format_requested_conjecture(**record), '2026-11-18T01:00:00Z'))
            if pending:
                store._event('external.human_conjecture.pending', record['external_id'],
                             {'source_references': [{'kind': 'node', 'id': 'H-0001', 'quote': 'snapshot'}]})


def test_claim_is_single_cycle_owned_and_consumed_means_delivery_only(tmp_path):
    database = tmp_path / 'graph.sqlite3'
    add_direction(database, direction())
    first = claim_conjecture(database, cycle_id='2')
    assert first['status'] == 'claimed' and first['record'] == direction()
    assert claim_conjecture(database, cycle_id='3') is None
    assert claim_conjecture(database, cycle_id='2') == first
    consume_conjecture(database, external_id=direction()['external_id'], cycle_id='2')
    consume_conjecture(database, external_id=direction()['external_id'], cycle_id='2')
    recovered = claim_conjecture(database, cycle_id='2')
    assert recovered['status'] == 'consumed' and recovered['record'] == direction()
    assert recovered['source_references'] == first['source_references']
    assert claim_conjecture(database, cycle_id='4') is None
    with pytest.raises(ValueError, match='ownership'):
        consume_conjecture(database, external_id=direction()['external_id'], cycle_id='other')


def test_concurrent_claimers_cannot_share_one_pending_direction(tmp_path):
    database = tmp_path / 'graph.sqlite3'
    add_direction(database, direction())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda cycle: claim_conjecture(database, cycle_id=cycle), ['1', '2']))
    assert sum(item is not None for item in results) == 1
    assert list_conjectures(database) == []


def test_legacy_plan_only_or_mismatched_external_never_executes(tmp_path):
    database = tmp_path / 'graph.sqlite3'
    with ScienceGraphStore(database):
        pass
    marker = format_requested_conjecture(**direction())
    assert claim_conjecture(database, cycle_id='1', required_context=marker) is None
    add_direction(database, direction(), pending=False)
    assert claim_conjecture(database, cycle_id='1') is None
    wrong = dict(direction(), hypothesis='A different stored text')
    assert claim_conjecture(database, cycle_id='1', required_context=format_requested_conjecture(**wrong)) is None
    assert claim_conjecture(database, cycle_id='1', required_context=marker) is None
    legacy = list_conjectures(database, status=None)[0]
    assert legacy['status'] == 'reconciliation_required'
    newer = dict(direction(), external_id='human-conjecture-' + 'b' * 32, hypothesis='Visible new direction')
    add_direction(database, newer)
    assert claim_conjecture(database, cycle_id='2', required_context=marker)['record'] == newer


def test_proposal_recovery_retains_snapshot_and_ignores_new_pending(tmp_path):
    workflow = Workflow(make_spec(tmp_path), RepairFixtureBackend(direct=True))
    state = workflow.initialize()
    database = workflow._graph_database_path()
    add_direction(database, direction())
    received = []
    class CrashBeforeResponse(Exception):
        pass
    def stop(_state, role, context, **kwargs):
        received.append(context['operator_guidance'])
        raise CrashBeforeResponse()
    workflow._call = stop
    with pytest.raises(CrashBeforeResponse):
        workflow._qualify_and_lock(state, '1', {})
    saved = json.loads((workflow.cycle_dir / 'research_state.json').read_text())
    assert saved['human_direction']['record'] == direction()
    newer = dict(direction(), external_id='human-conjecture-' + 'b' * 32, hypothesis='A new pending subject')
    add_direction(database, newer)
    with pytest.raises(CrashBeforeResponse):
        workflow._qualify_and_lock(saved, '1', {})
    assert received[0] == received[1]
    assert list_conjectures(database)[0]['record'] == newer


def test_existing_lead_episode_does_not_claim_newly_registered_direction(tmp_path):
    workflow = Workflow(make_spec(tmp_path), RepairFixtureBackend(direct=True))
    state = workflow.initialize()
    add_direction(workflow._graph_database_path(), direction())
    state['role_outputs']['research_lead_frontier'] = {'needs_literature': True}
    class AtLead(Exception):
        pass
    def stop(_state, role, context, **kwargs):
        assert not context.get('operator_guidance')
        raise AtLead()
    workflow._call = stop
    with pytest.raises(AtLead):
        workflow._qualify_and_lock(state, '1', {})
    assert list_conjectures(workflow._graph_database_path())[0]['status'] == 'pending'


@pytest.mark.parametrize('operation', ['claim', 'consume'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_delivery_never_creates_or_repairs_missing_or_invalid_canonical_graph(tmp_path, operation, damage):
    database = tmp_path / 'canonical.sqlite3'
    if damage == 'corrupt':
        database.write_bytes(b'not a canonical SQLite science graph')
    before = database.read_bytes() if database.exists() else None
    with pytest.raises(ValueError):
        if operation == 'claim':
            claim_conjecture(database, cycle_id='1')
        else:
            consume_conjecture(database, external_id=direction()['external_id'], cycle_id='1')
    assert (database.read_bytes() if database.exists() else None) == before
    assert not database.with_name(database.name + '-wal').exists()
