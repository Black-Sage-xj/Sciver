"""Judge dialog is an explicit read-only SDK decision log view, never TUI guesswork."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from tm_observer.judge_review_ui import read_judge_reviews, render_judge_review_template, dialog
from tm_observer.run_views import _judge_card, _investigation_card, _tui_summary, run_detail_body


class JudgeReviewUITests(unittest.TestCase):
    @staticmethod
    def setup_run(root: Path):
        cycle = root / '70'
        cycle.mkdir(parents=True)
        return {'observer_id':'a'*16,'research_root':str(root), 'cycle_dir':str(cycle),
                'cycle':'70','run_id':1,'task_name':'Test','state':'RUNNING'}

    def test_reads_scientific_judge_decision_not_status_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.setup_run(Path(tmp))
            rows = [
                {'seq':1,'type':'role.completed','payload':{'role':'investigator',
                   'stage':'investigate','output':{'verdict':'irrelevant'}}},
                {'seq':2,'at':'2026-10-12T15:30:00+08:00','type':'role.completed',
                 'payload':{'role':'judge','stage':'terminal_assessment','output':{
                     'verdict':None,'rationale':'必须完成完整检验，尚未裁决',
                     'objection':{'requested_resolution':'补足跨网络数据'}}}},
                {'seq':3,'at':'2026-10-12T15:35:00+08:00','type':'role.completed',
                 'payload':{'role':'judge','stage':'goal_completion','output':{
                     'verdict':'not_passed','rationale':'Final Gate 尚未满足'}}},
            ]
            (Path(run['cycle_dir'])/'events.jsonl').write_text(
                ''.join(json.dumps(item,ensure_ascii=False)+'\n' for item in rows),encoding='utf-8')
            reviews=read_judge_reviews(run)
            self.assertEqual(len(reviews),2)
            self.assertIsNone(reviews[0]['output']['verdict'])
            self.assertEqual(reviews[1]['output']['verdict'],'not_passed')
            html=render_judge_review_template(reviews,70)
            self.assertIn('必须完成完整检验',html)
            self.assertIn('补足跨网络数据',html)
            self.assertIn('未登记（不可从 done 推断）',html)
            self.assertIn('Final Gate 尚未满足',html)
            soup=BeautifulSoup(_judge_card('Status: done\nReworks: 1 / 3',
                                            reviews=reviews,cycle=70),'html.parser')
            trigger=soup.select_one('[data-judge-review-open]')
            self.assertIsNotNone(trigger)
            self.assertIn('2 次裁决',trigger.text)
            self.assertEqual(len(soup.select('[data-judge-review-panel]')),2)
            self.assertEqual(soup.select_one('.tm-judge-item p').text,'done')
            self.assertNotIn('done</strong>',html)

    def test_no_record_has_no_fake_verdict_and_unsafe_script_escaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=self.setup_run(Path(tmp))
            self.assertEqual(read_judge_reviews(run),[])
            empty=_judge_card('Status: done\nReworks: 1 / 3',reviews=[],cycle=70)
            self.assertIn('查看裁决详情',empty)
            self.assertIn('尚未读取到正式 Judge',empty)
            sample=[{'stage':'terminal_assessment','at':'now','seq':5,
                     'output':{'verdict':'supported','rationale':'<img src=x onerror=alert(1)>'}}]
            content=render_judge_review_template(sample,70)
            self.assertIn('&lt;img src=x onerror=alert(1)&gt;',content)
            self.assertNotIn('<img src=x onerror',content)
            self.assertIn('data-judge-review-select',render_judge_review_template(sample*2,70))
            self.assertIn('id="tm-judge-review-dialog"',dialog())

    def test_cost_first_row_elapsed_second_goal_is_separate_prominent_field(self):
        header=('Process RUNNING   State RUNNING\n'
                'Cycle 70 / 80     Phase investigator:running       Elapsed 09:46:13\n'
                'Started 10-12T01:01:01  Cycle start 10-12T02:02:02  Cost $10')
        soup=BeautifulSoup(_tui_summary(header,show_codex_quota=True),'html.parser')
        labels=[item.text for item in soup.select('.tm-tui-kv dt')]
        self.assertEqual(labels,['State','Cycle','Codex 额度 · 查询中…','Cost','Elapsed','Started','Cycle start'])
        self.assertIn('09:46:13',soup.select('.tm-tui-kv')[4].text)
        card=BeautifulSoup(_investigation_card('Attempt: 2\nCurrent executor: Investigator'),'html.parser')
        goal=card.select_one('.tm-investigator-goal-field')
        self.assertIsNotNone(goal)
        self.assertIn('当前 Goal',goal.text)
        self.assertIn('本次 Investigator',goal.text)
        self.assertEqual(goal.select_one('[data-goal-open]').text.strip(),'查看 Goal ↗')
        full=run_detail_body({'observer_id':'a'*16,'run_id':1,'task_name':'Test','cycle':'70','state':'RUNNING'})
        self.assertIn('tm-judge-review-dialog',full)
        self.assertIn('data-judge-modal-content',full)


if __name__=='__main__':unittest.main()
