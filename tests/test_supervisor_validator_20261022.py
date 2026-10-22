"""Exercise the actual subprocess validator, without any model calls."""
from pathlib import Path
import shutil

import pytest

from theory_master import supervisor_runtime_repair as repair


def candidate(tmp_path):
    package = tmp_path / 'tool/theory_master'
    (package / 'tests').mkdir(parents=True)
    target = package / 'target.py'
    target.write_text('VALUE = True\n')
    return package, target


@pytest.mark.parametrize('body,accepted', [
    ('def test_real():\n    assert True\n', True),
    ('def test_real():\n    assert False\n', False),
    ('VALUE = True\n', False),
    ('import pytest\ndef test_real():\n    pytest.skip("unavailable")\n', False),
    ('def test_real(:\n    pass\n', False),
])
def test_real_test_execution(tmp_path, body, accepted, monkeypatch):
    package, target = candidate(tmp_path)
    for name in repair._TESTS:
        (package / 'tests' / name).write_text(body)
    # Host filters must not turn a failed required test into a false pass.
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k nonexistent')
    ok, detail = repair._validate_candidate(tmp_path, target)
    assert ok is accepted, detail
    if accepted:
        assert '3 pytest tests' in detail


def test_one_empty_suite_cannot_hide_behind_other_passing_suites(tmp_path):
    package, target = candidate(tmp_path)
    for index, name in enumerate(repair._TESTS):
        (package / 'tests' / name).write_text(
            'VALUE = True\n' if index == 0 else 'def test_real():\n    assert True\n')
    ok, detail = repair._validate_candidate(tmp_path, target)
    assert not ok
    assert 'incomplete' in detail


def test_actual_canvas_suites_run_in_copied_engine(tmp_path):
    package, _ = candidate(tmp_path)
    repo = Path(repair.__file__).resolve().parents[1]
    shutil.copytree(repo / 'theory_master', package / 'theory_master',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in repair._TESTS:
        shutil.copy2(repo / 'tests' / name, package / 'tests' / name)
    ok, detail = repair._validate_candidate(tmp_path, package / 'theory_master/canvas_projection.py')
    assert ok, detail
    assert '16 pytest tests' in detail
