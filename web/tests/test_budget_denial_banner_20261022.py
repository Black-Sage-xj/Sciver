"""The real Budget Reviewer decision is not a Supervisor runtime error."""
import json

from tm_observer.supervisor_view import banner_html, home_status, index_chip


def test_budget_review_rejected_has_distinct_banner_and_keeps_history(tmp_path):
    cycle = tmp_path / "184"
    (cycle / "supervisor").mkdir(parents=True)
    data = {
        "status": "needs_attention", "phase": "investigator:budget_denied",
        "execution_active": False,
        "investigator_budget": {"policy_version": "legacy", "rejected": True},
        "attention": {"code": "investigator_budget_review_rejected",
                      "reason": "The frozen test is not yet distinguishable"},
    }
    (cycle / "research_state.json").write_text(json.dumps(data))
    (cycle / "run_state.json").write_text(json.dumps({
        **data, "status": "NEEDS_ATTENTION"}))
    (cycle / "supervisor/status.json").write_text(json.dumps({
        "schema": "theory-master/supervisor-status/v1",
        "status": "hold", "code": "investigator_budget_review_rejected",
        "profile": "attention", "summary": "Budget Reviewer rejected",
    }))
    run = {"research_root": str(tmp_path), "cycle": "184",
           "observer_id": "8dd38a9977eef7e5",
           "state": "NEEDS_ATTENTION"}
    html = banner_html(run, {"status": "NEEDS_ATTENTION"})
    assert 'data-budget-denial="true"' in html
    assert "预算审核拒绝" in html
    assert "查看原始审核决定" in html
    assert "Supervisor 已停下" not in html
    assert home_status(run, {"status": "NEEDS_ATTENTION"}) is None
    assert "预算审核拒绝" in index_chip(run)
    for broken in (
        {**data, "investigator_budget": {"rejected": False}},
        {**data, "attention": {"code": "investigator_budget_review_failed"}},
        {**data, "status": "failed", "phase": "failed"},
    ):
        (cycle / "research_state.json").write_text(json.dumps(broken))
        assert 'data-budget-denial="true"' not in banner_html(
            run, {"status": "NEEDS_ATTENTION"})

def test_large_run_state_still_shows_authenticated_budget_decision(tmp_path):
    cycle = tmp_path / "184"
    cycle.mkdir()
    science = {
        "status": "needs_attention", "phase": "investigator:budget_denied",
        "execution_active": False,
        "investigator_budget": {"policy_version": "legacy", "rejected": True},
        "attention": {"code": "investigator_budget_review_rejected"},
    }
    (cycle / "research_state.json").write_text(json.dumps(science))
    (cycle / "run_state.json").write_text(json.dumps({
        **science, "status": "NEEDS_ATTENTION",
        "historical_trace": "x" * 250_000,
    }))
    run = {"research_root": str(tmp_path), "cycle": "184",
           "observer_id": "8dd38a9977eef7e5", "state": "NEEDS_ATTENTION"}
    assert 'data-budget-denial="true"' in banner_html(run, {"status": "NEEDS_ATTENTION"})
