"""Deterministic chat-only fallback and fresh Worker liveness; no provider turns."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tm_observer.research_chat import ResearchChatError, create, send, set_model, snapshot
from tm_observer.research_chat_context import current_worker_context
from theory_master.codex_backend import CodexProviderRequestRejected


@pytest.fixture
def state(tmp_path):
    return tmp_path, {"runs": [{"observer_id":"task1", "research_root": str(tmp_path), "task_root":str(tmp_path)}]}


def test_only_explicit_unsupported_model_retries_once_and_preserves_question(state):
    root, st = state
    ident = create(root, st, {'mode':'project', 'task_id':'task1'})['session_id']
    set_model(root, ident, 'gpt-6.1-sol')
    seen = []
    class FakeClient:
        def __init__(self, cwd, routes):
            self.model = routes['research_chat'].model
        def ensure_thread(self, role, thread_id, model, provider):
            assert thread_id is None
            return 'thread-for-' + model
        def invoke(self, role, prompt, thread_id, model, provider, **kwargs):
            seen.append((model,thread_id,prompt))
            assert 'why did the workers stop?' in prompt
            if model == 'gpt-6.1-sol':
                raise CodexProviderRequestRejected(
                    "Codex provider HTTP 400 invalid_request_error: The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.")
            assert 'Conversation history:' in prompt  # fresh model thread gets context
            return thread_id, 'Worker process check confirms the science continues.'
        def close(self): pass
    with patch('theory_master.codex_backend.CodexBackend',FakeClient):
        response = send(root, root, st, ident, {'content':'why did the workers stop?'})
    assert response['ok']
    assert [m for m, *_ in seen] == ['gpt-6.1-sol','gpt-6-sol']
    record = snapshot(root,st,ident)['session']
    assert record['model'] == 'gpt-6-sol'
    assert record['codex_thread_id'] == 'thread-for-gpt-6-sol'
    assert record['messages'][-1]['role']=='assistant'
    assert sum(m['role']=='user' for m in record['messages']) == 1
    assert any('自动重试一次' in m['content'] for m in record['messages'])


def test_generic_bad_request_not_switched_and_not_retried(state):
    root, st = state
    ident = create(root, st, {'mode':'project', 'task_id':'task1'})['session_id']
    set_model(root, ident, 'gpt-6.1-sol')
    seen = []
    class FakeClient:
        def __init__(self, cwd, routes): pass
        def ensure_thread(self, role, thread_id, model, provider): return 'first-thread'
        def invoke(self, role, prompt, thread_id, model, provider, **kwargs):
            seen.append(model)
            raise CodexProviderRequestRejected('Codex provider HTTP 400 invalid_request_error: invalid tool schema')
        def close(self): pass
    with patch('theory_master.codex_backend.CodexBackend',FakeClient):
        with pytest.raises(ResearchChatError):
            send(root,root,st,ident,{'content':'check now'})
    assert seen == ['gpt-6.1-sol']
    record = snapshot(root,st,ident)['session']
    assert record['model'] == 'gpt-6.1-sol'
    assert record['active_run']['status'] == 'error'
def test_runtime_context_counts_worker_processes_not_stopped_allocator(tmp_path):
    workers=[{'worker_id':'worker-1','effective_status':'RUNNING','process_alive':True,'current_cycle':'136','account':'1'},
             {'worker_id':'worker-2','effective_status':'RUNNING','process_alive':True,'current_cycle':'137','account':'2'}]
    scheduler={'status':'STOPPED','process_alive':False,'cycles_started_by_worker':{'worker-1':2,'worker-2':2},'max_cycles_per_worker':2}
    with patch('theory_master.worker_runtime.list_workers',return_value=workers), \
         patch('theory_master.worker_runtime.scheduler_status',return_value=scheduler):
        text=current_worker_context(tmp_path)
    assert '当前存活 Worker 数：2 / 2' in text
    assert 'Cycle 136' in text and 'Cycle 137' in text
    assert '调度器：状态=STOPPED' in text
    assert 'worker-1=2/2' in text
    assert '停止分配不代表已启动的 Worker 停止运行' in text


def test_manual_model_change_invalidates_old_thread_without_erasing_history(state):
    root, st = state
    ident = create(root, st, {'mode':'project', 'task_id':'task1'})['session_id']
    path = root / 'research-chat' / f'{ident}.json'
    row=json.loads(path.read_text())
    row['codex_thread_id']='old-model-thread'
    count=len(row['messages'])
    path.write_text(json.dumps(row))
    set_model(root,ident,'gpt-6-luna')
    now=json.loads(path.read_text())
    assert now['codex_thread_id'] is None
    assert len(now['messages']) == count
    assert now['model'] == 'gpt-6-luna'
