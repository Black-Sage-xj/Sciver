from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from theory_master.contract_stages import _minimal_frontier_structure
from theory_master.hypothesis_dossier import (
    write_hypothesis_dossier, append_hypothesis_evidence,
)
from theory_master.workflow import Workflow
from theory_master.backend_fakes import FakeBackend


class _BriefLeadFake(FakeBackend):
    def invoke(self, role, prompt, thread_id, model, provider):
        tid, raw = super().invoke(role, prompt, thread_id, model, provider)
        if role == "research_lead" and "Current task: hypothesis_display" in prompt:
            return tid, json.dumps({
                "canvas_title": "短标题：网络能量机制",
                "canvas_claim": "网络能量差是否由冻结结构量解释",
                "canvas_conditions": "仅限冻结网络族和决定性检验范围",
            }, ensure_ascii=False)
        return tid, raw


class HypothesisDossierProjectionTest(unittest.TestCase):
    def test_frontier_candidates_cannot_bypass_post_lock_display_stage(self):
        candidate = {
            "hypothesis": "H", "scope": "S", "why_now": "W",
            "goal_advance": "G", "decisive_test": "T",
        }
        _minimal_frontier_structure({"candidates": [candidate]}, {})
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            _minimal_frontier_structure(
                {"candidates": [{**candidate, "canvas_title": "准确短标题"}]}, {}
            )

    def test_post_lock_display_writes_only_selected_h_copy(self):
        from tests.test_core import TheoryMasterTest

        class DisplayBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.display_prompts = []

            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "research_lead" and "Current task: hypothesis_display" in prompt:
                    self.display_prompts.append(prompt)
                    return self.ensure_thread(role, thread_id, model, provider), json.dumps({
                        "canvas_title": "独立检验下的网络假说",
                        "canvas_claim": "网络结构量能否预测目标能量差",
                        "canvas_conditions": "仅限冻结网络族与决定性检验范围",
                    }, ensure_ascii=False)
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = TheoryMasterTest().make(Path(temp), reworks=0)
            backend = DisplayBackend()
            state = Workflow(spec, backend).run()
            self.assertIn(state["status"], {"over", "completed"})
            self.assertEqual(len(backend.display_prompts), 1)
            prompt = backend.display_prompts[0]
            self.assertIn("frozen_hypothesis", prompt)
            self.assertNotIn('"canvas_access"', prompt)
            self.assertNotIn('"goal_advance"', prompt)
            self.assertEqual(state["hypothesis_display"]["status"], "generated")
            self.assertEqual(state["hypothesis_lock"]["hypothesis"], "smoke-h")
            self.assertEqual(state["hypothesis_lock"]["decisive_test"], "register deterministic smoke evidence")
            canvas = json.loads(spec.branch_canvas.read_text(encoding="utf-8"))
            h = next(node for node in canvas["nodes"] if node.get("id") == "H-0001")
            self.assertIn("独立检验下的网络假说", h["text"])
            self.assertIn("主张：网络结构量能否预测目标能量差", h["text"])
            self.assertIn("条件：仅限冻结网络族与决定性检验范围", h["text"])
            self.assertIn("状态：待检验", h["text"])
            dossier = (spec.notes_mirror_dir / "hypotheses/H-0001.md").read_text(encoding="utf-8")
            self.assertIn("smoke-h", dossier)
            self.assertIn("register deterministic smoke evidence", dossier)

    def test_hypothesis_display_failure_falls_back_without_scientific_stop(self):
        from tests.test_core import TheoryMasterTest

        class UnavailableDisplay(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "research_lead" and "Current task: hypothesis_display" in prompt:
                    raise RuntimeError("optional display service unavailable")
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = TheoryMasterTest().make(Path(temp), reworks=0)
            state = Workflow(spec, UnavailableDisplay(reworks=0)).run()
            self.assertIn(state["status"], {"over", "completed"})
            self.assertEqual(state["hypothesis_display"]["status"], "fallback")
            self.assertEqual(state["hypothesis_lock"]["hypothesis"], "smoke-h")
            canvas = json.loads(spec.branch_canvas.read_text(encoding="utf-8"))
            h = next(node for node in canvas["nodes"] if node.get("id") == "H-0001")
            self.assertIn("摘要暂不可用", h["text"])
            self.assertIn("状态：待检验", h["text"])
            self.assertLessEqual(len(h["text"]), 600)
            self.assertEqual(h["text"].count("smoke-h"), 1)  # title only; no full claim fallback
            dossier = (spec.notes_mirror_dir / "hypotheses/H-0001.md").read_text(encoding="utf-8")
            self.assertIn("register deterministic smoke evidence", dossier)

    def test_display_contract_rejects_scientific_field_edits(self):
        from theory_master.contract_stages import _hypothesis_display_structure
        good = {
            "canvas_title": "标题",
            "canvas_claim": "待解释的具体预测关系",
            "canvas_conditions": "冻结的适用范围和检验条件",
        }
        _hypothesis_display_structure(good, {})
        with self.assertRaisesRegex(ValueError, "exactly"):
            _hypothesis_display_structure({**good, "hypothesis": "changed!"}, {})
        with self.assertRaisesRegex(ValueError, "canvas_title"):
            _hypothesis_display_structure({**good, "canvas_title": "a" * 49}, {})
        with self.assertRaisesRegex(ValueError, "canvas_claim"):
            _hypothesis_display_structure({**good, "canvas_claim": "摘要暂不可用"}, {})
        with self.assertRaisesRegex(ValueError, "canvas_conditions"):
            _hypothesis_display_structure({**good, "canvas_conditions": "见档案"}, {})

    def test_exact_scientific_text_and_multiple_cycle_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            canvas = root / "exploration.canvas"
            canvas.write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
            args = dict(
                canvas_path=canvas, notes_root=root / "notes",
                hypothesis_id="H-0017", cycle_id="17",
                hypothesis="FULL H: a significant and strictly preserved scientific proposition",
                scope="S exact", why_now="WHY exact",
                goal_advance="G exact", decisive_test="TEST exact",
                display_title="H 的短标题", display_summary="摘要不等于科学证明",
                lineage_anchor_node_ids=["H-0002", "H-0003"],
                evolution_operator="specialize",
            )
            link = write_hypothesis_dossier(**args)
            self.assertEqual(link, "notes/hypotheses/H-0017")
            dossier = root / (link + ".md")
            before = dossier.read_text(encoding="utf-8")
            self.assertIn(args["hypothesis"], before)
            self.assertIn(args["decisive_test"], before)
            self.assertIn("上游锚点：H-0002、H-0003", before)
            self.assertIn("上游谱系：H-0002（specialize）、H-0003（specialize）", before)
            self.assertIn("SQLite 是科学图谱权威", before)
            write_hypothesis_dossier(**args)
            self.assertEqual(dossier.read_text(encoding="utf-8"), before)
            write_hypothesis_dossier(**{**args, "cycle_id": "18", "decisive_test": "TEST later"})
            after = dossier.read_text(encoding="utf-8")
            self.assertEqual(after.count("<!-- frozen-cycle:17 -->"), 1)
            self.assertEqual(after.count("<!-- frozen-cycle:18 -->"), 1)
            self.assertIn("TEST later", after)
            append_hypothesis_evidence(
                canvas_path=canvas, wikilink_stem=link, cycle_id="18",
                evidence_refs=["E-18-01", "E-18-02", "E-18-01"],
            )
            append_hypothesis_evidence(
                canvas_path=canvas, wikilink_stem=link, cycle_id="18",
                evidence_refs=["E-18-01", "E-18-02"],
            )
            self.assertEqual(dossier.read_text(encoding="utf-8").count("E-18-01"), 1)


    def test_existing_sdk_dossier_repairs_parent_list_and_authority_line_without_losing_notes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            canvas = root / "exploration.canvas"
            canvas.write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
            args = dict(
                canvas_path=canvas, notes_root=root / "notes", hypothesis_id="H-0020",
                cycle_id="20", hypothesis="Frozen scientific claim", scope="Exact scope",
                why_now="Reason", goal_advance="Goal", decisive_test="Frozen test",
                display_title="简短标题", display_summary="摘要暂不可用；完整命题与适用条件见档案。状态：待检验。",
                lineage_anchor_node_ids=["H-0002"], evolution_operator="derive",
            )
            link = write_hypothesis_dossier(**args)
            dossier = root / (link + ".md")
            text = dossier.read_text(encoding="utf-8")
            text = text.replace(
                "SQLite 是科学图谱权威，Canvas 仅为只读投影。",
                "科学裁决与活动状态以 Canvas/运行记录为准。",
            )
            text = text.replace("- 上游锚点：H-0002", "- 上游锚点：H-0002")
            text += "\nHuman annotation: retain this note.\n"
            dossier.write_text(text, encoding="utf-8")
            updated_args = {**args, "lineage_anchor_node_ids": ["H-0002", "H-0003"]}
            write_hypothesis_dossier(**updated_args)
            after = dossier.read_text(encoding="utf-8")
            self.assertIn("SQLite 是科学图谱权威", after)
            self.assertIn("上游锚点：H-0002、H-0003", after)
            self.assertIn("上游谱系：H-0002（derive）、H-0003（derive）", after)
            self.assertIn("Human annotation: retain this note.", after)

    def test_real_workflow_fake_backend_generates_compact_h_and_dossier(self):
        from tests.test_core import TheoryMasterTest
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = TheoryMasterTest().make(Path(temp), reworks=0)
            state = Workflow(spec, _BriefLeadFake(reworks=0)).run()
            self.assertIn(state["status"], {"over", "completed"})
            canvas = json.loads(spec.branch_canvas.read_text(encoding="utf-8"))
            hs = [n for n in canvas["nodes"] if "type: Hypothesis" in (n.get("text") or "")]
            self.assertEqual(len(hs), 1)
            text = hs[0]["text"]
            self.assertIn("# 短标题：网络能量机制", text)
            self.assertIn("主张：网络能量差是否由冻结结构量解释", text)
            self.assertIn("条件：仅限冻结网络族和决定性检验范围", text)
            self.assertIn("状态：待检验", text)
            self.assertIn("[[notes/hypotheses/H-0001]]", text)
            self.assertNotIn("**Evidence refs**:", text)
            self.assertNotIn("deterministic fixture is immediately executable", text)
            full = (spec.notes_mirror_dir / "hypotheses/H-0001.md").read_text(encoding="utf-8")
            self.assertIn("smoke-h", full)
            self.assertIn("deterministic fixture is immediately executable", full)
            self.assertIn("register deterministic smoke evidence", full)
            self.assertIn("Evidence 引用索引", full)
            self.assertIn("人类可读报告", full)
            self.assertIn("[[notes/1--", full)


if __name__ == "__main__":
    unittest.main()
