"""Logged Investigator Native Goal reader and explicit, non-executing human drafts.

Editing a draft must NEVER update the running Codex goal, the scientific
Research Plan, frozen Hypothesis, SDK invocation state, or any future Attempt.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .stage_conversation import _root, _read_jsonl

MAX_GOAL_CHARS = 4000


class GoalDraftError(ValueError):
    def __init__(self, message: str, status: int = 400, *, code: str = "goal_draft_error"):
        super().__init__(message)
        self.status = status
        self.code = code


def _recorded_goal(run: dict[str, Any]) -> dict[str, Any]:
    """Read the most recent logged Investigator instruction without inference.

    Older event schemas may identify the role as ``agent`` and omit
    ``input_kind``. An explicit AGENT_INPUT remains a read-only legacy prompt
    in that case. A Cycle with no Investigator input yet is pending, not proof
    that its Goal is missing; never borrow content from another Cycle.
    """
    cycle = _root(run)
    events, warnings = _read_jsonl(cycle, 'runtime_events.jsonl')
    if any(any(marker in warning for marker in (
            'source exceeds', 'event cap reached', 'oversized record omitted'))
           for warning in warnings):
        raise GoalDraftError(
            'Cycle 事件日志不完整或过大，不能确认最新 Investigator 指令。',
            409, code='goal_input_unavailable',
        )
    if any('rejected' in warning or 'unreadable' in warning for warning in warnings):
        raise GoalDraftError(
            'Cycle 事件日志无法可靠读取，不能确认最新 Investigator 指令。',
            409, code='goal_input_unavailable',
        )
    for event in reversed(events):
        if str(event.get('event') or event.get('type') or '') != 'AGENT_INPUT':
            continue
        role = str(event.get('role') or event.get('agent') or '').strip().lower()
        if role != 'investigator':
            continue
        raw_kind = event.get('input_kind')
        if raw_kind is None or raw_kind == '':
            kind = 'legacy_prompt'
        elif isinstance(raw_kind, str):
            kind = raw_kind
        else:
            continue
        if kind == 'native_goal_setup_turn':
            continue
        if kind not in {'native_goal_objective', 'task', 'continuation', 'legacy_prompt'}:
            continue
        source = event.get('content')
        native = kind == 'native_goal_objective'
        limit = MAX_GOAL_CHARS if native else 120_000
        if isinstance(source, str) and source and len(source) <= limit:
            return {
                'cycle': cycle.name,
                'objective': source,
                'sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
                'recorded_at': str(event.get('ts') or ''),
                'source': ('runtime_events.jsonl / AGENT_INPUT / legacy prompt (input_kind missing)'
                           if kind == 'legacy_prompt'
                           else 'runtime_events.jsonl / AGENT_INPUT / ' + kind),
                'mode': 'native' if native else 'legacy',
            }
        raise GoalDraftError(
            '最新的 Investigator Goal/Prompt 记录不完整或超出允许长度；已拒绝回退到更旧输入。',
            409, code='goal_input_invalid',
        )
    log_not_created = any('runtime_events.jsonl: unavailable' in warning for warning in warnings)
    raise GoalDraftError(
        '当前 Cycle 尚未记录 Investigator 的实际 Goal/Prompt 输入。'
        + (' 运行日志可能尚未生成。' if log_not_created else ' Investigator 尚未开始或实际输入记录尚未写入。')
        + ' 请稍后重试；不会使用其他 Cycle 的内容。',
        409, code='goal_input_pending',
    )


def _ledger_dir(run: dict[str, Any]) -> Path:
    research = Path(str(run['research_root'])).resolve(strict=True)
    folder = research / '.theory-master'
    if not folder.is_dir() or folder.is_symlink() or folder.resolve(strict=True).parent != research:
        raise GoalDraftError('当前任务缺少安全的元数据目录', 409)
    return folder


def _draft_path(run: dict[str, Any], cycle: str) -> Path:
    return _ledger_dir(run) / f'web-native-goal-draft-{cycle}.json'


def _read_draft(run: dict[str, Any], cycle: str) -> dict[str, Any] | None:
    path = _draft_path(run, cycle)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 14_000:
        raise GoalDraftError('Goal 草稿存储状态异常，已拒绝读取', 409)
    try:
        item = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise GoalDraftError('Goal 草稿记录无法读取', 409) from exc
    if not isinstance(item, dict) or item.get('cycle') != cycle:
        raise GoalDraftError('Goal 草稿登记不一致', 409)
    return item


def view_goal(run: dict[str, Any]) -> dict[str, Any]:
    goal = _recorded_goal(run)
    draft = _read_draft(run, goal['cycle']) if goal['mode'] == 'native' else None
    safe_draft = None
    if draft is not None:
        safe_draft = {
            'text': draft.get('text'), 'revision': draft.get('revision'),
            'updated_at': draft.get('updated_at'),
            'base_matches_goal': draft.get('base_sha256') == goal['sha256'],
        }
    return {**goal, 'draft': safe_draft, 'editable_as_draft_only': goal['mode'] == 'native',
            'effective': 'original_native_goal_unchanged'}


def save_goal_draft(run: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        'cycle', 'text', 'expected_goal_sha256', 'expected_revision', 'acknowledge_draft_only'
    }:
        raise GoalDraftError('无效 Goal 草稿请求')
    text = payload['text']
    if (not isinstance(text, str) or not text.strip() or len(text) > MAX_GOAL_CHARS
            or '\x00' in text):
        raise GoalDraftError('Goal 草稿须为 1–4000 字符的非空文本')
    if payload['acknowledge_draft_only'] is not True:
        raise GoalDraftError('必须先确认保存不会改变运行中或下次 Attempt 的 Native Goal')
    if type(payload['expected_revision']) is not int or payload['expected_revision'] < 0:
        raise GoalDraftError('草稿版本号无效')
    folder = _ledger_dir(run)
    lock = folder / 'web-native-goal-draft.lock'
    if lock.is_symlink():
        raise GoalDraftError('Goal 草稿锁文件不安全', 409)
    with lock.open('a+') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        # Repeat the authoritative read inside lock: disallow stale-cycle or
        # stale-native-Goal edits and retain concurrent browser optimistic lock.
        goal = _recorded_goal(run)
        if goal['mode'] != 'native':
            raise GoalDraftError('此 Cycle 使用旧式 Investigator Prompt，不存在可修订的 Native Goal 草稿。', 409)
        if (payload['cycle'] != goal['cycle']
                or payload['expected_goal_sha256'] != goal['sha256']):
            raise GoalDraftError('Cycle 或实际 Native Goal 已发生变化，请刷新后再编辑', 409)
        previous = _read_draft(run, goal['cycle'])
        revision = int(previous.get('revision') or 0) if previous else 0
        if payload['expected_revision'] != revision:
            raise GoalDraftError('Goal 草稿已被其他编辑更新，请重新加载', 409)
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        record = {'cycle': goal['cycle'], 'base_sha256': goal['sha256'],
                  'revision': revision + 1, 'text': text, 'updated_at': now,
                  'applied': False, 'kind': 'human_unapplied_native_goal_draft'}
        destination = _draft_path(run, goal['cycle'])
        history = folder / 'web-native-goal-drafts.jsonl'
        if destination.is_symlink() or history.is_symlink():
            raise GoalDraftError('Goal 草稿或历史路径不安全', 409)
        fd, name = tempfile.mkstemp(prefix='.goal-draft-', dir=folder)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as writer:
                json.dump(record, writer, ensure_ascii=False, indent=2)
                writer.write('\n')
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(name, destination)
        finally:
            Path(name).unlink(missing_ok=True)
        # Explicit immutable history: never alter original runtime events or
        # SDK scientific state. Audit includes a SHA, text and revision.
        with history.open('a', encoding='utf-8') as writer:
            writer.write(json.dumps(record, ensure_ascii=False) + '\n')
            writer.flush()
            os.fsync(writer.fileno())
        return {'ok': True, 'cycle': goal['cycle'], 'revision': revision + 1,
                'saved_at': now, 'effective': False,
                'message': '草稿已保存并留有历史记录；当前及未来 Native Goal 均未更改。'}
