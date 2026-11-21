"""Explicit delivery repair admits only source-bound administrative equivalence."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from theory_master.delivery_repair import (
    current_delivery_payload, explicit_repair_payload, freeze_delivery_revision,
    repair_delivery, science_authority_digest,
)
from theory_master.science_graph_store import ScienceGraphStore
from test_delivery_repair import _completed, _report
from test_operator_delivery import empty_qualification_state


def _administrative_drift(tmp_path, *, empty=True, timestamp=True):
    spec, workflow, backend, cycle = _completed(tmp_path)
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assessment = store.goal_gate_history(cycle.name)[-1]
    original_state = (cycle / 'research_state.json').read_bytes()
    if empty:
        later = spec.research_root / '2'
        later.mkdir()
        (later / '.theory-master-cycle').write_text('theory-master/v2\nfixture\n')
        state = empty_qualification_state()
        state['cycle_id'] = '2'
        (later / 'research_state.json').write_text(json.dumps(state))
    if timestamp:
        with ScienceGraphStore(spec.graph_database) as store:
            store.db.execute("UPDATE frontier_branches SET changed_at='2026-11-20T00:00:00+00:00'")
    assert science_authority_digest(spec, spec.graph_database) != assessment['science_digest']
    return spec, workflow, backend, cycle, assessment, original_state


@pytest.mark.parametrize('empty,timestamp', [(True, False), (False, True), (True, True)])
def test_explicit_rebuild_and_cached_reassessment_reuse_original_authority(tmp_path, empty, timestamp):
    spec, workflow, backend, cycle, assessment, original_state = _administrative_drift(
        tmp_path, empty=empty, timestamp=timestamp)
    raw = current_delivery_payload(spec, spec.graph_database, cycle)
    assert raw['science_digest'] != assessment['science_digest']
    with pytest.raises(ValueError, match='scientific authority'):
        freeze_delivery_revision(spec, spec.graph_database, cycle, require_unchanged_science=True)
    original_receipt = Path(assessment['receipt_path']).read_bytes()
    result = repair_delivery(workflow, cycle=1)
    assert result['science_digest'] == assessment['science_digest']
    assert result['delivery_revision'] == assessment['delivery_revision']
    assert (cycle / 'research_state.json').read_bytes() == original_state
    assert backend.goal_calls == 1
    assessed = repair_delivery(workflow, cycle=1, reassess=True)
    assert assessed['assessment']['verdict'] == 'not_passed'
    assert backend.goal_calls == 1  # Same revision reuses the stored assessment.
    assert Path(assessment['receipt_path']).read_bytes() == original_receipt
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert len(store.goal_gate_history(cycle.name)) == 1
        assert store.goal_terminal() is None
    # The explicit capability never changes ambient authority reads.
    assert current_delivery_payload(spec, spec.graph_database, cycle)['science_digest'] == raw['science_digest']


@pytest.mark.parametrize('change', [
    'plan', 'model', 'scope', 'evidence', 'frontier_status', 'frontier_reason',
    'later_active', 'later_protocol', 'later_resource', 'later_hypothesis', 'later_evidence',
    'source_document', 'snapshot_file', 'snapshot_manifest', 'foreign_assessment',
])
def test_explicit_equivalence_rejects_real_or_unverified_authority_change(tmp_path, change):
    spec, workflow, backend, cycle, assessment, _ = _administrative_drift(tmp_path)
    if change == 'plan':
        Path(spec.document).write_text(Path(spec.document).read_text()+'\nChanged scientific condition.\n')
    elif change == 'model':
        path = spec.research_root / 'scientific_model_state.json'
        model = json.loads(path.read_text());model['summary'] = 'Different scientific model'
        path.write_text(json.dumps(model))
    elif change == 'scope':
        path = cycle / 'research_state.json';state = json.loads(path.read_text())
        state['hypothesis_lock']['scope'] = 'Different frozen scope';path.write_text(json.dumps(state))
    elif change == 'evidence':
        with (cycle / 'evidence/registry.jsonl').open('a') as handle:
            handle.write('{"evidence_id":"changed-authority"}\n')
    elif change.startswith('frontier_'):
        field = 'status' if change == 'frontier_status' else 'reason'
        value = 'closed' if field == 'status' else 'A different scientific opportunity'
        with ScienceGraphStore(spec.graph_database) as store:
            store.db.execute(f'UPDATE frontier_branches SET {field}=?', (value,))
    elif change.startswith('later_'):
        later = spec.research_root / '2';path = later / 'research_state.json'
        state = json.loads(path.read_text())
        if change == 'later_active':state['execution_active'] = True
        if change == 'later_protocol':state['protocol_health'] = 'violation'
        if change == 'later_resource':state['cycle_budget'] = {'exhausted':True}
        if change == 'later_hypothesis':state['hypothesis_lock'] = {'id':'H-0002', 'status':'locked'}
        if change == 'later_evidence':
            (later / 'evidence').mkdir()
            (later / 'evidence/registry.jsonl').write_text('{"evidence_id":"new-evidence"}\n')
        path.write_text(json.dumps(state))
    elif change == 'source_document':
        report = _report(spec, cycle);report.write_text(report.read_text()+'\nOriginal source changed.\n')
    elif change in {'snapshot_file', 'snapshot_manifest'}:
        receipt = json.loads(Path(assessment['receipt_path']).read_text())
        path = Path(receipt['snapshot_manifest'])
        if change == 'snapshot_file':path = path.parent / 'scientific_model.json'
        path.chmod(0o644);path.write_text('{}')
    else:
        assessment = {**assessment, 'assessment_id':'another-assessment'}
    with pytest.raises((ValueError, OSError)):
        explicit_repair_payload(spec, spec.graph_database, cycle, assessment)
    assert backend.goal_calls == 1


def test_correction_of_older_cycle_cannot_ignore_a_later_real_scientific_result(tmp_path):
    spec, workflow, backend, cycle, assessment, _ = _administrative_drift(tmp_path)
    later = spec.research_root / '2';state = empty_qualification_state()
    state.update(status='over', scientific_phase='complete', scientific_verdict='supported',
                 hypothesis_lock={'id':'H-0002', 'status':'locked', 'hypothesis':'New H',
                                  'scope':'New scope', 'decisive_test':'New test'})
    (later / 'research_state.json').write_text(json.dumps(state))
    with ScienceGraphStore(spec.graph_database) as store:
        store.add_hypothesis('H-0002', title='A later actual scientific result')
        store.register_cycle_hypothesis(cycle_id='2', hypothesis_id='H-0002')
        store.append_outcome(outcome_id='cycle-outcome-2', hypothesis_id='H-0002',
                             cycle_id='2', verdict='supported', summary='Later science exists')
    with pytest.raises(ValueError):
        repair_delivery(workflow, cycle=1)
    assert backend.goal_calls == 1


def test_assessment_bound_identity_is_not_an_ambient_goal_capability(tmp_path):
    spec, workflow, backend, cycle, assessment, _ = _administrative_drift(tmp_path)
    state = json.loads((cycle / 'research_state.json').read_text())
    with pytest.raises(ValueError, match='explicit delivery repair'):
        workflow._run_goal_completion(state, {'verdict':'supported'}, repair_assessment=assessment)
    with pytest.raises(ValueError, match='unchanged scientific authority'):
        freeze_delivery_revision(spec, spec.graph_database, cycle, repair_assessment=assessment)
    with pytest.raises(ValueError, match='scientific authority'):
        workflow._run_goal_completion(state, {'verdict':'supported'})
    assert backend.goal_calls == 1


def test_unchanged_legacy_identity_does_not_require_administrative_fallback(tmp_path):
    spec, _, _, cycle = _completed(tmp_path)
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assessment = store.goal_gate_history(cycle.name)[-1]
    with patch('theory_master.specification.cycle_number', return_value=None), \
         patch('theory_master.operator_delivery.assessment_authority_digest',
               side_effect=AssertionError('Unchanged legacy identity needs no equivalence fallback')):
        assert explicit_repair_payload(spec, spec.graph_database, cycle, assessment)['science_digest'] == assessment['science_digest']


@pytest.mark.parametrize('new_locked_h_during_goal', [False, True])
def test_explicit_reassessment_keeps_cas_after_administrative_equivalence(tmp_path, new_locked_h_during_goal):
    from test_operator_delivery import setup_request
    spec, workflow, backend, cycle, request, author_calls = setup_request(tmp_path)
    later = spec.research_root / '2';later.mkdir()
    (later / '.theory-master-cycle').write_text('theory-master/v2\nfixture\n')
    (later / 'research_state.json').write_text(json.dumps(empty_qualification_state()))
    with ScienceGraphStore(spec.graph_database) as store:
        store.db.execute("UPDATE frontier_branches SET changed_at='2026-11-20T00:00:00+00:00'")
    original = backend.invoke

    def invoke(role, prompt, thread_id, model, provider):
        result = original(role, prompt, thread_id, model, provider)
        if new_locked_h_during_goal and role == 'judge' and 'Current task: goal_completion ' in prompt:
            state = empty_qualification_state()
            state['hypothesis_lock'] = {'status':'locked', 'id':'H-0002'}
            (later / 'research_state.json').write_text(json.dumps(state))
        return result

    backend.invoke = invoke
    result = repair_delivery(workflow, cycle=1, author_request=request, reassess=True)
    assert len(author_calls) == 1 and backend.goal_calls == 2
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        if new_locked_h_during_goal:
            assert result['assessment']['assessment_status'] == 'stale'
            assert store.goal_terminal() is None
            assert len(store.goal_gate_history(cycle.name)) == 1
        else:
            assert result['assessment']['verdict'] == 'passed'
            assert store.goal_terminal() is not None
