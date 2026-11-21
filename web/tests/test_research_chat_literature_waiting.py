"""Offline checks for bounded progress and the loaded Chat waiting contract."""
from __future__ import annotations

import io
import json
import signal

import pytest

from tests.test_literature_service import LiteratureBackend, Retriever
from theory_master.skills import developer_instructions, role_skills, skills_root
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
def test_literature_operations_are_discoverable_but_absent_from_unrelated_turns(mode, skill):
    prompt = chat._skill_context({'mode': mode, 'skill': skill})
    source = skills_root() / skill / 'SKILL.md'
    assert str(source) in prompt and source.read_text(encoding='utf-8') in prompt
    instructions = developer_instructions('research_chat')
    literature = next(ref for ref in role_skills('research_chat') if ref.directory == 'research-literature')
    assert literature.directory in instructions
    operations = literature.skill_md.read_text(encoding='utf-8')
    for token in ('tm_observer.research_chat_literature', '--job-id', '--retrieval-manifest',
                  'write_stdin', 'report_href'):
        assert token in operations
        assert token not in instructions
        assert token not in prompt
