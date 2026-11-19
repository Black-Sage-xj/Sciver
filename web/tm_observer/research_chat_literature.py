"""Bind Chat literature jobs to real sessions and the shared Literature service.

The Chat session may already be locked by its caller. This module uses its own
short index lock, per-job execution lease and preparation lock; it never locks Chat.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
from typing import Any
import uuid
from urllib.parse import quote, urlsplit

from . import research_chat, research_preparation as prep
from .collector import collect_state

_ID = re.compile(r'[a-f0-9]{32}\Z')
_SCHEMA = 'sciver/chat-literature-index/v1'
_MAX_JSON = 32 * 1024 * 1024


class ChatLiteratureError(ValueError):
    def __init__(self, message: str, status: int = 400, stop_reason: str = 'literature_request_rejected'):
        super().__init__(message)
        self.status = status
        self.stop_reason = stop_reason


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


_OWNER_LOCK = '.chat-literature-owner.lock'
_SERVICE_LOCK = '.literature-job.lock'


def _owner() -> dict:
    """Diagnostic identity only; kernel locks determine whether a job is live."""
    owner = {'lease_id': uuid.uuid4().hex, 'pid': os.getpid(), 'process_token': '', 'pid_namespace': ''}
    try:
        # /proc may belong to the host while getpid() uses an SDK PID namespace.
        # /proc/self identifies this process in that procfs view. Record its
        # namespace explicitly: the Web process may observe a different one.
        record = Path('/proc/self/stat').read_text()
        owner.update(pid=int(record.split('(', 1)[0]),
                     process_token=record.rsplit(')', 1)[1].split()[19],
                     pid_namespace=str(Path('/proc/self/ns/pid').readlink()))
    except (OSError, IndexError, ValueError):
        pass
    return owner


def _lock_held(path: Path) -> bool:
    """Read lock state without creating a file or interpreting namespace PIDs."""
    if path.is_symlink():
        raise ChatLiteratureError('文献运行锁不安全', 409, 'literature_lock_invalid')
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(descriptor)


def _owner_live(root: Path) -> bool:
    # The service lock also recognizes older jobs whose index stored only a PID.
    return _lock_held(root / _OWNER_LOCK) or _lock_held(root / _SERVICE_LOCK)


@contextmanager
def _owner_lease(root: Path):
    root = _directory(root, create=True)
    lock = root / _OWNER_LOCK
    if lock.is_symlink():
        raise ChatLiteratureError('文献运行锁不安全', 409, 'literature_lock_invalid')
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ChatLiteratureError('该文献调研已在执行，请先等待或取消原调用', 409, 'already_running') from exc
        # Cover registration and final cleanup as well as the service itself.
        # Keep the lock file: unlinking would let a second inode bypass exclusion.
        yield


def _directory(path: Path, *, create: bool = False) -> Path:
    path = Path(path).expanduser().absolute()
    if path != path.resolve() or path.is_symlink():
        raise ChatLiteratureError('文献工作区路径不安全', 403)
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ChatLiteratureError('文献工作区不存在', 404)
    return path


def _json(path: Path) -> dict:
    if path != path.resolve() or path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_JSON:
        raise ChatLiteratureError('文献记录不存在或不安全', 409, 'literature_record_invalid')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ChatLiteratureError('文献记录无法读取', 409, 'literature_record_invalid') from exc
    if not isinstance(value, dict):
        raise ChatLiteratureError('文献记录格式无效', 409, 'literature_record_invalid')
    return value


def _session(state_dir: Path, ident: str) -> tuple[Path, dict]:
    if not isinstance(ident, str) or not _ID.fullmatch(ident):
        raise ChatLiteratureError('会话不存在', 404)
    state = _directory(state_dir)
    row = _json(state / 'research-chat' / (ident + '.json'))
    if row.get('id') != ident or row.get('mode') not in {'preparation', 'project', 'frontier'}:
        raise ChatLiteratureError('该会话不支持文献调研', 409)
    if row['mode'] == 'preparation' and row.get('task_id'):
        raise ChatLiteratureError('筹备会话不能同时绑定已有任务', 409)
    return state, row


def _index_path(state: Path, ident: str, *, create: bool = False) -> Path:
    return _directory(state / 'research-chat-literature', create=create) / (ident + '.json')


def _index(path: Path, ident: str) -> dict:
    if not path.exists() and not path.is_symlink():
        return {'schema': _SCHEMA, 'session_id': ident, 'jobs': []}
    value = _json(path)
    if (value.get('schema') != _SCHEMA or value.get('session_id') != ident
            or not isinstance(value.get('jobs'), list) or len(value['jobs']) > 1000):
        raise ChatLiteratureError('会话文献索引无效', 409)
    return value


@contextmanager
def _locked_index(path: Path):
    lock = path.with_suffix('.lock')
    if lock.is_symlink():
        raise ChatLiteratureError('文献索引锁不安全', 403)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def _save(path: Path, value: dict) -> None:
    if path.is_symlink() or path.parent != path.parent.resolve():
        raise ChatLiteratureError('文献索引保存路径不安全', 403)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _binding(state: Path, row: dict, search_root: Path | None = None, recorded: dict | None = None) -> dict:
    ident = row['id']
    if row['mode'] == 'preparation':
        root = prep.workspace(state, ident)
        prep._metadata(root, ident)
        return {'mode': 'preparation', 'task_id': None, 'workspace_root': str(root / 'literature' / 'studies')}
    if search_root is not None:
        current = collect_state(Path(search_root), include_git_info=False)
        task = next((task for task in current.get('runs', []) if task.get('observer_id') == row.get('task_id')), None)
    else:
        task = (recorded or {}).get('task')
    if not isinstance(task, dict) or task.get('observer_id') != row.get('task_id'):
        raise ChatLiteratureError('会话绑定的科研任务不存在', 404, 'binding_mismatch')
    research_chat._chat_task_binding(task)
    research_root = _directory(Path(str(task.get('research_root') or '')))
    if hashlib.sha256(str(research_root).encode()).hexdigest()[:16] != row.get('task_id'):
        raise ChatLiteratureError('文献任务与当前科研目录绑定不一致', 409, 'binding_mismatch')
    clean_task = {key: task[key] for key in ('observer_id', 'task_root', 'research_root')}
    return {'mode': row['mode'], 'task_id': row['task_id'], 'task': clean_task,
            'workspace_root': str(research_root / 'chat-literature' / ident)}


def _entry_binding(state: Path, row: dict, index: dict, entry: dict,
                   search_root: Path | None = None) -> dict:
    recorded = entry.get('binding', index.get('binding'))
    if not isinstance(recorded, dict):
        raise ChatLiteratureError('文献索引与当前会话任务绑定不一致', 409, 'binding_mismatch')
    if recorded.get('mode') == 'preparation' and row['mode'] != 'preparation':
        # A promoted conversation retains its own frozen preparation reports.
        # Verify both ends of the creation receipt before reading the old root.
        if row.get('preparation_id') != row['id']:
            raise ChatLiteratureError('文献索引与当前会话任务绑定不一致', 409, 'binding_mismatch')
        metadata = prep._metadata(prep.workspace(state, row['id']), row['id'])
        created = metadata.get('created_task')
        if not isinstance(created, dict) or created.get('observer_id') != row.get('task_id'):
            raise ChatLiteratureError('文献索引与当前会话任务绑定不一致', 409, 'binding_mismatch')
        _binding(state, row, search_root, recorded={'task': created})
        current = _binding(state, {'id': row['id'], 'mode': 'preparation'})
    else:
        current = _binding(state, row, search_root, recorded=recorded)
    if recorded != current:
        raise ChatLiteratureError('文献索引与当前会话任务绑定不一致', 409, 'binding_mismatch')
    return current


_BUDGET_FIELDS = {'max_rounds', 'max_sources'}


def _within_authorized_budget(actual: Any, authorized: dict) -> bool:
    if not isinstance(actual, dict):
        return False
    return ({key: value for key, value in actual.items() if key not in _BUDGET_FIELDS}
            == {key: value for key, value in authorized.items() if key not in _BUDGET_FIELDS}
            and all(type(actual.get(key)) is int and type(authorized.get(key)) is int
                    and 1 <= actual[key] <= authorized[key] for key in _BUDGET_FIELDS))


def _request(payload: dict, binding: dict) -> tuple[str, dict]:
    from theory_master.literature_service import LiteratureServiceError, _request as normalize
    if not isinstance(payload, dict) or set(payload) - {'query', 'scope', 'job_id', 'max_rounds', 'max_sources', 'retrieval_manifests'}:
        raise ChatLiteratureError('文献调研参数无效')
    ident = payload.get('job_id', '')
    if not isinstance(ident, str) or (ident and not _ID.fullmatch(ident)):
        raise ChatLiteratureError('文献调研标识无效')
    ident = ident or uuid.uuid4().hex
    try:
        request = normalize({key: value for key, value in payload.items() if key != 'job_id'})
    except LiteratureServiceError as exc:
        reason = 'invalid_budget' if str(exc).startswith(('max_rounds ', 'max_sources ')) else 'invalid_request'
        raise ChatLiteratureError(str(exc), 400, reason) from exc
    manifests = request['retrieval_manifests']
    if manifests:
        if binding['mode'] != 'preparation':
            raise ChatLiteratureError('只能复用当前筹备空间的已登记检索资料')
        literature = Path(binding['workspace_root']).parent
        for value in manifests:
            path = Path(value)
            if (not path.is_absolute() or path != path.resolve() or path.is_symlink()
                    or path.name != 'manifest.json' or path.parent.parent != literature
                    or not _ID.fullmatch(path.parent.name)):
                raise ChatLiteratureError('复用资料必须属于当前筹备空间的已登记检索', 403)
            record = _json(path)
            if record.get('schema') != 'sciver/preparation-literature/v1' or record.get('preparation_id') != literature.parent.name:
                raise ChatLiteratureError('复用检索资料身份不一致', 403)
    return ident, request


def _job_root(binding: dict, ident: str) -> Path:
    if not isinstance(ident, str) or not _ID.fullmatch(ident):
        raise ChatLiteratureError('文献调研标识无效', 404)
    root = Path(binding['workspace_root']) / ident
    if root != root.resolve() or root.is_symlink():
        raise ChatLiteratureError('文献调研路径不安全', 403)
    return root


def _report(root: Path) -> str:
    path = root / 'report.md'
    if path != path.resolve() or path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_JSON:
        return ''
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return ''


def _public_sources(manifest: dict) -> list[dict]:
    from theory_master.scholarly_selection import _identity_helpers
    identity = _identity_helpers()
    reviewed = manifest.get('source_reviews') or {}
    reviewed = reviewed if isinstance(reviewed, dict) else {}
    def doi_ref(value):
        doi = identity.doi_key(value)
        return 'doi:' + doi if doi else ''
    def stable_ref(value):
        doi = doi_ref(value)
        arxiv = identity.arxiv_key(value) if not doi else ''
        return doi or ('arxiv:' + arxiv if arxiv else '')
    aliases = manifest.get('source_aliases') or {}
    aliases = {stable_ref(key) or key: stable_ref(value) or value for key, value in aliases.items()
               if isinstance(key, str) and isinstance(value, str)} if isinstance(aliases, dict) else {}
    def canonical(value):
        value = stable_ref(value) or str(value or '')
        seen = set()
        while value in aliases and value not in seen:
            seen.add(value)
            value = aliases[value]
        return value
    def refs(source):
        row_aliases = source.get('source_aliases') or []
        row_aliases = row_aliases if isinstance(row_aliases, list) else []
        values = [str(source.get('source_id') or ''), doi_ref(source.get('doi')),
                  stable_ref(source.get('arxiv_id')), stable_ref(source.get('url')), *row_aliases]
        return {canonical(value) for value in values if isinstance(value, str) and value}
    reviewed_order = {}
    for index, (key, delivery) in enumerate(reviewed.items()):
        reviewed_order.setdefault(canonical(key), index)
        if isinstance(delivery, dict):
            for record in delivery.get('evidence_records') or []:
                if isinstance(record, dict) and (reference := stable_ref(record.get('source_ref'))):
                    reviewed_order.setdefault(canonical(reference), index)
    selected_order = {}
    for index, choice in enumerate(manifest.get('selected_sources') or []):
        if not isinstance(choice, dict):
            continue
        paper = choice.get('paper') if isinstance(choice.get('paper'), dict) else {}
        for value in refs({**paper, 'source_id': choice.get('source_id') or paper.get('source_id')}):
            selected_order.setdefault(value, index)
    output = manifest.get('output') or {}
    landscape = output.get('landscape') if isinstance(output, dict) else {}
    landscape = landscape if isinstance(landscape, dict) else {}
    representatives = list(landscape.get('representative_papers') or [])
    for route in landscape.get('major_routes') or []:
        if isinstance(route, dict):
            representatives.extend(route.get('representative_papers') or [])
    representative_order = {}
    for index, paper in enumerate(representatives):
        if isinstance(paper, dict) and (key := doi_ref(paper.get('source_ref') or paper.get('doi'))):
            representative_order.setdefault(canonical(key), index)
    result = []
    for source in manifest.get('sources') or []:
        if not isinstance(source, dict):
            continue
        doi = str(source.get('doi') or '')[:500]
        url = str(source.get('url') or '')[:4096]
        try:
            parsed = urlsplit(url)
            safe = (parsed.scheme in {'http', 'https'} and parsed.hostname
                    and not parsed.username and not parsed.password
                    and not re.search(r'[\x00-\x20\x7f\\]', url))
        except ValueError:
            safe = False
        if not safe:
            url = 'https://doi.org/' + quote(doi, safe='/') if re.fullmatch(r'10\.\d{4,9}/[^\s]+', doi) else ''
        identifiers = refs(source)
        category, order = 'retrieval_candidate', len(result)
        for name, priorities in (('reviewed', reviewed_order), ('selected', selected_order),
                                 ('representative', representative_order)):
            matches = [priorities[key] for key in identifiers if key in priorities]
            if matches:
                category, order = name, min(matches)
                break
        result.append({'title': str(source.get('title') or '未命名文献')[:2000], 'doi': doi,
                       'url': url, 'reading_level': source.get('reading_level') if source.get('reading_level')
                       in {'metadata', 'abstract', 'full_text'} else 'metadata',
                       'agent_reviewed': category == 'reviewed', 'selection_status': category,
                       '_order': order})
    priority = {'reviewed': 0, 'selected': 1, 'representative': 2, 'retrieval_candidate': 3}
    result.sort(key=lambda source: (priority[source['selection_status']], source['_order']))
    for source in result:
        source.pop('_order')
    return result[:200]


def _public_activity(manifest: dict, job_status: str) -> list[dict]:
    result = []
    if manifest.get('workflow_version') == 2:
        result.append({'label': '规划互补检索方向', 'status': 'completed' if manifest.get('search_plan') else
                       'running' if job_status == 'running' else 'interrupted', 'started_at': '', 'completed_at': ''})
    for item in (manifest.get('invocations') or [])[-100:]:
        if not isinstance(item, dict):
            continue
        key = str(item.get('key') or '')
        label = ('综合与比较' if key.endswith('-synthesis') else
                 '核对文献材料' if item.get('stage') == 'targeted_source' else '梳理领域与选择文献')
        status = item.get('status') if item.get('status') in {'running', 'completed', 'failed'} else 'pending'
        if status == 'running' and job_status != 'running':
            status = 'interrupted'
        result.append({'label': label, 'status': status,
                       'started_at': str(item.get('started_at') or '')[:80],
                       'completed_at': str(item.get('completed_at') or '')[:80]})
    return result


def _public_landscape_routes(manifest: dict) -> list[dict]:
    """Expose the saved scientific map without implying original-text verification."""
    from theory_master.scholarly_selection import _identity_helpers
    identity = _identity_helpers()
    output = manifest.get('output')
    landscape = output.get('landscape') if isinstance(output, dict) else {}
    routes = landscape.get('major_routes') if isinstance(landscape, dict) else []
    if not isinstance(routes, list):
        return []
    known = {identity.doi_key(source.get('doi')): source for source in manifest.get('sources') or []
             if isinstance(source, dict) and identity.doi_key(source.get('doi'))}
    result = []
    for route in routes[:6]:
        if not isinstance(route, dict):
            continue
        name = route.get('name') if isinstance(route.get('name'), str) else ''
        focus = route.get('focus') if isinstance(route.get('focus'), str) else ''
        if not (name.strip() or focus.strip()):
            continue
        citations, seen = [], set()
        representatives = route.get('representative_papers') or []
        for paper in representatives if isinstance(representatives, list) else []:
            doi = identity.doi_key(paper.get('source_ref') or paper.get('doi')) if isinstance(paper, dict) else ''
            if doi in known and doi not in seen:
                seen.add(doi)
                citations.append({'doi': doi, 'title': str(known[doi].get('title') or doi)[:160],
                                  'url': 'https://doi.org/' + quote(doi, safe='/')})
            if len(citations) >= 3:
                break
        result.append({'name': name.strip()[:160], 'focus': focus.strip()[:1200], 'citations': citations})
    return result


def _projection(entry: dict, binding: dict, session_id: str, *, include_output: bool = False,
                include_details: bool = False) -> dict:
    root = _job_root(binding, entry.get('job_id'))
    manifest = _json(root / 'manifest.json') if (root / 'manifest.json').exists() else {}
    if manifest and manifest.get('schema') != 'sciver/literature-job/v1':
        raise ChatLiteratureError('文献调研执行记录无效', 409)
    if manifest and (manifest.get('job_id') != entry['job_id']
                     or not _within_authorized_budget(manifest.get('request'), entry['request'])):
        raise ChatLiteratureError('文献执行记录与已登记问题不一致', 409)
    report = _report(root)
    turns = sum(isinstance(item, dict) and item.get('status') == 'completed' for item in manifest.get('invocations', []))
    invoked = manifest.get('literature_agent_invoked') is True and manifest.get('model_invoked') is True
    status = str(manifest.get('status') or 'registered')
    integrity_error = ''
    delivery_verification = ''
    if status == 'completed' or manifest.get('delivery_integrity') is not None:
        from theory_master.literature_delivery_integrity import LiteratureDeliveryIntegrityError, verify_completed_delivery
        try:
            delivery_verification = verify_completed_delivery(root, manifest)['status']
        except LiteratureDeliveryIntegrityError:
            integrity_error = 'Literature 交付资料缺损、已变化或绑定不一致；请检查原始交付记录。'
            status = 'incomplete'
    if status == 'running' and not _owner_live(root):
        status = 'interrupted'
    if status == 'completed' and not (invoked and turns > 0 and report.strip()):
        status = 'incomplete'
    sources = manifest.get('sources') or []
    output = manifest.get('output') or {}
    ident = entry['job_id']
    result = {'job_id': ident, 'service_job_id': manifest.get('job_id'), 'status': status,
              'query': entry['request']['query'], 'scope': str((manifest.get('search_plan') or {}).get('scope')
                  or entry['request'].get('scope', ''))[:2000],
              'created_at': entry['created_at'], 'updated_at': manifest.get('updated_at') or entry['created_at'],
              'thread_id': manifest.get('thread_id'), 'literature_agent_invoked': invoked,
              'model_invoked': manifest.get('model_invoked') is True, 'agent_turns': turns,
              'source_count': len(sources), 'source_access_counts': {
                  level: sum(isinstance(source, dict) and source.get('reading_level') == level for source in sources)
                  for level in ('metadata', 'abstract', 'full_text')},
              'round': manifest.get('round', 0), 'stop_reason': str(manifest.get('stop_reason') or ''),
              'remaining_scientific_question': str(output.get('remaining_scientific_question') or '')[:6000],
              'report_available': bool(report.strip()),
              'report_href': f'/api/research-chat/{session_id}/literature/{ident}/report',
              'scientific_evidence_created': False}
    if delivery_verification:
        result['delivery_verification'] = delivery_verification
    if integrity_error:
        result.update(stop_reason='delivery_integrity_failed', error=integrity_error,
                      report_available=False)
    if include_details:
        result.update(sources=_public_sources(manifest), activity=_public_activity(manifest, status),
            access_revalidated=any(isinstance(item, dict) and item.get('access_validation_version') == 2
                for item in manifest.get('invocations') or []),
            landscape_routes=_public_landscape_routes(manifest),
            search_queries=[{'query': str(item.get('query') or '')[:180], 'purpose': str(item.get('purpose') or '')[:500]}
                for item in (manifest.get('search_plan') or {}).get('queries', [])[:6] if isinstance(item, dict)])
    if include_output:
        result.update(manifest=str(root / 'manifest.json'), report=str(root / 'report.md'), output=output)
    return result


def run_study(state_dir: Path, search_root: Path, ident: str, payload: dict, *, backend=None, retriever=None,
              cancel_event=None, on_event=None) -> dict:
    """Run/resume one Literature job without mutating Plan, graph or a Cycle."""
    from theory_master.literature_service import run_literature
    state, row = _session(state_dir, ident)
    binding = _binding(state, row, search_root)
    job_id, request = _request(payload, binding)
    root = _job_root(binding, job_id)
    guard = prep._material_locked(prep.workspace(state, ident)) if row['mode'] == 'preparation' else nullcontext()
    # Corpus acquisition never holds the Plan lock. Creation rejects a busy
    # corpus instead of freezing a partial material set; no Chat lock is used.
    with guard, ExitStack() as execution:
        if row['mode'] == 'preparation':
            metadata = prep._metadata(prep.workspace(state, ident), ident)
            if metadata.get('creation') is not None or metadata.get('created_task') is not None:
                raise ChatLiteratureError('该筹备已确认立项，不能追加未纳入审阅的文献调研', 409, 'preparation_frozen')
        execution.enter_context(_owner_lease(root))
        path = _index_path(state, ident, create=True)
        with _locked_index(path):
            index = _index(path, ident)
            entry = next((item for item in index['jobs'] if item.get('job_id') == job_id), None)
            if entry:
                if _entry_binding(state, row, index, entry, search_root) != binding:
                    raise ChatLiteratureError('筹备文献已冻结，请在正式项目中创建新的文献调研', 409, 'preparation_frozen')
                # Our lease excludes new adapters. The existing service lock
                # excludes an older worker without consulting its PID receipt.
                if _lock_held(root / _SERVICE_LOCK):
                    raise ChatLiteratureError('该文献调研已在执行，请先等待或取消原调用', 409, 'already_running')
                previous = entry['request']
                for key in _BUDGET_FIELDS:
                    # Omitted defaults never enlarge a previously bounded job;
                    # lower values retain the service's existing monotonic cap.
                    request[key] = max(previous[key], request[key]) if key in payload else previous[key]
                if not _within_authorized_budget(previous, request):
                    raise ChatLiteratureError('同一文献调研不能更换问题或范围', 409, 'request_mismatch')
                manifest_path = _job_root(binding, job_id) / 'manifest.json'
                manifest = _json(manifest_path) if manifest_path.exists() else {}
                if manifest.get('status') == 'completed' or manifest.get('delivery_integrity') is not None:
                    # Completed delivery seals include the original request.
                    # Returning that delivery requires no further resources.
                    request = dict(manifest['request'])
                    if not _within_authorized_budget(request, previous):
                        raise ChatLiteratureError('文献执行记录与已登记问题不一致', 409)
                entry['request'] = request
            else:
                if len(index['jobs']) >= 1000:
                    raise ChatLiteratureError('该会话文献任务已达到上限，请使用新会话', 413)
                entry = {'job_id': job_id, 'request': request, 'created_at': _now(),
                         'model': row.get('model') or 'gpt-6-sol',
                         'reasoning_effort': row.get('reasoning_effort') or 'medium'}
                index['jobs'].append(entry)
                index.setdefault('binding', binding)
            entry['binding'] = binding
            entry['active_owner'] = _owner()
            _save(path, index)
        try:
            run_literature(root, request, backend=backend, retriever=retriever,
                           model=entry['model'], reasoning_effort=entry['reasoning_effort'],
                           cancel_event=cancel_event, on_event=on_event)
        except ValueError as exc:
            raise ChatLiteratureError('文献调研尚未完成：请求、运行锁或资料状态需要检查', 409) from exc
        finally:
            with _locked_index(path):
                latest = _index(path, ident)
                saved = next((item for item in latest['jobs'] if item.get('job_id') == job_id), None)
                if saved and saved.get('active_owner') == entry['active_owner']:
                    saved['active_owner'] = None
                    saved['finished_at'] = _now()
                    _save(path, latest)
        return {'ok': True, 'session_id': ident, **_projection(entry, binding, ident, include_output=True)}


def list_studies(state_dir: Path, ident: str) -> dict:
    state, row = _session(state_dir, ident)
    home = state / 'research-chat-literature'
    if not home.exists() and not home.is_symlink():
        return {'ok': True, 'session_id': ident, 'jobs': []}
    path = _index_path(state, ident)
    index = _index(path, ident)
    if not index['jobs']:
        return {'ok': True, 'session_id': ident, 'jobs': []}
    return {'ok': True, 'session_id': ident, 'jobs': [
        _projection(entry, _entry_binding(state, row, index, entry), ident)
        for entry in reversed(index['jobs'])]}


def read_study(state_dir: Path, ident: str, job_id: str) -> dict:
    """Return bounded public progress, without local artifacts or raw Agent IO."""
    state, row = _session(state_dir, ident)
    if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
        raise ChatLiteratureError('文献调研不存在', 404)
    home = state / 'research-chat-literature'
    if not home.exists() and not home.is_symlink():
        raise ChatLiteratureError('文献调研不存在', 404)
    index = _index(_index_path(state, ident), ident)
    entry = next((item for item in index['jobs'] if item.get('job_id') == job_id), None)
    if entry is None:
        raise ChatLiteratureError('文献调研不存在', 404)
    binding = _entry_binding(state, row, index, entry)
    return {'ok': True, 'session_id': ident, 'job': _projection(entry, binding, ident, include_details=True)}


def read_report(state_dir: Path, ident: str, job_id: str) -> str:
    listing = list_studies(state_dir, ident)
    projected = next((item for item in listing['jobs'] if item['job_id'] == job_id), None)
    if projected is None:
        raise ChatLiteratureError('文献报告不存在', 404)
    if projected.get('stop_reason') == 'delivery_integrity_failed':
        raise ChatLiteratureError('Literature 交付资料缺损或已变化，不能读取为完整报告', 409)
    state, row = _session(state_dir, ident)
    index = _index(_index_path(state, ident), ident)
    entry = next(item for item in index['jobs'] if item.get('job_id') == job_id)
    binding = _entry_binding(state, row, index, entry)
    root = _job_root(binding, job_id)
    text = _report(root)
    if not text.strip():
        raise ChatLiteratureError('Literature 尚未生成可读报告', 404)
    from theory_master.literature_service import _expand_report_references
    manifest = _json(root / 'manifest.json') if (root / 'manifest.json').exists() else {}
    return _expand_report_references(text, manifest.get('sources') or [])


def _cli_progress():
    """Expose real stage changes while keeping the final stdout receipt parseable."""
    labels = {
        'literature.job_started': '文献作业已开始',
        'literature.planning_started': '正在规划检索方向',
        'literature.planning_completed': '检索方向已形成',
        'literature.retrieval_started': '正在检索与获取文献',
        'literature.retrieval_completed': '本项文献获取已返回',
        'literature.retrieval_reused': '已复用现有文献材料',
        'literature.agent_started': 'Literature 正在阅读与综合',
        'literature.agent_completed': 'Literature 已完成本项分析',
        'literature.job_finished': '文献作业已返回，状态见最终回执',
    }
    seen = set()

    def progress(event):
        kind = event.get('event')
        label = labels.get(kind)
        step = (kind, event.get('invocation_key') or event.get('key') or '')
        if label is None or step in seen:
            return
        seen.add(step)
        print(f'Literature · {label}', file=sys.stderr, flush=True)

    return progress


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='调用共享 Literature Agent，为当前科研对话调研文献')
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--search-root', type=Path, required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--query', required=True)
    parser.add_argument('--scope', default='')
    parser.add_argument('--job-id', default='')
    parser.add_argument('--max-rounds', type=int)
    parser.add_argument('--max-sources', type=int)
    parser.add_argument('--retrieval-manifest', action='append', default=[])
    args = parser.parse_args(argv)
    cancel = threading.Event()
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, lambda _signum, _frame: cancel.set())
    try:
        payload = {'query': args.query, 'scope': args.scope, 'job_id': args.job_id,
                   'retrieval_manifests': args.retrieval_manifest}
        if args.max_rounds is not None:
            payload['max_rounds'] = args.max_rounds
        if args.max_sources is not None:
            payload['max_sources'] = args.max_sources
        result = run_study(args.state_dir, args.search_root, args.session,
                           payload, cancel_event=cancel, on_event=_cli_progress())
    except ChatLiteratureError as exc:
        print(json.dumps({'ok': False, 'status': 'blocked', 'error': str(exc),
                          'http_status': exc.status, 'stop_reason': exc.stop_reason}, ensure_ascii=False))
        return 1
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({'ok': False, 'status': 'blocked', 'error': '文献调研未完成，请检查会话绑定、资料与执行记录。'}, ensure_ascii=False))
        return 1
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in {'completed', 'paused'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
