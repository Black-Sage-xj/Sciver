"""Offline checks for bounded progress and the loaded Chat waiting contract."""
from __future__ import annotations

import io
import json
from pathlib import Path
import signal

import pytest

from tests.test_literature_service import LiteratureBackend, Retriever
from theory_master.skills import role_skills, skills_root
from tm_observer import research_chat as chat
from tm_observer import research_chat_literature as literature


def test_cli_progress_uses_fixed_labels_deduplicates_and_flushes(monkeypatch):
    class ProgressStream(io.StringIO):
        flushes = 0

        def flush(self):
            self.flushes += 1

    stream = ProgressStream()
    monkeypatch.setattr(literature.sys, 'stderr', stream)
    progress = literature._cli_progress()
    payload = {'event': 'literature.agent_started', 'invocation_key': 'round-01-reading-01',
               'stage': 'untrusted full paper text', 'raw': 'private provider response'}
    progress(payload)
    progress({**payload, 'at': 'a later duplicate event'})
    progress({**payload, 'invocation_key': 'round-01-reading-02'})
    progress({'event': 'unknown raw provider event', 'raw': 'private provider response'})
    assert stream.getvalue().splitlines() == ['Literature · Literature 正在阅读与综合'] * 2
    assert stream.flushes == 2
    assert 'untrusted' not in stream.getvalue() and 'private' not in stream.getvalue()


def test_cli_reports_real_service_phases_and_keeps_stdout_json(tmp_path, monkeypatch, capsys):
    state, search = tmp_path / 'state', tmp_path / 'workspace'
    search.mkdir()
    monkeypatch.setenv('THEORY_MASTER_DATA_ROOT', str(tmp_path / 'data'))
    ident = chat.create(state, {'runs': []}, {'mode': 'preparation', 'entry': 'clear'})['session_id']
    actual_run = literature.run_study
    backend, retriever = LiteratureBackend(), Retriever()

    def run_with_fixtures(*args, **kwargs):
        return actual_run(*args, **kwargs, backend=backend, retriever=retriever)

    monkeypatch.setattr(literature, 'run_study', run_with_fixtures)
    assert literature.main(['--state-dir', str(state), '--search-root', str(search),
                            '--session', ident, '--query', 'Compare the mechanism and counterexample']) == 0
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert receipt['status'] == 'completed' and receipt['agent_turns'] == len(backend.calls)
    lines = captured.err.splitlines()
    assert lines[0] == 'Literature · 文献作业已开始'
    assert lines[-1] == 'Literature · 文献作业已返回，状态见最终回执'
    assert lines.count('Literature · Literature 正在阅读与综合') == len(backend.calls)
    assert lines.count('Literature · 本项文献获取已返回') == len(retriever.calls)
    assert all(len(line) < 80 and line.startswith('Literature · ') for line in lines)
    assert 'Source-grounded observations' not in captured.err
    assert receipt['report'] not in captured.err


@pytest.mark.parametrize('signum', [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize('failure', [False, True])
def test_cli_progress_keeps_signal_cancellation_and_handler_restoration(
        tmp_path, monkeypatch, capsys, signum, failure):
    originals = {signal.SIGTERM: object(), signal.SIGINT: object()}
    installed = dict(originals)

    def set_signal(kind, handler):
        previous = installed[kind]
        installed[kind] = handler
        return previous

    def run(*args, cancel_event, on_event, **kwargs):
        on_event({'event': 'literature.job_started'})
        installed[signum](signum, None)
        assert cancel_event.is_set()
        if failure:
            raise RuntimeError('private failure payload')
        on_event({'event': 'literature.job_finished', 'status': 'paused', 'stop_reason': 'cancelled'})
        return {'status': 'paused', 'stop_reason': 'cancelled'}

    monkeypatch.setattr(literature.signal, 'signal', set_signal)
    monkeypatch.setattr(literature, 'run_study', run)
    assert literature.main(['--state-dir', str(tmp_path), '--search-root', str(tmp_path),
                            '--session', 'a' * 32, '--query', 'A bounded question']) == int(failure)
    assert installed == originals
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert receipt['status'] == ('blocked' if failure else 'paused')
    assert 'private failure payload' not in captured.out + captured.err
    if not failure:
        assert receipt['stop_reason'] == 'cancelled'


@pytest.mark.parametrize('mode,skill', [('preparation', 'research-preparation'),
                                       ('project', 'research-discussion')])
def test_actual_loaded_skill_exposes_waiting_and_failure_exceptions(mode, skill):
    prompt = chat._skill_context({'mode': mode, 'skill': skill})
    source = skills_root() / skill / 'SKILL.md'
    assert str(source) in prompt and source.read_text(encoding='utf-8') in prompt
    assert 'yield_time_ms=30000' in prompt and '`60000`' in prompt
    assert 'write_stdin' in prompt and '30–60 秒' in prompt
    assert '阶段没有变化时继续等待，不反复执行' in prompt
    assert '进程结束后再读最终 JSON 回执、报告和来源访问层级' in prompt
    assert '不启动重复作业' in prompt
    assert '等待约定不阻止取消或有依据的故障诊断' in prompt
    assert '恢复仍使用同一 `job_id`' in prompt
    assert 'SDK Cycle' in prompt


def test_packaged_chat_skills_are_the_runtime_sources_and_share_waiting_contract():
    refs = {ref.directory: ref for ref in role_skills('research_chat')}
    contracts = []
    for name in ('research-preparation', 'research-discussion'):
        path = refs[name].skill_md
        assert path == skills_root() / name / 'SKILL.md'
        text = path.read_text(encoding='utf-8')
        contracts.append(next(paragraph for paragraph in text.split('\n\n')
                              if paragraph.startswith('Literature CLI 同步执行')))
    assert contracts[0] == contracts[1]
    assert not (Path(__file__).resolve().parents[2] / 'skills').exists()
