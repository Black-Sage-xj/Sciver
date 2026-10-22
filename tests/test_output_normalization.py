from __future__ import annotations

import unittest

from theory_master.contracts import role_contract
from theory_master.output_normalization import OutputNormalizationError, normalize_agent_output


class OutputNormalizationTest(unittest.TestCase):
    def test_valid_object_is_unchanged(self):
        result = normalize_agent_output('{"a":1,"b":[2]}', {"a": 0, "b": []})
        self.assertEqual(result.value, {"a": 1, "b": [2]})
        self.assertEqual(result.classification, "valid")
        self.assertEqual(result.repairs, ())

    def test_wrappers_and_trailing_comma_are_normalized(self):
        fence = chr(96) * 3
        raw = "Here is the result:\n" + fence + 'json\n{"a":1,"b":[2,],}\n' + fence
        result = normalize_agent_output(raw, {"a": 0, "b": []})
        self.assertEqual(result.value, {"a": 1, "b": [2]})
        self.assertEqual(result.classification, "syntax_damage")
        kinds = {item["kind"] for item in result.repairs}
        self.assertIn("strip_prefix_prose", kinds)
        self.assertIn("strip_suffix_code_fence", kinds)
        self.assertIn("remove_trailing_commas", kinds)

    def test_missing_closing_delimiter_is_appended(self):
        raw = '{"reviews":[{"candidate_id":"c1","verdict":"qualified","reason":"ok"}]'
        result = normalize_agent_output(raw, {"reviews": []})
        self.assertEqual(result.value["reviews"][0]["candidate_id"], "c1")
        self.assertIn("append_missing_closing_delimiters", [item["kind"] for item in result.repairs])

    def test_missing_comma_is_inserted(self):
        result = normalize_agent_output('{"a":1 "b":2}', {"a": 0, "b": 0})
        self.assertEqual(result.value, {"a": 1, "b": 2})
        self.assertIn("insert_missing_comma", [item["kind"] for item in result.repairs])

    def test_premature_root_close_uses_current_contract_keys(self):
        contract = role_contract("research_lead", phase="candidate_selection")
        raw = '{"selected_candidate_id":"c1"},"selection_reason":"choose c1"}'
        result = normalize_agent_output(raw, contract.prompt_schema)
        self.assertEqual(result.value["selected_candidate_id"], "c1")
        repairs = list(result.repairs)
        self.assertEqual(repairs[0]["kind"], "merge_premature_top_level_close")
        self.assertEqual(
            repairs[0]["fields"],
            ["selection_reason"],
        )

    def test_premature_root_close_refuses_unknown_contract_field(self):
        contract = role_contract("research_lead", phase="cycle_synthesis")
        raw = '{"scientific_synthesis":{}},"invented_field":1}'
        with self.assertRaises(OutputNormalizationError) as caught:
            normalize_agent_output(raw, contract.prompt_schema)
        self.assertEqual(caught.exception.classification, "syntax_damage")

    def test_unterminated_string_is_content_loss(self):
        with self.assertRaises(OutputNormalizationError) as caught:
            normalize_agent_output('{"summary":"incomplete scientific content', {"summary": "..."})
        self.assertEqual(caught.exception.classification, "content_loss")


if __name__ == "__main__":
    unittest.main()
