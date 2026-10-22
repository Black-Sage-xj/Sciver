"""Offline A–F contracts for Investigator historical-result reuse.

Prompt assembly only. No live model and no research Cycle. Real execution
remains TMA-20261022-0109.
"""
from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path

from theory_master.identity import identities_root
from theory_master.native_goal import (
    NATIVE_GOAL_SAFE_CHARS, investigator_goal, native_goal_setup_turn, read_goal_findings,
    NativeGoalResult,
)
from theory_master.prompt_stages import stage_context
from theory_master.skills import developer_instructions, skills_root
from theory_master.stage_registry import STAGE_CARDS


ROOT = Path(__file__).resolve().parents[1]
LOCK = {
    "hypothesis": "Signed linear channel dominates the exposed training ledger",
    "scope": "Fixed network, protocol, force domain, and error bound",
    "decisive_test": "Run the frozen independent held-out comparison",
}


def _identity() -> str:
    runtime = (identities_root() / "investigator.md").read_text(encoding="utf-8")
    source = (ROOT / "identities" / "investigator.md").read_text(encoding="utf-8")
    if source != runtime:
        raise AssertionError("Investigator identity source and packaged copy diverged")
    return runtime


class HistoricalReusePromptTests(unittest.TestCase):
    def setUp(self):
        self.identity = _identity()
        self.goal = investigator_goal(
            "investigate", {"frozen_objective": LOCK}, layout="v2",
        )

    def test_a_applicable_confirmed_result_is_cited_not_bulk_repeated(self):
        self.assertIn("适用则直接继承并继续未解决部分", self.identity)
        self.assertIn(
            "cite an applicable prior result when it already answers this frozen step",
            self.goal,
        )
        self.assertNotIn("必须重新", self.identity)
        self.assertNotIn("always recompute", self.goal.lower())

    def test_b_protocol_or_error_gap_still_allows_recomputation(self):
        self.assertIn("数值误差缺陷而必需的重算", self.identity)
        self.assertIn("方法、数值误差缺陷", self.identity)
        self.assertNotIn("never recompute", self.goal.lower())
        self.assertNotIn("禁止重算", self.identity)

    def test_c_required_independent_check_is_not_skipped_for_similar_history(self):
        self.assertIn("不得因历史相似而跳过", self.identity)
        self.assertIn("do not skip a required independent check", self.goal)
        for value in LOCK.values():
            self.assertIn(value, self.goal)
        self.assertIn("FROZEN SCIENTIFIC OBJECTIVE (immutable)", self.goal)

    def test_d_new_uncertainty_is_not_a_meaningless_repeat(self):
        self.assertIn("新判别对象或新不确定性，不是无意义重复", self.identity)
        self.assertIn("复现成功不是新科学发现", self.identity)
        self.assertNotIn("复现成功不是新科学发现", self.goal)

    def test_e_continuation_rework_and_recovery_keep_existing_records(self):
        self.assertIn("不得清空或覆盖", self.identity)
        self.assertNotIn("重启", self.identity)
        self.assertNotIn("drop_thread", inspect.getsource(investigator_goal))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            note = workspace / "historical_ledger.md"
            payload = "KEEP-LINEAR-SQUARE\n"
            note.write_text(payload, encoding="utf-8")
            before = note.read_bytes()
            context = {
                "hypothesis_lock": dict(LOCK),
                "cycle_workspace": str(workspace),
                "previous_executor": "investigator",
                "previous_scientific_delta": {"findings": ["archived linear/square ledger"]},
                "judge_objections": [{
                    "requested_resolution": "complete the missing independent comparison",
                }],
            }
            continued = stage_context(
                "investigator", "investigate", context, continuation=True,
            )
            rework = stage_context(
                "investigator", "judge_rework", context, continuation=True,
            )
            recovered = stage_context(
                "investigator", "investigate", context, continuation=False,
            )
            self.assertEqual(continued["workspace_access"]["cycle_workspace"], str(workspace))
            self.assertEqual(rework["workspace_access"]["cycle_workspace"], str(workspace))
            self.assertNotIn("previous_scientific_delta", continued)
            self.assertEqual(
                recovered["previous_scientific_delta"]["findings"],
                ["archived linear/square ledger"],
            )
            self.assertEqual(rework["frozen_objective"], LOCK)
            self.assertEqual(continued["frozen_objective"], LOCK)
            goal = investigator_goal("judge_rework", rework, layout="v2", allow_setup_reference=True)
            setup = native_goal_setup_turn(
                json.dumps(rework, ensure_ascii=False) + "\n\nSDK DELIVERY INTERFACE",
                goal,
            )
            self.assertIn("RETAINED Investigator thread", goal)
            self.assertIn("complete the missing independent comparison", goal)
            self.assertIn(str(workspace), setup)
            for banned in ("clear the workspace", "delete existing", "start from scratch", "清空"):
                self.assertNotIn(banned, goal)
                self.assertNotIn(banned, setup)
            self.assertEqual(note.read_text(encoding="utf-8"), payload)
            self.assertEqual(note.read_bytes(), before)
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "findings.json"
            handoff.write_text(json.dumps({"findings": [{
                "claim": "Cited applicable ledger; independent check still open",
                "method": "reuse",
                "result": {"cited": True},
            }]}), encoding="utf-8")
            parsed = json.loads(read_goal_findings(
                NativeGoalResult("complete", "retained-thread", 1, 0), handoff,
            ))
            self.assertEqual(parsed["findings"][0]["statement"],
                             "Cited applicable ledger; independent check still open")
            self.assertNotIn("reuse_decision", parsed["findings"][0])

    def test_f_goal_budget_and_no_duplicated_identity(self):
        developer = developer_instructions("investigator")
        self.assertEqual(developer.count("历史证据复用"), 1)
        self.assertNotIn("历史证据复用", self.goal)
        self.assertNotIn("可复用 Workspace 产物", self.goal)
        self.assertEqual(self.goal.count("applicable prior result"), 1)
        v1 = investigator_goal("investigate", {"frozen_objective": LOCK}, layout="v1")
        self.assertLess(len(self.goal), len(v1))
        self.assertLessEqual(len(self.goal), NATIVE_GOAL_SAFE_CHARS)
        long_science = {
            "hypothesis": "H" * 186,
            "scope": "scope" * 73,
            "decisive_test": "first shell ΔE Q C0 Q1 " * 55,
        }
        long_goal = investigator_goal(
            "investigate", {"frozen_objective": long_science}, layout="v2",
        )
        self.assertLessEqual(len(long_goal), NATIVE_GOAL_SAFE_CHARS)
        self.assertIn(long_science["decisive_test"], long_goal)
        design = (ROOT / "design" / "SDK设计文档.md").read_text(encoding="utf-8")
        self.assertIn("Native Goal 只保留一句计算前成本与复用提醒", design)
        skill = (skills_root() / "numerical-investigation" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("先查再算，复用优先", skill)
        self.assertNotIn("历史证据复用", skill)

    def test_boundaries_stay_outside_the_reuse_rule(self):
        self.assertIn("已暴露样本不能冒充盲测或 Final Gate 证据", self.identity)
        self.assertIn("Never self-terminate or request native blocked", self.goal)
        self.assertIn("silently weaken the frozen estimand", self.goal)
        self.assertNotIn("随机网络", self.identity)
        self.assertNotIn("random network", self.goal.lower())
        for token in ("reuse_decision", "historical_evidence_id", "recompute_flag"):
            self.assertNotIn(token, self.identity)
            self.assertNotIn(token, self.goal)
        stages = {stage for role, stage in STAGE_CARDS if role == "investigator"}
        self.assertTrue({
            "preserve_research", "investigate", "integrate_literature", "judge_rework",
        } <= stages)
        self.assertNotIn("historical_reuse", stages)
        invocation = (ROOT / "theory_master" / "workflow_invocation.py").read_text(encoding="utf-8")
        self.assertIn('layout="v2"', invocation)
        self.assertNotIn("restart_investigator_for_prompt", invocation)


if __name__ == "__main__":
    unittest.main()
