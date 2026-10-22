"""Provider rejection details stay actionable without exposing credential-like values."""
from __future__ import annotations

import json

import pytest

from tm_observer.research_chat import ResearchChatError, _chat_failure_message, create, send, set_model
from theory_master.codex_backend import CodexProviderRequestRejected


def test_provider_rejection_is_explained_and_redacted_in_persisted_chat(tmp_path):
    state = {"runs": [{"observer_id": "task1", "research_root": str(tmp_path), "task_root": str(tmp_path)}]}
    ident = create(tmp_path, state, {"mode": "project", "task_id": "task1"})["session_id"]
    set_model(tmp_path, ident, "gpt-6.1-sol")
    failure = CodexProviderRequestRejected(
        "Codex provider HTTP 400 invalid_request_error: invalid tool schema; api_key=super-secret-token")

    class FakeBackend:
        def invoke(self, role, prompt, thread_id, model, provider):
            assert "why did the response fail?" in prompt
            raise failure

    with pytest.raises(ResearchChatError):
        send(tmp_path, tmp_path, state, ident, {"content": "why did the response fail?"}, backend=FakeBackend())

    record = json.loads((tmp_path / "research-chat" / f"{ident}.json").read_text(encoding="utf-8"))
    serialized = json.dumps(record, ensure_ascii=False)
    system_message = next(m["content"] for m in reversed(record["messages"]) if m["role"] == "system")
    assert "模型请求被 Provider 拒绝" in system_message
    assert "gpt-6.1-sol" in system_message
    assert "medium" in system_message
    assert "invalid tool schema" in system_message
    assert "api_key=[REDACTED]" in system_message
    assert "super-secret-token" not in serialized


def test_non_provider_exception_does_not_leak_raw_error_text():
    message, detail = _chat_failure_message(RuntimeError("private path /private/token=abc123"), {"model": "gpt-6-sol"})
    assert message == "模型回复失败（RuntimeError）。请重试上一条消息。"
    assert detail is None
    assert "/private/token" not in message
    assert "abc123" not in message
