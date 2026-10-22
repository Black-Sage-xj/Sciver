"""Observer documentation must show current Investigator v8 without old fields."""
import unittest

from tm_observer.current_flow import CURRENT_FLOW_MERMAID
from tm_observer.stage_flow import CONTEXT_FIELD_STRUCTURES, STAGE_CONTRACT_RULES
from tm_observer.field_notes import default_description


class V8ObserverTest(unittest.TestCase):
    def test_current_investigator_flow_only_requires_statement(self):
        self.assertIn("findings[].statement（唯一必需）", CURRENT_FLOW_MERMAID)
        self.assertIn("Final Formatting 保留 A 层 Finding 数量", CURRENT_FLOW_MERMAID)
        self.assertNotIn("findings[].evidence_kind", CURRENT_FLOW_MERMAID)
        self.assertNotIn("local_source → source_refs", CURRENT_FLOW_MERMAID)
        self.assertNotIn("conditional scope / limitations", CURRENT_FLOW_MERMAID)

    def test_stage_card_notes_remain_current(self):
        rules = "\n".join(STAGE_CONTRACT_RULES[("investigator", "investigate")])
        self.assertIn("statement", rules)
        self.assertIn("Final Formatting", rules)
        self.assertIn("不再输出", rules)

    def test_judge_input_does_not_advertise_authored_types(self):
        assertive = CONTEXT_FIELD_STRUCTURES[("judge", "terminal_assessment", "investigation_result")]
        self.assertEqual(len(assertive), 1)
        self.assertNotIn("evidence_kind", assertive[0])
        for stage in ("investigate", "judge_rework", "integrate_literature"):
            fields = CONTEXT_FIELD_STRUCTURES[("investigator", stage, "previous_scientific_delta")]
            self.assertNotIn("evidence_kind", fields[0])

    def test_field_description_is_scientifically_qualified(self):
        desc = default_description("output", "findings[].statement")
        self.assertIn("自包含", desc)
        self.assertIn("成立前提", desc)


if __name__ == "__main__":
    unittest.main()
