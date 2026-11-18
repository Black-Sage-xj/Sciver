"""Stable auth ownership; every account/runtime path stays in tmp_path."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from theory_master import codex_accounts as accounts
from theory_master.worker_routes import _pinned_codex_home


def auth(identity, token='initial'):
    return json.dumps({'tokens': {'account_id': identity, 'access_token': token}})


@pytest.fixture
def layout(tmp_path, monkeypatch):
    daily, root, runtime = (tmp_path / n for n in ('daily', 'accounts', 'tm-runtime'))
    monkeypatch.setattr(accounts, 'account_root', lambda: root)
    monkeypatch.setattr(accounts, '_legacy_home', lambda: daily)
    monkeypatch.setattr(accounts, 'runtime_home', lambda: runtime)
    return daily, root, runtime


def initialize(layout):
    daily, _, _ = layout
    daily.mkdir()
    (daily / 'auth.json').write_text(auth('id-one'))
    result = accounts.ensure_layout()
    (result['2'] / 'auth.json').write_text(auth('id-two'))
    accounts.use_account('2', restart_app=False)
    return result


def atomic_refresh(path, content):
    fresh = path.with_name('fresh.json')
    fresh.write_text(content)
    os.replace(fresh, path)


def test_status_and_selection_queries_never_initialize(layout):
    daily, root, runtime = layout
    assert accounts.account_status()['selected'] is None
    assert accounts.selected_account() is None
    assert accounts.read_layout()['root'] == root
    assert not root.exists() and not daily.exists() and not runtime.exists()


@pytest.mark.parametrize('home_key', ['daily_runtime', 'runtime'])
def test_old_process_refresh_binds_identity_after_selector_switch(layout, home_key):
    paths = initialize(layout)
    refresh = auth('id-one', 'refreshed')
    atomic_refresh(paths[home_key] / 'auth.json', refresh)
    before = {n: (paths[n] / 'auth.json').read_bytes() for n in ('1', '2')}
    accounts.account_status()
    assert {n: (paths[n] / 'auth.json').read_bytes() for n in ('1', '2')} == before
    assert not (paths[home_key] / 'auth.json').is_symlink()
    accounts.ensure_layout()
    assert (paths['1'] / 'auth.json').read_text() == refresh
    assert (paths['2'] / 'auth.json').read_text() == auth('id-two')
    assert accounts.selected_account() == '2'


@pytest.mark.parametrize('refresh', [auth('foreign', 'new'), 'opaque-auth'])
def test_unknown_identity_is_preserved_without_overwriting_an_account(layout, refresh):
    paths = initialize(layout)
    atomic_refresh(paths['runtime'] / 'auth.json', refresh)
    before = {n: (paths[n] / 'auth.json').read_bytes() for n in ('1', '2')}
    accounts.ensure_layout()
    assert {n: (paths[n] / 'auth.json').read_bytes() for n in ('1', '2')} == before
    copies = list((paths['root'] / 'unattributed-auth').glob('*.json'))
    assert len(copies) == 1 and copies[0].read_text() == refresh


def test_duplicate_identity_is_ambiguous_and_preserved(layout):
    paths = initialize(layout)
    (paths['2'] / 'auth.json').write_text(auth('id-one', 'second'))
    refresh = auth('id-one', 'ambiguous')
    atomic_refresh(paths['runtime'] / 'auth.json', refresh)
    accounts.ensure_layout()
    assert (paths['1'] / 'auth.json').read_text() == auth('id-one')
    assert (paths['2'] / 'auth.json').read_text() == auth('id-one', 'second')
    assert next((paths['root'] / 'unattributed-auth').glob('*')).read_text() == refresh


def test_pinned_worker_refresh_cannot_overwrite_another_identity(layout, tmp_path):
    paths = initialize(layout)
    home = _pinned_codex_home(tmp_path / 'task', 'worker-1', '2')
    atomic_refresh(home / 'auth.json', auth('id-one', 'worker-old'))
    _pinned_codex_home(tmp_path / 'task', 'worker-1', '2')
    assert (paths['1'] / 'auth.json').read_text() == auth('id-one', 'worker-old')
    assert (paths['2'] / 'auth.json').read_text() == auth('id-two')
    assert (home / 'auth.json').resolve() == paths['2'] / 'auth.json'


def test_later_atomic_refresh_is_not_removed_during_attribution(layout, monkeypatch):
    paths = initialize(layout)
    runtime_auth = paths['runtime'] / 'auth.json'
    atomic_refresh(runtime_auth, auth('id-one', 'first'))
    original = accounts._write_auth
    def write_and_refresh(data, target):
        original(data, target)
        atomic_refresh(runtime_auth, auth('id-two', 'second'))
    monkeypatch.setattr(accounts, '_write_auth', write_and_refresh)
    accounts.ensure_layout()
    assert (paths['1'] / 'auth.json').read_text() == auth('id-one', 'first')
    assert runtime_auth.read_text() == auth('id-two', 'second')
    assert (paths['2'] / 'auth.json').read_text() == auth('id-two')
