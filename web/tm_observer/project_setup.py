"""Preview-first project setup; original research directories remain untouched."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

from .research_chat import ResearchChatError, _draft_plan, _message, _plan

_SUFFIXES = {'.md', '.txt', '.tex', '.bib', '.pdf', '.csv', '.tsv', '.json', '.yaml', '.yml', '.toml', '.npy', '.npz', '.parquet', '.h5', '.hdf5', '.mat', '.py', '.r', '.jl', '.m', '.ipynb', '.png', '.jpg', '.svg'}
_SKIP = {'node_modules', 'venv', 'env', 'build', 'dist', '__pycache__'}
_SECRET = re.compile(r'(?:^|[-_.])(auth|credentials?|secrets?|passwords?|tokens?)(?:[-_.]|$)', re.I)
_MAX_FILE = 100 * 1024 * 1024
_MAX_TOTAL = 250 * 1024 * 1024


def source_inventory(raw: str) -> dict:
    source = Path(raw.strip()).expanduser()
    if not source.is_absolute() or source.is_symlink() or not source.is_dir():
        raise ResearchChatError('请输入服务所在电脑上已有项目的绝对目录路径（不能是符号链接）')
    source = source.resolve()
    files, visited, truncated = [], 0, False
    for base, dirs, names in os.walk(source, followlinks=False):
        depth = len(Path(base).relative_to(source).parts)
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in _SKIP and not _SECRET.search(d) and depth < 4 and not (Path(base) / d).is_symlink())
        for name in sorted(names):
            visited += 1
            if visited > 1500 or len(files) >= 200:
                truncated = True
                break
            path = Path(base) / name
            if name.startswith('.') or _SECRET.search(name) or path.is_symlink() or path.suffix.lower() not in _SUFFIXES:
                continue
            info = path.stat()
            if stat.S_ISREG(info.st_mode) and info.st_size <= _MAX_FILE:
                files.append({'path': str(path.relative_to(source)), 'size': info.st_size})
        if truncated:
            break
    return {'directory': str(source), 'files': files, 'truncated': truncated,
            'existing_plan': (source / 'research_plan.md').is_file()}


def _input_file(source: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts or any(p.startswith('.') for p in rel.parts):
        raise ResearchChatError('导入文件路径无效')
    path = source / rel
    if any((source / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1)):
        raise ResearchChatError('导入文件变成了符号链接，请重新选择', 409)
    if source not in path.resolve().parents or not path.is_file():
        raise ResearchChatError('导入文件已不存在，请重新选择', 409)
    return path


def _digest(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def new_setup(payload: dict) -> dict:
    name = str(payload.get('name') or '').strip()
    if not re.fullmatch(r'[\w][\w -]{0,59}', name, re.UNICODE):
        raise ResearchChatError('项目名须为 1–60 个字母、汉字、数字、空格、下划线或连字符')
    setup = {'name': name, 'slug': re.sub(r' +', '-', name), 'answers': {}, 'onboarding_version': 2,
             'use_model': payload.get('use_model') is True, 'source_files': []}
    raw = str(payload.get('source_directory') or '').strip()
    if raw:
        if payload.get('read_source') is not True:
            raise ResearchChatError('请先允许读取选中的原项目资料')
        inventory = source_inventory(raw)
        selected = payload.get('source_files')
        if not isinstance(selected, list) or not selected or len(selected) > 100 or len(set(map(str, selected))) != len(selected):
            raise ResearchChatError('请选择 1–100 个要导入的文件')
        allowed = {item['path']: item['size'] for item in inventory['files']}
        if any(not isinstance(item, str) or item not in allowed for item in selected):
            raise ResearchChatError('文件清单已变化，请重新检查目录')
        if sum(allowed[item] for item in selected) > _MAX_TOTAL:
            raise ResearchChatError('一次导入最多 250 MB，请缩小文件选择范围')
        setup.update(source_directory=inventory['directory'], source_files=selected)
    return setup


def _quote(text: str) -> str:
    return re.sub(r'(?m)^#', r'\#', text)


def handle_turn(row: dict, content: str, search_root: Path, state_dir: Path, backend, **callbacks) -> None:
    setup = row['setup']
    answers = setup['answers']
    fields = (('goal', 'contract', 'completion', 'constraints') if setup.get('onboarding_version') == 2
              else ('goal', 'completion', 'constraints'))
    if row.get('completed_action'):
        _message(row, 'assistant', '项目已建立，可在 Task List 查看。需要另建项目时，使用「建立科研项目」。')
        return
    if content.strip() == '/back':
        for field in reversed(fields):
            if field in answers:
                del answers[field]
                break
        row['pending_action'] = None
    else:
        field = next((key for key in fields if key not in answers), None)
        if field:
            answers[field] = content.strip()
        else:
            extra = setup.setdefault('clarifications', [])
            if len(extra) >= 8 or sum(map(len, extra)) + len(content.strip()) > 4000:
                raise ResearchChatError('补充说明已达 4000 字符上限，请直接编辑下方草稿')
            extra.append(content.strip())
    questions = {
        'goal': '这个项目最终想回答什么科学问题？请说明研究对象和你希望得到的结果。',
        'contract': '研究必须遵守哪些长期边界？例如可用的数据、研究方法、证据标准或不能改变的科学定义。暂不确定的部分可以说明。',
        'completion': '怎样才算完成目标？请给出可核验的指标、对照或交付物；还不确定也可以直接说明。',
        'constraints': '有哪些数据、计算资源、时间或方法限制？请说明已有结论中哪些仍需验证。没有额外限制可回复「暂无」。',
    }
    missing = next((key for key in fields if key not in answers), None)
    if missing:
        _message(row, 'assistant', questions[missing] + '\n\n可用 /back 返回上一步；对话会自动保存。')
        return
    source = Path(setup['source_directory']) if setup.get('source_directory') else None
    entries, excerpts, total = [], [], 0
    for relative in setup['source_files']:
        path = _input_file(source, relative)
        size = path.stat().st_size
        total += size
        if size > _MAX_FILE or total > _MAX_TOTAL:
            raise ResearchChatError('源文件已增大，请重新选择导入范围', 409)
        entries.append({'path': relative, 'size': size, 'sha256': _digest(path)})
        if path.suffix.lower() in {'.md', '.txt', '.tex', '.bib'} and len('\n'.join(excerpts)) < 16000:
            excerpts.append(f'FILE {relative}\n' + path.read_text(encoding='utf-8', errors='replace')[:1600])
    destination = search_root.resolve() / 'projects' / setup['slug']
    from theory_master.task import ensure_runtime_outside_package
    ensure_runtime_outside_package(destination)
    context = '\n\n'.join(excerpts)[:16000]
    goal = _quote(answers['goal'])
    draft = _plan(goal, destination, context)
    contract_note = ('用户定义的研究边界与证据要求：\n\n' + _quote(answers['contract'])
                     if answers.get('contract') else '')
    if contract_note:
        draft = draft.replace('# Research Contract\n\n', '# Research Contract\n\n' + contract_note + '\n\n')
    completion_note = '用户定义的完成标准：\n\n' + _quote(answers['completion'])
    draft = draft.replace('# Final Gate\n\n', '# Final Gate\n\n' + completion_note + '\n\n')
    manifest_text = '\n'.join('- benchmark/source/' + item['path'] for item in entries)
    required = ('\n\n用户提供的资源与限制：\n' + _quote(answers['constraints']) +
                '\n\n确认后导入的资料副本（原目录保持不变；benchmark 指系统配置 benchmark_root，读取该绝对路径下 source/）：\n' + (manifest_text or '无外部资料；研究数据尚待补充。'))
    if setup.get('clarifications'):
        required += '\n\n用户补充说明：\n' + _quote('\n'.join(setup['clarifications']))
    draft = draft.replace('\n# Scientific Model\n', required + '\n\n# Scientific Model\n')
    prior_note = ''
    if source and 'research_plan.md' in setup['source_files']:
        prior = _input_file(source, 'research_plan.md').read_text(encoding='utf-8', errors='replace')[:12000]
        parts = re.split(r'(?m)^# Scientific Model\s*$', prior, maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            initial = _quote(parts[1].strip())
            prior_note = '用户已有初态（保留为待验证模型，不代表已确立结论）：\n\n' + initial
            draft = draft.split('# Scientific Model\n', 1)[0] + '# Scientific Model\n\n' + prior_note + '\n'
    model_drafted = False
    if setup['use_model']:
        assisted, model_drafted = _draft_plan(goal, context + required + ('\n研究边界与证据要求：' + answers['contract'] if contract_note else '') + '\n完成标准：' + answers['completion'], state_dir, backend,
            model=str(row.get('model') or 'gpt-6-sol'), reasoning_effort=str(row.get('reasoning_effort') or 'medium'), **callbacks)
        if model_drafted:
            from theory_master.specification import scientific_sections
            if scientific_sections(assisted)[0].strip() == goal.strip():
                if contract_note and answers['contract'] not in assisted:
                    assisted = assisted.replace('# Research Contract\n', '# Research Contract\n\n' + contract_note + '\n', 1)
                if answers['completion'] not in assisted:
                    assisted = assisted.replace('# Final Gate\n', '# Final Gate\n\n' + completion_note + '\n', 1)
                if (answers['constraints'] not in assisted or any(item['path'] not in assisted for item in entries)):
                    assisted = assisted.replace('# Required Context\n', '# Required Context\n' + required + '\n', 1)
                if prior_note and initial not in assisted:
                    assisted = assisted.replace('# Scientific Model\n', '# Scientific Model\n\n' + prior_note + '\n', 1)
                if len(assisted) <= 24000:
                    draft = assisted
                else:
                    model_drafted = False
            else:
                model_drafted = False
    if len(draft) > 24000:
        raise ResearchChatError('Research Plan 草稿超过 24000 字符，请缩短目标、完成标准或资料摘录')
    if callbacks.get('cancel_event') is not None and callbacks['cancel_event'].is_set():
        from theory_master.codex_backend import CodexTurnCancelled
        raise CodexTurnCancelled('project plan generation stopped')
    row['pending_action'] = {'id': uuid.uuid4().hex, 'kind': 'setup', 'title': '确认建立科研项目',
        'summary': f'项目：{setup["name"]}；位置：{destination}；导入 {len(entries)} 个文件，共 {total} 字节。只建立任务，不启动研究。',
        'directory': str(destination), 'details': draft, 'manifest': entries,
        'source_directory': str(source) if source else None}
    _message(row, 'assistant', ('模型已完善草稿。' if model_drafted else '已整理基础 Research Plan 草稿。') +
        '请核对目标、完成标准、资料清单和初始模型。可以编辑后更新预览，或继续补充说明。最后点击「确认建立项目」才会写入项目。')


def confirm_setup(row: dict, action: dict, search_root: Path, state_dir: Path) -> dict:
    directory = Path(action['directory'])
    expected = search_root.resolve() / 'projects' / row['setup']['slug']
    if directory != expected or expected.parent.is_symlink() or directory.is_symlink():
        raise ResearchChatError('项目目标路径已变化', 409)
    from theory_master.task import ensure_runtime_outside_package
    ensure_runtime_outside_package(directory)
    lock = state_dir / 'research-chat' / 'project-create.lock'
    with lock.open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        receipt = directory / '.sciver-import.json'
        ownership = {'session_id': row['id'], 'action_id': action['id']}
        if directory.exists():
            if receipt.is_symlink() or not receipt.is_file() or json.loads(receipt.read_text()) != ownership:
                raise ResearchChatError('目标项目目录已存在，未覆盖任何内容。请更改项目名后重试。', 409)
        else:
            # Check source changes before creating any target files.
            for item in action['manifest']:
                path = _input_file(Path(action['source_directory']), item['path'])
                if path.stat().st_size != item['size'] or _digest(path) != item['sha256']:
                    raise ResearchChatError('源资料在预览后发生变化；请补充一条消息更新预览，再次确认。', 409)
            directory.mkdir(parents=True)
            receipt.write_text(json.dumps(ownership) + '\n')
        config = directory / 'theory-master.config.json'
        if not config.exists():
            for item in action['manifest']:
                src = _input_file(Path(action['source_directory']), item['path'])
                target = directory / 'benchmark/source' / item['path']
                if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent != directory and directory in parent.parents):
                    raise ResearchChatError('导入目标变成符号链接，未覆盖资料', 409)
                if target.exists():
                    if target.stat().st_size == item['size'] and _digest(target) == item['sha256']:
                        continue
                    raise ResearchChatError('目标资料已变化，未覆盖资料', 409)
                target.parent.mkdir(parents=True, exist_ok=True)
                with src.open('rb') as source_handle, target.open('xb') as dest_handle:
                    shutil.copyfileobj(source_handle, dest_handle)
                if target.stat().st_size != item['size'] or _digest(target) != item['sha256']:
                    raise ResearchChatError('复制期间源文件发生变化，未初始化任务；请恢复原资料后重试。', 409)
            plan = directory / 'research_plan.md'
            if not plan.exists():
                plan.write_text(action['details'], encoding='utf-8')
            elif plan.read_text(encoding='utf-8') != action['details']:
                raise ResearchChatError('待初始化计划被修改，不能覆盖', 409)
        else:
            from theory_master.task import TaskBinding
            try:
                TaskBinding.load(directory)
            except (OSError, ValueError, KeyError):
                pass
            else:
                config = None
        if config is not None:
            env = dict(os.environ)
            env['THEORY_MASTER_SEARCH_ROOT'] = str(search_root.resolve())
            proc = subprocess.run([sys.executable, '-c', 'from theory_master.task import bootstrap; from pathlib import Path; import sys; bootstrap(Path(sys.argv[1]))', str(directory / 'research_plan.md')],
                env=env, text=True, capture_output=True, timeout=120)
            if proc.returncode:
                raise ResearchChatError('项目资料已保存，但初始化未完成。对话保留，可再次确认重试；不会重复创建。', 409)
        from theory_master.task import TaskBinding
        binding = TaskBinding.load(directory)
        from theory_master.onboarding import install_benchmark_inputs
        install_benchmark_inputs(binding)
        observer_id = hashlib.sha256(str(binding.research_root).encode()).hexdigest()[:16]
        result = {'ok': True, 'task_id': observer_id, 'redirect': '/run/', 'task_url': f'/run/{observer_id}/'}
        row['completed_action'] = {'id': action['id'], 'result': result}
        row['task_id'] = observer_id
        row['pending_action'] = None
        _message(row, 'system', f'项目「{row["setup"]["name"]}」已建立，已加入 Task List。原项目资料保持不变。')
        return result
