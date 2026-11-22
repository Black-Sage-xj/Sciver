import json
from pathlib import Path
from types import SimpleNamespace

from theory_master.contract_stages import role_contract
from theory_master.lead_prompt_history import confirm_lead_prompt, context_changes, prepare_lead_prompt
from theory_master.prompt_stages import build_initial_prompt


def session(root, cycle, current, *, thread="alpha", invocation="inv-1"):
    route = role_contract("research_lead", phase="frontier_proposal")
    return SimpleNamespace(
        owner=SimpleNamespace(cycle_dir=root / str(cycle), spec=SimpleNamespace(research_root=root)),
        stage="frontier_proposal", identifier=thread, contract=route, prompt_context=current,
        continuation=cycle > 1, invocation_id=invocation, agent_graph=None, guidance_view=None,
    )


def render(s):
    full = build_initial_prompt(prefix="Lead", role="research_lead", stage=s.stage,
                                contract_ref=s.contract.ref, contract_schema=s.contract.prompt_projection(s.prompt_context),
                                context=s.prompt_context, write_rule="")
    return prepare_lead_prompt(s, full)


def test_repeated_stage_only_sends_new_results_and_honors_delivery_boundaries(tmp_path):
    old = {"goal": "Fixed scientific objective", "agent_graph": {"hypotheses": [["H1", "open"]]},
           "qualification_feedback": "old issue"}
    first = session(tmp_path, 1, old)
    assert old["goal"] in render(first)
    confirm_lead_prompt(first)
    current = {"goal": old["goal"], "agent_graph": {"hypotheses": [["H1", "closed"], ["H2", "open"]]}}
    second = session(tmp_path, 2, current, invocation="inv-2")
    prompt = render(second)
    assert old["goal"] not in prompt and "Procedure for this stage only" not in prompt
    update = json.loads(prompt.split("Context:\n")[1])["lead_stage_update"]
    assert update["changes"] == [
        {"remove": ["qualification_feedback"]},
        {"set": ["agent_graph", "hypotheses", 0, 1], "value": "closed"},
        {"append": ["agent_graph", "hypotheses"], "values": [["H2", "open"]]},
    ]
    assert json.loads(Path(update["stage_snapshot"]).read_text())["context"] == current
    retry = session(tmp_path, 2, current, invocation="inv-retry")
    assert old["goal"] in render(retry)  # interrupted before commit
    confirm_lead_prompt(retry)
    other = session(tmp_path, 3, current, thread="beta")
    assert old["goal"] in render(other)  # provider replacement


def test_list_removal_preserves_order():
    assert context_changes(["x", "y"], ["z"]) == [
        {"set": [0], "value": "z"}, {"truncate": [], "length": 1}
    ]
