"""The Supervisor (not an Attention alias) owns blocked-science disposition."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from theory_master.backends import FakeBackend
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap
from theory_master.workflow import Workflow
from theory_master.workflow_support import RecoveryStop
from theory_master.supervisor_intervention import KEY, LEGACY_ATTENTION_KEY


def _workflow(tmp_path: Path) -> Workflow:
    plan = tmp_path / "research_plan.md"
    plan.write_text(
        "# Goal\nFind a scientific mechanism\n\n"
        "# Research Contract\nUse frozen inputs only\n\n"
        "# Final Gate\nIndependent check\n\n# Required Context\n- local\n", encoding="utf-8",
    )
    binding = bootstrap(plan)
    wf = Workflow(TaskSpec.from_config(binding.config_path), FakeBackend(reworks=0))
    return wf


def _blocked_at_qualification(wf: Workflow) -> None:
    """Emulate an Investigator blocked RecoveryStop after the lock is frozen."""
    def stop(state: dict, _run_id: str, _prior_cycle: dict):
        checkpoint = wf.cycle_dir / "checkpoint.json"
        checkpoint.write_text("{}", encoding="utf-8")
        state.update({
            "status": "needs_attention", "phase": "investigator:goal_suspended",
            "resumable": True, "execution_active": False,
            "hypothesis_lock": {
                "status": "locked", "id": "H-0001",
                "hypothesis": "mechanism", "scope": "frozen",
                "decisive_test": "frozen test",
            },
            "last_safe_checkpoint": str(checkpoint),
            "attention": {
                "code": "investigator_native_goal_blocked",
                "goal_status": "blocked", "reason": "A specific missing mechanism input",
            },
        })
        raise RecoveryStop("Native Goal blocked")
    wf._qualify_and_lock = stop


@pytest.mark.parametrize("supervisor,legacy,expected_call", [
    ("1", "0", True),    # modern authoritative switch wins conflicting alias
    ("0", "1", False),   # legacy alias cannot override modern OFF
    ("0", "0", False),
    ("1", "1", True),
])
def test_blocked_science_controlled_only_by_supervisor(tmp_path, supervisor, legacy, expected_call):
    wf = _workflow(tmp_path)
    _blocked_at_qualification(wf)
    with patch.dict("os.environ", {KEY: supervisor, LEGACY_ATTENTION_KEY: legacy}):
        with patch.object(wf, "attention_takeover_enabled", side_effect=AssertionError(
            "Workflow must not consult old Attention alias"
        )):
            with patch("theory_master.supervisor_attention_review.review_supervisor_attention",
                       return_value={"action": "hold", "reason": "No new in-scope route",
                                     "guidance": "", "session_id": "SUP-fixture"}) as review:
                result = wf._execute_cycle()
    assert (review.call_count == 1) == expected_call
    assert result["status"] == "needs_attention"
    assert result.get("preservation") is None
    if expected_call:
        assert result["supervisor_attention_review"] == result["internal_attention_review"]
        assert result["supervisor_attention_review"]["session_id"] == "SUP-fixture"
    else:
        assert "supervisor_attention_review" not in result
    events = [json.loads(x) for x in (wf.cycle_dir / "events.jsonl").read_text().splitlines()]
    decisions = [x for x in events if x["type"] == "supervisor.scientific_review_decided"]
    assert len(decisions) == int(expected_call)
    if expected_call:
        assert decisions[0]["payload"]["authority"] == "supervisor"


def test_supervisor_preserve_requires_its_own_scoped_authorization(tmp_path):
    wf = _workflow(tmp_path)
    _blocked_at_qualification(wf)
    stage_output = {
        "hypothesis_id": "H-0001",
        "node_id": "external-c1-preservation",
    }
    closure = {
        "status": "OVER", "closure_reason": "investigator_preservation",
        "scientific_verdict": None,
    }
    with patch.dict("os.environ", {KEY: "1", LEGACY_ATTENTION_KEY: "0"}):
        with patch("theory_master.supervisor_attention_review.review_supervisor_attention",
                   return_value={"action": "preserve", "reason": "No viable in-scope path",
                                 "guidance": "", "session_id": "SUP-tested-0001"}):
            with patch("theory_master.research_preservation.run_preservation_stage",
                       return_value=stage_output) as preserve:
                with patch("theory_master.research_preservation.finalize_preserved_cycle",
                           return_value=closure) as finalize:
                    result = wf._execute_cycle()
    assert result["status"] == "over"
    assert result["phase"] == "investigator:preserved"
    assert result["closure_reason"] == "investigator_preservation"
    assert result["resumable"] is False
    preserve.assert_called_once()
    finalize.assert_called_once()
    assert preserve.call_args.kwargs["authorization"] == "supervisor_approved"
    assert finalize.call_args.kwargs["authorization"] == "supervisor_approved"
    approval = json.loads((wf.cycle_dir / "preservation" / "supervisor_authorization.json").read_text())
    assert approval["source"] == "supervisor_attention"
    assert approval["hypothesis_id"] == "H-0001"
    assert approval["session_id"] == "SUP-tested-0001"
    assert result.get("scientific_verdict") is None


def test_legacy_module_is_alias_not_another_reviewer():
    from theory_master.internal_attention_review import review_attention_in_sdk
    from theory_master.supervisor_attention_review import review_supervisor_attention
    assert review_attention_in_sdk is review_supervisor_attention
