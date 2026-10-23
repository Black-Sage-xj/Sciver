from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from theory_master.identity import identity_block, identity_files, identities_root
from theory_master.skills import developer_instructions
from theory_master.stage_registry import (
    PROCEDURE_CARDS,
    STAGE_CARDS,
    procedure_card_text,
    stage_card_text,
)


LEAD_STAGES = (
    "frontier_proposal",
    "hypothesis_display",
    "candidate_selection",
    "selection_repair",
    "cycle_synthesis",
    "frontier_maintenance",
)


class IdentityLayerOptimizationTests(unittest.TestCase):
    def test_identity_assembly_includes_each_shared_attachment_once(self):
        expected_names = ["{role}.md", "_invocation_handoff.md", "_paths.md"]
        for role in ("research_lead", "challenger"):
            with self.subTest(role=role):
                files = identity_files(role)
                self.assertEqual(
                    [path.name for path in files],
                    [name.format(role=role) for name in expected_names],
                )
                expected_block = "\n\n".join(
                    path.read_text(encoding="utf-8").strip() for path in files
                )
                block = identity_block(role)
                self.assertEqual(block, expected_block)

                instructions = developer_instructions(role)
                self.assertEqual(instructions.count(block), 1)
                for path in files:
                    self.assertEqual(
                        instructions.count(path.read_text(encoding="utf-8").strip()), 1,
                        path.name,
                    )

    def test_research_lead_identity_retains_stable_authority_and_boundaries(self):
        identity = identity_block("research_lead")
        for phrase in (
            "candidate frontier",            "只执行当前 Stage Card 与 contract 授权的职责",
            "不扩展到其他 Stage",

            "合格候选的选择权",
            "Scientific Model 内容与演化决定权",
            "frontier_status",
            "supported/rejected/open",
            "Challenger",
            "Judge",
            "Hypothesis Lock",
            "SDK",
            "Investigator",
            "Literature",
            "Reporter",
            "SQLite",
            "science_graph_tools",
            "exploration.canvas",
            "research_plan.md",
            "存在性命题只能由合格实例支持",
            "有限搜索无结果不能充当一般性反证",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, identity)

        # These are invocation/stage methods and remain in Stage/Procedure Cards.
        for stage_detail in (
            "frontier_proposal",
            "frontier_maintenance",
            "candidate_selection",
            "selection_repair",
            "cycle_synthesis",
            "lineage_anchor_node_ids",
            "why_now",
            "reconsideration_trigger",
            "network-level sample size",
        ):
            with self.subTest(stage_detail=stage_detail):
                self.assertNotIn(stage_detail, identity)

    def test_challenger_keeps_independent_reviewer_authority_without_review_recipe(self):
        identity = identity_block("challenger")
        for phrase in (
            "Hypothesis Qualification",            "只执行当前 Stage Card 与 contract 授权的审查职责",
            "不扩展到其他 Stage",

            "独立 reviewer",
            "Research Lead",
            "candidate",
            "agent_graph",
            "Judge",
            "不选择 winner",
            "不修改、替换或自行生成 candidate",
            "不审查已锁定的 Main Hypothesis",
            "不进行理论推导、数值实验",
            "只读 search/detail",
            "不调用 `view`",
            "workspace_access",
            "SQLite",
            "不是科研数据源",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, identity)

        for stage_detail in (
            "candidate_review",
            "candidate.goal_advance",
            "Final Gate",
            "matching, contrary or inconclusive",
            "eligible pairs",
            "sample size",
        ):
            with self.subTest(stage_detail=stage_detail):
                self.assertNotIn(stage_detail, identity)

    def test_lead_and_challenger_stage_methods_remain_stage_scoped(self):
        for stage in LEAD_STAGES:
            with self.subTest(role="research_lead", stage=stage):
                self.assertIn(("research_lead", stage), STAGE_CARDS)
                card = stage_card_text("research_lead", stage)
                for label in (
                    f"Stage: {stage}",
                    "Current objective:",
                    "May do:",
                    "Must not:",
                    "Exit / handoff:",
                ):
                    self.assertIn(label, card)
                if ("research_lead", stage) in PROCEDURE_CARDS:
                    self.assertTrue(procedure_card_text("research_lead", stage).startswith(
                        "Procedure for this stage only:\n"
                    ))

        self.assertIn(("challenger", "candidate_review"), STAGE_CARDS)
        challenger_card = stage_card_text("challenger", "candidate_review")
        for label in ("Current objective:", "May do:", "Must not:", "Exit / handoff:"):
            self.assertIn(label, challenger_card)
        self.assertTrue(procedure_card_text("challenger", "candidate_review").startswith(
            "Procedure for this stage only:\n"
        ))

    def test_handoff_contains_common_invocation_rules_not_role_specific_review_steps(self):
        handoff = (identities_root() / "_invocation_handoff.md").read_text(encoding="utf-8")
        for phrase in (
            "Stage Card",
            "输出 contract",
            "Evidence refs",
            "workflow/runtime state",
            "有目的地搜索",
            "没有访问权限",
            "不得无目的遍历",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, handoff)
        for stage_specific in (
            "frontier_proposal",
            "candidate_review",
            "candidate.goal_advance",
            "P(d)",
            "Main Hypothesis",
        ):
            with self.subTest(stage_specific=stage_specific):
                self.assertNotIn(stage_specific, handoff)

    def test_path_attachment_preserves_anchor_semantics_and_sync_data_isolation(self):
        paths = (identities_root() / "_paths.md").read_text(encoding="utf-8")
        for anchor in (
            "task_home",
            "note_area",
            "research_root",
            "cycle_dir",
            "cycle_workspace",
            "benchmark_root",
            "legacy_research_root",
            "notes_dir",
            "notes_vault_root",
            "repo_root",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, paths)
        for boundary in (
            "不得硬编码",
            "不得推测",
            "不得猜测",
            "绝对路径",
            "库内相对路径",
            "Obsidian",
            "只读",
            "原始网络",
            "CSV",
            "NPZ",
            ".data`/`.pickle",
            "长 JSON rows",
            "运行事件",
            "计算脚本",
            "checkpoint",
            "Evidence",
        ):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, paths)

    @mock.patch.dict("os.environ", {"THEORYMASTER_COMPUTE_PYTHON": sys.executable})
    def test_new_and_resumed_threads_keep_identity_and_read_only_sandbox_boundaries(self):
        from theory_master.backends import CodexBackend

        for role in ("research_lead", "challenger"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp:
                sdk = SimpleNamespace(
                    Sandbox=SimpleNamespace(workspace_write="write", read_only="read")
                )
                thread = SimpleNamespace(id=f"{role}-identity-thread")
                client = mock.Mock()
                client.thread_start.return_value = thread
                client.thread_resume.return_value = thread
                backend = CodexBackend(Path(temp))
                backend.cycle_id = "identity-layer-test"

                with mock.patch.dict(sys.modules, {"openai_codex": sdk}), mock.patch.object(
                    backend, "_ensure_client", return_value=client
                ):
                    thread_id = backend.ensure_thread(role, None, "gpt-6-sol", "CodexHTTPS")
                    start_args = client.thread_start.call_args.kwargs
                    self.assertEqual(start_args["sandbox"], "read")
                    instructions = start_args["developer_instructions"]
                    block = identity_block(role)
                    self.assertEqual(instructions.count(block), 1)
                    for path in identity_files(role):
                        self.assertEqual(
                            instructions.count(path.read_text(encoding="utf-8").strip()), 1,
                            path.name,
                        )

                    # Simulate a restored remote thread after the local cache is dropped.
                    backend.drop_thread(thread_id)
                    resumed_id = backend.ensure_thread(
                        role, thread_id, "gpt-6-sol", "CodexHTTPS"
                    )
                    self.assertEqual(resumed_id, thread_id)
                    resume_args = client.thread_resume.call_args.kwargs
                    self.assertNotIn("developer_instructions", resume_args)
                    self.assertEqual(client.thread_start.call_count, 1)
                    self.assertEqual(client.thread_resume.call_count, 1)
                    self.assertEqual(start_args["sandbox"], "read")
                backend.close()


if __name__ == "__main__":
    unittest.main()
