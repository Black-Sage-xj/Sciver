from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tm_observer.change_requests import create_request, load_change_requests, validate_request
from tm_observer.current_flow import _after_flow_mermaid, current_flow_body, flow_node_ids
from tm_observer.web import _tm_system_requests


def request_fields(*, affected_flow_nodes: list[str] | None = None) -> dict[str, object]:
    return {
        "title": "Challenger context reduction",
        "status": "proposed",
        "summary": "Reduce Challenger seed context and preserve guided exploration.",
        "change_types": ["prompt", "routing"],
        "sdk_design_refs": ["tool/theory_master/design/SDK设计文档.md#qualification"],
        "problem": {
            "owner": "Challenger",
            "stage": "candidate_review",
            "issue": "The seed context duplicates SDK state and obscures the candidate audit.",
        },
        "design_motivation": "The larger packet previously made all context immediately available.",
        "current_failure_mode": "The packet now increases tokens and mixes runtime state with scientific context.",
        "proposal": "Keep a minimal seed context and permit guided local exploration.",
        "cost_audit": {"context_tokens": "decrease", "model_calls": "unchanged"},
        "risk_audit": {"level": "low", "side_effects": "missing context if navigation links drift"},
        "priority": "P0",
        "affected_flow_nodes": affected_flow_nodes or [],
        "affected_files": ["tool/theory_master/theory_master/prompt_stages.py"],
        "validation": ["Challenger prompt regression passes"],
    }


class ChangeRequestTests(unittest.TestCase):
    def test_current_flow_exposes_stable_request_target_ids(self) -> None:
        ids = flow_node_ids()
        self.assertIn("CHALLENGER", ids)
        self.assertIn("INV_CTX", ids)
        self.assertIn("JUDGE", ids)
        self.assertNotIn("SEM_B", ids)
        self.assertNotIn("ROUTER", ids)
        self.assertNotIn("INTERNAL", ids)
        self.assertEqual(len(ids), len(set(ids)))

    def test_create_request_allocates_unique_daily_ids_and_loads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = datetime(2026, 10, 6, 13, 8, tzinfo=timezone.utc)
            first = create_request(
                request_fields(affected_flow_nodes=["CHALLENGER"]),
                root,
                allowed_flow_nodes=flow_node_ids(),
                now=now,
            )
            second = create_request(
                request_fields(affected_flow_nodes=["INV_CTX"]),
                root,
                allowed_flow_nodes=flow_node_ids(),
                now=now,
            )
            self.assertEqual(first["request_id"], "TMCR-20261006-001")
            self.assertEqual(second["request_id"], "TMCR-20261006-002")
            loaded = load_change_requests(root, allowed_flow_nodes=flow_node_ids())
            self.assertFalse(loaded.warnings)
            self.assertEqual([item["request_id"] for item in loaded.requests], ["TMCR-20261006-002", "TMCR-20261006-001"])

    def test_unknown_flow_node_fails_closed(self) -> None:
        payload = request_fields(affected_flow_nodes=["NOT_A_REAL_NODE"])
        payload.update({
            "schema": "theory-master/change-request/v1",
            "request_id": "TMCR-20261006-001",
            "created_at": "2026-10-06T13:08:00+00:00",
        })
        with self.assertRaisesRegex(ValueError, "unknown affected_flow_nodes"):
            validate_request(payload, allowed_flow_nodes=flow_node_ids())

    def test_invalid_file_is_reported_not_silently_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = request_fields(affected_flow_nodes=["NOT_A_REAL_NODE"])
            payload.update({
                "schema": "theory-master/change-request/v1",
                "request_id": "TMCR-20261006-001",
                "created_at": "2026-10-06T13:08:00+00:00",
            })
            (root / "TMCR-20261006-001.json").write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_change_requests(root, allowed_flow_nodes=flow_node_ids())
            self.assertFalse(loaded.requests)
            self.assertEqual(len(loaded.warnings), 1)
            self.assertIn("unknown affected_flow_nodes", loaded.warnings[0])

    def test_selected_request_marks_nodes_red_and_renders_navigation(self) -> None:
        request = request_fields(affected_flow_nodes=["CHALLENGER", "INV_CTX"])
        request.update({
            "request_id": "TMCR-20261006-099",
            "created_at": "2026-10-06T13:08:00+00:00",
        })
        body = current_flow_body((request,), "TMCR-20261006-099")
        # Request impact is red on the canonical graph and navigable by node ID.
        self.assertIn("classDef requestImpact fill:#ffe3e3,stroke:#c22", body)
        self.assertIn("class CHALLENGER,INV_CTX requestImpact", body)
        self.assertIn("Current Workflow · 实际运行", body)
        self.assertIn('data-flow-node="CHALLENGER"', body)
        self.assertIn('data-flow-node="INV_CTX"', body)
        self.assertIn("scrollIntoView", body)

    def test_after_change_projection_renders_expected_topology(self) -> None:
        request = request_fields(affected_flow_nodes=["LEAD_LIT", "LEAD_LIT_TASK", "LOCK"])
        request.update({
            "request_id": "TMCR-20261006-101",
            "schema": "theory-master/change-request/v1",
            "created_at": "2026-10-06T13:08:00+00:00",
            "after_flow_projection": {
                "remove_nodes": ["LEAD_LIT", "LEAD_LIT_TASK"],
                "replace_nodes": {},
                "add_edges": [{"from": "LEAD", "to": "CHALLENGER"}],
                "note": "preview only",
            },
        })
        validate_request(request, allowed_flow_nodes=flow_node_ids())
        mermaid = _after_flow_mermaid(request)
        self.assertNotIn('LEAD_LIT{', mermaid)
        self.assertNotIn('LEAD_LIT_TASK[', mermaid)
        self.assertIn('LEAD --> CHALLENGER', mermaid)
        current = current_flow_body((request,), "TMCR-20261006-101")
        after = current_flow_body((request,), "TMCR-20261006-101", view="after")
        self.assertIn("Proposal Preview", current)
        self.assertIn("Proposal Preview · TMCR-20261006-101", after)
        self.assertIn('<h2>Current Workflow · 实际运行</h2>', after)
        self.assertIn('<h2>Proposal Preview · TMCR-20261006-101</h2>', after)
        self.assertIn('classDef afterImpact', after)
        self.assertIn('class LOCK afterImpact', after)
        self.assertEqual(after.count('class="mermaid"'), 2)

    def test_observer_only_history_is_not_a_formal_tm_request(self) -> None:
        tm_request = {"request_id": "TMCR-20261006-002", "record_scope": "tm_system"}
        observer_history = {"request_id": "TMCR-20261006-003", "record_scope": "observer_ui_history"}
        self.assertEqual(_tm_system_requests((observer_history, tm_request)), (tm_request,))

    def test_empty_impact_is_explicit_not_fabricated(self) -> None:
        request = request_fields(affected_flow_nodes=[])
        request.update({
            "request_id": "TMCR-20261006-100",
            "created_at": "2026-10-06T13:08:00+00:00",
        })
        body = current_flow_body((request,), "TMCR-20261006-100")
        self.assertNotIn("classDef changeImpact", body)
        self.assertIn("无 Scientific Current Flow 节点影响", body)


if __name__ == "__main__":
    unittest.main()
