"""Plan editing, material publication and reviewed creation stay independent."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest

from tests.test_literature_service import LiteratureBackend, Retriever
from tm_observer import research_chat as chat, research_chat_literature as studies
from tm_observer import research_preparation as prep
from tm_observer import research_preparation_literature as retrieval

PLAN = ('# Goal\n\nExplain the observed scaling.\n\n'
        '# Research Contract\n\nCompare two mechanisms using independent samples.\n\n'
        '# Final Gate\n\nResolve the prediction comparison with uncertainty.\n\n'
        '# Required Context\n\nUse measurement definitions and source notes.\n')


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    monkeypatch.setenv('THEORY_MASTER_DATA_ROOT', str(tmp_path / 'data'))
    state, search = tmp_path / 'state', tmp_path / 'workspace'
    search.mkdir()
    ident = chat.create(state, {'runs': []}, {'mode': 'preparation', 'entry': 'clear'})['session_id']
    initial = prep.snapshot(state, ident)
    current = prep.update(state, ident, {'expected_revision': initial['revision'], 'draft': PLAN})
    return state, search, ident, prep.workspace(state, ident), current


def confirm(row):
    return {'confirm': True, 'expected_revision': row['revision']}


def test_material_work_never_blocks_plan_or_reads_unfinished_bodies(preparation, monkeypatch):
    state, search, ident, root, current = preparation
    material = root / 'literature' / 'in-progress.txt'
    material.parent.mkdir()
    material.write_text('unfinished material')
    real_read = prep._read
    def read(path, *args):
        assert Path(path) != material, 'Plan editing must not read an unfinished corpus body'
        return real_read(path, *args)
    monkeypatch.setattr(prep, '_read', read)
    with prep._material_locked(root), ThreadPoolExecutor(max_workers=1) as pool:
        saved = pool.submit(prep.update, state, ident, {
            'expected_revision': current['revision'], 'title': 'Plan saved during Literature',
        }).result(timeout=1)
        assert saved['title'] == 'Plan saved during Literature'
        creation = pool.submit(prep.create_project, state, search, ident, confirm(saved))
        with pytest.raises(prep.PreparationError) as busy:
            creation.result(timeout=1)
        assert busy.value.status == 409 and '文献资料' in str(busy.value)
    assert prep._metadata(root, ident)['creation'] is None
    assert not (search / 'projects').exists()


def test_live_legacy_plan_lock_returns_retryable_conflict(preparation):
    state, search, ident, root, current = preparation
    with prep._locked(root), ThreadPoolExecutor(max_workers=1) as pool:
        for operation, args in ((prep.update, (state, ident, {'expected_revision': current['revision'], 'title': 'retry'})),
                                (prep.create_project, (state, search, ident, confirm(current)))):
            with pytest.raises(prep.PreparationError) as busy:
                pool.submit(operation, *args).result(timeout=1)
            assert busy.value.status == 409
    assert prep.update(state, ident, {'expected_revision': current['revision'], 'title': 'retry'})['title'] == 'retry'


def test_material_changes_require_review_without_conflicting_with_plan_edit(preparation):
    state, search, ident, root, reviewed = preparation
    (root / 'new-source.md').write_text('New literature after review')
    changed = prep.snapshot(state, ident)
    assert changed['plan_revision'] == reviewed['plan_revision']
    assert changed['material_revision'] != reviewed['material_revision']
    with pytest.raises(prep.PreparationError, match='文献资料已变化'):
        prep.create_project(state, search, ident, confirm(reviewed))
    assert not (search / 'projects').exists()
    saved = prep.update(state, ident, {'expected_revision': reviewed['revision'], 'title': 'Independent Plan edit'})
    assert saved['material_revision'] == changed['material_revision']
    receipt = prep.create_project(state, search, ident, confirm(saved))
    assert (Path(receipt['snapshot_path']) / 'materials/new-source.md').read_text() == 'New literature after review'


def test_old_review_token_can_edit_plan_but_creation_requires_new_review(preparation):
    state, search, ident, root, current = preparation
    legacy = current['plan_revision']
    with pytest.raises(prep.PreparationError, match='重新审阅'):
        prep.create_project(state, search, ident, {'confirm': True, 'expected_revision': legacy})
    saved = prep.update(state, ident, {'expected_revision': legacy, 'title': 'Saved from older client'})
    assert '.' in saved['revision']
    receipt = prep.create_project(state, search, ident, confirm(saved))
    assert prep.create_project(state, search, ident, {'confirm': True, 'expected_revision': legacy})['idempotent']
    assert receipt['status'] == 'created'


def test_legacy_frozen_snapshot_still_verifies_without_material_token(preparation, tmp_path):
    state, search, ident, root, current = preparation
    value = prep._metadata(root, ident)
    legacy_files = prep._freeze(root, value, current['draft'], [])
    target = tmp_path / 'legacy-project'
    target.mkdir()
    manifest = prep._publish_snapshot(target, legacy_files)
    assert prep._verify_snapshot(target, current['draft_revision'], manifest, current['plan_revision']) == PLAN


def test_active_literature_allows_plan_edit_and_requires_review_after_finish(preparation):
    state, search, ident, root, initial = preparation
    entered, release = threading.Event(), threading.Event()
    def event(row):
        if row['event'] == 'literature.job_started':
            entered.set()
            assert release.wait(5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(studies.run_study, state, search, ident,
            {'query': 'Compare the mechanism and its counterexample', 'scope': 'A bounded scientific question'},
            backend=LiteratureBackend(), retriever=Retriever(), on_event=event)
        try:
            assert entered.wait(2)
            saved = pool.submit(prep.update, state, ident, {
                'expected_revision': initial['revision'], 'title': 'Concurrent Plan edit',
            }).result(timeout=1)
            with pytest.raises(prep.PreparationError) as busy:
                pool.submit(prep.create_project, state, search, ident, confirm(saved)).result(timeout=1)
            assert busy.value.status == 409
        finally:
            release.set()
        assert future.result(timeout=5)['status'] == 'completed'
    with pytest.raises(prep.PreparationError, match='文献资料已变化'):
        prep.create_project(state, search, ident, confirm(saved))
    reviewed = prep.snapshot(state, ident)
    receipt = prep.create_project(state, search, ident, confirm(reviewed))
    assert (Path(receipt['snapshot_path']) / 'research_plan.md').read_text() == PLAN


def test_host_publication_uses_same_lock_order_and_refuses_busy_materials(preparation):
    state, search, ident, root, current = preparation
    with prep._material_locked(root):
        with pytest.raises(retrieval.PreparationLiteratureError, match='文献资料'):
            with retrieval._locked_workspace(root):
                pytest.fail('Busy corpus cannot accept an independent publication')
        # Failed publication must release the Plan lock rather than invert it.
        assert prep.update(state, ident, {'expected_revision': current['revision'], 'title': 'No deadlock'})['title'] == 'No deadlock'


def test_hidden_staging_and_runtime_files_do_not_expand_reviewed_materials(preparation):
    state, search, ident, root, current = preparation
    for folder in ('.literature-running', '.theory-master-tmp', 'tmp', '__pycache__'):
        directory = root / folder
        directory.mkdir()
        (directory / 'partial.txt').write_text('Not a published material')
    assert prep.snapshot(state, ident)['revision'] == current['revision']
    receipt = prep.create_project(state, search, ident, confirm(current))
    manifest = json.loads((Path(receipt['snapshot_path']) / 'manifest.json').read_text())
    assert len(manifest['files']) == 3
