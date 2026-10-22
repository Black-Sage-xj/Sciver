"""Run read-only web list/detail smoke tests. Never invoke Runner controls."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from tm_observer.run_views import PAGE_SIZE, _read_run_state, run_detail_body, run_index_body


class RunListTests(unittest.TestCase):
    @staticmethod
    def rows(size=43):
        return [{"observer_id": f"{i:016x}", "run_id": 1, "task_name": f"Task {i:02d}",
                 "cycle": "cycle-0001", "state": "LIVE" if i % 2 == 0 else "OVER",
                 "active_agent": "investigator" if i % 2 == 0 else None,
                 "last_activity_at": "2026-10-11T00:00:00+00:00"} for i in range(size)]

    def test_search_pagination_and_stable_identifier(self):
        state = {"runs": self.rows()}
        html = run_index_body(state)
        self.assertEqual(html.count('class="tm-run-task-link"'), PAGE_SIZE)
        self.assertIn('rel="next"', html)
        self.assertIn('/run/0000000000000000/', html)
        self.assertEqual(html.count('class="tm-run-clickable"'), PAGE_SIZE)
        self.assertEqual(html.count('data-task-url='), PAGE_SIZE)
        self.assertIn('window.location.assign(row.dataset.taskUrl)', html)
        self.assertIn("['Enter',' '].includes(event.key)", html)
        self.assertNotIn('<th>操作</th>', html)
        self.assertEqual(html.count('<th>'), 5)
        self.assertEqual(html.count('<tr class="tm-run-clickable"'), PAGE_SIZE)
        self.assertIn('colspan="5"', run_index_body({"runs": []}))
        self.assertNotIn('/runs/', html)
        page3 = run_index_body(state, page=3)
        self.assertEqual(page3.count('class="tm-run-task-link"'), 3)
        self.assertIn('/run/000000000000002a/', page3)
        q = run_index_body(state, query="Task 04")
        self.assertEqual(q.count('class="tm-run-task-link"'), 1)
        status = run_index_body(state, status="OVER")
        self.assertIn('21 个任务', status)
        self.assertEqual(status.count('class="tm-run-task-link"'), 20)
        self.assertIn('rel="next"', status)
        self.assertEqual(run_index_body(state, page=999).count('class="tm-run-task-link"'), 3)

    def test_task_list_surfaces_live_task_outside_first_page(self):
        rows=self.rows(43)
        for row in rows:
            row['state']='OVER'
        rows[42]['state']='LIVE'
        page=run_index_body({'runs':rows})
        self.assertIn('class="tm-run-refresh tm-run-live-link" href="/run/000000000000002a/"',page)
        rows[41]['state']='LIVE'
        multiple=run_index_body({'runs':rows})
        self.assertIn('class="tm-run-refresh tm-run-live-link" href="/run/?status=LIVE"',multiple)
        self.assertIn('查看 2 个正在运行的任务',multiple)
        rows[42]['state']='OVER'
        rows[41]['state']='OVER'
        self.assertNotIn('tm-run-live-link',run_index_body({'runs':rows}))

    def test_untrusted_list_data_is_escaped(self):
        entry = self.rows(1)[0]
        entry["task_name"] = '<img src=x onerror=alert(1)>'
        entry["state"] = 'LIVE" onmouseover="alert(1)'
        text = run_index_body({"runs": [entry]}, query='"/><script>x</script>')
        self.assertNotIn('<img', text)
        self.assertNotIn('<script>x</script>', text)
        self.assertNotIn('data-task-url="../', text)
        self.assertNotIn('<script>x</script>', text)
        self.assertIn('&lt;script&gt;x&lt;/script&gt;', text)
        no_filter = run_index_body({"runs": [entry]})
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', no_filter)
        self.assertNotIn('class="tm-run-state tm-run-state-LIVE', no_filter)
        # An invalid observer_id never becomes a direct URL.
        entry["observer_id"] = '../cycle-0001'
        self.assertNotIn('tm-run-task-link', run_index_body({"runs": [entry]}))

    def test_detail_reads_only_discovered_cycle_and_shows_separate_canvas_slot(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            research_root = base / 'research'
            cycle = research_root / 'cycle-0001'
            cycle.mkdir(parents=True)
            state = {"agents": {"investigator": {"state": "running", "model": "a-model"}},
                     "current_activity": '<dangerous>& in research', "project_tokens_total": 3456}
            (cycle / 'run_state.json').write_text(json.dumps(state), encoding='utf-8')
            entry = {"research_root": str(research_root), "cycle_dir": str(cycle),
                     "run_id": 1, "task_name": "Alpha", "cycle": "cycle-0001",
                     "state": "LIVE", "active_agent": "investigator"}
            self.assertEqual(_read_run_state(entry), state)
            html = run_detail_body(entry)
            self.assertIn('3,456', html)
            self.assertIn('class="tm-tui-screen"', html)
            self.assertIn('Sciver', html)
            self.assertIn('a-model', html)
            self.assertIn('&lt;dangerous&gt;&amp; in research', html)
            self.assertIn('当前任务暂未提供可阅读的科研画布', html)
            self.assertIn('Explore map', run_detail_body(entry, canvas_available=True))
            self.assertNotIn('href="/api/canvas-md/', html)
            self.assertNotIn('onclick=', html)
            elsewhere = base / 'outside'
            elsewhere.mkdir()
            (elsewhere / 'run_state.json').write_text(json.dumps({"secret": "hidden"}), encoding='utf-8')
            self.assertEqual(_read_run_state({**entry, "cycle_dir": str(elsewhere)}), {})
            (cycle / 'run_state.json').unlink()
            (cycle / 'run_state.json').symlink_to(elsewhere / 'run_state.json')
            self.assertEqual(_read_run_state(entry), {})


if __name__ == '__main__':
    unittest.main()


class WebTuiMonitoringTests(unittest.TestCase):
    def test_layout_matches_textual_compose_and_keyboard_shortcuts(self):
        import shutil
        import subprocess
        from tm_observer.run_views import run_detail_body
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cycle = root / '1'
            cycle.mkdir()
            (cycle / 'run_state.json').write_text(json.dumps({
                'master_id': 1, 'cycle_id': '1', 'status': 'RUNNING',
                'agents': {'investigator': {'state': 'running', 'model': 'gpt-6-sol'}},
                'current_agent': 'investigator',
            }), encoding='utf-8')
            run = {'observer_id': 'a' * 16, 'research_root': str(root), 'cycle_dir': str(cycle),
                   'task_name': 'Mock', 'run_id': 1, 'cycle': '1', 'state': 'LIVE'}
            page = run_detail_body(run)
            order = ['tm-tui-titlebar', 'tm-tui-header-stats', 'tm-tui-workflow',
                     'tm-tui-mid', 'tm-tui-activity-col', '运行动态',
                     '最近进展', 'tm-tui-center',
                     'data-tui-pane="hypothesis"', 'data-tui-pane="investigation"',
                     'data-tui-pane="judge"', 'tm-token-section']
            indices = [page.index(item) for item in order]
            self.assertEqual(indices, sorted(indices))
            # Previously approved two-column layout: left activity + scrollable
            # progress, right four vertically stacked scientific/detail cards.
            from bs4 import BeautifulSoup
            from tm_observer.dashboard_ui import STYLE
            home = BeautifulSoup(page, 'html.parser')
            mid = home.select_one('.tm-tui-mid')
            self.assertIsNotNone(mid)
            self.assertEqual([x.get('class', [None])[0] for x in mid.find_all(recursive=False)],
                             ['tm-tui-activity-col', 'tm-tui-center'])
            left, right = mid.find_all(recursive=False)
            self.assertIsNotNone(left.select_one('[data-tui-pane="last"]'))
            self.assertIsNone(left.select_one('details.tm-run-event-disclosure'))
            self.assertIsNone(left.select_one('.tm-recent-event-scroll'))
            self.assertEqual([x.get('data-tui-pane') for x in right.find_all(recursive=False)],
                             ['hypothesis','investigation','judge','tokens'])
            self.assertIn('grid-template-columns:minmax(0,1.3fr) minmax(310px,.86fr)', STYLE)
            self.assertNotIn('.tm-tui-insights { display:grid', STYLE)
            self.assertNotIn('class="tm-run-stats"', page)
            self.assertNotIn('class="tm-run-tabs"', page)
            self.assertIn('class="tm-tui-agent-table"', page)
            self.assertNotIn('修改模型或 Effort 时确认并自动保存', page)
            self.assertNotIn('tm-tui-route-hint', page)
            self.assertEqual(page.count('data-agent-role='), 6)  # six editable Agents
            self.assertNotIn('<th scope="col">操作</th>', page)
            self.assertNotIn('data-agent-apply', page)
            self.assertIn('tm-route-save-dialog', page)
            self.assertIn('当前 Cycle 的下一次安全模型调用', page)
            self.assertIn('仅下一 Cycle 生效', page)
            self.assertIn('data-route-activation-edit', page)
            self.assertIn('name="tm-route-activation" value="next_invocation"', page)
            self.assertIn('name="tm-route-activation" value="next_cycle"', page)
            self.assertIn('chooseRouteActivation(role,model,effort', page)
            self.assertIn('submitAgentModel(field)', page)
            self.assertIn('data-saved-model=', page)
            self.assertIn('data-saved-effort=', page)
            self.assertNotIn('class="tm-tui-default"', page)
            self.assertIn('data-route-default-value=', page)
            self.assertIn("routeChoiceLabels(field,true)", page)
            self.assertIn("toggleUsagePopover(tokenButton)", page)
            self.assertIn("document.body.append(panel)", page)
            self.assertIn("event.key==='Escape'&&usagePopover", page)
            self.assertNotIn('<kbd>1</kbd> Logs', page)
            self.assertNotIn('<kbd>2</kbd> Thread', page)
            self.assertNotIn('<kbd>3</kbd> Events', page)
            self.assertNotIn('<kbd>4</kbd> Artifacts', page)
            self.assertNotIn('<kbd>R</kbd> Research', page)
            self.assertNotIn('aria-label="TUI shortcut bar"', page)
            self.assertNotIn('tm-recent-event-scroll', page)
            self.assertIn('href="?tab=events', page) if 'href="?tab=events' in page else None
            self.assertNotIn('<kbd>Q</kbd> 返回列表', page)
            self.assertNotIn('<kbd>P</kbd> Pause', page)
            self.assertIn('id="tm-web-settings"', page)
            self.assertIn('TASK CONSOLE', page)
            self.assertNotIn('RUN / WEB TUI', page)
            self.assertNotIn('tm-tui-controls', page)
            self.assertIn('id="tm-operator" hidden', page)
            self.assertIn('data-operator-action="pause"', page)
            self.assertIn('setInterval(refresh,8000)', page)
            # Task/Cycle identity lives outside refreshed content panes.
            self.assertIn("oldIdentity.textContent=newIdentity.textContent", page)
            self.assertIn("window.tmStageConversationNotifyCycle", page)
            self.assertNotIn("view==='events'&&host.querySelector('.tm-stage-reader", page)
            self.assertIn("if(view==='events'&&oldPanes[n].dataset.tuiPane==='secondary')continue", page)
            self.assertIn('openDetails', page)
            self.assertIn('if(openDetails.has(index))detail.open=true', page)
            self.assertIn("historyScroll=new Map", page)
            self.assertIn("timeline.scrollHeight-timeline.clientHeight-timeline.scrollTop<28", page)
            self.assertIn("timeline.scrollHeight:previous.top", page)
            self.assertIn("applyHistoryView()", page)
            self.assertIn('data-history-view="important"', page)
            self.assertIn('data-history-panel="important"', page)
            self.assertIn("firstTimeline.scrollTop=firstTimeline.scrollHeight", page)
            secondary = run_detail_body(run, tab='thread', fragment=True)
            self.assertIn('冻结 Hypothesis', secondary)
            self.assertNotIn('THREAD', secondary)
            self.assertNotIn('ARTIFACTS', secondary)
            self.assertIn('<kbd>Esc</kbd> 返回主屏', secondary)
            self.assertIn('<kbd>O</kbd> 复制内容', secondary)
            self.assertNotIn('CURRENT ACTIVITY', secondary)
            if shutil.which('node'):
                scripts = page.split('<script>')[1:]
                for piece in scripts:
                    script = piece.split('</script>', 1)[0]
                    test = subprocess.run(['node', '--check', '--input-type=commonjs'],
                                          input=script, text=True, capture_output=True, timeout=8)
                    self.assertEqual(test.returncode, 0, test.stderr)

    def test_overview_codex_quota_bar_uses_active_account_and_read_only_poll(self):
        import shutil
        import subprocess

        if not shutil.which("node"):
            self.skipTest("Node.js unavailable")
        page = run_detail_body({
            "observer_id": "a" * 16, "run_id": 1, "task_name": "Mock",
            "cycle": "1", "state": "OVER",
        })
        self.assertEqual(page.count('data-codex-quota aria-label='), 1)
        self.assertIn('class="tm-run-quota-card-link" href="/services/"', page)
        self.assertNotIn('tm-run-quota-detail', page)
        self.assertIn('role="progressbar" aria-label="5 小时剩余额度"', page)
        self.assertIn('setInterval(loadCodexQuota,300000)', page)
        self.assertIn('setInterval(refresh,8000)', page)
        fragment = run_detail_body({
            "observer_id": "a" * 16, "run_id": 1, "task_name": "Mock",
            "cycle": "1", "state": "OVER",
        }, fragment=True)
        self.assertIn('data-codex-quota', fragment)
        secondary = run_detail_body({
            "observer_id": "a" * 16, "run_id": 1, "task_name": "Mock",
            "cycle": "1", "state": "OVER",
        }, tab="research", fragment=True)
        self.assertNotIn('data-codex-quota', secondary)
        source = page.split("<script>")[-1].split("</script>", 1)[0]
        anchor = "      let busy=false;"
        self.assertIn(anchor, source)
        source = source.replace(anchor,
            "      window.__quotaTest={paintCodexQuota,loadCodexQuota};\n" + anchor)
        test = r"""
const vm = require('node:vm'), assert = require('node:assert/strict');
const intervals = [], requests = [], followups = [];
const elements = {};
for (const name of ['account','value','week','status']) elements[name]={textContent:''};
const progress = {attrs:{},setAttribute(k,v){this.attrs[k]=v;},
                  removeAttribute(k){delete this.attrs[k];}};
const fill = {style:{width:'0%'}};
let quota = {
  querySelector(key) {
    return ({'[data-quota-account]':elements.account,
      '[data-quota-value]':elements.value,
      '[data-quota-week]':elements.week,'[data-quota-status]':elements.status,
      '[role="progressbar"]':progress,'[data-quota-fill]':fill})[key] || null;
  }
};
const host = {addEventListener(){},
  querySelector(key){return key==='[data-codex-quota]'?quota:null;}};
const document = {hidden:false,addEventListener(){},
  getElementById(id){return id==='tm-run-live'?host:null;}};
const window = {addEventListener(){}};
const now=Math.floor(Date.now()/1000);
let payload = {active_account:'account-2',refreshing:false,accounts:[
  {account_home:'account-1',ok:true,primary:{used_percent:92,remaining_percent:8},
   secondary:{used_percent:9,remaining_percent:91},queried_at:now},
  {account_home:'account-2',ok:true,primary:{used_percent:27,remaining_percent:73},
   secondary:{used_percent:34,remaining_percent:66},queried_at:now}
]};
let offline = false;
const fetch = async (url, options={}) => {
  requests.push({url, options});
  assert.equal(url,'/codex-usage-cached.json');
  assert.equal(options.method,undefined);
  if(offline) throw new Error('network unavailable');
  return {ok:true,json:async()=>payload};
};
vm.runInNewContext(process.argv[1],{
  document, window, location:{search:'', pathname:'/run/aaaaaaaaaaaaaaaa/'},
  history:{}, URLSearchParams, fetch, setInterval:(fn,ms)=>intervals.push(ms),
  setTimeout:(fn,ms)=>{followups.push({fn,ms});return 1;}
});
const {loadCodexQuota,paintCodexQuota}=window.__quotaTest;
(async()=>{
  await loadCodexQuota();
  assert.equal(elements.account.textContent,'Codex 2');
  assert.equal(elements.value.textContent,'5 小时剩余：73%');
  assert.equal(elements.week.textContent,'7 天剩余：66%');
  assert.equal(fill.style.width,'73%');
  assert.equal(progress.attrs['aria-valuenow'],'73');
  assert.deepEqual(intervals.sort((a,b)=>a-b),[8000,300000]);
  assert.equal(requests.length,1,'initial UI load and immediate user refresh share one request');
  const beforeConcurrent=requests.length;
  await Promise.all([loadCodexQuota(),loadCodexQuota()]);
  assert.equal(requests.length,beforeConcurrent+1,'concurrent polling must never duplicate provider RPC');
  // Services displays remaining_percent directly, including decimals.
  payload = {active_account:'account-2',accounts:[{
    account_home:'account-2',ok:true,primary:{used_percent:35.2,remaining_percent:64.8},
    secondary:{used_percent:64,remaining_percent:36},queried_at:now
  }]};
  await loadCodexQuota();
  assert.equal(elements.value.textContent,'5 小时剩余：64.8%');
  assert.equal(elements.week.textContent,'7 天剩余：36%');
  payload.refreshing=true;
  await loadCodexQuota();
  assert.deepEqual(followups.map(x=>x.ms),[25000]);
  payload.refreshing=false;
  await followups[0].fn();
  assert.equal(followups.length,1,'one scheduled followup only');
  quota = null;
  paintCodexQuota();  // isolated live reload may temporarily replace the card
  quota = {querySelector:key=>({
    '[data-quota-account]':elements.account, '[data-quota-value]':elements.value,
    '[data-quota-week]':elements.week,'[data-quota-status]':elements.status,'[role="progressbar"]':progress,
    '[data-quota-fill]':fill})[key]};
  paintCodexQuota();
  assert.equal(fill.style.width,'64.8%');
  payload = {active_account:'account-2',accounts:[{
    account_home:'account-2',ok:true,
    primary:{used_percent:null,remaining_percent:null},secondary:{used_percent:-10,remaining_percent:-10}
  }]};
  await loadCodexQuota();
  assert.equal(elements.value.textContent,'5 小时剩余：64.8%');
  assert.equal(elements.week.textContent,'7 天剩余：36%');
  assert.equal(fill.style.width,'64.8%');
  assert.equal(progress.attrs['aria-valuenow'],'64.8');
  assert.match(elements.status.textContent,/上次成功/);
  payload = {active_account:'unknown',accounts:[{
    account_home:'account-1',ok:true,primary:{used_percent:81,remaining_percent:19}
  }]};
  await loadCodexQuota();
  assert.equal(elements.account.textContent,'Codex 2');
  assert.equal(fill.style.width,'64.8%');
  offline = true;
  await loadCodexQuota();
  assert.equal(elements.value.textContent,'5 小时剩余：64.8%');
  assert.equal(fill.style.width,'64.8%');
  assert.match(elements.status.textContent,/刷新失败/);
  assert.ok(requests.length >= 4);
})().catch(error=>{console.error(error); process.exitCode=1;});
"""
        result = subprocess.run(
            ["node", "-e", test, source], capture_output=True,
            text=True, timeout=12,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_token_popover_is_detached_from_table_and_default_choice_is_transient(self):
        import shutil
        import subprocess
        from tm_observer.run_views import run_detail_body
        if not shutil.which("node"):
            self.skipTest("Node.js unavailable")
        page=run_detail_body({"observer_id":"a"*16,"run_id":1,"task_name":"Mock",
                              "cycle":"1","state":"OVER"})
        source=page.split("<script>")[-1].split("</script>",1)[0]
        anchor="      function select(viewName){"
        self.assertIn(anchor,source)
        source=source.replace(anchor,
            "      window.__testUi={routeChoiceLabels,toggleUsagePopover,closeUsagePopover};\n"+anchor)
        verify=r"""
const vm=require('vm'), assert=require('assert');
const host={addEventListener(){},querySelector(){return null;}}, appended=[];
function el(tag){
  const o={tag,children:[],style:{},attrs:{},dataset:{},isConnected:true,
   setAttribute(k,v){this.attrs[k]=v;},append(...xs){this.children.push(...xs);},
   addEventListener(){},
   focus(){this.focused=true;},remove(){this.removed=true;},
   getBoundingClientRect(){return {width:310,height:220};}};
  return o;
}
const document={
 getElementById:(id)=>id==='tm-run-live'?host:null,
 addEventListener(){},createElement:el,
 body:{append(p){appended.push(p);}}};
const window={addEventListener(){},innerWidth:1200,innerHeight:900};
vm.runInNewContext(process.argv[1],{window,document,location:{search:''},
 setInterval(){},URLSearchParams,history:{}});
const {routeChoiceLabels,toggleUsagePopover,closeUsagePopover}=window.__testUi;
const select={dataset:{routeDefaultValue:'gpt-6.1-sol@codex'},options:[
 {value:'gpt-6.1-sol@codex',textContent:'GPT-6.1 Sol',dataset:{}},
 {value:'gpt-6-luna@codex',textContent:'GPT-6 Luna',dataset:{}}]};
assert.equal(select.options[0].textContent,'GPT-6.1 Sol');
routeChoiceLabels(select,true);
assert.equal(select.options[0].textContent,'GPT-6.1 Sol（默认）');
assert.equal(select.options[1].textContent,'GPT-6 Luna');
routeChoiceLabels(select,false);
assert.equal(select.options[0].textContent,'GPT-6.1 Sol');
const button=el('button');
button.dataset.tokenDetail=JSON.stringify({'任务累计':'10,000','累计输入':'8,000'});
button.getBoundingClientRect=()=>({left:420,right:580,top:190,bottom:240});
toggleUsagePopover(button);
assert.equal(appended.length,1);
const panel=appended[0];
assert.equal(panel.className,'tm-usage-popover');
assert.equal(panel.attrs.role,'dialog');
assert.equal(button.attrs['aria-expanded'],'true');
assert.equal(panel.children[1].children[1].textContent,'10,000');
assert(panel.style.left.endsWith('px')&&panel.style.top.endsWith('px'));
closeUsagePopover();
assert(panel.removed);
assert.equal(button.attrs['aria-expanded'],'false');
"""
        result=subprocess.run(["node","-e",verify,source],capture_output=True,text=True,timeout=12)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_web_theme_uses_existing_dashboard_palette_not_terminal_theme(self):
        from pathlib import Path
        from tm_observer import dashboard_ui
        css = dashboard_ui.STYLE
        scoped = css.split('/* Run detail: Textual\'s information hierarchy, Observatory\'s visual language. */', 1)[1].split('.tm-run-placeholder {', 1)[0]
        self.assertIn('var(--tm-panel)', scoped)
        self.assertIn('var(--tm-border)', scoped)
        self.assertIn('border-radius:12px', scoped)
        self.assertIn('grid-template-columns:minmax(0,1.3fr) minmax(310px,.86fr)', scoped)
        self.assertIn('.tm-run-events-recent', scoped)
        self.assertIn('.tm-token-disclosure', scoped)
        self.assertIn('.tm-run-events-scroll { height:320px; max-height:50dvh; overflow-y:auto', scoped)
        self.assertNotIn('#00e5f5', scoped)
        self.assertNotIn('#00c4d7', scoped)
        self.assertNotIn('height:calc(100dvh', scoped)
        self.assertNotIn('font-family:ui-monospace, SFMono-Regular', scoped)

    def test_agent_textual_workflow_becomes_semantic_table_without_recomputing_data(self):
        from tm_observer.run_views import _tui_workflow_table, _tui_summary
        raw = ('AGENT/STAGE    MODEL                         COST      TIME     STATE\n'
               + f'{"Investigator":<13}  {"GPT-6 Sol · low":<24}  {"—":>8}  {"02:20":>8}  {"running":>8}')
        table = _tui_workflow_table(raw, settings={'research_lead': {'model':'gpt-6-sol', 'provider':'CodexHTTPS', 'reasoning_effort':'low'}}, catalog=[{'model':'gpt-6-sol', 'provider':'CodexHTTPS'}])
        self.assertIn('<th scope="col">MODEL</th>', table)
        self.assertIn('gpt-6-sol@CodexHTTPS', table)
        self.assertIn('<th scope="col">EFFORT</th>', table)
        self.assertNotIn('<th scope="col">操作</th>', table)
        self.assertNotIn('data-agent-apply', table)
        self.assertNotIn('class="tm-tui-default"', table)
        self.assertIn('data-route-default-value=', table)
        self.assertIn('data-route-model', table)
        self.assertIn('data-route-effort', table)
        self.assertIn('<col class="tm-col-model">', table)
        self.assertIn('<col class="tm-col-effort">', table)
        from tm_observer.dashboard_ui import STYLE
        self.assertIn('table-layout:fixed', STYLE)
        self.assertIn('.tm-col-tokens { width:174px; }', STYLE)
        self.assertIn('max-width:160px', STYLE)
        self.assertIn('position:fixed; z-index:2200', STYLE)
        self.assertIn('.tm-col-model { width:155px; }', STYLE)
        self.assertIn('.tm-col-effort { width:96px; }', STYLE)
        self.assertIn('Investigator', table)
        self.assertIn('tm-tui-state-running', table)
        self.assertIn('02:20', table)
        self.assertNotIn('<pre', table)
        bad = _tui_workflow_table('INVALID')
        self.assertIn('<pre', bad)
        injected = _tui_workflow_table(raw.replace('Investigator', '<script>'))
        self.assertNotIn('<script>', injected)
        self.assertIn('&lt;script&gt;', injected)
        header = _tui_summary('Process RUNNING   State RUNNING\nCycle 1 / 3     Phase investigator:running       Elapsed 30:50\nStarted  10-11T16:26:08  Cycle start 10-11T16:26:08  Cost — · Tokens 418,480')
        self.assertIn('tm-tui-kv-grid', header)
        self.assertNotIn('investigator:running', header)
        from bs4 import BeautifulSoup
        self.assertEqual(
            [node.get_text(' ', strip=True) for node in BeautifulSoup(header,'html.parser').select('.tm-tui-kv dt')],
            ['State','Cycle','Cost','Elapsed','Started','Cycle start'])
        self.assertIn('418,480', header)
        self.assertIn('10-11 16:26:08', header)
        self.assertNotIn('10-11T16:26:08', header)
        self.assertIn('tm-tui-process-green', header)
        self.assertIn('data-tui-op="max_cycles"', header)
        with_attention = _tui_summary(
            'Process STOPPED   State NEEDS ATTENTION\n'
            'Cycle 1 / 3     Phase investigator:goal_suspended  Elapsed 36:50\n'
            'Started  10-11T16:26:08  Cycle start 10-11T16:26:08  Cost — · Tokens 418,480\n'
            'NEEDS ATTENTION\nReason  Missing decisive evidence\nAction  Review evidence'
        )
        self.assertIn('class="tm-tui-attention"', _tui_summary('Process STOPPED   State NEEDS ATTENTION\nCycle 1 / 3     Phase suspended            Elapsed 36:50\nStarted  10-11T16:26:08  Cycle start 10-11T16:26:08  Cost —', attention={'reason':'Only Challenger-qualified candidate', 'requested_action':'Review', 'resume_behavior':'Resume at checkpoint','takeover':'disabled'}))
        self.assertIn('Missing decisive evidence', with_attention)
        self.assertIn('tm-tui-process-red', with_attention)

    def test_idle_literature_uses_frozen_route_and_status_under_agent(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import _tui_workflow_table
        names = ("Research Lead", "Challenger", "Investigator", "Literature", "Judge", "Reporter")
        raw = 'AGENT/STAGE    MODEL                         COST      TIME     STATE\n'
        raw += "\n".join(
            f'{name:<13}  {"GPT-6 Luna · low":<24}  {"—":>8}  {"00:00":>8}  {("idle" if name == "Literature" else "done"):>8}'
            for name in names)
        catalog = [{"model":"gpt-6-luna","provider":"CodexHTTPS","label":"GPT-6 Luna"},
                   {"model":"gpt-6-sol","provider":"CodexHTTPS","label":"GPT-6 Sol"}]
        data = {"literature":{"model":"gpt-6-luna","provider":"CodexHTTPS","reasoning_effort":"low"}}
        page = BeautifulSoup(_tui_workflow_table(raw,settings=data,catalog=catalog),"html.parser")
        literature = page.select_one('tr[data-agent-role="literature"]')
        self.assertEqual(literature.select_one('[data-route-model] option[selected]')['value'],
                         'gpt-6-luna@CodexHTTPS')
        self.assertEqual(literature.select_one('[data-route-effort] option[selected]')['value'], 'low')
        self.assertEqual(literature['data-saved-model'], 'gpt-6-luna@CodexHTTPS')
        self.assertEqual(literature['data-saved-effort'], 'low')
        self.assertEqual(literature.select_one('td:first-child .tm-tui-agent-state').get_text(), 'idle')
        self.assertEqual(len(literature.select('td')), 6)
        self.assertEqual(len(page.select('thead th')), 6)
        self.assertNotIn('STATE', [th.get_text() for th in page.select('thead th')])
        self.assertEqual(literature.select_one('select[data-route-model]').get('aria-label'),'Literature 模型')

    def test_codex_default_effort_uses_existing_medium_choice_without_saving(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import _tui_workflow_table
        raw = 'AGENT/STAGE    MODEL                         COST      TIME     STATE\n'
        raw += f'{"Research Lead":<13}  {"GPT-6 Sol":<24}  {"—":>8}  {"00:12":>8}  {"running":>8}'
        catalog = [
            {'model': 'gpt-6-sol', 'provider': 'CodexHTTPS'},
            {'model': 'gpt-5.6-sol', 'provider': 'FixtureProvider'},
        ]
        def rendered(settings):
            soup = BeautifulSoup(_tui_workflow_table(raw, settings=settings, catalog=catalog), 'html.parser')
            row = soup.select_one('tr[data-agent-role="research_lead"]')
            return row, row.select_one('select[data-route-effort]')

        row, effort = rendered({'research_lead': {
            'model':'gpt-6-sol','provider':'CodexHTTPS','reasoning_effort':'',
        }})
        self.assertEqual(effort.select_one('option[selected]').get('value'), 'medium')
        self.assertEqual(row.get('data-saved-effort'), 'medium')
        self.assertEqual(effort.get('data-route-default-value'), 'medium')
        self.assertNotIn('value="" selected disabled', str(effort))
        self.assertNotIn('tm-tui-pending', str(row))

        row, effort = rendered({'research_lead': {
            'model':'gpt-6-sol','provider':'CodexHTTPS','reasoning_effort':'high',
        }})
        self.assertEqual(effort.select_one('option[selected]').get('value'), 'high')

        row, effort = rendered({'research_lead': {
            'model':'gpt-6-sol','provider':'CodexHTTPS','reasoning_effort':'',
            'pending': {'model':'gpt-6-sol','provider':'CodexHTTPS','reasoning_effort':'xhigh'},
        }})
        self.assertEqual(effort.select_one('option[selected]').get('value'), 'xhigh')

        row, effort = rendered({'research_lead': {
            'model':'gpt-5.6-sol','provider':'FixtureProvider','reasoning_effort':'',
        }})
        self.assertEqual(effort.select_one('option[selected]').get('value'), '')
        self.assertEqual(row.get('data-saved-effort'), '—')

    def test_agent_role_colors_are_shared_by_timeline_and_scientific_cards(self):
        from bs4 import BeautifulSoup
        from tm_observer.dashboard_ui import STYLE
        from tm_observer.run_views import _recent_timeline, _investigation_card, _judge_card

        events = [
            ("Research Lead", "research_lead"),
            ("Challenger", "challenger"),
            ("Investigator", "investigator"),
            ("Literature", "literature"),
            ("Judge", "judge"),
            ("Reporter", "reporter"),
        ]
        lines = [f"21:00:{idx:02d}  {name} done 00:10"
                 for idx, (name, _) in enumerate(events)]
        lines.extend([
            "21:00:06  Judge verdict: open",
            "21:00:07  Cycle started · 61",
            "21:00:08  Workflow failed: unsafe <script>alert(1)</script>",
            "21:00:09  investigatorx done",
        ])
        html = BeautifulSoup(_recent_timeline("\n".join(lines)), "html.parser")
        rows = html.select(".tm-run-events-recent li")
        self.assertEqual(len(rows), 9)  # Cycle started is a group header
        self.assertEqual(len(html.select("details.tm-cycle-group")), 2)
        self.assertIn("Cycle 61", html.select("details.tm-cycle-group")[1].summary.get_text())
        self.assertTrue(all(not group.has_attr("open") for group in html.select("details.tm-cycle-group")))
        for idx, (name, key) in enumerate(events):
            self.assertIn("tm-role-" + key, rows[idx]["class"])
            self.assertEqual(rows[idx].select_one(".tm-log-role").get_text(), name)
            self.assertIn("done 00:10", rows[idx].get_text())
        self.assertIn("tm-role-judge", rows[6]["class"])
        self.assertIn("tm-log-milestone", rows[6]["class"])
        self.assertIn("tm-log-attention", rows[7]["class"])
        self.assertFalse(any(cls.startswith("tm-role-") for cls in rows[7]["class"]))
        self.assertNotIn("<script>", str(html))
        self.assertFalse(any(cls.startswith("tm-role-") for cls in rows[8]["class"]))

        investigation = BeautifulSoup(_investigation_card(
            "Attempt          1\nInv tokens       1.0k / 10.0k\nCurrent executor Investigator"),
            "html.parser")
        judge = BeautifulSoup(_judge_card("Status: idle\nReason: -"), "html.parser")
        self.assertIn("tm-role-investigator", investigation.section["class"])
        self.assertIn("tm-agent-review", investigation.section["class"])
        self.assertIn("tm-role-judge", judge.section["class"])
        self.assertIn("tm-agent-review", judge.section["class"])
        self.assertIn("tm-role-investigator", _investigation_card(""))
        for role in ("research_lead", "challenger", "investigator", "literature", "judge", "reporter"):
            self.assertIn(".tm-role-" + role + " { --role-accent:", STYLE)
        self.assertIn(".tm-log-item .tm-log-role { color:var(--role-accent)", STYLE)
        # Three cards share flat geometry, but inherit their OWN Agent colors.
        # Frozen Research Lead remains blue; Investigator purple, Judge rose.
        self.assertIn(".tm-log-item[class*=\"tm-role-\"] { border-left-color:var(--role-accent)", STYLE)
        self.assertIn(".tm-role-research_lead { --role-accent:#60a5fa", STYLE)
        self.assertIn(".tm-tui-center { --tm-science-card-bg:#1c2431; --tm-science-card-border:#60a5fa; --tm-science-card-heading:#60a5fa;", STYLE)
        self.assertIn("--tm-science-card-value:#dbeafe", STYLE)
        self.assertIn("--tm-science-card-action:#93c5fd", STYLE)
        self.assertIn(".tm-role-investigator { --role-accent:#c084fc; --role-background:#30203f;", STYLE)
        self.assertIn(".tm-role-judge { --role-accent:#fb7185; --role-background:#3a1c27;", STYLE)
        self.assertIn(".tm-tui-center > .tm-agent-review { border:1px solid var(--role-accent); border-radius:10px; background:var(--role-background); padding:14px 17px;", STYLE)
        self.assertIn(".tm-tui-center > .tm-agent-review > .tm-tui-section-title { color:var(--role-accent); }", STYLE)
        self.assertIn(".tm-tui-center > .tm-agent-review .tm-insight-stat strong { color:var(--role-accent); }", STYLE)
        self.assertIn(".tm-tui-center > .tm-agent-review .tm-budget-track > span { background:var(--role-accent); }", STYLE)
        self.assertNotIn(".tm-tui-center > .tm-agent-review { border-left-width:3px", STYLE)
        self.assertNotIn("background:linear-gradient(110deg,var(--role-background)", STYLE)
        self.assertNotIn("linear-gradient(110deg,var(--tm-science-card-tint)", STYLE)
        self.assertIn("border:1px solid var(--tm-science-card-border,#405070)", STYLE)

    def test_recent_cycles_group_all_events_and_preserve_exact_source_links(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import _recent_timeline

        messages = [
            "Research Lead done 00:10", "Cycle started · 60",
            "Investigator done 01:10", "Judge verdict: open",
            "Cycle started · 61", "Investigator · skill loaded · network-graph",
            "Workflow failed: holdout missing",
            "Cycle started · 62", "Research Lead done 00:05",
        ]
        events = [{"at": f"2026-10-12T21:00:{i:02d}+08:00",
                   "kind": "LLM_TURN_ENDED", "role": "investigator",
                   "hint": msg} for i, msg in enumerate(messages)]
        raw = "\n".join(f"21:00:{i:02d}  {msg}" for i, msg in enumerate(messages))
        html = BeautifulSoup(_recent_timeline(raw, events), "html.parser")
        groups = html.select("details.tm-cycle-group")
        self.assertEqual(len(groups), 4)
        self.assertEqual([len(group.select(".tm-log-item")) for group in groups],
                         [1, 2, 2, 1])
        self.assertEqual([group.select_one(".tm-cycle-label").get_text()
                          for group in groups],
                         ["较早记录", "Cycle 60", "Cycle 61", "Cycle 62"])
        self.assertEqual([bool(group.has_attr("open")) for group in groups],
                         [False, False, False, False])
        self.assertEqual(len({group["data-cycle-group-key"] for group in groups}), 4)
        self.assertIn("21:00:07", groups[-1].summary.get_text())
        self.assertIn("1 条", groups[-1].summary.get_text())
        self.assertEqual(len(html.select(".tm-run-events-recent li")), 6)
        links = html.select("a.tm-recent-select")
        self.assertEqual(len(links), 6)
        self.assertEqual(links[-1]["data-recent-at"], events[-1]["at"])
        self.assertIn("tab=events", links[-1]["href"])
        self.assertIn("tm-log-attention", groups[2].select(".tm-log-item")[-1]["class"])
        # When no start marker has been recorded, a single unknown group is
        # visible; do not fabricate the current Cycle based on task state.
        unknown = BeautifulSoup(_recent_timeline(
            "21:00:01  Investigator done"), "html.parser")
        self.assertEqual(len(unknown.select("details.tm-cycle-group")), 1)
        self.assertIn("较早记录", unknown.summary.get_text())
        self.assertNotIn("open", unknown.details.attrs)

    def test_legacy_cycle_prefix_is_a_real_start_marker(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import _recent_timeline
        raw = ("21:00:00  Cycle started · cycle-0057\n"
               "21:00:01  Research Lead done 00:20")
        dom = BeautifulSoup(_recent_timeline(raw), "html.parser")
        group = dom.select_one("details.tm-cycle-group")
        self.assertIsNotNone(group)
        self.assertIn("Cycle 0057", group.summary.get_text())
        self.assertEqual(len(group.select(".tm-log-item")), 1)
        self.assertNotIn("较早记录", dom.get_text())

    def test_repeat_cycle_start_on_resume_remains_inside_same_group(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import _recent_timeline
        raw = "\n".join([
            "21:00:00  Cycle started · 69",
            "21:00:01  Research Lead done 00:10",
            "21:00:02  Cycle started · 69",
            "21:00:03  Investigator done 01:15",
            "21:00:04  Cycle started · 70",
            "21:00:05  Judge verdict: open",
        ])
        dom = BeautifulSoup(_recent_timeline(raw), "html.parser")
        groups = dom.select("details.tm-cycle-group")
        self.assertEqual([g.select_one(".tm-cycle-label").get_text()
                          for g in groups], ["Cycle 69", "Cycle 70"])
        self.assertEqual([len(g.select(".tm-log-item")) for g in groups], [3, 1])
        self.assertIn("再次进入运行", groups[0].select(".tm-log-item")[1].get_text())
        self.assertEqual([g["data-cycle-group-key"] for g in groups], ["cycle-69", "cycle-70"])
        self.assertNotIn("新 Cycle 启动", dom.get_text())
        self.assertEqual([g.has_attr("open") for g in groups], [False, False])

    def test_repeat_cycle_start_merges_by_identity_and_retains_source_links(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import _recent_timeline
        labels = [
            "Cycle started · 69", "Investigator done",
            "Cycle started · 70", "Research Lead done",
            "Cycle started · 69", "Judge verdict: open",
        ]
        raw = "\n".join(f"08:38:{n:02d}  {label}" for n, label in enumerate(labels))
        source = [{"at":f"2026-10-12T08:38:{n:02d}+08:00",
                   "kind":"CYCLE_STARTED" if label.startswith("Cycle started") else "AGENT_STARTED",
                   "role":"","hint":label} for n,label in enumerate(labels)]
        soup = BeautifulSoup(_recent_timeline(raw, source), "html.parser")
        groups = soup.select("details.tm-cycle-group")
        self.assertEqual(len(groups),2)
        self.assertEqual([group["data-cycle-group-key"] for group in groups], ["cycle-69","cycle-70"])
        self.assertEqual([len(group.select(".tm-log-item")) for group in groups],[3,1])
        rows = groups[0].select(".tm-log-item")
        self.assertIn("再次进入运行",rows[-2].get_text())
        self.assertEqual(rows[-2].a["data-recent-at"],source[4]["at"])
        self.assertIn("kind=CYCLE_STARTED",rows[-2].a["href"])
        self.assertIn("08:38:00",groups[0].summary.get_text())
        self.assertNotIn("新 Cycle 启动",soup.get_text())

    def test_cycle_group_refresh_is_keyed_not_positional(self):
        from tm_observer.run_views import run_detail_body
        from pathlib import Path
        import tempfile, json
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            cycle = root / "1"
            cycle.mkdir()
            (cycle / "run_state.json").write_text(json.dumps(
                {"master_id": 1, "cycle_id": "1", "status": "RUNNING"}),
                encoding="utf-8")
            page = run_detail_body({"observer_id":"a"*16, "run_id":1,
                "cycle":"1", "state":"RUNNING", "research_root":str(root),
                "cycle_dir":str(cycle), "task_name":"Test"})
        self.assertIn("previousCycleOpen=new Map", page)
        self.assertIn("data-cycle-group-key", page)
        self.assertIn("previousCycleOpen.has(key)", page)
        self.assertIn("detail.open=previousCycleOpen.get(key)", page)
        self.assertIn("historyScroll=new Map", page)
        self.assertIn("timeline.scrollHeight:previous.top", page)
        self.assertIn("expandedImportant=new Set", page)
        self.assertIn("historyView='recent'", page)

    def test_dashboard_compact_reason_history_and_scientific_drilldown(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import (_run_focus, _recent_timeline,
                                          _investigation_card, _judge_card, _token_breakdown)
        long_reason = "agent-generated validation cannot claim holdout or gate identity for the frozen task <unsafe> tail"
        activity = ("Agent       -\nState       FAILED   | phase: failed\n"
                    "Purpose     waiting for work\nLast action truncated...")
        focus = BeautifulSoup(_run_focus({"activity": activity}, "FAILED",
                            {"last_event": long_reason}), "html.parser")
        self.assertEqual(focus.select_one(".tm-focus-mark").get_text(), "FAILED")
        self.assertEqual(focus.select_one(".tm-focus-message").get_text(), long_reason)
        self.assertNotIn("<unsafe>", str(focus))
        self.assertIn("原始 Activity", focus.get_text())
        self.assertIsNone(focus.select_one(".tm-focus-latest"))
        linked = BeautifulSoup(_run_focus({"activity": activity}, "RUNNING",
            {"current_activity": "Agent reasoning"},
            [{"at": "2026-10-13T00:18:52+08:00", "kind": "TOOL_ACTIVITY",
              "role": "investigator", "hint": "python freeze_g8_design.py <unsafe>"}],
            "72"), "html.parser")
        latest = linked.select_one(".tm-focus-latest")
        self.assertIn("python freeze_g8_design.py", latest.get_text())
        self.assertNotIn("<unsafe>", str(linked))
        self.assertIn("jump=latest", latest["href"])
        self.assertIn("cycle=72", latest["href"])
        self.assertIn("tab=events", latest["href"])
        preferred = BeautifulSoup(_run_focus({"activity": activity}, "RUNNING",
            {"current_activity": "Agent reasoning",
             "latest_dialogue_at": "2026-10-13T00:18:52+08:00",
             "latest_dialogue_kind": "TOOL_ACTIVITY",
             "latest_dialogue_role": "investigator",
             "latest_dialogue_hint": "command · python freeze_g8_design.py"},
            [{"at": "2026-10-13T00:14:46+08:00", "kind": "CYCLE_STARTED",
              "role": "", "hint": "Cycle started · 72"}],
            "72"), "html.parser")
        preferred_link = preferred.select_one(".tm-focus-latest")
        self.assertIn("python freeze_g8_design.py", preferred_link.get_text())
        self.assertNotIn("Cycle started", preferred_link.get_text())
        self.assertIn("kind=TOOL_ACTIVITY", preferred_link["href"])
        log = ("19:00:00  Research Lead done 00:10\n"
               "19:01:00  Challenger done 00:10\n"
               "19:02:00  Investigator done 00:10\n"
               "19:03:00  Judge verdict: open\n"
               "19:04:00  Cycle started · 61\n"
               "19:05:00  Investigator done 00:10\n"
               "19:06:00  Workflow failed: gate identity")
        timeline = BeautifulSoup(_recent_timeline(log), "html.parser")
        self.assertEqual(len(timeline.select(".tm-run-events-recent li")), 6)
        self.assertIn("Workflow failed", timeline.get_text())
        groups = timeline.select("details.tm-cycle-group")
        self.assertEqual(len(groups), 2)
        self.assertNotIn("open", groups[0].attrs)
        self.assertNotIn("open", groups[-1].attrs)
        self.assertIn("Cycle 61", groups[-1].summary.get_text())
        self.assertEqual([len(group.select(".tm-log-item")) for group in groups], [4, 2])
        self.assertIsNone(timeline.select_one(".tm-timeline-history"))
        scroll = timeline.select_one(".tm-run-events-scroll")
        self.assertEqual(scroll["tabindex"], "0")
        self.assertEqual(scroll["role"], "region")
        self.assertIn("滚动", scroll["aria-label"])
        full_log = "\n".join(f"21:00:{i:02d}  event {i}" for i in range(67))
        full_view = BeautifulSoup(_recent_timeline(full_log), "html.parser")
        self.assertEqual(len(full_view.select(".tm-run-events-recent li")), 67)
        self.assertIn("event 0", full_view.get_text())
        self.assertIn("event 66", full_view.get_text())
        self.assertNotIn("完整执行历史", full_view.get_text())
        self.assertIn("data-cycle-group-key", str(timeline))
        from tm_observer.dashboard_ui import STYLE
        self.assertIn(".tm-cycle-summary { display:flex", STYLE)
        self.assertIn(".tm-cycle-summary { display:flex", STYLE)
        self.assertIn(".tm-cycle-summary::before { content:", STYLE)
        self.assertNotIn(".tm-cycle-caption {", STYLE)
        inv = BeautifulSoup(_investigation_card(
            "Attempt          1\nRound            0 (history; no round cap)\n"
            "Inv tokens       1,341.0k / 50,000.0k\n"
            "Current executor Investigator\nHistory\nA1 R1 Investigator * running"), "html.parser")
        self.assertIn("1", inv.select_one(".tm-insight-stat").get_text())
        self.assertEqual(inv.select_one(".tm-budget-track")["aria-valuenow"], "2.7")
        self.assertEqual(inv.select_one(".tm-tui-section-title").get_text(strip=True), "Investigation当前 Cycle")
        judge = BeautifulSoup(_judge_card(
            "Branch Judge (local milestone)\nStatus: idle\nReworks: 0 / 3\n"
            "Previous verdict: -\nReason: -\nObjections: (none)"), "html.parser")
        self.assertIn("暂无可展示", judge.get_text())
        self.assertNotIn("Rework 0", judge.select_one(".tm-tui-section-title").get_text())
        totals = {"cycle": {"tokens_in":1000, "cached_input_tokens":900, "tokens_out":100, "reasoning_tokens":30},
                  "project": {"tokens_in":5000, "cached_input_tokens":4000, "tokens_out":500, "reasoning_tokens":120}}
        tokens = BeautifulSoup(_token_breakdown(
            "CURRENT TURN\nInput        300\n  Cached     270  90.0%\n"
            "  Uncached   30\nOutput       30\nReasoning    5\nCYCLE USAGE",totals),"html.parser")
        self.assertIn("90.0%",tokens.select_one(".tm-token-disclosure > summary").get_text())
        self.assertEqual(len(tokens.select(".tm-token-scope")),3)
        self.assertNotIn("open",tokens.select_one(".tm-token-disclosure").attrs)
        self.assertIn("300",tokens.select_one(".tm-token-scope").get_text())
        self.assertNotIn("Research Lead",tokens.select_one(".tm-token-disclosure > summary").get_text())

    def test_saved_cycle_sections_and_bounded_tail(self):
        from tm_observer.run_monitor import TABS, data, tail_lines
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cycle = root / 'cycle-0004'
            cycle.mkdir()
            (cycle / 'run_state.json').write_text(json.dumps({
                'status': 'NEEDS_ATTENTION', 'phase': 'review',
                'current_agent': 'judge', 'project_tokens_total': 987,
                'current_activity': '<unsafe activity>',
                'judge_objections': ['missing evidence'],
                'agents': {'judge': {'state': 'waiting', 'model': 'x-model'}},
            }), encoding='utf-8')
            (cycle / 'research_state.json').write_text(json.dumps({'status': 'NEEDS_ATTENTION'}), encoding='utf-8')
            (cycle / 'orchestrator.log').write_text('older\n' * 300 + 'password=abc123\n', encoding='utf-8')
            (cycle / 'runtime_events.jsonl').write_text(json.dumps({'event':'JUDGE', 'stage':'goal_completion','content':'PRIVATE'}) + '\n', encoding='utf-8')
            run = {'observer_id':'a'*16, 'research_root':str(root), 'cycle_dir':str(cycle), 'task_name':'task', 'run_id':1, 'cycle':'cycle-0004', 'state':'NEEDS_ATTENTION'}
            assert data(run)['current_agent'] == 'judge'
            assert len(tail_lines(run,'orchestrator.log',limit=30)) <= 30
            assert '[redacted]' in tail_lines(run,'orchestrator.log',limit=30)[-1]
            from tm_observer.run_views import run_detail_body
            html = run_detail_body(run, tab='judge')
            self.assertIn('missing evidence', html)
            self.assertIn('x-model', html)
            self.assertIn('setInterval(refresh,8000)', html)
            self.assertIn('运行动态', html)
            self.assertIn('最近进展', html)
            self.assertIn('data-tui-pane="investigation"', html)
            self.assertIn('data-tui-pane="judge"', html)
            self.assertIn('Token 构成', html)
            self.assertNotIn('<unsafe activity>', run_detail_body(run,tab='activity'))
            self.assertIn('&lt;unsafe activity&gt;', run_detail_body(run,tab='activity'))
            self.assertEqual(len(TABS), 9)
            fragment = run_detail_body(run,tab='events',fragment=True)
            self.assertIn('tm-stage-reader', fragment)
            self.assertNotIn('PRIVATE', fragment)
            self.assertNotIn('<script>', fragment)
            self.assertNotIn('canvas-md',fragment)
            (cycle/'orchestrator.log').unlink()
            (cycle/'orchestrator.log').symlink_to(cycle/'run_state.json')
            self.assertEqual(tail_lines(run,'orchestrator.log'), [])


class RunBrowserNavigationTests(unittest.TestCase):
    def test_click_row_and_keyboard_activate_task_link(self):
        from bs4 import BeautifulSoup
        html_page = run_index_body({'runs': RunListTests.rows(1)})
        doc = BeautifulSoup(html_page, 'html.parser')
        row = doc.select_one('table.tm-run-table tbody tr')
        self.assertIsNotNone(row)
        self.assertEqual(row['tabindex'], '0')
        self.assertEqual(row['role'], 'link')
        self.assertEqual(row['data-task-url'], '/run/0000000000000000/')
        script = html_page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.assertIn("table.addEventListener('click'", script)
        self.assertIn("table.addEventListener('keydown'", script)
        link = row.select_one('a.tm-run-task-link')
        self.assertEqual(link['href'], '/run/0000000000000000/')
        self.assertEqual(link.get_text(), 'Task 00')


class OperatorUxTests(unittest.TestCase):
    def test_full_attention_role_colors_and_events_search(self):
        from tm_observer.run_views import _tui_summary, run_detail_body
        from tm_observer.run_events_ui import render
        full='Only Challenger-qualified candidate at the scientific frontier; ' + 'boundary ' * 100
        header=('Process STOPPED   State NEEDS ATTENTION\n'
                'Cycle 1 / 3     Phase investigator:goal_suspended  Elapsed 36:50\n'
                'Started  10-11T16:26:08  Cycle start 10-11T16:26:08  Cost —')
        html=_tui_summary(header, attention={
            'reason':full,'requested_action':'Review the request and decide whether to resume.',
            'resume_behavior':'Resume continues from persisted checkpoint.','takeover':'disabled'})
        self.assertIn(full,html)
        self.assertEqual(html.count('<dt>Reason</dt>'),1)
        self.assertNotIn('theory-master resume 55',html)
        feed=render({'events':[
            {'role':'research_lead','kind':'AGENT_STARTED','time':'2026-10-11','content':'start'},
            {'role':'judge','kind':'LLM_MESSAGE','time':'2026-10-11','content':'decision'},
            {'role':'investigator','kind':'LLM_FAILED','time':'2026-10-11','content':'<bad> secret'},
        ],'events_partial':True})
        self.assertIn('data-event-filter="role"',feed)
        self.assertIn('data-event-filter="category"',feed)
        self.assertIn('data-event-filter="search"',feed)
        for role in ('research_lead','judge','investigator'):
            self.assertIn('tm-role-'+role,feed)
        self.assertIn('data-event-category="error"',feed)
        self.assertIn('&lt;bad&gt;',feed)
        self.assertIn('早期记录未加载',feed)


class AccountingDisplayTests(unittest.TestCase):
    def test_fees_and_tokens_are_independent_and_scoped(self):
        from tm_observer.run_views import _tui_workflow_table
        from bs4 import BeautifulSoup
        raw = (
            'AGENT/STAGE    MODEL                         COST      TIME     STATE\n'
            'Research Lead  GPT-6.1 Sol · high               —     08:10     done\n'
            'Challenger     GPT-6.1 Sol · medium             —     03:20     done\n'
            'Investigator   GPT-6.1 Sol · low                —  01:12:30  running\n'
            'Literature     GPT-6 Luna · low                 —     00:00     idle\n'
            'Judge          GPT-6.1 Sol · medium             —     00:00     idle\n'
            'Reporter       GPT-6 Luna · low           ~$0.011     01:04     done'
        )
        def metric(total, money=None, **extra):
            return dict(tokens_in=total-101 if total else None, tokens_out=101 if total else None,
                        tokens_total=total,cached_input_tokens=(total-101)//2 if total else None,
                        reasoning_tokens=5 if total else None,
                        cost=money or {"source":"unavailable","amount":None,"currency":None,
                                       "model_calls":1 if total else 0})
        roles = {
            "research_lead":{"project":metric(449439),"cycle":metric(91271)},
            "investigator":{"project":metric(14569492),"cycle":metric(4911456)},
            "reporter":{"project":metric(243685,{"source":"estimated","amount":0.01065922,
                                                  "currency":"USD","model_calls":6}),
                        "cycle":metric(None)}
        }
        totals={"project":metric(17358137,{"source":"estimated","amount":0.01065922,"currency":"USD",
                                         "model_calls":29,"priced_model_calls":6}),
                "cycle":metric(5473377)}
        soup=BeautifulSoup(_tui_workflow_table(raw,role_usage=roles),"html.parser")
        self.assertEqual(len(soup.select("th")),6)
        self.assertEqual(len(soup.select('tr[data-agent-role]')),6)
        self.assertEqual([header.get_text() for header in soup.select('thead th')],
                         ['AGENT / STAGE', 'MODEL', 'EFFORT', 'COST', 'TOKENS（累计）', 'TIME'])
        self.assertEqual(len(soup.select('colgroup col')), 6)
        literature = soup.select_one('tr[data-agent-role="literature"]')
        self.assertEqual(len(literature.select('td')), 6)
        self.assertEqual(literature.select_one('td:first-child strong').get_text(), 'Literature')
        self.assertEqual(literature.select_one('td:first-child .tm-tui-agent-state').get_text(), 'idle')
        self.assertIn('tm-tui-state-idle', literature.select_one('.tm-tui-agent-state')['class'])
        self.assertIsNotNone(literature.select_one('select[data-route-model]'))
        self.assertIsNotNone(literature.select_one('select[data-route-effort]'))
        self.assertEqual(literature.select_one('[data-route-model] option[selected]').get('value'), '')
        self.assertEqual(literature.select_one('[data-route-effort] option[selected]').get('value'), '')
        self.assertEqual(literature.select_one('[data-route-model] option[selected]').get_text(), '未配置')
        self.assertEqual(len(soup.select('tr[data-agent-role="research_lead"] td')), 6)
        self.assertEqual(soup.select_one('tr[data-agent-role="research_lead"] td:first-child .tm-tui-agent-state').get_text(), 'done')
        lead=soup.select_one('tr[data-agent-role="research_lead"]')
        self.assertIn("449,439",lead.get_text())
        self.assertIn("本轮 91,271",lead.get_text())
        self.assertIn("未计价",lead.get_text())
        detail=json.loads(lead.select_one('[data-token-detail]')['data-token-detail'])
        self.assertIn("其中缓存输入",detail)
        self.assertEqual(detail["任务累计"],"449,439")
        self.assertEqual(detail["当前 Cycle"],"91,271")
        investigator=soup.select_one('tr[data-agent-role="investigator"]')
        self.assertIn("14,569,492",investigator.get_text())
        self.assertIn("本轮 4,911,456",investigator.get_text())
        self.assertIn("未计价",investigator.get_text())
        reporter=soup.select_one('tr[data-agent-role="reporter"]')
        self.assertIn("~$0.011 估",reporter.get_text())
        self.assertIn("本轮未记录",reporter.get_text())
        self.assertIn("243,685",reporter.get_text())
        self.assertIsNone(soup.select_one(".tm-tui-usage-summary"))
        self.assertIsNone(soup.select_one(".tm-tui-metric-help"))
        self.assertNotIn("任务累计 Token", soup.get_text())
        self.assertNotIn("当前 Cycle Token", soup.get_text())
        self.assertNotIn("已计价费用", soup.get_text())
        self.assertNotIn("Token 是 SDK 实际留存的用量", soup.get_text())
        self.assertNotIn("COST 只计已知价格的调用", soup.get_text())
        self.assertIsNotNone(soup.select_one('tr[data-agent-role="research_lead"] .tm-usage-hit'))
        self.assertIsNotNone(soup.select_one('tr[data-agent-role="reporter"] .tm-usage-cost'))

    def test_unrecorded_is_not_zero_and_html_escapes_cost_currency(self):
        from tm_observer.run_views import _cost_label,_tokens
        self.assertEqual(_tokens(None),"未记录")
        self.assertEqual(_tokens(0),"0")
        self.assertEqual(_cost_label({"amount":None,"model_calls":1}),"未计价")
        self.assertEqual(_cost_label({"amount":None,"model_calls":0}),"—")
        self.assertEqual(_cost_label({"amount":0.01,"currency":"USD","source":"estimated"}),"~$0.010 估")


class EventsFocusedReaderAndUsageHitTests(unittest.TestCase):
    def test_events_hides_management_but_primary_retains_it(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from bs4 import BeautifulSoup
        from tm_observer.run_views import run_detail_body
        with TemporaryDirectory() as folder:
            root=Path(folder)
            cycle=root/"58"
            cycle.mkdir()
            (cycle/".theory-master-cycle").write_text("fixture",encoding="utf-8")
            (cycle/"run_state.json").write_text(json.dumps({
                "status":"RUNNING","cycle_id":"58","master_id":1
            }),encoding="utf-8")
            task={"observer_id":"8dd38a9977eef7e5","research_root":str(root),
                  "cycle_dir":str(cycle),"cycle":"58","task_name":"Test",
                  "run_id":1,"state":"RUNNING"}
            primary=BeautifulSoup(run_detail_body(task,tab="activity",canvas_available=True),"html.parser")
            events=BeautifulSoup(run_detail_body(task,tab="events",canvas_available=True),"html.parser")
            self.assertIsNotNone(primary.select_one("#tm-web-settings"))
            self.assertIsNone(primary.select_one("#tm-tui-controls"))
            self.assertIsNotNone(primary.select_one("#tm-operator[hidden]"))
            self.assertIsNotNone(primary.select_one(".tm-tui-header a.tm-explore-map"))
            self.assertIsNone(events.select_one("#tm-web-settings"))
            self.assertIsNone(events.select_one("#tm-tui-controls"))
            self.assertIsNone(events.select_one(".tm-explore-map"))
            self.assertIsNotNone(events.select_one(".tm-stage-reader"))
            self.assertIsNone(events.select_one(".tm-tui-event-detail"))
            self.assertIn("window.location.assign(location.pathname+",str(events))
            self.assertIn("const params=new URLSearchParams();",str(primary))
            self.assertIn("function openCycleEditor(button)",str(primary))
            self.assertIn("tm-cycle-max-save",str(primary))

    def test_explore_map_is_overview_child_only_and_preserves_task_link(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import run_detail_body

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cycle = root / "70"
            cycle.mkdir()
            (cycle / "run_state.json").write_text(json.dumps({
                "cycle_id":"70", "status":"RUNNING", "master_id":1
            }), encoding="utf-8")
            run = {"observer_id":"8dd38a9977eef7e5", "research_root":str(root),
                   "cycle_dir":str(cycle), "cycle":"70", "task_name":"H8",
                   "state":"RUNNING", "run_id":1}
            page=BeautifulSoup(run_detail_body(run,canvas_available=True),"html.parser")
            overview=page.select_one(".tm-tui-header.tm-tui-panel")
            self.assertIsNotNone(overview)
            link=overview.select_one("a.tm-explore-map[data-explore-map]")
            self.assertIsNotNone(link)
            self.assertEqual(link["href"],
                             "/research-explorer/?observer_id=8dd38a9977eef7e5")
            self.assertEqual(link.select_one("strong").get_text(),"Explore map")
            self.assertEqual(len(page.select("[data-explore-map]")),1)
            self.assertIsNone(page.select_one(".tm-run-placeholder"))
            self.assertIsNone(page.select_one("#tm-tui-controls [data-explore-map]"))
            self.assertNotIn("Canvas / MD", page.get_text())
            for tab in ("research","events"):
                detail=BeautifulSoup(run_detail_body(run,tab=tab,canvas_available=True),
                                     "html.parser")
                self.assertEqual(detail.select("[data-explore-map]"),[])
            unavailable=BeautifulSoup(run_detail_body(run,canvas_available=False),
                                      "html.parser")
            self.assertIsNone(unavailable.select_one("a.tm-explore-map"))
            self.assertIsNotNone(unavailable.select_one(
                ".tm-tui-header .tm-explore-map-unavailable"))
            self.assertIn("暂未提供",unavailable.select_one(
                ".tm-explore-map-unavailable").get_text())
            fragment=BeautifulSoup(run_detail_body(
                run,fragment=True,canvas_available=True),"html.parser")
            self.assertIsNotNone(fragment.select_one(".tm-tui-header a.tm-explore-map"))
            self.assertIsNone(fragment.select_one("#tm-tui-controls"))

    def test_cache_hit_rate_in_token_popover_is_prominent_and_correct(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import _usage_cell, _cache_hit_rate

        meter = {
            "project": {
                "tokens_total": 1237957, "tokens_in": 1226172,
                "tokens_out": 11785, "cached_input_tokens": 957824,
            },
            "cycle": {
                "tokens_total": 108948, "tokens_in": 107534,
                "cached_input_tokens": 86027,
            },
        }
        summary = BeautifulSoup(_usage_cell(meter), "html.parser").select_one(
            "button[data-token-detail]"
        )
        details = json.loads(summary["data-token-detail"])
        self.assertEqual(details["累计缓存命中率"], "78.1%")
        self.assertEqual(details["本轮缓存命中率"], "80.0%")
        self.assertEqual(list(details)[:4], [
            "任务累计", "累计缓存命中率", "当前 Cycle", "本轮缓存命中率",
        ])
        self.assertEqual(details["累计输入"], "1,226,172")
        self.assertEqual(details["其中缓存输入"], "957,824")
        self.assertEqual(details["本轮输入"], "107,534")
        self.assertEqual(details["本轮缓存输入"], "86,027")
        self.assertNotIn("78.1%", summary.get_text())  # Popup-only metric.
        self.assertEqual(_cache_hit_rate({"tokens_in": 1, "cached_input_tokens": 0}), "0.0%")
        self.assertEqual(_cache_hit_rate({"tokens_in": 1, "cached_input_tokens": 1}), "100.0%")
        for case in (
            None, {}, {"tokens_in": 0, "cached_input_tokens": 0},
            {"tokens_in": 100}, {"tokens_in": None, "cached_input_tokens": 1},
            {"tokens_in": 100, "cached_input_tokens": 101},
            {"tokens_in": 100, "cached_input_tokens": -1},
            {"tokens_in": True, "cached_input_tokens": 1},
            {"tokens_in": 100, "cached_input_tokens": False},
            {"tokens_in": 100.0, "cached_input_tokens": 50},
        ):
            with self.subTest(case=case):
                self.assertEqual(_cache_hit_rate(case), "未记录")
        missing_cycle = json.loads(BeautifulSoup(
            _usage_cell({"project": meter["project"]}), "html.parser"
        ).select_one("[data-token-detail]")["data-token-detail"])
        self.assertEqual(missing_cycle["本轮缓存命中率"], "未记录")

    def test_token_count_and_cycle_are_entire_clickable_summary(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import _usage_cell
        meter={"cycle":{"tokens_total":18190},"project":{
            "tokens_total":371653,"tokens_in":281797,"tokens_out":89856,
            "cached_input_tokens":206912,"reasoning_tokens":21884}}
        card=BeautifulSoup(_usage_cell(meter),"html.parser")
        summary=card.select_one("button.tm-usage-hit[data-token-detail]")
        self.assertIsNotNone(summary)
        self.assertEqual(summary["aria-haspopup"],"dialog")
        self.assertIn("371,653",summary.get_text())
        self.assertIn("本轮 18,190",summary.get_text())
        details=json.loads(summary["data-token-detail"])
        self.assertEqual(details["累计输入"],"281,797")
        self.assertEqual(details["其中缓存输入"],"206,912")
        self.assertEqual(details["其中推理输出"],"21,884")
        self.assertNotIn("累计输入",summary.get_text())
        self.assertIsNone(card.select_one("details.tm-usage-detail"))
        self.assertIsNone(card.select_one(".tm-usage-breakdown"))


class FrozenHypothesisResearchViewTests(unittest.TestCase):
    """The scientific lock, not a dashboard paraphrase, drives Research."""

    @staticmethod
    def fixture(root, *, lock=None):
        cycle=root/"61"
        cycle.mkdir()
        snap={
            "master_id":1,"cycle_id":"61","cycle_index":61,
            "status":"FAILED","phase":"failed",
            "qualification_status":"qualified",
            "qualification_rationale":"SDK selected one Challenger-qualified candidate",
            "hypothesis_lock": lock if lock is not None else {
                "id":"H-0051","status":"locked",
                "hypothesis":"冻结假说 <source>","scope":"原生力 F ∈ [8,10]",
                "decisive_test":"独立检验 ΔE₁ = L₁ − Q₁\n不能事后调参",
                "qualification":{"review":{"verdict":"qualified",
                    "reason":"Challenger 判定可检验，非支持结论"},
                    "selection_rationale":"only qualified candidate"}
            },
        }
        (cycle/"run_state.json").write_text(json.dumps(snap,ensure_ascii=False),encoding="utf-8")
        return {"observer_id":"a"*16,"research_root":str(root),"cycle_dir":str(cycle),
                "cycle":"61","run_id":1,"task_name":"Scientific Task","state":"FAILED"}

    def test_main_clickable_lock_and_research_exact_frozen_fields(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import run_detail_body
        from tm_observer.dashboard_ui import STYLE
        with tempfile.TemporaryDirectory() as folder:
            run=self.fixture(Path(folder))
            home=BeautifulSoup(run_detail_body(run),"html.parser")
            hypothesis=home.select_one('a.tm-hypothesis-card[data-tui-pane="hypothesis"]')
            self.assertIsNotNone(hypothesis)
            self.assertEqual(hypothesis['href'],'?tab=research')
            self.assertIn('Research Lead', hypothesis.select_one('.tm-hypothesis-card-label').get_text())
            self.assertIn('当前冻结研究对象', hypothesis.select_one('.tm-hypothesis-card-label').get_text())
            self.assertNotIn('HYPOTHESIS', hypothesis.select_one('.tm-hypothesis-card-label').get_text())
            right = home.select_one('.tm-tui-center')
            self.assertIsNotNone(right)
            self.assertIs(hypothesis.parent, right)
            self.assertIsNotNone(right.find(recursive=False))
            investigation = right.select_one('[data-tui-pane="investigation"]')
            self.assertIsNotNone(investigation)
            self.assertLess(str(home).index('data-tui-pane="hypothesis"'),
                            str(home).index('data-tui-pane="investigation"'))
            self.assertIsNone(home.select_one('.tm-tui-header .tm-hypothesis-card'))
            self.assertEqual(len(home.select('.tm-hypothesis-card')), 1)
            self.assertIn('H-0051',hypothesis.get_text())
            self.assertIn('冻结假说',hypothesis.get_text())
            self.assertIn('&lt;source&gt;',str(hypothesis))
            self.assertEqual(len(home.select('[data-tui-view="thread"],[data-tui-view="artifacts"]')),0)
            self.assertNotIn('<kbd>2</kbd> Thread',str(home))
            self.assertNotIn('<kbd>4</kbd> Artifacts',str(home))
            page=BeautifulSoup(run_detail_body(run,tab='research'),"html.parser")
            self.assertEqual([x.select_one('h3').get_text(strip=True) for x in
                              page.select(".tm-research-block")],
                             ["Hypothesis","Scope","decisive_test"])
            self.assertIn('冻结假说 <source>',page.select_one(".tm-research-main").get_text())
            self.assertNotIn('<source>',str(page))
            self.assertIn('原生力 F ∈ [8,10]',page.select_one(".tm-research-scope").get_text())
            self.assertIn('不能事后调参',page.select_one(".tm-research-test").get_text())
            self.assertIn('Challenger 判定可检验',page.select_one(".tm-research-background").get_text())
            self.assertNotIn('open',page.select_one('details.tm-research-background').attrs)
            self.assertIsNone(page.select_one('#tm-web-settings'))
            self.assertIsNone(page.select_one('#tm-tui-controls'))
            self.assertIn("const params=new URLSearchParams();",str(page))
            for role in ("tm-hypothesis-card","tm-research-prose","tm-research-background"):
                self.assertIn(role,STYLE)
            self.assertIn('.tm-tui-center > .tm-hypothesis-card', STYLE)
            self.assertIn('white-space:normal; overflow-wrap:anywhere', STYLE)

    def test_legacy_web_links_use_research_without_removing_sdk_records(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import run_detail_body
        from theory_master.tui.web_bridge import SECONDARY
        with tempfile.TemporaryDirectory() as folder:
            run=self.fixture(Path(folder))
            for old in ('thread','artifacts'):
                page=BeautifulSoup(run_detail_body(run,tab=old),"html.parser")
                self.assertIsNotNone(page.select_one(".tm-research-frozen"))
                self.assertEqual(page.select_one(".tm-tui-screen")["data-tui-screen"],"research")
            self.assertIn('thread',SECONDARY)
            self.assertIn('artifacts',SECONDARY)

    def test_before_lock_no_fabricated_scientific_object(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import run_detail_body
        with tempfile.TemporaryDirectory() as folder:
            run=self.fixture(Path(folder),lock={})
            home=BeautifulSoup(run_detail_body(run),"html.parser")
            self.assertIn('尚未冻结',home.select_one(".tm-hypothesis-card").get_text())
            research=BeautifulSoup(run_detail_body(run,tab='research'),"html.parser")
            self.assertIn('尚未完成 Hypothesis Lock',research.get_text())
            self.assertFalse(research.select('.tm-research-block'))


class AllSecondaryViewsHideRunManagementTests(unittest.TestCase):
    def test_home_only_settings_controls_and_canvas_including_logs_and_aliases(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import run_detail_body
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cycle = root / "61"
            cycle.mkdir()
            (cycle / ".theory-master-cycle").write_text("fixture", encoding="utf-8")
            (cycle / "run_state.json").write_text(json.dumps({
                "cycle_id": "61", "status": "FAILED", "master_id": 1,
                "hypothesis_lock": {"id": "H-0051", "status": "locked",
                                    "hypothesis": "研究命题", "scope": "适用范围",
                                    "decisive_test": "检验准则"},
            }, ensure_ascii=False), encoding="utf-8")
            run = {"observer_id": "a" * 16, "research_root": str(root),
                   "cycle_dir": str(cycle), "task_name": "Test",
                   "cycle": "61", "run_id": 1, "state": "FAILED"}
            for canvas_available in (True, False):
                home = BeautifulSoup(run_detail_body(
                    run, canvas_available=canvas_available), "html.parser")
                for selector in ("#tm-web-settings", "#tm-operator[hidden]",
                                 ".tm-explore-map"):
                    self.assertIsNotNone(home.select_one(selector), selector)
                self.assertIsNone(home.select_one("#tm-tui-controls"))
                for tab in ("events", "research", "thread", "artifacts"):
                    with self.subTest(tab=tab, canvas_available=canvas_available):
                        page = BeautifulSoup(run_detail_body(
                            run, tab=tab, canvas_available=canvas_available), "html.parser")
                        self.assertIsNotNone(page.select_one(".tm-tui-secondary"))
                        for selector in ("#tm-web-settings", "#tm-tui-controls",
                                         "#tm-operator", ".tm-explore-map"):
                            self.assertIsNone(page.select_one(selector),
                                              f"{tab} unexpectedly has {selector}")
                        text = page.select_one(".tm-tui-secondary").get_text(" ", strip=True)
                        self.assertNotIn("运行设置", text)
                        self.assertNotIn("任务控制 · Run", text)
                        self.assertNotIn("Explore map", text)
                for retired in ("logs",):
                    old_page = BeautifulSoup(run_detail_body(
                        run, tab=retired, canvas_available=canvas_available),"html.parser")
                    self.assertIsNone(old_page.select_one(".tm-tui-secondary"))
                    self.assertIsNotNone(old_page.select_one(".tm-tui-history-panel"))
                    self.assertIsNotNone(old_page.select_one("#tm-web-settings"))
            js = str(home)
            self.assertIn("const params=new URLSearchParams();", js)
            self.assertIn("window.location.assign(location.pathname+", js)
            self.assertNotIn("if(['events','research'].includes(viewName)", js)


class RecentEventsRightDetailTests(unittest.TestCase):
    def test_precise_source_metadata_only_makes_corresponding_timeline_rows_clickable(self):
        from bs4 import BeautifulSoup
        from tm_observer.run_views import _recent_timeline
        raw=("21:53:18  Investigator · skill loaded · numerical-investigation\n"
             "21:53:26  Investigator · skill loaded · network-graph")
        stamps=[
            {"at":"2026-10-11T21:53:18+08:00","kind":"SKILL_LOADED",
             "role":"investigator","hint":"Investigator · skill loaded · numerical-investigation"},
            {"at":"2026-10-11T21:53:26+08:00","kind":"SKILL_LOADED",
             "role":"investigator","hint":"Investigator · skill loaded · network-graph"},
        ]
        html=BeautifulSoup(_recent_timeline(raw,stamps),"html.parser")
        links=html.select("a.tm-recent-select")
        self.assertEqual(len(links),2)
        self.assertEqual(links[0]["data-recent-at"],stamps[0]["at"])
        self.assertEqual(links[1]["data-recent-hint"],stamps[1]["hint"])
        self.assertNotIn("target", links[0].attrs)
        self.assertNotIn("rel", links[0].attrs)
        from urllib.parse import parse_qs, urlsplit
        parsed=parse_qs(urlsplit(links[1]["href"]).query)
        self.assertEqual(parsed["tab"],["events"])
        self.assertEqual(parsed["hint"],[stamps[1]["hint"]])
        self.assertEqual(parsed["at"],[stamps[1]["at"]])
        mismatch=BeautifulSoup(_recent_timeline(raw,stamps[::-1]),"html.parser")
        self.assertEqual(len(mismatch.select(".tm-recent-select")),0)
        self.assertEqual(len(mismatch.select(".tm-run-events-recent li")),2)
