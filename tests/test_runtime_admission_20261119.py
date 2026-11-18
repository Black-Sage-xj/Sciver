"""Control-plane admission regressions; temporary roots and no model/service."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from theory_master.multi_worker import BranchClaims, CycleClaims
from theory_master.runtime_admission import admission_lock, assert_no_worker_claims
from theory_master.runtime_manager import (
    RuntimeRegistryError, foreground_runtime, load_registry, registry_path,
    runtime_health, update_registry,
)


def test_existing_cycle_claim_refuses_single_foreground(tmp_path):
    claims = CycleClaims(tmp_path)
    claim = claims.claim_cycle('worker-1')
    with pytest.raises(RuntimeError, match='multi-Worker claims'):
        with foreground_runtime(tmp_path, master_id=1, operation='run'):
            pytest.fail('single owner admitted')
    assert claims.list_claims()[0]['claim_id'] == claim['claim_id']
    assert not runtime_health(tmp_path)['active']


@pytest.mark.parametrize('kind', ['branch', 'cycle'])
def test_held_runner_lock_refuses_both_claim_generations(tmp_path, kind):
    directory = tmp_path / '.theory-master'
    directory.mkdir()
    # JSON may be absent or stale; the held lock is actual execution ownership.
    update_registry(tmp_path, state='STOPPED')
    with (directory / 'runner.lock').open('a+') as runner:
        fcntl.flock(runner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch('theory_master.multi_worker.frontier_snapshot', return_value=(
                'revision', [{'hypothesis_id': 'H-1', 'entry_node_id': 'H-1'}])):
            with pytest.raises(ValueError, match='runner.lock is held'):
                if kind == 'cycle':
                    CycleClaims(tmp_path).claim_cycle('worker-1')
                else:
                    BranchClaims(tmp_path, tmp_path / 'graph.sqlite3').claim('worker-1')


@pytest.mark.parametrize('content', ['{broken', '[]', '{}'])
def test_corrupt_registry_is_unknown_and_never_replaced_on_admission(tmp_path, content):
    path = registry_path(tmp_path)
    path.parent.mkdir(exist_ok=True)
    path.write_text(content)
    assert runtime_health(tmp_path)['state'] == 'REGISTRY_INVALID'
    assert runtime_health(tmp_path)['ownership_unknown']
    with pytest.raises(RuntimeRegistryError):
        load_registry(tmp_path)
    with pytest.raises((RuntimeError, ValueError)):
        CycleClaims(tmp_path).claim_cycle('worker-1')
    with pytest.raises(RuntimeRegistryError):
        with foreground_runtime(tmp_path, master_id=1, operation='run'):
            pytest.fail('corrupt registry admitted')
    assert path.read_text() == content
    # The refused foreground path must release its physical owner lock.
    with (path.parent / 'runner.lock').open('a+') as runner:
        fcntl.flock(runner, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize('name,schema', [
    ('branch_claims.json', 'theory-master/branch-claims/v1'),
    ('cycle_claims.json', 'theory-master/cycle-claims/v1'),
])
def test_both_worker_registry_generations_block_single_owner(tmp_path, name, schema):
    directory = tmp_path / '.theory-master'
    directory.mkdir()
    (directory / name).write_text(json.dumps({'schema': schema, 'claims': {'owned': {}}}))
    with admission_lock(tmp_path), pytest.raises(RuntimeError, match='multi-Worker claims'):
        assert_no_worker_claims(tmp_path)


@pytest.mark.parametrize('name', ['runtime.json', 'branch_claims.json', 'cycle_claims.json'])
def test_unsafe_or_corrupt_registries_fail_closed(tmp_path, name):
    directory = tmp_path / '.theory-master'
    directory.mkdir()
    foreign = tmp_path / 'foreign.json'
    foreign.write_text('{}')
    (directory / name).symlink_to(foreign)
    with admission_lock(tmp_path), pytest.raises(RuntimeError, match='Unsafe'):
        assert_no_worker_claims(tmp_path)
    assert foreign.read_text() == '{}'


@pytest.mark.parametrize('name', ['branch_claims.json', 'cycle_claims.json'])
@pytest.mark.parametrize('content', ['{}', '[]', '{broken'])
def test_worker_admission_never_reinitializes_corrupt_claim_records(tmp_path, name, content):
    directory = tmp_path / '.theory-master'
    directory.mkdir()
    path = directory / name
    path.write_text(content)
    with pytest.raises((RuntimeError, ValueError)):
        CycleClaims(tmp_path).claim_cycle('worker-1')
    assert path.read_text() == content


def test_managed_launch_checks_physical_owner_before_any_service_call(tmp_path):
    from types import SimpleNamespace
    from theory_master.runtime_manager import launch_managed_runtime
    update_registry(tmp_path, state='STOPPED')
    with (tmp_path / '.theory-master/runner.lock').open('a+') as runner:
        fcntl.flock(runner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch('theory_master.runtime_manager.legacy_live_owner', return_value=None), \
             patch('theory_master.runtime_manager.subprocess.run') as service:
            with pytest.raises(ValueError, match='runner.lock is held'):
                launch_managed_runtime(SimpleNamespace(research_root=tmp_path),
                                       operation='run', identifier='test')
        service.assert_not_called()
