from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from theory_master.codex_backend import CodexBackend
from theory_master.identity import identity_block, identities_root
from theory_master.prompt_stages import stage_context
from theory_master.stage_registry import procedure_card_text, stage_card_text, stage_for


class JudgeLiteratureReporterPromptTest(unittest.TestCase):
    def test_judge_receives_optional_investigator_science_verbatim(self):
        for result in ({"numeric": 0.1, "bounds": [0, 0.1], "nested": {"exact": True}},
                       [1, 0, {"uncertainty": "unresolved"}], "raw science", 0, [], {}):
            with self.subTest(result=result):
                source = {"statement": "bounded qualified Finding", "scope": "test scope",
                          "limitations": "observational", "method": "induction over n",
                          "result": result, "evidence_id": "SDK-owned", "criterion_ids": ["SDK-owned"]}
                ctx = stage_context("judge", "terminal_assessment", {
                    "hypothesis_lock": {"hypothesis": "H", "scope": "S", "decisive_test": "T"},
                    "investigation_result": {"findings": [source]},
                })
                finding = ctx["investigation_result"]["findings"][0]
                self.assertEqual(finding["method"], source["method"])
                self.assertEqual(finding["result"], result)
                self.assertEqual(finding["scope"], "test scope")
                self.assertNotIn("criterion_ids", finding)
                self.assertNotIn("evidence_id", finding)

    def test_judge_no_fabricated_optional_fields_or_science_clipping(self):
        long_text = "Scientific argument. " * 1200
        projected = stage_context("judge", "terminal_assessment", {
            "hypothesis_lock": {"hypothesis": "H", "decisive_test": "T"},
            "investigation_result": {"findings": [
                {"statement": "S", "method": long_text, "result": {"untrimmed": long_text}},
                {"statement": "Another independent result"},
            ]},
        })
        rows = projected["investigation_result"]["findings"]
        self.assertEqual(rows[0]["method"], long_text)
        self.assertEqual(rows[0]["result"]["untrimmed"], long_text)
        self.assertEqual(rows[1], {"statement": "Another independent result"})

    def test_reporter_report_context_is_narrative_only(self):
        context = {
            "scientific_synthesis": {"conclusions": ["bounded result"], "open_questions": []},
            "evidence_scope_digest": [{"scope": "valid only in range"}],
            "branch_canvas": "/tmp/exploration.canvas",
            "canvas_focus": {"status": "retired"},
        }
        projected = stage_context("reporter", "report", context)
        self.assertIn("scientific_synthesis", projected)
        self.assertNotIn("agent_graph", projected)
        self.assertNotIn("canvas_access", projected)
        self.assertNotIn("canvas_focus", projected)
        self.assertNotIn("branch_canvas", projected)
        with self.assertRaisesRegex(ValueError, "no phase contract"):
            from theory_master.contracts import role_contract
            role_contract("reporter", phase="canvas_maintenance")

    def test_goal_completion_card_retains_gate_and_not_extra_procedure(self):
        card = stage_card_text("judge", "goal_completion")
        for token in ("Final Gate", "research_plan.md", "exploration.canvas", "not_passed",
                      "Do not produce a next-step plan"):
            self.assertIn(token, card)
        self.assertEqual(procedure_card_text("judge", "goal_completion"), "")

    def test_reporter_identity_source_matches_actual_runtime_resource(self):
        packaged = identities_root() / "reporter.md"
        source = Path(__file__).resolve().parents[1] / "identities" / "reporter.md"
        self.assertEqual(source.read_bytes(), packaged.read_bytes())
        self.assertIn("NPZ/CSV", identity_block("reporter"))

    def test_literature_novelty_is_actual_routable_stage(self):
        self.assertEqual(stage_for("literature", {}, {
            "literature_task": {"task_type": "novelty_check"},
        }), "novelty_check")
        import sys
        web_root = Path(__file__).resolve().parents[1] / "web"
        if str(web_root) not in sys.path:
            sys.path.insert(0, str(web_root))
        from web.tm_observer.stage_flow import DISPLAYED_AGENT_STAGES, stage_workflow_mermaid
        self.assertIn(("literature", "novelty_check"), DISPLAYED_AGENT_STAGES)
        diagram = stage_workflow_mermaid()
        self.assertIn('LEAD_LIT_KIND -- "novelty_check" --> literature__novelty_check', diagram)
        self.assertIn('literature__novelty_check -. "Lead detour result only"', diagram)

    def test_developer_input_collected_for_three_roles_without_llm(self):
        class FakeClient:
            def __init__(self):
                self.started = []
            def thread_start(self, **kwargs):
                self.started.append(kwargs)
                return SimpleNamespace(id="thread-" + str(len(self.started)))
        for role in ("judge", "literature", "reporter"):
            with self.subTest(role=role):
                backend = CodexBackend.__new__(CodexBackend)
                backend.cwd = Path(tempfile.gettempdir())
                backend.task_config_path = None
                backend.cycle_id = "mock-cycle"
                backend.client_account = None
                backend.threads = {}
                backend.thread_accounts = {}
                backend._new_thread_ids = set()
                backend._maybe_adjust_quota_account = lambda *_: None
                backend._thread_config = lambda *_: {}
                backend._compute_python = lambda: "/usr/bin/python3"
                client = FakeClient()
                backend._ensure_client = lambda: client
                seen = []
                backend.collector = SimpleNamespace(
                    on_agent_input=lambda **kw: seen.append(kw),
                    on_codex=lambda *args, **kwargs: None,
                )
                backend.ensure_thread(role, None, "mock-model", "mock-provider")
                self.assertEqual(len(client.started), 1)
                self.assertEqual(len(seen), 1)
                self.assertEqual(seen[0]["role"], role)
                self.assertEqual(seen[0]["input_kind"], "developer_instructions")
                self.assertEqual(seen[0]["stage"], "thread_initialization")
                self.assertEqual(
                    seen[0]["content"], client.started[0]["developer_instructions"],
                )
                self.assertIn("TheoryMaster Cycle: mock-cycle", seen[0]["content"])


if __name__ == "__main__":
    unittest.main()
