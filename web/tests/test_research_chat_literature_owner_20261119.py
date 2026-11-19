"""Kernel leases keep Literature jobs resumable across SDK PID namespaces."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from tests.test_literature_service import LiteratureBackend, Retriever
from theory_master.literature_service import _locked
from tm_observer import research_chat as chat, research_chat_literature as literature


@pytest.fixture
def study(tmp_path, monkeypatch):
    state, search = tmp_path / 'state', tmp_path / 'workspace'
    search.mkdir()
    monkeypatch.setenv('THEORY_MASTER_DATA_ROOT', str(tmp_path / 'data'))
    ident = chat.create(state, {'runs': []}, {'mode': 'preparation', 'entry': 'clear'})['session_id']
    cancel = threading.Event()
    cancel.set()
    payload = {'query': 'Compare the mechanism and its counterexample', 'scope': 'A bounded scientific question'}
    result = literature.run_study(state, search, ident, payload, backend=LiteratureBackend(),
                                  retriever=Retriever(), cancel_event=cancel)
    root = Path(result['manifest']).parent
    manifest = json.loads(Path(result['manifest']).read_text())
    manifest['status'] = 'running'
    Path(result['manifest']).write_text(json.dumps(manifest))
    index_path = state / 'research-chat-literature' / (ident + '.json')
    index = json.loads(index_path.read_text())
    # This receipt can describe an unrelated host process such as kthreadd.
    unrelated = Path('/proc/2/stat').read_text().rsplit(')', 1)[1].split()[19]
    index['jobs'][0]['active_owner'] = {'pid': 2, 'process_token': unrelated}
    index_path.write_text(json.dumps(index))
    return state, search, ident, {**payload, 'job_id': result['job_id']}, root, index_path


def test_owner_diagnostic_reads_self_procfs_instead_of_namespace_pid(monkeypatch):
    visible_pid = int(Path('/proc/self/stat').read_text().split('(', 1)[0])
    monkeypatch.setattr(literature.os, 'getpid', lambda: 2)
    owner = literature._owner()
    assert owner['pid'] == visible_pid and owner['process_token']
    assert owner['lease_id'] != literature._owner()['lease_id']


def test_stale_legacy_owner_projects_interrupted_and_resumes_idempotently(study, monkeypatch):
    state, search, ident, payload, root, index_path = study
    monkeypatch.setattr(literature.os, 'kill', lambda *args: pytest.fail('PID liveness is not authoritative'))
    assert literature.list_studies(state, ident)['jobs'][0]['status'] == 'interrupted'
    backend, retriever = LiteratureBackend(), Retriever()
    result = literature.run_study(state, search, ident, payload, backend=backend, retriever=retriever)
    assert result['status'] == 'completed'
    calls = len(backend.calls), len(retriever.calls)
    assert literature.run_study(state, search, ident, payload, backend=backend, retriever=retriever)['status'] == 'completed'
    assert calls == (len(backend.calls), len(retriever.calls))
    assert json.loads(index_path.read_text())['jobs'][0]['active_owner'] is None


def test_legacy_service_lock_protects_live_job_then_allows_resume(study):
    state, search, ident, payload, root, index_path = study
    before = index_path.read_bytes()
    backend, retriever = LiteratureBackend(), Retriever()
    with _locked(root):
        assert literature.list_studies(state, ident)['jobs'][0]['status'] == 'running'
        with pytest.raises(literature.ChatLiteratureError) as caught:
            literature.run_study(state, search, ident, payload, backend=backend, retriever=retriever)
        assert caught.value.stop_reason == 'already_running'
        assert index_path.read_bytes() == before
    assert not backend.calls and not retriever.calls
    assert literature.list_studies(state, ident)['jobs'][0]['status'] == 'interrupted'
    assert literature.run_study(state, search, ident, payload, backend=backend, retriever=retriever)['status'] == 'completed'


def test_chat_lease_covers_registration_gap_and_rejects_duplicate(study):
    state, search, ident, payload, root, index_path = study
    with literature._owner_lease(root):
        assert literature._owner_live(root)
        assert literature.list_studies(state, ident)['jobs'][0]['status'] == 'running'
        with pytest.raises(literature.ChatLiteratureError) as caught:
            literature.run_study(state, search, ident, payload, backend=LiteratureBackend(), retriever=Retriever())
        assert caught.value.stop_reason == 'already_running'
    assert not literature._owner_live(root)
    # A previous UUID remaining in a receipt does not conflict with a new lease.
    index = json.loads(index_path.read_text())
    index['jobs'][0]['active_owner'] = literature._owner()
    index_path.write_text(json.dumps(index))
    assert literature.run_study(state, search, ident, payload, backend=LiteratureBackend(), retriever=Retriever())['status'] == 'completed'


def test_exception_clears_exact_owner_and_releases_lease(study, monkeypatch):
    state, search, ident, payload, root, index_path = study
    def failed(*args, **kwargs):
        assert literature._owner_live(root)
        assert json.loads(index_path.read_text())['jobs'][0]['active_owner']['lease_id']
        raise RuntimeError('fixture before service lock acquisition')
    monkeypatch.setattr('theory_master.literature_service.run_literature', failed)
    with pytest.raises(RuntimeError, match='fixture before service'):
        literature.run_study(state, search, ident, payload)
    assert not literature._owner_live(root)
    assert json.loads(index_path.read_text())['jobs'][0]['active_owner'] is None


def test_projection_probe_is_read_only_and_rejects_symlink_lock(tmp_path):
    root = tmp_path / 'job'
    root.mkdir()
    assert not literature._owner_live(root)
    assert not list(root.iterdir())
    target = tmp_path / 'outside'
    target.touch()
    (root / literature._OWNER_LOCK).symlink_to(target)
    with pytest.raises(literature.ChatLiteratureError) as caught:
        literature._owner_live(root)
    assert caught.value.stop_reason == 'literature_lock_invalid'


@pytest.mark.skipif(sys.platform != 'linux', reason='real pinned SDK Linux PID namespace regression')
def test_real_sdk_namespace_lease_survives_pid_mismatch_and_releases_on_crash(tmp_path):
    """Use command/exec only, with no thread/start, turn/start, or model call."""
    from openai_codex import Codex, CodexConfig
    root = tmp_path / 'job'
    runtime, scratch = tmp_path / 'runtime', tmp_path / '.tmp'
    for path in (root, runtime, scratch):
        path.mkdir()
    ready, release = root / 'ready.json', root / 'release'
    probe = """
import json, os, pathlib, time
from tm_observer.research_chat_literature import _owner, _owner_lease
root = pathlib.Path.cwd()
with _owner_lease(root):
    owner = _owner()
    (root / 'ready.json').write_text(json.dumps({'owner': owner, 'namespace_pid': os.getpid()}))
    deadline = time.monotonic() + 15
    while not (root / 'release').exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    os._exit(17)
"""
    client = Codex(CodexConfig(
        cwd=str(root), config_overrides=(
            'sandbox_mode="workspace-write"',
            'sandbox_workspace_write.network_access=true',
            'sandbox_workspace_write.exclude_slash_tmp=true',
            'sandbox_workspace_write.exclude_tmpdir_env_var=true',
            'sandbox_workspace_write.writable_roots=' + json.dumps([str(tmp_path)]),
        ),
        env={'CODEX_HOME': str(runtime), 'TMPDIR': str(scratch),
             'PYTHONPATH': str(Path(literature.__file__).resolve().parents[1])},
    ))
    results, errors = [], []
    def run():
        try:
            results.append(client._client._request_raw('command/exec', {
                'command': [sys.executable, '-c', probe], 'cwd': str(root), 'timeoutMs': 20000,
            }))
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and not errors and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), errors
        receipt = json.loads(ready.read_text())
        assert literature._owner_live(root)
        record = Path(f"/proc/{receipt['owner']['pid']}/stat").read_text()
        host_namespace = str(Path('/proc/self/ns/pid').readlink())
        identity_matches = record.rsplit(')', 1)[1].split()[19] == receipt['owner']['process_token']
        if receipt['owner']['pid_namespace'] == host_namespace:
            assert identity_matches
        else:
            assert receipt['owner']['pid'] == receipt['namespace_pid']
        release.touch()
        worker.join(timeout=25)
        assert not worker.is_alive() and not errors, errors
        assert results[0]['exitCode'] == 17, results
        assert not literature._owner_live(root)
        print('OWNER_NAMESPACE_PROBE ' + json.dumps({**receipt, 'held_while_active': True,
            'released_after_exit': True, 'child_exit_code': results[0]['exitCode'],
            'host_pid_namespace': host_namespace, 'host_pid_identity_matches': identity_matches}))
    finally:
        release.touch()
        client.close()
        worker.join(timeout=2)
