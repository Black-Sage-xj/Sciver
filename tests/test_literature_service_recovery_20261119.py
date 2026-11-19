"""No-network recovery tests for durable Literature requests and resources."""

from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

import theory_master.codex_backend as codex_backend
import theory_master.literature_service as service
from tests.test_literature_service import LiteratureBackend, Retriever, request


def load(root):
    return json.loads((root / "manifest.json").read_text())


@pytest.mark.parametrize("raw", ['{"unfinished":', '{"invalid_contract":true}'])
def test_invalid_durable_raw_is_revalidated_without_replacement(
    tmp_path, monkeypatch, raw
):
    class Invalid(LiteratureBackend):
        def invoke(self, *args, **kwargs):
            self.calls.append(args)
            return args[2], raw

    root, backend, retrieve = tmp_path / "job", Invalid(), Retriever()
    first = service.run_literature(root, request(), backend=backend, retriever=retrieve)
    invocation = load(root)["invocations"][0]
    original = {
        key: Path(invocation[key]).read_bytes()
        for key in ("raw_output_path", "prompt_path", "context_path")
    }
    monkeypatch.setattr(
        codex_backend,
        "CodexBackend",
        lambda *a, **k: pytest.fail("replay constructed a backend"),
    )
    for _ in range(4):
        resumed = service.run_literature(root, request(), retriever=retrieve)
        assert resumed["status"] == "failed"
        assert resumed["error"] == first["error"]
    assert len(backend.calls) == len(retrieve.calls) == 1
    assert load(root)["invocations"][0]["attempts"] == 1
    for key, data in original.items():
        assert Path(invocation[key]).read_bytes() == data


def test_unanswered_invocation_is_held_without_duplicate_call(tmp_path):
    class Interrupted(LiteratureBackend):
        def invoke(self, *args, **kwargs):
            self.calls.append(args)
            raise KeyboardInterrupt("lost process before a durable response")

    backend, retrieve, root = Interrupted(), Retriever(), tmp_path / "job"
    with pytest.raises(KeyboardInterrupt):
        service.run_literature(root, request(), backend=backend, retriever=retrieve)
    for _ in range(3):
        result = service.run_literature(
            root, request(), backend=backend, retriever=retrieve
        )
        assert result["status"] == "failed"
        assert "no durable response" in result["error"]
    assert len(backend.calls) == len(retrieve.calls) == 1
    assert load(root)["invocations"][0]["status"] == "blocked"


@pytest.mark.parametrize(
    "error,bound",
    [(ConnectionError("connection lost"), 1), (ValueError("permanent rejection"), 1)],
)
def test_transport_retries_are_bounded_on_the_original_thread(tmp_path, error, bound):
    class Failed(LiteratureBackend):
        def invoke(self, *args, **kwargs):
            self.calls.append(args)
            raise error

    backend, root = Failed(), tmp_path / "job"
    for _ in range(5):
        result = service.run_literature(
            root, request(), backend=backend, retriever=Retriever()
        )
        assert result["status"] == "failed"
    invocation = load(root)["invocations"][0]
    assert len(backend.calls) == invocation["attempts"] == bound
    assert len(invocation["attempt_history"]) == bound
    assert {call[2] for call in backend.calls} == {"fixture-literature-thread"}
    assert not Path(invocation["raw_output_path"]).exists()


def test_retry_cannot_replace_original_thread(tmp_path):
    class WrongThread(LiteratureBackend):
        def ensure_thread(self, role, thread, *args):
            return "replacement-thread" if thread else "fixture-literature-thread"

    backend, root = WrongThread(), tmp_path / "job"
    cancel = threading.Event()

    def stop_after_stage(event):
        if event["event"] == "literature.agent_completed":
            cancel.set()

    service.run_literature(
        root,
        request(),
        backend=backend,
        retriever=Retriever(),
        cancel_event=cancel,
        on_event=stop_after_stage,
    )
    result = service.run_literature(
        root, request(), backend=backend, retriever=Retriever()
    )
    assert "retain the original thread" in result["error"]
    assert len(backend.calls) == 1
    assert load(root)["thread_id"] == "fixture-literature-thread"


@pytest.mark.parametrize("failures,expected", [(1, "completed"), (4, "failed")])
def test_only_confirmed_preinvoke_failure_can_retry_with_a_bound(
    tmp_path, failures, expected
):
    class Preparing(LiteratureBackend):
        prepared = 0

        def ensure_thread(self, *args):
            self.prepared += 1
            if self.prepared <= failures:
                raise ConnectionError("connection failed before a turn request")
            return super().ensure_thread(*args)

    backend, root = Preparing(), tmp_path / "job"
    service.run_literature(root, request(), backend=backend, retriever=Retriever())
    result = service.run_literature(
        root, request(), backend=backend, retriever=Retriever()
    )
    assert result["status"] == expected, result
    if failures > 1:
        for _ in range(2):
            service.run_literature(
                root, request(), backend=backend, retriever=Retriever()
            )
        assert backend.prepared == 2 and not backend.calls


@pytest.mark.parametrize("kind", ["control", "quota", "provider", "cancel"])
def test_preinvoke_control_and_provider_stops_are_not_network_retries(tmp_path, kind):
    from theory_master.codex_errors import (
        CodexProviderRequestRejected,
        CodexTurnCancelled,
    )
    from theory_master.provider_errors import ProviderQuotaExhausted
    from theory_master.workflow_support import RecoveryStop

    error = {
        "control": RecoveryStop,
        "quota": ProviderQuotaExhausted,
        "provider": CodexProviderRequestRejected,
        "cancel": CodexTurnCancelled,
    }[kind]("connection timeout: must stop")

    class Preparing(LiteratureBackend):
        prepared = 0

        def ensure_thread(self, *args):
            self.prepared += 1
            raise error

    backend, root = Preparing(), tmp_path / "job"
    for _ in range(3):
        result = service.run_literature(
            root, request(), backend=backend, retriever=Retriever()
        )
        assert result["status"] == "failed"
    assert backend.prepared == 1 and not backend.calls


def test_wrong_returned_thread_keeps_raw_and_identity_without_reinvocation(tmp_path):
    class WrongThread(LiteratureBackend):
        def invoke(self, *args, **kwargs):
            _, raw = super().invoke(*args, **kwargs)
            return "unexpected-thread", raw

    backend, root = WrongThread(), tmp_path / "job"
    first = service.run_literature(
        root, request(), backend=backend, retriever=Retriever()
    )
    invocation = load(root)["invocations"][0]
    raw = Path(invocation["raw_output_path"]).read_bytes()
    second = service.run_literature(
        root, request(), backend=backend, retriever=Retriever()
    )
    assert first["status"] == second["status"] == "failed"
    assert "different thread" in second["error"]
    assert (
        len(backend.calls) == 1
        and raw == Path(invocation["raw_output_path"]).read_bytes()
    )
    assert (
        invocation["thread_id"]
        == load(root)["thread_id"]
        == "fixture-literature-thread"
    )
    assert invocation["returned_thread_id"] == "unexpected-thread"


def plan(*args, **kwargs):
    return {
        "queries": [
            {"query": "mechanism counterexample", "purpose": "Compare sources"}
        ],
        "relevance_terms": ["mechanism"],
        "scope": "Bounded review",
    }


@pytest.mark.parametrize(
    "failure", [None, ValueError("permanent failure"), KeyboardInterrupt("interrupt")]
)
def test_only_service_owned_backend_is_closed_on_all_exits(
    tmp_path, monkeypatch, failure
):
    created = []

    class Owned(LiteratureBackend):
        def __init__(self, cwd, **kwargs):
            super().__init__()
            self.cwd, self.close_calls = cwd, 0
            created.append(self)

        def invoke(self, *args, **kwargs):
            if failure:
                raise failure
            return super().invoke(*args, **kwargs)

        def close(self):
            self.close_calls += 1

    monkeypatch.setattr(codex_backend, "CodexBackend", Owned)
    if isinstance(failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            service.run_literature(
                tmp_path / "owned", request(), retriever=Retriever(), planner=plan
            )
    else:
        service.run_literature(
            tmp_path / "owned", request(), retriever=Retriever(), planner=plan
        )
    assert len(created) == 1 and created[0].close_calls == 1
    borrowed = Owned(tmp_path / "borrowed")
    if isinstance(failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            service.run_literature(
                borrowed.cwd, request(), backend=borrowed, retriever=Retriever()
            )
    else:
        service.run_literature(
            borrowed.cwd, request(), backend=borrowed, retriever=Retriever()
        )
    assert borrowed.close_calls == 0


def test_close_failure_does_not_hide_original_failure_or_manifest(
    tmp_path, monkeypatch
):
    class Owned(LiteratureBackend):
        def __init__(self, cwd, **kwargs):
            super().__init__()
            self.cwd = cwd

        def invoke(self, *args, **kwargs):
            raise ValueError("original contract-independent failure")

        def close(self):
            raise RuntimeError("cleanup failed")

    monkeypatch.setattr(codex_backend, "CodexBackend", Owned)
    result = service.run_literature(
        tmp_path / "job", request(), retriever=Retriever(), planner=plan
    )
    assert (
        result["status"] == "failed"
        and "original contract-independent" in result["error"]
    )
    assert "cleanup failed" in load(tmp_path / "job")["cleanup_error"]


def test_kwargs_backend_receives_cancellation_and_owned_cleanup(tmp_path, monkeypatch):
    cancel, created = threading.Event(), []

    class Owned(LiteratureBackend):
        def __init__(self, cwd, **kwargs):
            super().__init__()
            self.cwd, self.close_calls = cwd, 0
            created.append(self)

        def invoke(self, *args, **kwargs):
            assert kwargs["cancel_event"] is cancel
            cancel.set()
            raise RuntimeError("cancelled transport")

        def close(self):
            self.close_calls += 1

    monkeypatch.setattr(codex_backend, "CodexBackend", Owned)
    result = service.run_literature(
        tmp_path / "job",
        request(),
        retriever=Retriever(),
        planner=plan,
        cancel_event=cancel,
    )
    assert result["status"] == "paused" and result["stop_reason"] == "cancelled"
    assert created[0].close_calls == 1


def test_blocked_connectors_stop_before_model_and_retry_bound_retains_receipts(
    tmp_path,
):
    backend, calls, root = LiteratureBackend(), [], tmp_path / "job"

    def blocked(task, workspace):
        home = workspace / f"blocked-{len(calls)}"
        home.mkdir()
        (home / "failure.txt").write_text("All public source connectors failed")
        result = {
            "status": "blocked",
            "transport_ok": False,
            "reason": "All public source connectors failed",
            "output_dir": str(home),
        }
        calls.append(result)
        return result

    for _ in range(4):
        result = service.run_literature(
            root, request(), backend=backend, retriever=blocked
        )
        assert (
            result["status"] == "failed"
            and result["stop_reason"] == "transport_blocked"
        )
    state = load(root)
    assert not backend.calls and len(calls) == 2
    assert not state["transports"] and len(state["retrieval_attempt_history"]) == 2
    assert all(
        row["transport"]["reason"] == "All public source connectors failed"
        for row in state["retrieval_attempt_history"]
    )
    assert all(
        (root / f"blocked-{index}" / "failure.txt").read_text()
        == "All public source connectors failed"
        for index in range(2)
    )


def test_blocked_connector_can_recover_on_explicit_bounded_reentry(tmp_path):
    retrieve, calls, backend, root = (
        Retriever(),
        [],
        LiteratureBackend(),
        tmp_path / "job",
    )

    def first_blocked(task, workspace):
        calls.append(task)
        if len(calls) == 1:
            return {
                "status": "blocked",
                "transport_ok": False,
                "reason": "Temporary connector outage",
            }
        return retrieve(task, workspace)

    first = service.run_literature(
        root, request(), backend=backend, retriever=first_blocked
    )
    assert first["status"] == "failed" and not backend.calls
    second = service.run_literature(
        root, request(), backend=backend, retriever=first_blocked
    )
    assert second["status"] == "completed", second
    assert load(root)["retrieval_attempt_history"][0]["status"] == "failed"


@pytest.mark.parametrize("empty", [False, True])
def test_successful_partial_or_empty_search_is_distinct_from_connector_failure(
    tmp_path, empty
):
    retrieve = Retriever()

    def partial(task, workspace):
        result = retrieve(task, workspace)
        result["status"] = "partial"
        if empty:
            Path(result["papers_json"]).write_text("[]")
        return result

    class EmptyReview(LiteratureBackend):
        def invoke(self, *args, **kwargs):
            thread, raw = super().invoke(*args, **kwargs)
            if empty:
                output = json.loads(raw)
                output["evidence_records"] = []
                output["landscape"]["major_routes"] = []
                output["landscape"]["representative_papers"] = []
                output["landscape"]["recommended_reading_order"] = []
                raw = json.dumps(output)
            return thread, raw

    result = service.run_literature(
        tmp_path / "job", request(), backend=EmptyReview(), retriever=partial
    )
    assert result["status"] == "completed", result
    assert result["source_count"] == (0 if empty else 2)
