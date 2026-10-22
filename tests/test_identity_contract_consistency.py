from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path

from theory_master.contracts import PHASE_CONTRACTS, role_contract
from theory_master.identity import ROLES, identity_files, identities_root
from theory_master.prompt_stages import STAGE_CARDS, STAGE_CONTEXT_FIELDS, stage_card_text
from theory_master.stage_registry import procedure_card_text
from theory_master.reporting import ReportingMixin
from theory_master.skills import developer_instructions, role_skills, skills_root, write_rule


class IdentityContractConsistencyTest(unittest.TestCase):
    def test_authority_matrix_exists_and_names_all_roles(self):
        text = (identities_root() / "_authority.md").read_text(encoding="utf-8")
        for role in ("Research Lead", "Challenger", "Investigator", "Literature", "Judge", "Reporter", "SDK"):
            self.assertIn(role, text)
        self.assertIn("Reporter narrative-only", text)

    def test_reporter_current_contract_is_narrative_only(self):
        current = role_contract("reporter", phase="report")
        self.assertEqual(current.version, 3)
        self.assertEqual(set(current.prompt_schema), {"title", "summary"})
        current.validate({"title": "T", "summary": "S"}, {})
        with self.assertRaisesRegex(ValueError, "cannot create candidates"):
            current.validate({"title": "T", "summary": "S", "candidates": []}, {})
        with self.assertRaisesRegex(ValueError, "no contract version 1"):
            role_contract("reporter", version=1, phase="report")
        source = inspect.getsource(ReportingMixin._write_cycle_report)
        self.assertNotIn('report.get("candidates")', source)

    def test_reporter_has_no_canvas_stage_or_canvas_skill(self):
        identity = (identities_root() / "reporter.md").read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no phase contract"):
            role_contract("reporter", phase="canvas_maintenance")
        self.assertEqual(tuple(skill.directory for skill in role_skills("reporter")), ("report-writing",))
        self.assertNotIn("canvas-maintenance", identity)
        self.assertNotIn("canvas_access", identity)
        report_skill = (skills_root() / "report-writing" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Scholarly prose discipline", report_skill)
        self.assertIn("Do not manufacture missing comparisons", report_skill)
        self.assertIn("report-writing skill", stage_card_text("reporter", "report"))
        self.assertNotIn("canvas_maintenance", STAGE_CARDS)

    def test_reporter_writing_skill_keeps_academic_copy_rules(self):
        report = (skills_root() / "report-writing" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Scholarly prose discipline", report)
        self.assertIn("Do not manufacture missing comparisons", report)
        self.assertIn("Match an author or venue style", report)
        self.assertFalse((Path(__file__).resolve().parents[1] / "skills").exists())
        self.assertTrue((skills_root() / "report-writing" / "SKILL.md").is_file())

    def test_runtime_guards_are_developer_instructions_not_turn_prompt(self):
        from theory_master import codex_backend, workflow_invocation

        self.assertNotIn("PROCESS_CONTROL_GUARD", inspect.getsource(workflow_invocation))
        self.assertIn("writer_guard", inspect.getsource(codex_backend.CodexBackend.ensure_thread))

    def test_current_roles_do_not_restore_legacy_specialist_protocol(self):
        challenger = role_contract("challenger", phase="candidate_review")
        investigator = role_contract("investigator", phase="investigate")
        self.assertNotIn("critical_issues", str(challenger.prompt_schema))
        self.assertNotIn("critical_issue_updates", str(investigator.prompt_schema))
        self.assertNotIn("requested_next_capability", str(investigator.prompt_schema))

    def test_research_lead_frontier_context_includes_final_gate_but_not_required_context(self):
        fields = STAGE_CONTEXT_FIELDS[("research_lead", "frontier_proposal")]
        self.assertIn("goal", fields)
        self.assertIn("research_contract", fields)
        self.assertIn("final_gate", fields)
        self.assertNotIn("required_context", fields)

    def test_challenger_role_core_matches_candidate_review_navigation_contract(self):
        identity = (identities_root() / "challenger.md").read_text(encoding="utf-8")
        self.assertEqual(
            STAGE_CONTEXT_FIELDS[("challenger", "candidate_review")],
            ("goal", "research_contract", "final_gate", "candidate", "agent_graph", "agent_graph_reuse", "science_graph_tools", "workspace_access"),
        )
        self.assertIn("`candidate`", identity)
        self.assertIn("`agent_graph`", identity)
        self.assertNotIn("`canvas_access`", identity)
        self.assertIn("`workspace_access`", identity)
        self.assertNotIn("`canvas_focus`", identity)
        self.assertNotIn("`linked_reports`", identity)

    def test_investigator_core_uses_hypothesis_and_decisive_test_not_hidden_criteria(self):
        identity = (identities_root() / "investigator.md").read_text(encoding="utf-8")
        self.assertIn("decisive_test", identity)
        self.assertNotIn("cycle milestone", identity)
        self.assertNotIn("冻结 criterion", identity)
        self.assertNotIn("criterion", identity.lower())
        for stage in ("investigate", "integrate_literature", "judge_rework"):
            with self.subTest(stage=stage):
                fields = STAGE_CONTEXT_FIELDS[("investigator", stage)]
                self.assertIn("frozen_objective", fields)
                self.assertFalse(any("criter" in field for field in fields))
                card = stage_card_text("investigator", stage).lower()
                self.assertNotIn("hypothesis/criteria", card)
                self.assertNotIn("hypothesis and criteria", card)
                # Current stages may mention criterion-linked findings as
                # evidence bookkeeping, but must not impose an independent
                # hidden hypothesis/criteria contract on Investigator.

    def test_investigator_falsification_is_valid_research_not_a_target_to_avoid(self):
        """Role Core explicitly protects valid negative evidence and Judge authority."""
        runtime = (identities_root() / "investigator.md").read_text(encoding="utf-8")
        source = (Path(__file__).resolve().parents[1] / "identities" / "investigator.md").read_text(encoding="utf-8")
        self.assertEqual(source, runtime)
        rule = "可靠的反证与支持性证据具有同等科研价值"
        self.assertEqual(runtime.count(rule), 1)
        self.assertIn("而非证明其正确", runtime)
        self.assertIn("供 Judge 独立裁决", runtime)
        self.assertIn("不得为了得到 `supported`", runtime)
        developer = developer_instructions("investigator")
        self.assertEqual(developer.count(rule), 1)
        self.assertIn("冻结的 hypothesis、scope、decisive_test", developer)
        self.assertNotIn("新的完成标准", runtime)
        # Stage/Native Goal alone determine when the frozen science is ready
        # for independent adjudication; this Role Core adds no new exit gate.
        from theory_master.native_goal import investigator_goal
        objective = {"hypothesis": "H", "scope": "S", "decisive_test": "T"}
        goal = investigator_goal("investigate", {"frozen_objective": objective}, layout="v2")
        self.assertIn("Support, falsification or true inconclusive", goal)

    def test_literature_cross_mode_rules_live_once_in_role_core(self):
        identity = (identities_root() / "literature.md").read_text(encoding="utf-8")
        self.assertIn("`search_target`", identity)
        self.assertIn("`scholarly_retrieval_root`", identity)
        self.assertIn("外部 transport 归 SDK", identity)
        self.assertIn("不承担普通本地 retrieval", identity)
        self.assertNotIn("Core modes", identity)
        self.assertNotIn("cycle_workspace/literature", identity)
        for stage in ("landscape", "targeted_source", "novelty_check"):
            with self.subTest(stage=stage):
                card = stage_card_text("literature", stage)
                for token in ("scholarly_retrieval_root", "network sockets", "navigation entry point", "local project"):
                    self.assertNotIn(token, card)
        self.assertIn("source_lock", stage_card_text("literature", "targeted_source"))
        self.assertIn("novelty-check skill", stage_card_text("literature", "novelty_check"))
        self.assertIn("research-lit skill", stage_card_text("literature", "landscape"))

    def test_judge_role_core_is_stage_neutral_across_terminal_and_goal_gates(self):
        identity = (identities_root() / "judge.md").read_text(encoding="utf-8")
        self.assertIn("当前 Stage Card 决定裁决层级", identity)
        self.assertIn("`terminal_assessment`", identity)
        self.assertIn("`goal_completion`", identity)
        self.assertNotIn("独立 Evidence adjudicator", identity)
        self.assertNotIn("默认只裁决当前冻结 Main Hypothesis", identity)
        self.assertIn("frozen decisive test", stage_card_text("judge", "terminal_assessment"))
        self.assertIn("task-level Goal Contract", stage_card_text("judge", "goal_completion"))

    def test_research_lead_thread_init_contains_role_core_not_stage_manuals(self):
        names = [path.name for path in identity_files("research_lead")]
        self.assertEqual(names, ["research_lead.md", "_invocation_handoff.md", "_paths.md"])
        self.assertTrue((identities_root() / "_research_lead_protocol.md").is_file())
        self.assertTrue((identities_root() / "_research_lead_reading.md").is_file())
        text = developer_instructions("research_lead")
        self.assertNotIn("replacement_candidates[{", text)
        self.assertNotIn("blocking_issues", text)
        self.assertNotIn("Procedure for this stage only", text)
        self.assertNotIn("_research_lead_protocol", text)
        self.assertIn("candidate frontier", text)
        self.assertIn("权威工作流输入", text)
        self.assertIn("不要重新发现 SDK 已经提供的 workflow/runtime state", text)

    def test_stage_cards_cover_all_current_runtime_phase_contracts(self):
        for (role, stage), contract in PHASE_CONTRACTS.items():
            with self.subTest(role=role, stage=stage, contract=contract.ref):
                self.assertIn((role, stage), STAGE_CARDS)
                card = stage_card_text(role, stage)
                for label in ("Stage:", "Current objective:", "May do:", "Must not:", "Exit / handoff:"):
                    self.assertIn(label, card)

    def test_investigator_thread_init_is_minimal_role_core_plus_handoff(self):
        names = [path.name for path in identity_files("investigator")]
        self.assertEqual(names, ["investigator.md", "_investigator_handoff.md"])
        developer = developer_instructions("investigator")
        self.assertNotIn("investigation_attempt", developer)
        self.assertNotIn("branch_canvas", developer)
        self.assertIn("没有提前列出的文件清单不限制科学读取", developer)
        self.assertIn("Task scientific read root", developer)
        self.assertIn("空的 Cycle Evidence Registry", developer)
        self.assertIn("本 Cycle write root", developer)

    def test_investigator_domain_policy_is_on_demand_skill_not_role_core(self):
        identity = (identities_root() / "investigator.md").read_text(encoding="utf-8")
        for token in ("ss_fit", "SLURM_script", "parallel_capacity.py", ".pickle"):
            self.assertNotIn(token, identity)
        refs = {ref.directory: ref for ref in role_skills("investigator")}
        self.assertNotIn("polymer-numerical-policy", refs)
        self.assertNotIn("polymer-numerical-policy", developer_instructions("investigator"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "theory-master.config.json"
            config.write_text(json.dumps({"task_skills": {"investigator": ["polymer-numerical-policy"]}}), encoding="utf-8")
            local = root / ".theory-master" / "skills" / "polymer-numerical-policy"
            local.mkdir(parents=True)
            (local / "SKILL.md").write_text(
                "---\nname: polymer-numerical-policy\ndescription: task-local polymer policy\n---\n\nss_fit, SLURM_script, .pickle\n",
                encoding="utf-8",
            )
            refs = {ref.directory: ref for ref in role_skills("investigator", task_config_path=config)}
            self.assertEqual(refs["polymer-numerical-policy"].scope, "task")
            policy = refs["polymer-numerical-policy"].skill_md.read_text(encoding="utf-8")
            for token in ("ss_fit", "SLURM_script", ".pickle"):
                self.assertIn(token, policy)
            developer = developer_instructions("investigator", task_config_path=config)
            self.assertIn("polymer-numerical-policy", developer)
            self.assertNotIn("用户未指定窗口时拟合", developer)

    def test_sqlite_frontier_authority_is_documented_without_canvas_read_rules(self):
        lead_card = stage_card_text("research_lead", "frontier_maintenance")
        lead_procedure = procedure_card_text("research_lead", "frontier_maintenance")
        self.assertIn("independent of graph leaves", lead_card)
        self.assertIn("active/parked/closed", lead_card)
        self.assertIn("science_graph.sqlite3", lead_procedure)
        self.assertFalse((identities_root() / "_canvas_read.md").exists())
        self.assertFalse((identities_root() / "_canvas_renderer_policy.md").exists())
        for role in ROLES:
            self.assertFalse(any(path.name in {"_canvas_read.md", "_canvas_renderer_policy.md"}
                                 for path in identity_files(role)))


if __name__ == "__main__":
    unittest.main()
