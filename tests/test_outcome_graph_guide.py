"""Offline regressions for faithful guides, independent reports and legacy recovery."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.canvas_projection import CanvasProjectionMixin
from theory_master.contracts import role_contract
from theory_master.cycle_outcome_documents import render_outcome_document
from theory_master.hypothesis_display import compose_hypothesis_summary
from theory_master.outcome_guide import GRAPH_SUMMARY_LIMIT, graph_summary_text
from theory_master.prompt_stages import build_initial_prompt, stage_context
from theory_master.reporting import ReportingMixin
from theory_master.science_canvas_export import canvas_projection
from theory_master.science_graph_store import ScienceGraphStore


GUIDE = {
    "conclusion": "两组的均值界仍未定，修正未显示可辨收益。",
    "basis": "两组区间 [0.059,0.326]、[0.093,0.307] 均跨过冻结的 0.3 门槛。",
    "scope": "仅限历史暴露的 12 个独立样本，回顾性分析。",
    "open_question": "该测度内两组均值是否均不超过 0.3，当前区间无法区分。",
}


def test_fresh_contract_requires_a_separate_guide_and_rejects_authority_fields():
    contract = role_contract("reporter", phase="report")
    report = {"title": "The bounded mean remains unresolved", "summary": "Full report.",
              "graph_summary": GUIDE}
    contract.validate(report, {})
    contract.validate({**report, "graph_summary": {**GUIDE, "open_question": ""}}, {})
    for guide in (None, {}, {**GUIDE, "scope": ""}, {**GUIDE, "basis": []},
                  {**GUIDE, "verdict": "supported"},
                  {**GUIDE, "basis": "x" * (GRAPH_SUMMARY_LIMIT + 1)}):
        with pytest.raises(ValueError, match="graph_summary"):
            contract.validate({**report, "graph_summary": guide}, {})
    for field in ("frontier_status", "verdict", "hypothesis_id"):
        with pytest.raises(ValueError, match="unsupported fields"):
            contract.validate({**report, field: "unsupported"}, {})


def test_assembled_prompt_preserves_local_gap_origin_and_report_authority():
    context = stage_context("reporter", "report", {
        "branch": {"hypothesis": "Both means are bounded.", "decisive_test": "Compare both intervals."},
        "judge": {"verdict": "open", "rationale": "Both intervals straddle the threshold."},
        "scientific_synthesis": {"conclusions": ["The means remain unresolved."]},
        "evidence_manifest": {"records": [{"evidence_id": "E-1", "adjudicable": True,
            "summary": "Frozen intervals", "freshness": "historical_reanalysis", "origin_cycle": 1}]},
    })
    contract = role_contract("reporter", phase="report")
    prompt = build_initial_prompt(prefix="Cycle 2", role="reporter", stage="report",
                                  contract_ref=contract.ref, contract_schema=contract.prompt_projection(),
                                  context=context, write_rule="No direct file writes.")
    for phrase in ("graph_summary", "not a report prefix", "local unresolved gap",
                   "propose the next frontier", "historical_reanalysis", "origin_cycle"):
        assert phrase in prompt
    lead = role_contract("research_lead", phase="hypothesis_display").prompt_projection()
    assert "discriminating scientific difference" in lead
    assert "do not repeat the full motivation or invent novelty" in lead


@pytest.mark.parametrize("verdict", ["supported", "rejected", "open"])
def test_store_and_projections_keep_guide_separate_from_report_and_verdict(tmp_path: Path, verdict: str):
    database = tmp_path / "science_graph.sqlite3"
    report = "Background before the result. " * 60 + "Full report remains unchanged."
    guide = copy.deepcopy(GUIDE)
    if verdict == "supported":
        guide.update(conclusion="两组均值界在冻结范围内获支持。",
                     basis="两组区间上界 0.27、0.28 均低于冻结的 0.3 门槛。", open_question="")
    elif verdict == "rejected":
        guide.update(conclusion="至少一组均值超过冻结界限，合取命题被反驳。",
                     basis="一组区间下界 0.32 高于冻结的 0.3 门槛。", open_question="")
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Specific mean bound", summary=compose_hypothesis_summary(
            "Both group means satisfy the frozen bound", "The historical sample measure"))
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
        record = dict(outcome_id="cycle-outcome-1", hypothesis_id="H-0001", cycle_id="1",
                      verdict=verdict, summary=report, graph_summary=guide, record_only=True,
                      open_questions=["Generic task-wide question, not the guide's local gap"],
                      judge_rationale="The formal Judge rationale.", report_ref="notes/report.md")
        store.append_outcome(**record)
        raw = store.db.execute("select source_json from cycle_evaluations").fetchone()[0]
        store.append_outcome(**record)
        assert store.db.execute("select source_json from cycle_evaluations").fetchone()[0] == raw
        assert store.db.execute("select scientific_status from hypotheses").fetchone()[0] == "none"
        with pytest.raises(ValueError, match="replay conflicts"):
            store.append_outcome(**{**record, "graph_summary": {**guide, "basis": "Changed basis"}})
        stored = store.cycle_evaluation("1")
        assert stored["summary"] == report
        assert stored["graph_summary"] == guide
        assert stored["verdict"] == verdict
        focus = store.agent_view("H-0001")["focus_details"][0]["recent_outcomes"][0]
        active = store.agent_view()["current_frontiers"][0]["latest_outcome"]
        goal = store.agent_goal_completion_view()["hypotheses"][0]["recent_cycle_outcomes"][0]
        detail = store.agent_hypothesis_detail("H-0001")["cycle_outcomes"][0]
        for projection in (focus, active, goal, detail, store.agent_cycle_detail("1")):
            assert projection["graph_summary"] == guide
        assert store.agent_search(guide["conclusion"])["results"][0]["id"] == "H-0001"
        node = canvas_projection(store)["nodes"][0]
        assert "整体 H 尚未登记裁决" in node["text"]
        assert "状态：待检验" not in node["text"]
        assert "Generic task-wide question" not in node["text"]
        for value in guide.values():
            assert value in node["text"]
        assert "Background before the result" not in node["text"]
        assert node["outcome"]["summary"] == report
        document = render_outcome_document(stored)
        assert graph_summary_text(stored) in document
        assert report in document


@pytest.mark.parametrize("status", ["active", "parked", "closed"])
def test_all_frontier_reasons_and_missing_reasons_are_visible(tmp_path: Path, status: str):
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as store:
        store.add_hypothesis("H-0001", title="A concrete claim")
        reason = "The frozen local question is resolved; preserve it as a source."
        store.set_frontier(branch_id="f", hypothesis_id="H-0001", status=status, reason=reason)
        assert reason in canvas_projection(store)["nodes"][0]["text"]
        store.set_frontier(branch_id="f", hypothesis_id="H-0001", status=status, reason="")
        assert "原因未记录" in canvas_projection(store)["nodes"][0]["text"]


def test_legacy_outcomes_replay_without_new_fields_or_rewriting_sources(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Legacy claim")
        record = dict(outcome_id="old", hypothesis_id="H-0001", cycle_id="1", verdict="open",
                      summary="Original legacy report", record_only=True)
        store.append_outcome(**record)
        raw = store.db.execute("select source_json from cycle_evaluations").fetchone()[0]
        for _ in range(2):
            store.append_outcome(**record)
            canvas_projection(store)
        assert store.db.execute("select source_json from cycle_evaluations").fetchone()[0] == raw
        assert "graph_summary" not in json.loads(raw)
        assert store.agent_view("H-0001")["focus_details"][0]["recent_outcomes"][0]["result"] == record["summary"]


@pytest.mark.parametrize("legacy", [False, True])
def test_cached_report_to_sqlite_commit_keeps_full_body_and_never_calls_model(tmp_path: Path, legacy: bool):
    class Reporter(ReportingMixin, CanvasProjectionMixin):
        def _graph_database_path(self):
            return database

        def _path_anchors(self):
            return {"repo_root": str(tmp_path)}

        def _evidence_context(self):
            return {"evidence_manifest": {"records": []}}

        def _call(self, *args, **kwargs):
            raise AssertionError("Cached reports must not trigger a model call")

        def _emit(self, *args, **kwargs):
            pass

    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Frozen question")
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
    reporter = Reporter()
    reporter.cycle_dir = tmp_path / "research/1"
    (reporter.cycle_dir / "checkpoints").mkdir(parents=True)
    reporter.notes_dir = tmp_path / "notes"
    reporter.branch_canvas = None
    reporter.spec = SimpleNamespace(notes_mirror_dir=None)
    report = {"title": "Frozen mean remains unresolved", "summary": "Complete report with decisive intervals."}
    if not legacy:
        report["graph_summary"] = copy.deepcopy(GUIDE)
    state = {"role_outputs": {"reporter": report}, "last_seq": 1,
             "hypothesis_lock": {"id": "H-0001", "hypothesis": "Frozen question"}}
    branch = {"title": "Frozen question", "hypothesis_ref": "H-0001", "decisive_test": "Frozen interval test"}
    reporter._write_cycle_report(state, branch, {"verdict": "open", "rationale": "Intervals straddle threshold"},
                                 "Frozen question", "Both means are bounded", "open")
    assert report["summary"] in Path(branch["note_path"]).read_text()
    assert ("graph_summary" in state["cycle_outcome_record"]) is not legacy
    reporter._commit_cycle_science(state, database, tmp_path / "exploration.canvas", state["cycle_outcome_record"], None)
    with ScienceGraphStore(database, readonly=True) as store:
        outcome = store.cycle_evaluation("1")
        assert outcome["summary"] == report["summary"]
        assert outcome["verdict"] == "open"
        assert ("graph_summary" in outcome) is not legacy
        if not legacy:
            assert outcome["graph_summary"] == GUIDE
