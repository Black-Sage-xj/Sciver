"""Moved implementation owners remain part of process source identity."""
import sys
from types import SimpleNamespace

import pytest

from theory_master.runtime_manager import runtime_source_provenance


@pytest.mark.parametrize('module,key', [
    ('theory_master.worker_actions', 'worker_actions.py'),
    ('theory_master.codex_goal_transport', 'codex_goal_transport.py'),
    ('theory_master.native_goal_recovery', 'native_goal_recovery.py'),
    ('theory_master.supervisor_native_goal_recovery', 'supervisor_native_goal_recovery.py'),
    ('theory_master.supervisor_lineage_recovery', 'supervisor_lineage_recovery.py'),
    ('theory_master.supervisor_delivery_alias_recovery', 'supervisor_delivery_alias_recovery.py'),
    ('theory_master.public_submission', 'public_submission.py'),
    ('theory_master.submission_rework', 'submission_rework.py'),
    ('theory_master.invocation_execution', 'invocation_execution.py'),
    ('theory_master.evidence', 'evidence.py'),
    ('theory_master.evidence_provenance', 'evidence_provenance.py'),
    ('theory_master.contract_stages', 'contract_stages.py'),
    ('theory_master.delivery_repair', 'delivery_repair.py'),
    ('tm_observer.http_handler', 'web/tm_observer/http_handler.py'),
])
def test_mixed_owner_source_is_detected_without_importing_or_executing_it(tmp_path, monkeypatch, module, key):
    foreign = tmp_path / 'foreign.py'
    foreign.write_text('# foreign implementation\n')
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(__file__=str(foreign)))
    provenance = runtime_source_provenance()
    assert provenance['source_coherent'] is False
    assert provenance['module_paths'][key] == str(foreign)
    assert any(key + ':' in message for message in provenance['source_coherence_errors'])


@pytest.mark.parametrize('layout', ['checkout', 'wheel'])
@pytest.mark.parametrize('loaded', [False, True])
def test_same_distribution_web_owner_accepts_checkout_and_wheel_layouts(tmp_path, monkeypatch, layout, loaded):
    from theory_master import runtime_manager as runtime
    package_root = tmp_path / 'distribution'
    directory = package_root / ('web/tm_observer' if layout == 'checkout' else 'tm_observer')
    directory.mkdir(parents=True)
    owner = directory / 'http_handler.py'
    owner.write_text('# this distribution\n')
    monkeypatch.setattr(runtime, '_package_root', lambda: package_root)
    monkeypatch.setattr(runtime, '_RUNTIME_SOURCE_MODULES', {
        'web/tm_observer/http_handler.py': 'tm_observer.http_handler'})
    if loaded:
        monkeypatch.setitem(sys.modules, 'tm_observer.http_handler', SimpleNamespace(__file__=str(owner)))
    else:
        monkeypatch.delitem(sys.modules, 'tm_observer.http_handler', raising=False)
    provenance = runtime.runtime_source_provenance()
    assert provenance['source_coherent'] is True
    assert provenance['module_paths']['web/tm_observer/http_handler.py'] == str(owner)
    assert 'web/tm_observer/http_handler.py' in provenance['module_sha256']


def test_wheel_layout_still_rejects_web_loaded_from_another_distribution(tmp_path, monkeypatch):
    from theory_master import runtime_manager as runtime
    package_root = tmp_path / 'site-packages'
    own = package_root / 'tm_observer/http_handler.py'
    own.parent.mkdir(parents=True)
    own.write_text('# installed distribution\n')
    foreign = tmp_path / 'foreign/web/tm_observer/http_handler.py'
    foreign.parent.mkdir(parents=True)
    foreign.write_text('# foreign checkout\n')
    monkeypatch.setattr(runtime, '_package_root', lambda: package_root)
    monkeypatch.setattr(runtime, '_RUNTIME_SOURCE_MODULES', {
        'web/tm_observer/http_handler.py': 'tm_observer.http_handler'})
    monkeypatch.setitem(sys.modules, 'tm_observer.http_handler', SimpleNamespace(__file__=str(foreign)))
    provenance = runtime.runtime_source_provenance()
    assert provenance['source_coherent'] is False
    assert provenance['source_coherence_errors'] == ['web/tm_observer/http_handler.py: ' + str(foreign)]



def test_distribution_symlink_cannot_make_a_foreign_web_owner_trusted(tmp_path, monkeypatch):
    from theory_master import runtime_manager as runtime
    package_root = tmp_path / 'site-packages'
    own = package_root / 'tm_observer/http_handler.py'
    own.parent.mkdir(parents=True)
    foreign = tmp_path / 'foreign.py'
    foreign.write_text('# another distribution\n')
    own.symlink_to(foreign)
    monkeypatch.setattr(runtime, '_package_root', lambda: package_root)
    monkeypatch.setattr(runtime, '_RUNTIME_SOURCE_MODULES', {
        'web/tm_observer/http_handler.py': 'tm_observer.http_handler'})
    monkeypatch.delitem(sys.modules, 'tm_observer.http_handler', raising=False)
    provenance = runtime.runtime_source_provenance()
    assert provenance['source_coherent'] is False
    assert provenance['source_coherence_errors'] == ['web/tm_observer/http_handler.py: ' + str(foreign)]
