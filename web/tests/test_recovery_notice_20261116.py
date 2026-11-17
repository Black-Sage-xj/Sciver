"""Recovery context is distinct from failure and attention; no task mutations."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from tm_observer.dashboard_ui import dashboard_shell
from tm_observer.run_tui_bridge import read_tui
from tm_observer.run_views import _tui_summary, run_detail_body


HEADER = ('Process PAUSED    State PAUSED\n'
          'Cycle 1 / 10     Phase paused               Elapsed 01:02:04\n'
          'Started  10-24T18:38:24  Cycle start 10-24T18:38:24  Cost ~$5.49 estimated\n'
          'Recovery paused\nCheckpoint /truncated/terminal/path…')
CHECKPOINT = '/home/research/' + 'long-task-name-' * 20 + '/research/257/research_state.json'
INFO = {'resumable': True, 'execution_active': False, 'phase': 'paused', 'checkpoint': CHECKPOINT}


@pytest.mark.parametrize('status,tone,title', [
    ('PAUSED', 'info', '已暂停'), ('CHECKPOINT_REACHED', 'info', '已到达检查点'),
    ('INTERRUPTED', 'warning', '运行中断'), ('ORPHANED', 'warning', '运行进程失联'),
    ('UNRESPONSIVE', 'warning', '运行无响应'), ('RECOVERABLE', 'warning', '等待恢复'),
    ('FAILED', 'error', '运行失败'), ('FAILED_RECOVERY', 'error', '恢复失败'),
])
def test_notice_classification_and_full_checkpoint(status, tone, title):
    html = _tui_summary(HEADER, recovery_info=INFO, effective_status=status, resume_available=True)
    soup = BeautifulSoup(html, 'html.parser')
    notice = soup.select_one('.tm-runtime-notice')
    assert notice['data-tone'] == tone
    assert notice.strong.text == title
    assert notice['role'] == 'status'
    details = notice.select_one('details')
    assert 'open' not in details.attrs
    assert details.select('code')[1].text == CHECKPOINT
    assert 'Recovery paused' not in html and '/truncated/' not in html
    assert 'Resume' in notice.p.text
    assert bool(notice.select_one('[data-tui-view="events"]')) == (tone != 'info')


@pytest.mark.parametrize('status,info', [
    ('RUNNING', INFO), ('OVER', INFO), ('NEEDS_ATTENTION', INFO),
    ('PAUSED', {**INFO, 'execution_active': True}),
    ('PAUSED', {**INFO, 'phase': 'abandon_requested'}),
    ('PAUSED', {**INFO, 'phase': 'control_requested'}),
])
def test_stale_or_inapplicable_recovery_is_hidden(status, info):
    assert 'tm-runtime-notice' not in _tui_summary(HEADER, recovery_info=info, effective_status=status)


def test_missing_resume_capability_and_attention_priority():
    for overrides in ({'resume_available': False}, {'allow_process_actions': False},
                      {'recovery_info': {**INFO, 'resumable': False}}):
        arguments = {'recovery_info': INFO, 'resume_available': True, **overrides}
        soup = BeautifulSoup(_tui_summary(HEADER, **arguments), 'html.parser')
        assert 'Resume' not in soup.select_one('.tm-runtime-notice p').text
    attention = {'reason': '<script>missing evidence</script>', 'requested_action': 'Review'}
    html = _tui_summary(HEADER, recovery_info=INFO, attention=attention)
    soup = BeautifulSoup(html, 'html.parser')
    assert soup.select_one('.tm-tui-attention[open]')
    assert not soup.select_one('.tm-runtime-notice')
    assert not soup.script


def test_projection_adapter_redacts_diagnostics_without_changing_flags():
    payload = {'parts': {}, 'recovery_info': {**INFO, 'phase': 'paused api_key=secret-value', 'unknown': 'ignored'}}
    with patch('tm_observer.run_tui_bridge._safe_cycle', return_value=Path('/tmp/257')), \
         patch('tm_observer.run_tui_bridge.subprocess.run', return_value=SimpleNamespace(
             returncode=0, stdout=json.dumps(payload))):
        projected = read_tui({'research_root': '/tmp'})['recovery_info']
    assert projected['resumable'] is True
    assert projected['execution_active'] is False
    assert projected['checkpoint'] == CHECKPOINT
    assert 'secret-value' not in projected['phase']
    assert 'unknown' not in projected


@pytest.mark.skipif(os.environ.get('TM_BROWSER_E2E') != '1', reason='opt-in Chromium check')
def test_browser_disclosure_polling_and_mobile_layout():
    from playwright.sync_api import sync_playwright
    # Exercise the production polling reconciliation independently of network or task controls.
    body = run_detail_body({'observer_id': 'a'*16, 'task_name': 'Fixture', 'cycle': '1', 'state': 'PAUSED'})
    start = body.index('function reconcileHeaderPreservingQuota(')
    end = body.index('async function refresh()', start)
    reconcile = body[start:end]
    current = _tui_summary(HEADER, recovery_info=INFO, show_codex_quota=True, resume_available=True)
    fresh = _tui_summary(HEADER.replace('01:02:04', '01:02:12'), recovery_info=INFO,
                         show_codex_quota=True, resume_available=True)
    html = dashboard_shell('Recovery', current, active='console')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width':1100, 'height':800})
        page.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
        page.goto('http://recovery.test/')
        for theme in ('white', 'black', 'blue'):
            page.evaluate('(t)=>document.documentElement.setAttribute("data-sciver-theme",t)', theme)
            details = page.locator('[data-recovery-details]')
            assert not details.locator('code').last.is_visible()
            details.locator('summary').click()
            assert details.locator('code').last.is_visible()
            assert details.locator('code').last.inner_text() == CHECKPOINT
            page.evaluate('() => { window.reconcile=' + reconcile + '; }')
            assert page.evaluate('''(html)=>{
              const current=document.querySelector('[data-tui-pane="header"]');
              const quota=current.querySelector('[data-codex-quota]');
              const wrapper=document.createElement('div');wrapper.innerHTML=html;
              if(!reconcile(current,wrapper.firstElementChild))return false;
              return current.querySelector('[data-recovery-details]').open &&
                current.querySelector('[data-codex-quota]')===quota;
            }''', fresh)
            details.locator('summary').click()
        page.set_viewport_size({'width':390, 'height':844})
        page.locator('[data-recovery-details] summary').click()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        code = page.locator('[data-recovery-details] code').last
        assert code.evaluate('e=>e.scrollWidth<=e.clientWidth || e.getBoundingClientRect().width<=innerWidth')
        browser.close()
