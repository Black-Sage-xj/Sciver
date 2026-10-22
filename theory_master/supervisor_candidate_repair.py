"""Multi-file engineering candidates with independent differential acceptance.

The editing turn has no installation authority. A fresh read-only reviewer
assesses the complete diff and reproducer; SDK then proves the original failure
on frozen baseline, success on candidate, and frozen regressions on both.
"""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Any

from .supervisor_repair_receipts import _digest, source_manifest, _record_validation, failure_binding, _file_hash
from .supervisor_runtime_repair import _snapshot, _invoke_codex, _TESTS, _SECRET

VALIDATOR = 'differential-engine-repair/v1'
# Candidate cannot rewrite its own acceptance, execution policy or budget guards.
PROTECTED = frozenset({
    'execution_policy.py', 'investigator_budget.py', 'budget_reconciliation.py',
    'failure_classification.py', 'role_registry.py', 'backend_config.py', 'change_register.py',
    'supervisor_incident_session.py', 'worker_runtime.py', 'runtime_manager.py',
    'contracts.py', 'workflow_science.py', 'workflow_investigation.py', 'workflow_qualification.py',
    'workflow_goal.py', 'workflow_state.py', 'native_goal.py',
})


def candidate_snapshot(package: Path) -> dict:
    return {key: value for key, value in _snapshot(package).items()
            if not any(part in {'.pytest_cache', '.test-tmp'} for part in Path(key).parts)
            and key != 'validation.xml'}


def _hold(reason: str, **fields: Any) -> dict:
    return {'action': 'hold', 'phase': 'blocked', 'reason': reason, **fields}


def run_tests(package: Path, test_paths: list[Path]) -> dict:
    """Run offline with no inherited credentials, network or child processes."""
    report = package / 'validation.xml'
    report.unlink(missing_ok=True)
    tmp = package / '.test-tmp'
    tmp.mkdir(exist_ok=True)
    script = '''import os, sys
sys.path.insert(0, sys.argv[1])
import pytest
def offline(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system", "os.posix_spawn", "ctypes.dlopen"} or event.startswith("os.exec"):
        raise RuntimeError("SDK offline validation forbids network/process execution")
sys.addaudithook(offline)
raise SystemExit(pytest.main(sys.argv[2:]))
'''
    env = {'PATH': os.defpath, 'HOME': str(tmp), 'TMPDIR': str(tmp),
           'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'LANG': 'C.UTF-8'}
    try:
        proc = subprocess.run([sys.executable, '-I', '-c', script, str(package),
            '-q', '-c', os.devnull, '--confcutdir', str(package), '--junitxml', str(report),
            *map(str, test_paths)], cwd=package, env=env, capture_output=True, text=True, timeout=120)
        cases = list(ET.parse(report).getroot().iter('testcase'))
    except (OSError, subprocess.TimeoutExpired, ET.ParseError) as exc:
        return {'passed': False, 'error': type(exc).__name__, 'tests': 0}
    failures = [child for case in cases for child in case if child.tag in {'failure', 'error'}]
    skipped = any(case.find('skipped') is not None for case in cases)
    return {'passed': proc.returncode == 0 and bool(cases) and not failures and not skipped,
            'tests': len(cases), 'skipped': skipped, 'exit_code': proc.returncode,
            'failures': [' '.join((node.get('message') or '', node.text or ''))[-8000:] for node in failures],
            'output': _SECRET.sub(r'\1=[redacted]', (proc.stdout + proc.stderr)[-1800:]),
            'report_sha256': __import__('hashlib').sha256(report.read_bytes()).hexdigest()}


def accept_candidate(baseline: Path, candidate: Path, state: dict, *, backend: Any,
                     model: str, provider: str) -> dict:
    """No promotion here. All baseline inputs were frozen before the edit turn."""
    before, after = candidate_snapshot(baseline), candidate_snapshot(candidate)
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    extras = {'repair_plan.json', 'reproduction/test_incident.py'}
    sources = [k for k in changed if k not in extras]
    if not sources or len(sources) > 6:
        return _hold('Candidate must repair 1–6 existing engineering source files')
    for rel in sources:
        path = Path(rel)
        if (rel not in before or rel not in after or path.parts[0] != 'theory_master'
                or path.suffix != '.py' or path.name in PROTECTED
                or path.name.startswith(('supervisor', 'runtime_supervisor', 'worker_supervisor',
                                         'contract_', 'investigator_budget', 'hypothesis_', 'execution_policy'))):
            return _hold('Candidate changed protected code, tests or non-source files: ' + rel)
    plan = json.loads((candidate / 'repair_plan.json').read_text())
    selected = plan.get('regression_tests')
    if (not isinstance(selected, list) or not selected or len(selected) > 8
            or any(not isinstance(name, str) or Path(name).name != name
                   or not name.startswith('test_') or not name.endswith('.py')
                   or 'tests/' + name not in before for name in selected)):
        return _hold('Plan must select existing frozen regression test files')
    repro = candidate / 'reproduction/test_incident.py'
    if not repro.is_file() or repro.is_symlink() or repro.stat().st_size > 12000:
        return _hold('Missing or oversized incident reproducer')
    patch = ''.join(''.join(difflib.unified_diff(
        (baseline / rel).read_text().splitlines(True), (candidate / rel).read_text().splitlines(True),
        fromfile='before/' + rel, tofile='after/' + rel)) for rel in sources)
    if len(patch) > 24000:
        return _hold('Candidate diff exceeds independent review scope')
    from .supervisor_recovery import failure_details
    failure = failure_details(state)
    if not failure.get('kind') or not failure.get('message'):
        return _hold('Original exception identity is required for differential acceptance')
    prompt = ('Independently review this engineering candidate and incident reproducer. You have read-only authority. '
        'Reject contrived tests that check source hashes/versions, deliberately raise the expected error, weaken guards, '
        'change scientific decisions, touch credentials or merely bypass the failing operation. '
        'Approve only an attributable defect fix with a realistic reproducer and relevant frozen regressions. '
        'Return strict JSON {"approved": boolean, "reason": string}. Your approval alone never installs code.\n'
        + json.dumps({'failure': failure, 'plan': plan, 'diff': patch, 'reproducer': repro.read_text()}, ensure_ascii=False))
    thread = None
    try:
        thread, raw = backend.invoke_runtime_review(prompt, model, provider)
        approval = json.loads(raw)
    finally:
        if thread and callable(getattr(backend, 'drop_thread', None)):
            backend.drop_thread(thread)
    if not isinstance(approval, dict) or approval.get('approved') is not True or not str(approval.get('reason') or '').strip():
        return _hold('Independent candidate review rejected the plan', review=approval)
    # Exact same reproducer bytes run against old and new sources.
    (baseline / 'reproduction').mkdir(exist_ok=True)
    shutil.copy2(repro, baseline / 'reproduction/test_incident.py')
    old = run_tests(baseline, [baseline / 'reproduction/test_incident.py'])
    if (old['passed'] or old.get('tests') != 1 or old.get('skipped')
            or len(old.get('failures') or []) != 1
            or not all(str(failure[k]) in old['failures'][0] for k in ('kind', 'message'))):
        return _hold('Baseline did not reproduce the original exception', baseline_result=old)
    new = run_tests(candidate, [repro])
    if not new['passed'] or new['tests'] != 1:
        return _hold('Candidate did not resolve the reproduced failure', candidate_result=new)
    regression_names = sorted(set(selected) | set(_TESTS))
    # Every SDK mandatory suite must exist; tests cannot be supplied by candidate.
    results = {}
    for name in regression_names:
        for label, package in (('baseline', baseline), ('candidate', candidate)):
            result = run_tests(package, [package / 'tests' / name])
            results[label + '/' + name] = result
            original_failure_only = (label == 'baseline' and result.get('tests', 0) > 0
                and not result.get('skipped') and bool(result.get('failures'))
                and all(all(str(failure[k]) in text for k in ('kind', 'message')) for text in result['failures']))
            if not result['passed'] and not original_failure_only:
                return _hold('Frozen regression failed: ' + label + '/' + name, results=results)
    # Imports/test fixtures may not rewrite acceptance inputs or the candidate.
    for package, original in ((baseline, before), (candidate, after)):
        current = _snapshot(package)
        if any(current.get(key) != digest for key, digest in original.items()):
            return _hold('Source or frozen tests changed during validation')
    return {'phase': 'repair_validated', 'validator': VALIDATOR, 'sources': sources,
            'review': approval, 'baseline': old, 'candidate': new, 'regressions': results,
            'regression_hashes': {'tests/' + name: before['tests/' + name] for name in regression_names},
            'before': {k: before[k] for k in sources}, 'after': {k: after[k] for k in sources}}


def promote_candidate(engine: Path, baseline: Path, candidate: Path, accepted: dict) -> bool:
    """Multi-file CAS under the caller's exclusive deployment window, with rollback."""
    from .supervisor_runtime_repair import _promote_patch
    applied = []
    try:
        for rel in accepted['sources']:
            target = engine.parent / rel
            if target.read_bytes() != (baseline / rel).read_bytes():
                return False
        for rel in accepted['sources']:
            target = engine.parent / rel
            if not _promote_patch(target, (baseline / rel).read_bytes(), (candidate / rel).read_bytes()):
                raise ValueError('Concurrent source change during installation')
            applied.append(rel)
        return True
    except (OSError, ValueError):
        for rel in reversed(applied):
            if not _promote_patch(engine.parent / rel, (candidate / rel).read_bytes(), (baseline / rel).read_bytes()):
                raise RuntimeError('Deployment rollback needs reconciliation: ' + rel)
        return False


def attempt_candidate_repair(*, root: Path, cycle: Path, state: dict, review: dict,
                             backend: Any, model: str, provider: str) -> dict:
    if backend is None:
        return _hold('Independent read-only validator backend unavailable; retry after backend repair')
    engine = Path(__file__).resolve().parent
    from .supervisor_runtime_repair import _register_auto_change, _verify_auto_change
    baseline_manifest = source_manifest(engine)
    original_binding = failure_binding(cycle, state)
    with tempfile.TemporaryDirectory(prefix='tm-supervisor-candidate-') as td:
        workspace = Path(td)
        baseline, candidate = workspace / 'baseline', workspace / 'candidate'
        for package in (baseline, candidate):
            for name in ('theory_master', 'tests'):
                shutil.copytree(engine.parent / name, package / name,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        if source_manifest(baseline / 'theory_master') != baseline_manifest or source_manifest(candidate / 'theory_master') != baseline_manifest:
            return _hold('Source changed while freezing candidate inputs')
        # Register before the engineering model changes any candidate source.
        change_id = _register_auto_change(incident_id=str(review.get('session_id') or 'recovery'),
            cycle_dir=cycle, relative=Path('isolated-candidate'),
            before=json.dumps(baseline_manifest, sort_keys=True).encode(), after=b'candidate-pending',
            scope='Isolated multi-file candidate; independent differential acceptance pending')
        if not change_id:
            return _hold('Candidate registration failed')
        previous = previous_evidence(cycle)
        prompt = ('You are the Supervisor engineering skill in an isolated candidate workspace. '
            'Repair the attributable ordinary engineering defect in at most 6 existing theory_master/*.py files. '
            'Do not change supervisor/validation/execution-policy/budget authority, existing tests, scientific contracts, '
            'data or credentials. No network, model tests, Git or Worker controls. '
            'Create reproduction/test_incident.py with exactly one realistic offline pytest reproducer of the original '
            'exception, and repair_plan.json containing regression_tests (existing test filenames) and reason. '
            'Do not fake errors or assert version strings. SDK freezes the baseline and uses a separate read-only '
            'reviewer plus actual baseline/candidate execution before any installation. '
            'If blocked, explain the exact missing dependency; never fabricate acceptance.\n'
            + json.dumps({'change_id': change_id, 'failure': state.get('recovery'), 'attention': state.get('attention'),
                          'handoff': review.get('repair_suggestion'), 'prior_candidate_evidence': previous}, ensure_ascii=False))
        ok, detail = _invoke_codex(candidate, model, prompt)
        if not ok:
            return _hold('Isolated engineering turn failed: ' + detail)
        signature = None
        try:
            from .supervisor_recovery import incident_key
            proposed = candidate_snapshot(candidate)
            selected = json.loads((candidate / 'repair_plan.json').read_text()).get('regression_tests')
            signature = _digest([incident_key(state), baseline_manifest, selected,
                                 {k: v for k, v in proposed.items() if k != 'repair_plan.json'}])
            repeated = any(row.get('result', {}).get('candidate_signature') == signature
                           and row.get('result', {}).get('phase') == 'blocked' for row in previous)
            accepted = (_hold('Unchanged rejected candidate; select a different plan or dependency') if repeated else
                        accept_candidate(baseline, candidate, state, backend=backend, model=model, provider=provider))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            accepted = _hold('Candidate validation failed: ' + str(exc)[:1000])
        accepted['candidate_signature'] = signature
        evidence = retain_evidence(cycle, baseline, candidate, accepted)
        accepted['evidence_path'] = str(evidence)
        if accepted['phase'] != 'repair_validated':
            return accepted
        current = json.loads((cycle / 'research_state.json').read_text())
        if failure_binding(cycle, current) != original_binding:
            return _hold('Cycle changed before deployment; candidate retained', evidence_path=str(evidence))
        if source_manifest(engine) != baseline_manifest:
            return _hold('Installed source changed before deployment; candidate retained as rejected evidence')
        if not promote_candidate(engine, baseline, candidate, accepted):
            return _hold('Candidate installation CAS failed; no resume authorized')
        expected = {**baseline_manifest, **accepted['after']}
        if source_manifest(engine) != expected:
            return _hold('Installed source differs from validated candidate; resume denied', evidence_path=str(evidence))
        extra_inputs = {str((engine.parent / rel).resolve()): digest for rel, digest in accepted['regression_hashes'].items()}
        if any(_file_hash(Path(path)) != digest for path, digest in extra_inputs.items()):
            return _hold('Trusted regression inputs changed before receipt')
        tests = json.dumps({k: v for k, v in accepted.items() if k not in {'before', 'after'}}, ensure_ascii=False)
        if not _verify_auto_change(change_id, tests=tests, after=json.dumps(accepted['after'], sort_keys=True).encode()):
            return _hold('Installed candidate still awaits change-register verification')
        return _record_validation(cycle, state, engine, validator=VALIDATOR,
            module=accepted['sources'][0], change_id=change_id, tests=tests,
            manifest=source_manifest(engine), disposition='validated_isolated_candidate', extra_inputs=extra_inputs)


def retain_evidence(cycle: Path, baseline: Path, candidate: Path, result: dict) -> Path:
    """Keep rejected plans/tests/diffs for the next action and operator review."""
    from .runtime import write_json
    from .supervisor_recovery import read_job
    folder = cycle / 'supervisor' / 'candidates'
    if any(p.is_symlink() for p in (cycle, folder.parent, folder)):
        raise ValueError('Unsafe candidate evidence path')
    data = {'result': result, 'action_id': read_job(cycle).get('action_id'), 'files': {}, 'diff': ''}
    for rel in ('repair_plan.json', 'reproduction/test_incident.py'):
        path = candidate / rel
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 24000:
            data['files'][rel] = _SECRET.sub(r'\1=[redacted]', path.read_text())
    try:
        before, after = candidate_snapshot(baseline), candidate_snapshot(candidate)
        for rel in sorted(before.keys() & after.keys()):
            if before[rel] != after[rel] and Path(rel).suffix == '.py':
                data['diff'] += ''.join(difflib.unified_diff(
                    (baseline / rel).read_text().splitlines(True), (candidate / rel).read_text().splitlines(True),
                    fromfile='before/' + rel, tofile='after/' + rel))
        data['diff'] = _SECRET.sub(r'\1=[redacted]', data['diff'][:48000])
    except (OSError, ValueError, UnicodeError):
        data['snapshot_error'] = 'Unsafe or unreadable candidate inputs'
    path = folder / (_digest(data) + '.json')
    write_json(path, data)
    return path


def previous_evidence(cycle: Path) -> list[dict]:
    """Put prior rejected evidence inside the isolated editor's input boundary."""
    from .supervisor_recovery import read_job
    rows = []
    for event in (read_job(cycle).get('history') or [])[-3:]:
        name = (event.get('validation') or {}).get('evidence_path')
        if not name:
            continue
        path = Path(name)
        if path.parent != cycle / 'supervisor/candidates' or path.is_symlink():
            continue
        try:
            if path.stat().st_size > 160000:
                continue
            record = json.loads(path.read_text())
            rows.append({'result': record.get('result'), 'diff': str(record.get('diff') or '')[:8000],
                         'files': record.get('files')})
        except (OSError, ValueError):
            continue
    return rows
