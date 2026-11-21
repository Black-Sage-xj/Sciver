import json

from theory_master.codex_goal_transport import NativeGoalRunner


def test_authorization_hold_requires_current_reviewed_plan_quotes():
    runner = object.__new__(NativeGoalRunner)
    runner.context_prompt = json.dumps({
        "research_contract": "Separate approval is required before numerical work.",
        "final_gate": "Deliver a reviewable method plan with single-site marginals.",
    })
    limit = {
        "kind": "authorization_pending",
        "authorization_clause": "Separate approval is required before numerical work.",
        "deliverable_clause": "Deliver a reviewable method plan with single-site marginals.",
    }
    assert runner._execution_limit_matches_reviewed_plan(limit)
    assert not runner._execution_limit_matches_reviewed_plan({
        **limit,
        "deliverable_clause": "Deliver a reviewable method plan with single-bond marginals.",
    })
    assert runner._execution_limit_matches_reviewed_plan({"kind": "input_support_unavailable"})
