"""SQLite-native ResearchFocus lifecycle and informational events."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.research_focus import inspect_preferred_focus, check_preferred_research_focus
from theory_master.observability.incident_registry import _rows
from theory_master.science_graph_store import ScienceGraphStore


def graph_view(*, root_status="active", child_status=None):
    nodes = [
        {"id": "H-0001", "type": "Hypothesis", "frontiers": [
            {"status": root_status, "priority": "preferred" if root_status == "active" else None}]},
        {"id": "focus-1", "type": "External", "content_type": "ResearchFocus",
         "content": "target_node_id: H-0001\nsource: human\n"},
    ]
    edges = []
    if child_status is not None:
        nodes.append({"id": "H-0002", "type": "Hypothesis", "frontiers": [
            {"status": child_status, "priority": None}]})
        edges.append({"source_hypothesis": "H-0001", "target_hypothesis": "H-0002",
                      "evolution_operator": "derive"})
    return {"nodes": nodes, "edges": edges}


class _GraphViewStore:
    def __init__(self, graph):
        self._graph = graph

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def graph_view(self):
        return self._graph


class PreferredFocusLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.research_root = self.root / "research"
        self.research_root.mkdir()
        self.cycle = self.research_root / "1"
        self.cycle.mkdir()
        self.database = self.root / "science_graph.sqlite3"
        self.graph = graph_view()

        class WorkflowStub:
            def __init__(self, outer):
                self.spec = SimpleNamespace(research_root=outer.research_root, master_id=1)
                self.cycle_dir = outer.cycle

            def _graph_database_path(self):
                return outer.database

        outer = self
        self.workflow = WorkflowStub(self)

    def _check(self):
        with patch("theory_master.science_graph_store.ScienceGraphStore",
                   return_value=_GraphViewStore(self.graph)):
            return check_preferred_research_focus(self.workflow)

    def test_active_preferred_focus_registers_once_without_halt(self):
        created, exhausted = inspect_preferred_focus(self.graph, set())
        self.assertEqual(created, ["focus-1"])
        self.assertFalse(exhausted)
        self.assertIsNone(self._check())
        self.assertIsNone(self._check())
        rows = _rows(self.research_root)
        self.assertFalse(any(row.get("kind") == "preferred_focus_started" for row in rows))
        self.assertFalse(any(row.get("kind") == "preferred_focus_exhausted" for row in rows))

    def test_focus_exhausts_only_when_all_descendant_hypotheses_are_closed(self):
        self.graph = graph_view(root_status="active", child_status="active")
        self.assertIsNone(self._check())  # register the focus before closing its lineage
        self.graph = graph_view(root_status="closed", child_status="active")
        _, exhausted = inspect_preferred_focus(self.graph, {"focus-1"})
        self.assertFalse(exhausted)
        self.graph = graph_view(root_status="closed", child_status="closed")
        _, exhausted = inspect_preferred_focus(self.graph, {"focus-1"})
        self.assertEqual(exhausted[0]["tips"], ["H-0002"])
        self.assertIsNone(self._check())
        self.assertIsNone(self._check())
        self.assertFalse(any(row.get("kind") == "preferred_focus_exhausted"
                             for row in _rows(self.research_root)))
        self.assertFalse(any(row.get("kind") == "preferred_focus_exhausted"
                             for row in _rows(self.cycle)))

    def test_frontier_priority_is_owned_by_sqlite_and_cleared_on_close(self):
        with ScienceGraphStore(self.database) as store:
            store.add_hypothesis("H-0001", title="Focus H", summary="Claim.")
            store.set_frontier(branch_id="frontier:H-0001", hypothesis_id="H-0001",
                               status="active", preferred=True, reason="human preference")
            node = next(row for row in store.graph_view()["nodes"] if row["id"] == "H-0001")
            self.assertEqual(node["frontiers"][0]["priority"], "preferred")
            store.set_frontier(branch_id="frontier:H-0001", hypothesis_id="H-0001",
                               status="closed", preferred=True, reason="focus exhausted")
            node = next(row for row in store.graph_view()["nodes"] if row["id"] == "H-0001")
            self.assertEqual(node["frontiers"][0]["status"], "closed")
            self.assertIsNone(node["frontiers"][0]["priority"])

    def test_focus_does_not_veto_next_cycle_or_next_run(self):
        from theory_master.workflow import Workflow
        self.assertTrue(Workflow._can_continue_cycle_budget(
            {"status": "over", "invocation_stop_reason": "preferred_focus_exhausted"}))
        self.assertFalse(Workflow._can_continue_cycle_budget(
            {"status": "over", "goal_completion": {"verdict": "passed"}}))
        self.assertFalse(Workflow._can_continue_cycle_budget({"status": "needs_attention"}))


if __name__ == "__main__":
    unittest.main()
