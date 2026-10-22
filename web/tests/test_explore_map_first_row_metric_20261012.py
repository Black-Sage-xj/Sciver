"""Canvas/Markdown explorer lives in the FIRST ROW of the Run summary cards."""
import unittest
from bs4 import BeautifulSoup
from tm_observer.run_views import _tui_summary,run_detail_body

_HEADER=("Process PAUSED   State PAUSED\n"
         "Cycle 1 / 40     Phase provider_quota_exhausted       Elapsed 09:44:42\n"
         "Started  10-12T02:46:39  Cycle start 10-12T10:44:43  Cost ~$23.44 estimated")
_ID="8dd38a9977eef7e5"
_LINK=f"/research-explorer/?observer_id={_ID}"
_EXPLORER=f'<a class="tm-explore-map" data-explore-map href="{_LINK}">Explore map</a>'


class ExploreMapMetricTests(unittest.TestCase):
    def test_first_row_exact_order_and_eight_equal_metric_cards(self):
        soup=BeautifulSoup(_tui_summary(_HEADER,show_codex_quota=True,
                        explore_map_card=_EXPLORER),"html.parser")
        grid=soup.select_one('.tm-tui-kv-grid')
        cards=grid.find_all(recursive=False)
        self.assertEqual(len(cards),8)
        self.assertEqual([c.select_one('dt').get_text(' ',strip=True) if c.select_one('dt')
                          else ('Explore map' if c.select_one('[data-explore-map]') else '')
                          for c in cards[:4]],
                         ['State','Cycle','Explore map','Codex 额度 · 查询中…'])
        self.assertEqual([c.select_one('dt').get_text(' ',strip=True) for c in cards[4:]],
                         ['Cost','Elapsed','Started','Cycle start'])
        link=cards[2].select_one('a[data-explore-map]')
        self.assertEqual(link['href'],_LINK)
        self.assertEqual(len(soup.select('[data-explore-map]')),1)
        self.assertEqual(len(soup.select('[data-codex-quota]')),1)

    def test_canvas_unavailable_appears_as_disabled_card_not_link(self):
        disabled='<div class="tm-explore-map tm-explore-map-unavailable" data-explore-map>当前任务暂未提供可阅读的科研画布</div>'
        page=BeautifulSoup(_tui_summary(_HEADER,show_codex_quota=True,
                            explore_map_card=disabled),"html.parser")
        cards=page.select('.tm-tui-kv-grid > .tm-tui-kv')
        self.assertEqual(len(cards),8)
        self.assertIsNotNone(cards[2].select_one('.tm-explore-map-unavailable'))
        self.assertIsNone(cards[2].select_one('a'))
        self.assertEqual(len(page.select('[data-explore-map]')),1)

    def test_fallback_header_keeps_map_as_grid_card_not_below(self):
        page=BeautifulSoup(_tui_summary('unparseable',show_codex_quota=True,
                            explore_map_card=_EXPLORER),"html.parser")
        self.assertEqual(len(page.select('.tm-tui-kv-grid > .tm-tui-kv')),2)
        self.assertIsNotNone(page.select('.tm-tui-kv-grid > .tm-tui-kv')[0].select_one('a[data-explore-map]'))

    def test_run_uses_one_grid_card_with_exact_task_url(self):
        run={'observer_id':_ID,'cycle':'70','state':'PAUSED','task_name':'Fixture'}
        page=BeautifulSoup(run_detail_body(run,canvas_available=True),"html.parser")
        self.assertEqual(len(page.select('[data-explore-map]')),1)
        self.assertEqual(page.select_one('.tm-tui-kv-grid .tm-explore-map-card a')['href'],_LINK)
        self.assertIsNone(page.select_one('.tm-tui-header > [data-explore-map]'))
        self.assertIsNone(page.select_one('#tm-tui-controls [data-explore-map]'))
        for mode in ('events','research'):
            other=BeautifulSoup(run_detail_body(run,tab=mode,canvas_available=True),'html.parser')
            self.assertFalse(other.select('[data-explore-map]'))
