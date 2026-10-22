"""Fixed authorization increments; legacy approvals remain append-only-valid."""
import json
import tempfile
import unittest
from pathlib import Path

from theory_master.investigator_budget import LEDGER_REL, apply_review, budget_status
from test_investigator_budget_review import InvestigatorBudgetReviewTests


class FixedIncrementCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.research,self.cycle,self.state=InvestigatorBudgetReviewTests().setup_cycle(self.root,52_000_000)

    def test_new_grant_adds_only_to_authorized_allowance(self):
        row=apply_review(self.cycle,decision='approve',reason='Meaningful first evidence was registered',
            next_test='Validate new independent networks',evidence_ids=['E-1'])
        self.assertEqual(row['prior_allowance'],50_000_000)
        self.assertEqual(row['observed_tokens'],52_000_000)
        self.assertEqual(row['new_allowance'],100_000_000)
        self.assertEqual(row['allowance_basis'],'prior_allowance')
        budget=budget_status(self.cycle,self.research,self.state)
        self.assertEqual(budget['remaining_tokens'],48_000_000)
        self.assertEqual(budget['reviews'],1)

    def test_previously_recorded_v1_overshoot_then_new_fixed_grant(self):
        row=apply_review(self.cycle,decision='approve',reason='Scientifically meaningful progress',
            next_test='Independently replicate the output',evidence_ids=['E-1'])
        ledger=self.cycle/LEDGER_REL
        old=dict(row)
        old.pop('allowance_basis')
        old['new_allowance']=102_000_000
        ledger.write_text(json.dumps(old,ensure_ascii=False)+'\n')
        self.assertEqual(budget_status(self.cycle,self.research,self.state)['allowance_tokens'],102_000_000)
        self.state['agents']['investigator']['tokens_total']=125_000_000
        (self.cycle/'run_state.json').write_text(json.dumps(self.state))
        (self.cycle/'research_state.json').write_text(json.dumps(self.state))
        with (self.cycle/'evidence'/'registry.jsonl').open('a') as log:
            log.write(json.dumps({'evidence_id':'E-2','summary':'new progress'})+'\n')
        second=apply_review(self.cycle,decision='approve',reason='A reproducible new independent result establishes scientific progress',
            next_test='Test on a fresh cohort',evidence_ids=['E-2'])
        self.assertEqual(second['prior_allowance'],102_000_000)
        self.assertEqual(second['new_allowance'],152_000_000)
        self.assertEqual(budget_status(self.cycle,self.research,self.state)['allowance_tokens'],152_000_000)
        self.assertEqual(json.loads(ledger.read_text().splitlines()[0]),old)

    def test_unknown_allowance_basis_rejected(self):
        row=apply_review(self.cycle,decision='approve',reason='New scientific progress allows an independently falsifiable hypothesis',
            next_test='Independently reproduce numerical data using a new implementation',evidence_ids=['E-1'])
        row['allowance_basis']='observed'
        (self.cycle/LEDGER_REL).write_text(json.dumps(row)+'\n')
        with self.assertRaisesRegex(ValueError,'allowance basis'):
            budget_status(self.cycle,self.research,self.state)
