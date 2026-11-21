"""Objectively recheck a stopped report's registered document snapshot.

This capability replays presentation only. It never changes a report, Finding,
scientific verdict, frozen test, resource guard or completed role boundary.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ERROR_PREFIX = 'embedded report document is missing, ambiguous or outside task: '


def _capture(path: Path, inputs: dict[str, str]) -> bytes:
    if (not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents))
            or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024):
        raise ValueError('Unsafe report recovery input')
    with path.open('rb') as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('Report recovery input exceeds the byte limit')
    inputs[str(path)] = hashlib.sha256(raw).hexdigest()
    return raw


def _task_spec(root: Path):
    from .specification import TaskSpec
    from .supervisor_incident_session import _incident_chat_config_path
    config = _incident_chat_config_path(root)
    if config is None:
        raise ValueError('Report recovery requires a canonical Task binding')
    spec = TaskSpec.from_config(config)
    if spec.research_root.resolve() != root.resolve() or spec.branch_canvas is None:
        raise ValueError('Report recovery Task binding changed')
    return config, spec


def _build(cycle: Path, state: dict) -> tuple[dict, dict[str, str], list[str]]:
    from .evidence import EvidenceRegistry
    from .report_materials import report_materials
    config, spec = _task_spec(cycle.parent)
    inputs: dict[str, str] = {}
    for path in (config, spec.document, Path(state['last_safe_checkpoint'])):
        _capture(path, inputs)
    checkpoint = json.loads(Path(state['last_safe_checkpoint']).read_text())
    for role in ('reporter', 'judge'):
        if (not (state.get('role_outputs') or {}).get(role)
                or (checkpoint.get('role_outputs') or {}).get(role) != state['role_outputs'][role]):
            raise ValueError('Report recovery must retain the committed Reporter and Judge')
    home = (spec.task_home or spec.document.parent).resolve()
    vault = spec.branch_canvas.resolve().parent
    repo = None
    for start in (home, vault, cycle.parent):
        repo = next((p for p in (start, *start.parents) if (p / '.git').exists()), None)
        if repo is not None:
            break
    registry = EvidenceRegistry(cycle, cycle / 'workspace', task_local_root=vault,
        repo_root=repo, research_root=cycle.parent,
        legacy_research_root=spec.legacy_research_root, benchmark_root=spec.benchmark_root)
    missing = []
    for path in (registry.records_path, registry.registration_batches_path,
                 registry.lifecycle_path, registry.assessments_path, registry.criteria_path):
        if path.exists() or path.is_symlink():
            _capture(path, inputs)
        else:
            missing.append(str(path))
    # Derive the same admissibility/material view as reporting, without writing
    # the Evidence projections or modifying the authoritative ledgers.
    manifest = registry.rebuild(write=False)['manifest']
    for record in manifest['records']:
        path = (record.get('execution_materials') or {}).get('manifest_path')
        if path:
            _capture(Path(path), inputs)
    context = {'cycle_dir': str(cycle), 'cycle_workspace': str(cycle / 'workspace'),
               'task_home': str(home), 'research_root': str(cycle.parent),
               'notes_vault_root': str(vault), 'evidence_manifest': {'payload': manifest}}
    for name, path in (('repo_root', repo), ('benchmark_root', spec.benchmark_root),
                       ('legacy_research_root', spec.legacy_research_root)):
        if path is not None:
            context[name] = str(path)
    notes = spec.notes_mirror_dir or cycle / 'notes'
    return {'body': state['role_outputs']['reporter']['summary'], 'task_root': str(vault),
            'note_path': str(notes / 'outcomes' / (cycle.name + '.md')),
            'materials': report_materials(context)}, inputs, missing


def _probe(engine: Path, payload: dict) -> list[dict]:
    script = (
        'import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
        'from theory_master.research_report import snapshot_documents;'
        'p=json.load(open(sys.argv[2]));'
        'p["task_root"]=Path(p["task_root"]);p["note_path"]=Path(p["note_path"]);'
        'print(json.dumps(snapshot_documents(**p),ensure_ascii=False))'
    )
    with tempfile.TemporaryDirectory(prefix='tm-report-document-probe-') as td:
        path = Path(td) / 'inputs.json'
        path.write_text(json.dumps(payload, ensure_ascii=False))
        try:
            result = subprocess.run([sys.executable, '-I', '-c', script, str(engine.parent), str(path)],
                                    cwd=td, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('Fresh report document snapshot timed out') from exc
    if result.returncode:
        raise ValueError('Fresh report document snapshot still fails')
    return json.loads(result.stdout)


def _proof(cycle: Path, state: dict, engine: Path) -> tuple[dict, dict[str, str]]:
    from .research_report import DOCUMENT_EMBED, outside_code
    error = state['recovery']['message']
    ref = error[len(ERROR_PREFIX):]
    payload, inputs, missing = _build(cycle, state)
    embeds = []
    outside_code(payload['body'], lambda text: embeds.extend(DOCUMENT_EMBED.finditer(text)) or text)
    if ref not in [m['target'].partition('#')[0] for m in embeds]:
        raise ValueError('Failed document is not in the original Reporter output')
    documents = _probe(engine, payload)
    matched = False
    for document in documents:
        source = Path(document['source_ref'])
        if not source.is_absolute():
            source = Path(payload['task_root']) / source
        raw = _capture(source, inputs)
        if hashlib.sha256(raw).hexdigest() != document['sha256']:
            raise ValueError('Report document changed while being validated')
        if document['reference'].partition('#')[0] == ref:
            assets = [a for row in payload['materials'] for a in row['assets']
                      if a.get('kind') == 'document' and a.get('status') == 'available'
                      and a.get('path') == str(source)]
            if len({a['path'] for a in assets}) != 1 or not any(
                    a.get('relative_path') == Path(ref).as_posix()
                    and a.get('sha256') == document['sha256'] for a in assets):
                raise ValueError('Report recovery requires the exact admitted document alias and hash')
            matched = True
    if not matched:
        raise ValueError('The original missing document was not snapshotted')
    evidence = {'fresh_document_snapshot': 'passed', 'missing_inputs': missing,
                'documents': [{k: row[k] for k in ('reference', 'source_ref', 'section', 'sha256')}
                              for row in documents]}
    return evidence, inputs


def validate_report_document_reentry(cycle: Path, state: dict, *, engine: Path) -> dict | None:
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_repair_receipts import (
        REPORT_DOCUMENT_VALIDATOR, _digest, _record_validation, failure_binding, source_manifest,
    )
    recovery = state.get('recovery') or {}
    if (state.get('status') != 'failed' or state.get('execution_active')
            or state.get('protocol_health') != 'valid' or recovery.get('kind') != 'ValueError'
            or recovery.get('category') != 'runtime_validation'
            or not str(recovery.get('message') or '').startswith(ERROR_PREFIX)
            or resume_blocker(state, for_repair=True) is not None):
        return None
    binding, manifest = failure_binding(cycle, state), source_manifest(engine)
    evidence, inputs = _proof(cycle, state, engine)
    if failure_binding(cycle, state) != binding or source_manifest(engine) != manifest:
        raise ValueError('Report validation source or failure changed')
    return _record_validation(cycle, state, engine, validator=REPORT_DOCUMENT_VALIDATOR,
        module='research_report.py', change_id='existing-source:' + _digest(manifest),
        tests=json.dumps(evidence, sort_keys=True), manifest=manifest,
        disposition='already_fixed', extra_inputs=inputs)


def recheck_receipt_inputs(receipt: dict, state: dict) -> None:
    evidence = json.loads(receipt['tests'])
    for name in evidence['missing_inputs']:
        if Path(name).exists() or Path(name).is_symlink():
            raise ValueError('Report validation metadata changed')
    current, inputs = _proof(Path(receipt['cycle']), state, Path(receipt['source_root']))
    if current != evidence or inputs != receipt.get('extra_inputs'):
        raise ValueError('Report validation material or document identity changed')
