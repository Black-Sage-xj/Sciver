"""Deterministic tests for the production Lead -> SQLite Agent graph path."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from theory_master.prompt_stages import stage_context
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.workflow_invocation import InvocationMixin


class _GraphResolver:
    def __init__(self, graph_path: Path | None):
        self.graph_path = graph_path
        self.spec = SimpleNamespace(branch_anchor_id="")
        self.worker_focus_node_id = ""

    def _graph_database_path(self):
        return self.graph_path


def test_production_lead_context_uses_compact_sqlite_view_not_canvas_path(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Current focus", summary="A focused claim.")
        store.add_hypothesis("H-0002", title="Related claim", summary="A related claim.")
        store.append_outcome(
            outcome_id="cycle-outcome-1", hypothesis_id="H-0001", cycle_id="1",
            verdict="open", summary="The local test remains unresolved.",
        )
        store.add_evolution(
            edge_id="lineage-1", source="H-0001", target="H-0002",
            outcome_id="cycle-outcome-1", operator="derive",
        )

    resolver = _GraphResolver(database)
    graph = InvocationMixin._agent_graph_for_stage(
        resolver, "research_lead", "frontier_proposal",
        {"entry_hypothesis_id": "H-0001", "branch_canvas": str(tmp_path / "exploration.canvas")},
    )
    assert graph is not None
    assert graph["entry_hypothesis_id"] == "H-0001"
    assert graph["lineage"] == [["H-0001", "H-0002", "derive", "cycle-outcome-1"]]

    projected = stage_context("research_lead", "frontier_proposal", {
        "goal": "G", "research_contract": "C", "agent_graph": graph,
        "branch_canvas": str(tmp_path / "exploration.canvas"),
    })
    assert projected["agent_graph"] == graph
    assert "canvas_access" not in projected
    assert "exploration.canvas" not in str(projected)

    reuse = {"schema": graph["schema"], "entry_hypothesis_id": "H-0001"}
    continuation = stage_context("research_lead", "frontier_proposal", {
        "goal": "G", "research_contract": "C", "agent_graph_reuse": reuse,
        "branch_canvas": str(tmp_path / "exploration.canvas"),
    }, continuation=True)
    assert continuation["agent_graph_reuse"] == reuse
    assert "canvas_access" not in continuation


def test_missing_sqlite_graph_path_fails_closed(tmp_path: Path):
    import pytest
    resolver = _GraphResolver(None)
    with pytest.raises(RuntimeError, match="SQLite graph database path is not configured"):
        InvocationMixin._agent_graph_for_stage(
            resolver, "research_lead", "frontier_proposal", {
                "entry_hypothesis_id": "H-0001", "branch_canvas": str(tmp_path / "exploration.canvas")
            }
        )


def test_other_lead_stages_do_not_get_full_graph_by_default(tmp_path: Path):
    resolver = _GraphResolver(tmp_path / "science_graph.sqlite3")
    assert InvocationMixin._agent_graph_for_stage(
        resolver, "research_lead", "hypothesis_display", {}
    ) is None


def test_challenger_and_judge_get_role_specific_sqlite_views(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Source H", summary="Source claim.")
        store.add_hypothesis("H-0002", title="Derived H", summary="Derived claim.")
        store.append_outcome(
            outcome_id="cycle-outcome-1", hypothesis_id="H-0001", cycle_id="1",
            verdict="open", summary="The frozen local test remains unresolved.",
            decisive_test="Compare the predicted response.",
        )
        store.add_evolution(
            edge_id="lineage-1", source="H-0001", target="H-0002",
            outcome_id="cycle-outcome-1", operator="specialize",
        )

    resolver = _GraphResolver(database)
    canvas_path = str(tmp_path / "exploration.canvas")
    candidates = [{
        "id": "cand-1", "hypothesis": "A distinct claim.",
        "lineage_anchor_node_ids": ["H-0001"],
    }]
    challenger_graph = InvocationMixin._agent_graph_for_stage(
        resolver, "challenger", "candidate_review", {"candidates": candidates},
    )
    challenger = stage_context("challenger", "candidate_review", {
        "goal": "G", "research_contract": "C", "candidate": candidates,
        "candidates": candidates, "agent_graph": challenger_graph,
        "branch_canvas": canvas_path,
    })
    assert challenger["agent_graph"]["schema"] == "theorymaster/agent-candidate-review-graph/v1"
    assert challenger["agent_graph"]["source_hypotheses"][0]["id"] == "H-0001"
    assert "canvas_access" not in challenger
    assert canvas_path not in str(challenger)

    judge_graph = InvocationMixin._agent_graph_for_stage(
        resolver, "judge", "goal_completion", {"research_plan_path": str(tmp_path / "research_plan.md")},
    )
    judge = stage_context("judge", "goal_completion", {
        "research_plan_path": str(tmp_path / "research_plan.md"),
        "branch_canvas": canvas_path, "agent_graph": judge_graph,
    })
    assert judge["agent_graph"]["schema"] == "theorymaster/agent-goal-completion-graph/v1"
    assert len(judge["agent_graph"]["hypotheses"]) == 2
    assert "research_plan_access" in judge
    assert "canvas_access" not in judge
    assert canvas_path not in str(judge)

    from theory_master.contracts import role_contract
    import pytest
    with pytest.raises(ValueError, match="no phase contract"):
        role_contract("reporter", phase="canvas_maintenance")
    reporter = stage_context("reporter", "report", {
        "scientific_synthesis": {"conclusions": ["bounded result"], "open_questions": []},
        "agent_graph": {"should_not": "be exposed to the narrative-only Reporter"},
        "canvas_focus": {"should_not": "be exposed"},
        "branch_canvas": canvas_path,
    })
    assert "agent_graph" not in reporter
    assert "canvas_access" not in reporter
    assert "canvas_focus" not in reporter
    assert canvas_path not in str(reporter)


def test_hypothesis_detail_does_not_expose_placeholder_whole_h_open(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="No assessment yet", summary="Claim without a whole-H verdict.")
        detail = store.agent_hypothesis_detail("H-0001")
    assert detail["whole_h_assessment"] == {}


def test_science_graph_tool_context_reaches_lead_frontiers_and_challenger_review(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Focus", summary="Claim.")
    tools = {
        "database_path": str(database),
        "command_prefix": f"python -m theory_master.science_graph_tools --database {database}",
        "operations": {"hypothesis_detail": "hypothesis-detail <H-ID>", "search": "search <query>"},
        "access": "read_only",
        "detail_content_limit_chars": 12000,
    }
    lead = stage_context("research_lead", "frontier_proposal", {
        "goal": "G", "research_contract": "C", "agent_graph": {"schema": "test"},
        "science_graph_tools": tools,
    })
    assert lead["science_graph_tools"]["access"] == "read_only"
    assert str(database) in lead["science_graph_tools"]["command_prefix"]
    maintenance = stage_context("research_lead", "frontier_maintenance", {
        "goal": "G", "agent_graph": {"schema": "test"}, "science_graph_tools": tools,
    })
    assert "science_graph_tools" in maintenance
    assert "science_graph_tools" not in stage_context("research_lead", "hypothesis_display", {
        "frozen_hypothesis": {"hypothesis": "claim"}, "science_graph_tools": tools,
    })
    challenger = stage_context("challenger", "candidate_review", {
        "goal": "G", "candidate": [], "agent_graph": {"schema": "scoped"},
        "science_graph_tools": tools,
    })
    assert challenger["science_graph_tools"]["operations"]["search"] == "search <query>"
    assert "global_view" not in challenger["science_graph_tools"]["operations"]
