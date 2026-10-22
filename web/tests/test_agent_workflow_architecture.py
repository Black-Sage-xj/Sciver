from __future__ import annotations

import re
import unittest

from tm_observer.agent_workflow_architecture import agent_workflow_architecture_body
from tm_observer.stage_flow import stage_detail_catalog, stage_workflow_mermaid
from tm_observer.web import architecture_html


class AgentWorkflowArchitectureTests(unittest.TestCase):
    def test_public_architecture_page_uses_the_agent_workflow_not_archify(self):
        page = architecture_html(None)
        self.assertIn("Agent Workflow Architecture", page)
        self.assertIn("Research Lead", page)
        self.assertIn("Challenger", page)
        self.assertIn("Investigator", page)
        self.assertIn("Literature", page)
        self.assertIn("Judge", page)
        self.assertIn("Reporter", page)
        self.assertIn("Cycle OVER ≠ Goal complete", page)
        self.assertNotIn("/artifacts/architecture.html", page)
        self.assertNotIn("Archify Architecture artifact has not been built yet", page)

    def test_every_rendered_agent_node_exists_in_current_stage_contract_source(self):
        body = agent_workflow_architecture_body()
        details = {item["node"]: item for item in stage_detail_catalog()}
        current_flow = stage_workflow_mermaid()
        stage_keys = set(re.findall(r'data-aa-stage-key="([^"]+)"', body))
        self.assertTrue(stage_keys)
        self.assertNotIn("Workflow source mismatch", body)
        self.assertIn("challenger__repair_verification", stage_keys)
        for key in stage_keys:
            with self.subTest(stage=key):
                self.assertIn(key, details)
                self.assertIn(key, current_flow)

    def test_interaction_and_responsive_layout_are_present(self):
        body = agent_workflow_architecture_body()
        self.assertIn('data-aa-view="all"', body)
        self.assertIn('data-aa-view="main"', body)
        self.assertIn('aria-live="polite"', body)
        self.assertIn('data-aa-detail-panel', body)
        self.assertIn('@media(max-width:760px)', body)
        self.assertIn('textContent', body)

    def test_conditional_paths_and_authority_boundaries_are_explicit(self):
        body = agent_workflow_architecture_body()
        current_flow = stage_workflow_mermaid()
        self.assertIn('challenger__repair_verification -- "confirmed; eligible for Investigation" --> LOCK', current_flow)
        self.assertIn('challenger__repair_verification -- "unresolved blocker / fatal design flaw" --> QUALIFY_RETRY', current_flow)
        for phrase in (
            "CONDITIONAL PATHS",
            "selection_repair",
            "repair_verification",
            "preserve_research",
            "Forced terminal open",
            "Scientific Model Commit",
            "Goal Completion 独立关口",
            "Cycle OVER ≠ Goal complete",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, body)


if __name__ == "__main__":
    unittest.main()
