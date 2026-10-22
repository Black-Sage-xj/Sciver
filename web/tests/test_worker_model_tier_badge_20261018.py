"""The model preset belongs to the entire six-stage table, not one row."""
from bs4 import BeautifulSoup
import pytest

from tm_observer.run_views import _tui_workflow_table

ROLES = ("research_lead","challenger","investigator","literature","judge","reporter")
RAW = (
    'AGENT/STAGE    MODEL                         COST      TIME     STATE\n'
    'Research Lead  GPT-6.1 Sol · high               —     08:10     done\n'
    'Challenger     GPT-6.1 Sol · medium             —     03:20     done\n'
    'Investigator   GPT-6.1 Sol · low                —  01:12:30  running\n'
    'Literature     GPT-6 Luna · low                 —     00:00     idle\n'
    'Judge          GPT-6.1 Sol · medium             —     00:00     idle\n'
    'Reporter       GPT-6 Luna · low                 —     01:04     done')


@pytest.mark.parametrize('profile,label', [
    ('test','Codex 一档'),('codex','Codex 二档'),('advanced','Codex 三档')
])
def test_one_preset_in_model_heading_for_six_fixed_agent_routes(profile,label):
    settings={role:{'preset_profile':profile,'preset_tier':label} for role in ROLES}
    soup=BeautifulSoup(_tui_workflow_table(RAW,settings=settings),'html.parser')
    assert len(soup.select('thead th'))==6
    assert len(soup.select('tbody tr[data-agent-role]'))==6
    assert len(soup.select('[data-model-preset-tier]'))==1
    assert soup.select_one('thead th:nth-of-type(2) [data-model-preset-tier]').get_text(strip=True)==label+' ▾'
    assert not soup.select('tbody .tm-model-preset-tier')
    assert len(soup.select('tbody [data-route-model]'))==6
    assert len(soup.select('tbody [data-route-effort]'))==6


def test_mixed_model_overrides_are_aggregated_into_one_header_badge():
    settings={role:{'preset_profile':'codex','preset_tier':'Codex 二档'} for role in ROLES}
    settings['research_lead']['preset_tier']='Codex 二档 · 兼容覆盖'
    settings['investigator']['preset_tier']='Codex 二档 · 下轮生效'
    soup=BeautifulSoup(_tui_workflow_table(RAW,settings=settings),'html.parser')
    tier=soup.select_one('thead th:nth-of-type(2) [data-model-preset-tier]')
    assert tier.get_text(strip=True)=='Codex 二档 · 自定义覆盖 · 下轮生效 ▾'
    assert len(soup.select('[data-model-preset-tier]'))==1


def test_unset_profile_does_not_invent_a_tier():
    soup=BeautifulSoup(_tui_workflow_table(RAW,settings={}), 'html.parser')
    assert not soup.select('[data-model-preset-tier]')
    assert len(soup.select('tbody tr[data-agent-role]'))==6


def test_model_heading_is_one_clickable_button_for_six_agents():
    settings={role:{"preset_profile":"codex","preset_tier":"Codex 二档"} for role in ROLES}
    soup=BeautifulSoup(_tui_workflow_table(RAW,settings=settings),"html.parser")
    trigger=soup.select_one("thead th:nth-of-type(2) button[data-model-preset-open]")
    assert trigger is not None
    assert trigger["data-model-preset-profile"]=="codex"
    assert trigger["aria-haspopup"]=="dialog"
    assert not soup.select("tbody [data-model-preset-open]")
