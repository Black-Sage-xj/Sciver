from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from theory_master.invocation_preparation import InvocationPreparation
from theory_master.runtime import write_json
from theory_master.workflow_state import WorkflowStateMixin


class LeadOwner(WorkflowStateMixin):
    def __init__(self, root, cycle, worker=None):
        self.spec = SimpleNamespace(research_root=root, goal_id="goal", branch_anchor_id=None)
        self.branch_parent_id = None
        self.cycle_dir = root / cycle
        self.worker_id = worker
        self.backend = SimpleNamespace()
        self.collector = SimpleNamespace(state={})
        self.routes = {"research_lead": SimpleNamespace(model="m", provider="p")}

    @staticmethod
    def _thread_key(state, role):
        return role

    def _apply_pending_task_route(self, state, role, thread_id):
        pass


def prepare(owner, state):
    call = SimpleNamespace(owner=owner, state=state, actual="research_lead", stage="frontier_proposal",
                           context={}, fresh_thread=False)
    InvocationPreparation(call)._prepare_thread()
    return call


@pytest.fixture
def previous(tmp_path):
    owner = LeadOwner(tmp_path, "1")
    owner.collector.state["thread_usage_baselines"] = {"first-lead": {"input_tokens": 40000, "output_tokens": 5000}}
    owner._save_research_lead_state(thread_id="first-lead", route=owner.routes["research_lead"],
                                    cycle_id="1", account="1")
    write_json(tmp_path / "1/research_state.json", {"status": "over"})
    return owner


def test_ordinary_cycle_resumes_verified_lead_and_ignores_character_count(previous):
    new = LeadOwner(previous.spec.research_root, "2")
    call = prepare(new, {"runtime_codex_account": "1", "role_outputs": {"old": "x" * 210000}})
    assert call.thread_id == "first-lead" and call.continuation
    assert new.collector.state["thread_usage_baselines"]["first-lead"] == {"input_tokens": 40000, "output_tokens": 5000}
    new.reset_research_lead("operator changed project")
    call = prepare(new, {"threads": {"research_lead": "first-lead"}, "runtime_codex_account": "1"})
    assert call.thread_id is None and not call.continuation


def test_wrong_account_active_worker_and_unverified_history_start_new(previous):
    new = LeadOwner(previous.spec.research_root, "2")
    assert prepare(new, {"runtime_codex_account": "2"}).thread_id is None
    assert prepare(LeadOwner(new.spec.research_root, "2", "worker-2"),
                   {"runtime_codex_account": "1"}).thread_id is None
    new.backend.qualification_thread = lambda role, old: None
    assert prepare(new, {"runtime_codex_account": "1"}).thread_id is None
    del new.backend.qualification_thread
    write_json(previous.cycle_dir / "research_state.json", {"status": "active"})
    assert prepare(new, {"runtime_codex_account": "1"}).thread_id is None


def test_worker_uses_own_preceding_cycle(previous):
    root = previous.spec.research_root
    w1 = LeadOwner(root, "2", "worker-1")
    w1._save_research_lead_state(thread_id="worker-lead", route=w1.routes["research_lead"],
                                 cycle_id="2")
    write_json(w1.cycle_dir / "research_state.json",
               {"status": "over", "cycle_execution_context": {"worker_id": "worker-1"}})
    assert prepare(LeadOwner(root, "5", "worker-1"), {}).thread_id == "worker-lead"
    assert prepare(LeadOwner(root, "4", "worker-2"), {}).thread_id is None


def test_cycle_rebind_releases_writer_process_before_resuming_lead(previous, monkeypatch):
    import fcntl
    import subprocess
    import sys

    from openai_codex import Sandbox
    from theory_master.codex_backend import CodexBackend

    root = previous.spec.research_root
    workspace = root / "1" / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(root / "codex"))
    monkeypatch.setattr("theory_master.codex_backend._codex_home", lambda: root / "codex")
    owner = LeadOwner(root, "2")
    owner.spec.notes_mirror_dir = None
    owner.spec.branch_canvas = root / "exploration.canvas"
    owner.spec.branch_canvas.write_text('{"nodes": [], "edges": []}')
    owner._graph_database_path = lambda: root / "science_graph.sqlite3"
    backend = CodexBackend(workspace, owner.routes)
    owner.backend = backend
    lock_path = root / "lead-writer.lock"
    writer_code = """
import fcntl
import sys
with open(sys.argv[1], "a+") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    print("locked", flush=True)
    sys.stdin.read()
"""
    with subprocess.Popen(
        [sys.executable, "-c", writer_code, str(lock_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    ) as writer, lock_path.open("a+") as resumed_lock:
        assert writer.stdout.readline().strip() == "locked"

        def close_writer():
            writer.terminate()
            writer.wait(timeout=5)

        old_client = SimpleNamespace(close=Mock(side_effect=close_writer))
        backend.client = old_client
        backend.threads = {"first-lead": object()}
        client = Mock()

        def resume(thread_id, **kwargs):
            fcntl.flock(resumed_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return SimpleNamespace(id=thread_id)

        client.thread_resume.side_effect = resume
        monkeypatch.setattr(backend, "_ensure_client", lambda: client)
        monkeypatch.setattr(backend, "_thread_config", lambda *args: {})
        try:
            owner._bind_cycle(root / "2", create=True)
            old_client.close.assert_called_once()
            assert writer.poll() is not None
            assert backend.client is None and backend.threads == {}
            call = prepare(owner, {"runtime_codex_account": "1"})
            assert call.thread_id == "first-lead" and call.continuation
            assert backend.ensure_thread("research_lead", call.thread_id, "m", "p") == "first-lead"
            assert client.thread_resume.call_args.kwargs["cwd"] == str(root / "2" / "workspace")
            assert client.thread_resume.call_args.kwargs["sandbox"] == Sandbox.read_only
            client.thread_start.assert_not_called()
        finally:
            if writer.poll() is None:
                close_writer()


def test_failed_client_close_keeps_the_client_available_for_recovery(previous):
    root = previous.spec.research_root
    owner = LeadOwner(root, "2")
    owner.spec.notes_mirror_dir = None
    owner.spec.branch_canvas = root / "exploration.canvas"
    owner.spec.branch_canvas.write_text('{"nodes": [], "edges": []}')
    owner._graph_database_path = lambda: root / "science_graph.sqlite3"
    client = SimpleNamespace(close=Mock(side_effect=RuntimeError("close failed")))
    owner.backend = SimpleNamespace(cwd=root / "1", client=client,
                                    threads={"first-lead": object()})
    with pytest.raises(RuntimeError, match="close failed"):
        owner._bind_cycle(root / "2", create=True)
    assert owner.backend.client is client
    assert "first-lead" in owner.backend.threads
