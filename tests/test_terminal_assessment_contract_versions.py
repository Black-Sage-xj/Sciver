from __future__ import annotations

import unittest

from theory_master.contract_stages import contract_by_id, role_contract


class CurrentContractVersionFenceTest(unittest.TestCase):
    def test_judge_terminal_contract_is_current_only(self):
        contract = role_contract("judge", phase="terminal_assessment")
        self.assertEqual(contract.version, 8)
        self.assertIn("objection", contract.prompt_schema)
        self.assertNotIn("evidence_assessments", contract.prompt_schema)
        self.assertIsNone(contract_by_id(contract.contract_id, 7))
        self.assertIs(contract_by_id(contract.contract_id, contract.version), contract)

    def test_literature_targeted_source_contract_is_current_only(self):
        contract = role_contract("literature", phase="targeted_source")
        self.assertEqual(contract.version, 5)
        self.assertEqual(set(contract.prompt_schema), {"evidence_records", "remaining_scientific_question"})
        self.assertIsNone(contract_by_id(contract.contract_id, 4))
        self.assertIs(contract_by_id(contract.contract_id, contract.version), contract)


if __name__ == "__main__":
    unittest.main()
