"""Stopped report replay and one-use recovery evidence; no model calls."""
import copy
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master import supervisor_report_document_recovery as recovery
from theory_master import supervisor_repair_receipts as receipts
from theory_master.evidence import EvidenceRegistry
from theory_master.invocation_artifacts import InvocationArtifactTracker
from theory_master.runtime import write_json
from theory_master.supervisor_runtime_repair import _TESTS


@pytest.fixture
def stopped(tmp_path, monkeypatch):
    engine = tmp_path / 'sdk/theory_master'
    original = Path(recovery.__file__).parent
    shutil.copytree(original, engine, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (engine.parent / 'tests').mkdir()
    for name in _TESTS:
        shutil.copyfile(original.parent / 'tests' / name, engine.parent / 'tests' / name)
    root = tmp_path / 'research'
    cycle = root / '1'
    workspace = cycle / 'workspace'
    workspace.mkdir(parents=True)
    vault = tmp_path / 'vault'
    vault.mkdir()
    config, plan = vault / 'config.json', vault / 'research_plan.md'
    config.write_text('{}')
    plan.write_text('# Frozen plan\n')
    spec = SimpleNamespace(research_root=root, task_home=vault, document=plan,
                           branch_canvas=vault / 'exploration.canvas',
                           notes_mirror_dir=vault / 'notes', legacy_research_root=None,
                           benchmark_root=None)
    monkeypatch.setattr(recovery, '_task_spec', lambda root: (config, spec))
    producer = {'invocation_id': 'inv-1-investigator', 'role': 'investigator', 'stage': 'investigate'}
    tracker = InvocationArtifactTracker.start(cycle_dir=cycle, workspace=workspace, **producer)
    document = workspace / 'analysis/proof.md'
    document.parent.mkdir()
    document.write_text('# Proof\n\n## Result\nThe supplied identity.\n')
    manifest = tracker.finalize(status='completed')
    registry = EvidenceRegistry(cycle, workspace, research_root=root, task_local_root=vault)
    registry.register_role_output('investigator', {'evidence': [{'summary': 'Bounded result'}]},
        epoch='post_lock_test', producer_invocation=producer, hypothesis_ref='H-1',
        invocation_artifact_manifest=manifest)
    state = {'status': 'failed', 'phase': 'failed', 'cycle_id': '1', 'resumable': True,
             'execution_active': False, 'protocol_health': 'valid',
             'hypothesis_lock': {'id': 'H-1'},
             'scientific_verdict': 'supported',
             'role_outputs': {'reporter': {'title': 'Report',
                 'summary': '## Main result\n![[analysis/proof.md#Result|Proof]]'},
                 'judge': {'verdict': 'supported', 'rationale': 'Frozen result resolved.'}},
             'recovery': {'kind': 'ValueError', 'category': 'runtime_validation', 'at': 'original',
                 'message': recovery.ERROR_PREFIX + 'analysis/proof.md'}}
    checkpoint = cycle / 'checkpoints/000001-reporter.json'
    state['last_safe_checkpoint'] = str(checkpoint)
    write_json(checkpoint, copy.deepcopy(state))
    write_json(cycle / 'research_state.json', state)
    return engine, cycle, state, document, registry


def validate(stopped):
    engine, cycle, state, _, _ = stopped
    return recovery.validate_report_document_reentry(cycle, state, engine=engine)


def test_replay_preserves_science_report_and_evidence_projections(stopped):
    engine, cycle, state, document, registry = stopped
    paths = [cycle / 'research_state.json', Path(state['last_safe_checkpoint']), document,
             registry.records_path, registry.registration_batches_path,
             registry.manifest_path, registry.coverage_path]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths if p.exists()}
    record = validate(stopped)
    assert record['validator'] == receipts.REPORT_DOCUMENT_VALIDATOR
    assert json.loads(record['tests'])['fresh_document_snapshot'] == 'passed'
    assert record['failure'] == state['recovery']
    assert record['runtime_verification'] == 'pending'
    assert receipts.inspect_repair_receipt(cycle, state, engine=engine) == record
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == value for p, value in before.items())
    assert state['role_outputs']['judge']['verdict'] == 'supported'
    assert receipts.unused_repair_credit(state)
    state['supervisor_consumed_repairs'] = [receipts.repair_credit_key(state)]
    assert not receipts.unused_repair_credit(state)


@pytest.mark.parametrize('change', ['live', 'protocol', 'budget', 'abandoned', 'paused', 'kind', 'category'])
def test_recovery_never_waives_existing_resume_guards(stopped, change):
    _, _, state, _, _ = stopped
    if change == 'live': state['execution_active'] = True
    elif change == 'protocol': state['protocol_health'] = 'violation'
    elif change == 'budget': state['investigator_budget'] = {'rejected': True}
    elif change == 'abandoned': state['abandoned_cycle'] = {'reason': 'operator'}
    elif change == 'paused': state['recovery']['action'] = 'pause'
    elif change == 'kind': state['recovery']['kind'] = 'RuntimeError'
    else: state['recovery']['category'] = 'budget_validation'
    assert validate(stopped) is None
    assert receipts.RECEIPT_KEY not in state


@pytest.mark.parametrize('change', ['missing', 'drifted', 'report', 'judge', 'wrong_reference'])
def test_invalid_documents_or_changed_committed_outputs_do_not_authorize_resume(stopped, change):
    _, _, state, document, _ = stopped
    if change == 'missing': document.unlink()
    elif change == 'drifted': document.write_text('# A different result\n')
    elif change == 'report': state['role_outputs']['reporter']['summary'] += '\nChanged'
    elif change == 'judge': state['role_outputs']['judge']['verdict'] = 'rejected'
    else: state['recovery']['message'] = recovery.ERROR_PREFIX + 'another.md'
    with pytest.raises(ValueError):
        validate(stopped)
    assert receipts.RECEIPT_KEY not in state


@pytest.mark.parametrize('change', ['source', 'checkpoint', 'document', 'metadata', 'new_metadata', 'occurrence'])
def test_receipt_rechecks_source_checkpoint_and_original_material_identity(stopped, change):
    engine, cycle, state, document, registry = stopped
    validate(stopped)
    if change == 'source': (engine / 'research_report.py').write_text('# changed\n')
    elif change == 'checkpoint': Path(state['last_safe_checkpoint']).write_text('{}')
    elif change == 'document': document.write_text('changed')
    elif change == 'metadata': registry.records_path.write_text('')
    elif change == 'new_metadata': registry.lifecycle_path.write_text('')
    else: state['recovery']['at'] = 'another occurrence'
    with pytest.raises(ValueError):
        receipts.inspect_repair_receipt(cycle, state, engine=engine)


def test_readonly_projection_computes_without_creating_or_replacing_projection_files(stopped):
    _, _, _, _, registry = stopped
    registry.manifest_path.unlink(missing_ok=True)
    registry.coverage_path.unlink(missing_ok=True)
    result = registry.rebuild(write=False)
    assert result['manifest']['records']
    assert not registry.manifest_path.exists()
    assert not registry.coverage_path.exists()


def test_fresh_probe_timeout_is_a_safe_validation_failure(stopped, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('python', 30)
    monkeypatch.setattr(recovery.subprocess, 'run', timeout)
    with pytest.raises(ValueError, match='timed out'):
        validate(stopped)
    assert receipts.RECEIPT_KEY not in stopped[2]
