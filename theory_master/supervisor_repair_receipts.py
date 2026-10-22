"""SDK-owned engineering evidence; a receipt is never a scientific verdict.

Validators are explicit capabilities. Adding a fault family requires reviewed
SDK code and offline tests, not a model-supplied command or a success sentence.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .runtime import now, write_json

SCHEMA = 'theory-master/supervisor-repair/v1'
CANVAS_VALIDATOR = 'canvas-offline/v1'
ATTRIBUTE_VALIDATOR = 'python-method-availability/v1'
CANDIDATE_VALIDATOR = 'differential-engine-repair/v1'
CONFIG_VALIDATOR = 'local-route-configuration/v1'
# Import-only, non-instantiating probes; expanding this map requires review.
_ATTRIBUTE_CLASSES = {'Workflow': 'workflow'}
RECEIPT_KEY = 'supervisor_repair_receipt'


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Missing or unsafe validation input: {path.name}')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def failure_binding(cycle: Path, state: dict) -> str:
    # Bind the original occurrence (including recovery.at), scientific identity,
    # and all persisted guards. Bookkeeping counters do not change the failure.
    protected = {key: value for key, value in state.items()
                 if not key.startswith('supervisor_')}
    checkpoint = state.get('last_safe_checkpoint')
    checkpoint_hash = None
    if checkpoint:
        path = Path(str(checkpoint))
        if path.parent != cycle / 'checkpoints' or path.parent.is_symlink():
            raise ValueError('Repair checkpoint is outside the original Cycle')
        checkpoint_hash = _file_hash(path)
        checkpoint_state = json.loads(path.read_text())
        if (not isinstance(checkpoint_state, dict)
                or (state.get('cycle_id') is not None
                    and str(checkpoint_state.get('cycle_id')) != str(state['cycle_id']))
                or checkpoint_state.get('hypothesis_lock') != state.get('hypothesis_lock')):
            raise ValueError('Repair checkpoint does not match the frozen Cycle identity')
    elif not state.get('role_outputs'):
        raise ValueError('Repair has no committed checkpoint or role boundary')
    return _digest([str(cycle.resolve()), protected, checkpoint_hash])


def source_manifest(engine: Path) -> dict[str, str]:
    from .supervisor_runtime_repair import _snapshot, _TESTS
    manifest = {'theory_master/' + key: value for key, value in _snapshot(engine).items()}
    for name in _TESTS:
        path = engine.parent / 'tests' / name
        manifest['tests/' + name] = _file_hash(path)
    return manifest


def record_canvas_validation(cycle: Path, state: dict, engine: Path, *,
                             module: str, change_id: str, tests: str,
                             manifest: dict[str, str], disposition: str) -> dict:
    """Called only after the SDK ran its immutable validator on this source."""
    from .supervisor_runtime_repair import _SAFE_MODULES
    if module not in _SAFE_MODULES or not change_id or not tests:
        raise ValueError('Invalid validated repair metadata')
    if manifest != source_manifest(engine):
        raise ValueError('Source changed during validation; receipt refused')
    return _record_validation(cycle, state, engine, validator=CANVAS_VALIDATOR,
                              module=module, change_id=change_id, tests=tests,
                              manifest=manifest, disposition=disposition)


def _record_validation(cycle: Path, state: dict, engine: Path, *, validator: str,
                       module: str, change_id: str, tests: str,
                       manifest: dict[str, str], disposition: str, extra_inputs: dict[str, str] | None = None) -> dict:
    from .runtime_manager import _git_source_identity
    current = json.loads((cycle / 'research_state.json').read_text())
    if failure_binding(cycle, current) != failure_binding(cycle, state):
        raise ValueError('Persisted failure changed during validation')
    repo, head = _git_source_identity(engine)
    receipt = {'schema': SCHEMA, 'validator': validator, 'at': now(),
               'failure_binding': failure_binding(cycle, state), 'cycle': str(cycle.resolve()),
               'failure': json.loads(json.dumps(state.get('recovery') or {})),
               'checkpoint': state.get('last_safe_checkpoint'),
               'hypothesis_id': (state.get('hypothesis_lock') or {}).get('id'),
               'worker_id': state.get('worker_id'),
               'original_source_provenance': state.get('runtime_source_provenance'),
               'module': module, 'change_id': change_id, 'tests': tests,
               'source_manifest': manifest, 'source_root': str(engine.resolve()),
               'git_root': repo, 'git_head': head,
               'git_finalization': 'not_applicable' if disposition == 'already_fixed' else 'pending_review',
               'disposition': disposition, 'phase': 'repair_validated',
               'runtime_verification': 'pending', 'resume_performed': False}
    if extra_inputs:
        receipt['extra_inputs'] = extra_inputs
    receipt['repair_identity'] = _digest([validator, manifest, extra_inputs or {}])
    digest = _digest(receipt)
    directory = cycle / 'supervisor' / 'repairs'
    if any(path.is_symlink() for path in (cycle, cycle / 'supervisor', directory)):
        raise ValueError('Unsafe repair receipt directory')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (digest + '.json')
    if path.is_symlink():
        raise ValueError('Unsafe existing repair receipt')
    if path.exists():
        if json.loads(path.read_text()) != receipt:
            raise ValueError('Existing repair receipt differs')
    else:
        write_json(path, receipt)
    state[RECEIPT_KEY] = {'path': str(path), 'sha256': digest,
                          'repair_identity': receipt['repair_identity']}
    return receipt


def inspect_repair_receipt(cycle: Path, state: dict, *, engine: Path | None = None) -> dict:
    """Recheck evidence against *current* source, failure and checkpoint."""
    engine = engine or Path(__file__).resolve().parent
    ref = state.get(RECEIPT_KEY) or {}
    if not isinstance(ref, dict):
        raise ValueError('Invalid SDK repair receipt reference')
    path = Path(str(ref.get('path') or ''))
    directory = cycle / 'supervisor' / 'repairs'
    if path.parent != directory or any(p.is_symlink() for p in (cycle, directory.parent, directory, path)):
        raise ValueError('No safe SDK repair receipt')
    receipt = json.loads(path.read_text())
    if (not isinstance(receipt, dict) or _digest(receipt) != ref.get('sha256')
            or receipt.get('schema') != SCHEMA
            or receipt.get('validator') not in {CANVAS_VALIDATOR, ATTRIBUTE_VALIDATOR, CANDIDATE_VALIDATOR, CONFIG_VALIDATOR}
            or receipt.get('phase') != 'repair_validated'
            or receipt.get('failure_binding') != failure_binding(cycle, state)
            or receipt.get('source_root') != str(engine.resolve())
            or receipt.get('source_manifest') != source_manifest(engine)):
        raise ValueError('Repair receipt is stale, unsupported or inconsistent')
    identity = receipt.get('repair_identity')
    if identity is not None and (identity != _digest([receipt['validator'], receipt['source_manifest'], receipt.get('extra_inputs') or {}]) or identity != ref.get('repair_identity')):
        raise ValueError('Repair identity mismatch')
    for name, expected in (receipt.get('extra_inputs') or {}).items():
        path = Path(name)
        if not path.is_absolute() or _file_hash(path) != expected:
            raise ValueError('Repair validation inputs changed')
    if receipt['validator'] in {CANDIDATE_VALIDATOR, CONFIG_VALIDATOR}:
        return receipt
    from .supervisor_runtime_repair import _SAFE_MODULES
    modules = (_SAFE_MODULES if receipt['validator'] == CANVAS_VALIDATOR else
               {name + '.py' for name in _ATTRIBUTE_CLASSES.values()})
    if receipt.get('module') not in modules:
        raise ValueError('Unsupported repair module')
    return receipt


def validate_existing_method(cycle: Path, state: dict, *, engine: Path,
                             log: str) -> dict | None:
    """Prove a formerly missing method is available in a fresh interpreter.

    This accepts only the exact ordinary AttributeError and an attributable
    engine frame. It never constructs a Workflow or calls a scientific method.
    Other AttributeErrors and protocol violations need their own validators.
    """
    recovery = state.get('recovery') or {}
    if str(state.get('status') or '').lower() != 'failed' or state.get('execution_active'):
        return None
    message = str(recovery.get('message') or '')
    match = re.fullmatch(r"'([A-Za-z_]\w*)' object has no attribute '([A-Za-z_]\w*)'", message)
    if recovery.get('kind') != 'AttributeError' or not match:
        return None
    cls, attribute = match.groups()
    module = _ATTRIBUTE_CLASSES.get(cls)
    if module is None or attribute.startswith('__'):
        return None
    from .runtime_supervisor_review import resume_blocker
    if resume_blocker(state, runtime_revalidated=True, for_repair=True) is not None:
        return None
    end = log.rfind(message)
    trace = log[max(0, end - 6500):end] if end >= 0 else ''
    start = trace.rfind('Traceback (most recent call last)')
    frames = re.findall(r'File "([^"\n]+\.py)", line \d+', trace[start:]) if start >= 0 else []
    target = engine / (module + '.py')
    if not any(Path(frame).absolute() == target.absolute() for frame in frames):
        return None
    manifest = source_manifest(engine)
    binding = failure_binding(cycle, state)
    script = (
        'import importlib, inspect, json, sys; '
        'sys.path.insert(0, sys.argv[1]); '
        'm = importlib.import_module("theory_master." + sys.argv[2]); '
        'c = getattr(m, sys.argv[3]); '
        'f = inspect.getattr_static(c, sys.argv[4], None); '
        'assert inspect.isfunction(f), "method still absent or not an ordinary method"; '
        'assert c.__getattribute__ is object.__getattribute__, "custom attribute dispatch"; '
        'print(json.dumps({"class": sys.argv[3], "method": sys.argv[4], '
        '"signature": str(inspect.signature(f)), "available": True}))'
    )
    with tempfile.TemporaryDirectory(prefix='tm-supervisor-method-') as td:
        copied = Path(td)
        shutil.copytree(engine, copied / 'theory_master',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        from .supervisor_runtime_repair import _snapshot
        if {'theory_master/' + k: v for k, v in _snapshot(copied / 'theory_master').items()} != {
                k: v for k, v in manifest.items() if k.startswith('theory_master/')}:
            raise ValueError('Source changed while copying method probe inputs')
        # Isolated mode ignores PYTHONPATH/site user customizations; importing
        # the copied SDK must not fall back to a previously loaded Workflow.
        proc = subprocess.run([sys.executable, '-I', '-c', script, str(copied), module, cls, attribute],
                              cwd=copied, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode:
        return None
    evidence = json.loads(proc.stdout)
    if (evidence.get('available') is not True or source_manifest(engine) != manifest
            or failure_binding(cycle, state) != binding):
        raise ValueError('Method validation evidence changed')
    return _record_validation(
        cycle, state, engine, validator=ATTRIBUTE_VALIDATOR, module=target.name,
        change_id='existing-source:' + _digest(manifest), tests=json.dumps(evidence),
        manifest=manifest, disposition='already_fixed')


def validate_engineering_changes(cycle: Path, state: dict, *, engine: Path,
                                 target: Path | None, before: dict[str, str]) -> dict:
    """Independent recheck of an observed patch, never attribution to the agent.

    Scope and regression inputs are frozen before the engineering turn. A
    no-change reply without an existing valid receipt cannot certify a repair.
    """
    from .supervisor_runtime_repair import (
        _SAFE_MODULES, _TESTS, _validate_candidate, _register_auto_change, _verify_auto_change,
    )
    if target is None or target.parent != engine or target.name not in _SAFE_MODULES:
        return {'phase': 'blocked', 'validator': None, 'reason': 'No SDK validator for this fault family'}
    after = source_manifest(engine)
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    relative = Path('theory_master') / target.name
    if changed != {str(relative)}:
        return {'phase': 'blocked', 'validator': CANVAS_VALIDATOR,
                'reason': 'No attributable single-module patch, or validator inputs changed'}
    binding = failure_binding(cycle, state)
    with tempfile.TemporaryDirectory(prefix='tm-supervisor-recheck-') as td:
        sandbox = Path(td)
        copied = sandbox / 'tool/theory_master'
        shutil.copytree(engine, copied / 'theory_master',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        (copied / 'tests').mkdir()
        for name in _TESTS:
            shutil.copy2(engine.parent / 'tests' / name, copied / 'tests' / name)
        if source_manifest(copied / 'theory_master') != after:
            raise ValueError('Source changed while copying validation inputs')
        ok, detail = _validate_candidate(sandbox, copied / relative)
        if source_manifest(copied / 'theory_master') != after:
            raise ValueError('Validation inputs changed while tests ran')
    if not ok:
        return {'phase': 'blocked', 'validator': CANVAS_VALIDATOR, 'reason': detail}
    if source_manifest(engine) != after or failure_binding(cycle, state) != binding:
        raise ValueError('Failure or source changed during independent validation')
    # This records SDK acceptance of the observed diff; it does not claim the
    # engineering model authored concurrent edits or already committed them.
    change_id = _register_auto_change(
        incident_id=binding[:20], cycle_dir=cycle, relative=relative,
        before=b'', before_sha256=before[str(relative)], after=target.read_bytes())
    if not change_id or not _verify_auto_change(change_id, tests=detail, after=target.read_bytes()):
        raise ValueError('Repair change registration/verification failed')
    return record_canvas_validation(cycle, state, engine, module=target.name,
                                    change_id=change_id, tests=detail, manifest=after,
                                    disposition='validated_observed_patch')


def repair_credit_key(state: dict) -> str:
    ref = state.get(RECEIPT_KEY) or {}
    return str(ref.get('repair_identity') or ref.get('sha256') or '') if isinstance(ref, dict) else ''


def unused_repair_credit(state: dict) -> bool:
    key = repair_credit_key(state)
    return bool(key and key not in (state.get('supervisor_consumed_repairs') or []))
