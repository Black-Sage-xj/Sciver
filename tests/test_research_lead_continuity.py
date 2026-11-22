from types import SimpleNamespace

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
