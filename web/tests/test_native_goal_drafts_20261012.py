"""Web Native Goal display/draft tests, using only local synthetic events."""
from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import mock

from tm_observer.goal_drafts import GoalDraftError, view_goal, save_goal_draft
from tm_observer.goal_ui import modal
from tm_observer.run_views import run_detail_body


class GoalDraftTests(unittest.TestCase):
    @staticmethod
    def task(base: Path) -> dict:
        research = base / 'research'
        cycle = research / 'cycle-0002'
        cycle.mkdir(parents=True)
        (research / '.theory-master').mkdir()
        events = [
            {'event':'AGENT_INPUT','role':'investigator','input_kind':'native_goal_objective',
             'content':'OLDER: frozen goal','ts':'2026-10-12T01:00:00Z'},
            {'event':'AGENT_INPUT','role':'investigator','input_kind':'native_goal_setup_turn',
             'content':'setup details','ts':'2026-10-12T02:00:00Z'},
            {'event':'AGENT_INPUT','role':'investigator','input_kind':'native_goal_objective',
             'content':'CURRENT: frozen goal, decisive test intact','ts':'2026-10-12T02:01:00Z'},
        ]
        (cycle / 'runtime_events.jsonl').write_text(
            ''.join(json.dumps(row)+'\n' for row in events),encoding='utf-8'
        )
        return {'observer_id':'a'*16,'research_root':str(research),
                'cycle_dir':str(cycle),'cycle':cycle.name,'run_id':1,
                'task_name':'Synthetic','state':'RUNNING'}

    @staticmethod
    def payload(view, text='revision example', revision=0):
        return {'cycle':view['cycle'],'text':text,
                'expected_goal_sha256':view['sha256'],
                'expected_revision':revision,'acknowledge_draft_only':True}

    def test_exact_submitted_goal_from_events_not_rebuilt_by_current_code(self):
        with tempfile.TemporaryDirectory() as temp:
            run=self.task(Path(temp))
            got=view_goal(run)
            self.assertEqual(got['objective'],'CURRENT: frozen goal, decisive test intact')
            self.assertEqual(got['recorded_at'],'2026-10-12T02:01:00Z')
            self.assertIsNone(got['draft'])
            self.assertEqual(got['effective'],'original_native_goal_unchanged')
            self.assertFalse(list(Path(run['research_root']).glob('**/*draft*')))

    def test_save_draft_does_not_modify_native_goal_or_research_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            run=self.task(Path(temp))
            original_file=Path(run['cycle_dir'])/'runtime_events.jsonl'
            pristine=original_file.read_bytes()
            before=view_goal(run)
            outcome=save_goal_draft(run,self.payload(before,text='Draft <new> proposed guidance'))
            self.assertTrue(outcome['ok'])
            self.assertIs(outcome['effective'],False)
            self.assertEqual(outcome['revision'],1)
            after=view_goal(run)
            self.assertEqual(after['objective'],before['objective'])
            self.assertEqual(after['draft']['text'],'Draft <new> proposed guidance')
            self.assertTrue(after['draft']['base_matches_goal'])
            self.assertEqual(original_file.read_bytes(),pristine)
            guard=Path(run['research_root'])/'.theory-master'
            history=(guard/'web-native-goal-drafts.jsonl').read_text().splitlines()
            self.assertEqual(len(history),1)
            self.assertEqual(json.loads(history[0])['applied'],False)
            second=save_goal_draft(run,self.payload(after,text='second draft',revision=1))
            self.assertEqual(second['revision'],2)
            self.assertEqual(len((guard/'web-native-goal-drafts.jsonl').read_text().splitlines()),2)
            self.assertEqual(original_file.read_bytes(),pristine)

    def test_stale_revision_goal_mutation_and_missing_ack_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            run=self.task(Path(temp)); first=view_goal(run)
            save_goal_draft(run,self.payload(first))
            with self.assertRaisesRegex(GoalDraftError,'其他编辑'):
                save_goal_draft(run,self.payload(first,revision=0))
            wrong=self.payload(first,revision=1)
            wrong['acknowledge_draft_only']=False
            with self.assertRaisesRegex(GoalDraftError,'必须先确认'):
                save_goal_draft(run,wrong)
            wrong=self.payload(first,revision=1);wrong['expected_goal_sha256']='bad'
            with self.assertRaisesRegex(GoalDraftError,'发生变化'):
                save_goal_draft(run,wrong)
            wrong=self.payload(first,revision=1);wrong['text']='A'*4001
            with self.assertRaisesRegex(GoalDraftError,'1–4000'):
                save_goal_draft(run,wrong)
            events=Path(run['cycle_dir'])/'runtime_events.jsonl'
            with events.open('a') as writer:
                writer.write(json.dumps({'event':'AGENT_INPUT','role':'investigator',
                    'input_kind':'native_goal_objective','content':'NEW REAL GOAL'})+'\n')
            with self.assertRaisesRegex(GoalDraftError,'发生变化'):
                save_goal_draft(run,self.payload(first,revision=1))
            self.assertFalse(view_goal(run)['draft']['base_matches_goal'])

    def test_legacy_run_shows_logged_prompt_without_native_edit_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            run=self.task(Path(temp))
            events=Path(run['cycle_dir'])/'runtime_events.jsonl'
            legacy='Legacy actual Investigator prompt: '+('scientific work '*350)
            with events.open('a') as writer:
                writer.write(json.dumps({'event':'AGENT_INPUT','role':'investigator',
                    'input_kind':'continuation','content':legacy})+'\n')
            source=view_goal(run)
            self.assertEqual(source['mode'],'legacy')
            self.assertEqual(source['objective'],legacy)
            self.assertFalse(source['editable_as_draft_only'])
            self.assertIsNone(source['draft'])
            with self.assertRaisesRegex(GoalDraftError,'旧式 Investigator Prompt'):
                save_goal_draft(run,self.payload(source))

    def test_missing_goal_is_pending_until_the_current_cycle_records_it(self):
        with tempfile.TemporaryDirectory() as temp:
            run=self.task(Path(temp))
            events=Path(run['cycle_dir'])/'runtime_events.jsonl'
            events.write_text('')
            with self.assertRaisesRegex(GoalDraftError,'尚未记录') as caught:
                view_goal(run)
            self.assertEqual(caught.exception.code,'goal_input_pending')
            self.assertEqual(caught.exception.status,409)
            events.write_text(json.dumps({'event':'AGENT_INPUT','role':'investigator',
                'input_kind':'native_goal_objective','content':'GOAL ARRIVED','ts':'later'})+'\n')
            got=view_goal(run)
            self.assertEqual(got['objective'],'GOAL ARRIVED')
            self.assertEqual(got['mode'],'native')

    def test_latest_incomplete_goal_never_falls_back_to_an_older_goal(self):
        with tempfile.TemporaryDirectory() as temp:
            run=self.task(Path(temp))
            events=Path(run['cycle_dir'])/'runtime_events.jsonl'
            with events.open('a',encoding='utf-8') as writer:
                writer.write(json.dumps({'event':'AGENT_INPUT','role':'investigator',
                    'input_kind':'native_goal_objective','content':''})+'\n')
            with self.assertRaisesRegex(GoalDraftError,'拒绝回退') as caught:
                view_goal(run)
            self.assertEqual(caught.exception.code,'goal_input_invalid')

    def test_legacy_agent_only_untyped_input_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            run=self.task(Path(temp))
            events=Path(run['cycle_dir'])/'runtime_events.jsonl'
            legacy='Pre-native actual Investigator prompt: '+('old instructions '*20)
            with events.open('a',encoding='utf-8') as writer:
                writer.write(json.dumps({'type':'AGENT_INPUT','agent':'Investigator',
                    'content':legacy,'ts':'2026-10-12T03:00:00Z'})+'\n')
            got=view_goal(run)
            self.assertEqual(got['mode'],'legacy')
            self.assertEqual(got['objective'],legacy)
            self.assertIn('input_kind missing',got['source'])
            self.assertFalse(got['editable_as_draft_only'])
            self.assertIsNone(got['draft'])

    def test_http_is_authenticated_csrf_checked_and_draft_only(self):
        from tm_observer.auth import BasicAuthCredentials
        from tm_observer.web import make_handler
        with tempfile.TemporaryDirectory() as temp:
            run=self.task(Path(temp))
            cache=mock.Mock(search_root=Path(temp))
            cache.get.return_value={'runs':[run]}
            handler=make_handler(cache,auth=BasicAuthCredentials('test-user','test-password'))
            server=ThreadingHTTPServer(('127.0.0.1',0),handler)
            worker=threading.Thread(target=server.serve_forever,daemon=True)
            worker.start()
            base=f'http://127.0.0.1:{server.server_address[1]}'
            url=base+'/api/run/'+run['observer_id']+'/goal'
            auth='Basic '+base64.b64encode(b'test-user:test-password').decode()
            try:
                no_auth=Request(url,headers={'Accept':'application/json'})
                with urlopen(no_auth,timeout=3) as response:
                    self.assertEqual(response.url,base+'/login')
                get=Request(url,headers={'Authorization':auth})
                with urlopen(get,timeout=3) as response:
                    source=json.loads(response.read())
                self.assertIn('CURRENT: frozen',source['objective'])
                data=json.dumps(self.payload(source)).encode()
                post=Request(url,data=data,headers={'Authorization':auth,
                    'Content-Type':'application/json','X-TM-Operator-Intent':'1'},method='POST')
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(post,timeout=3)
                self.assertEqual(ctx.exception.code,403)
                post.add_header('Origin',base)
                with urlopen(post,timeout=3) as response:
                    result=json.loads(response.read())
                self.assertFalse(result['effective'])
                self.assertEqual(result['revision'],1)
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(post,timeout=3)
                self.assertEqual(ctx.exception.code,409)
                Path(run['cycle_dir'],'runtime_events.jsonl').write_text('',encoding='utf-8')
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(get,timeout=3)
                self.assertEqual(ctx.exception.code,409)
                self.assertEqual(json.loads(ctx.exception.read())['code'],'goal_input_pending')
            finally:
                server.shutdown();server.server_close();worker.join(timeout=3)

    def test_goal_dialog_edit_is_explicit_collapsible_and_preserves_saved_draft(self):
        page = modal('a'*16)
        self.assertIn('data-goal-editor hidden', page)
        self.assertIn('data-goal-begin-edit hidden', page)
        self.assertIn('data-goal-cancel', page)
        source = page.split('<script>', 1)[1].split('</script>', 1)[0]
        harness = r"""
const vm=require('node:vm'), assert=require('node:assert/strict');
function element(){return {
  handlers:{},hidden:false,disabled:false,value:'',checked:false,textContent:'',isConnected:true,
  addEventListener(type,fn){(this.handlers[type]??=[]).push(fn);},
  async emit(type,arg={}){for(const fn of this.handlers[type]||[])await fn({target:this,...arg});},
  focus(){this.focused=true;}
};}
const names=['edit','ack','save','begin-edit','editor','cancel','original','source','revision','status','retry'];
const el=Object.fromEntries(names.map(name=>[name,element()]));
el.editor.hidden=true;el['begin-edit'].hidden=true;
const dialog=element();
dialog.querySelector=query=>{const name=/^\[data-goal-(.+)\]$/.exec(query)?.[1];return el[name]??null;};
dialog.showModal=()=>{dialog.open=true;};
dialog.close=()=>{dialog.open=false;dialog.emit('close');};
const document=element();
document.getElementById=name=>name==='tm-native-goal-dialog'?dialog:null;
const opener=element(); opener.closest=query=>query==='[data-goal-open]'?opener:null;
const close=element();close.closest=query=>query==='[data-goal-close]'?close:null;
let confirms=0, allowConfirm=true, writes=[];
let remote={mode:'native',editable_as_draft_only:true,cycle:'cycle-0002',
            objective:'FROZEN NATIVE GOAL',sha256:'digest',recorded_at:'now',
            source:'SDK',draft:null};
const fetch=async (url,opts={})=>{
  assert.equal(url,'/api/run/'+'a'.repeat(16)+'/goal');
  if(opts.method==='POST'){
    const body=JSON.parse(opts.body);writes.push(body);
    assert.equal(body.acknowledge_draft_only,true);
    assert.equal(opts.headers['X-TM-Operator-Intent'],'1');
    remote={...remote,draft:{text:body.text,revision:(remote.draft?.revision||0)+1,
            updated_at:'later',base_matches_goal:true}};
    return {ok:true,json:async()=>({ok:true,revision:remote.draft.revision,saved_at:'later',effective:false})};
  }
  return {ok:true,json:async()=>({...remote})};
};
vm.runInNewContext(process.argv[1],{document,window:{confirm:()=>{confirms++;return allowConfirm;},setTimeout:(fn)=>{setImmediate(fn);return 1;}},fetch});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await document.emit('click',{target:opener}); await tick();
  assert.equal(dialog.open,true);
  assert.equal(el.editor.hidden,true,'editor must remain collapsed on read');
  assert.equal(el['begin-edit'].hidden,false,'native goal permits Edit button');
  assert.equal(el.original.textContent,'FROZEN NATIVE GOAL');
  await el['begin-edit'].emit('click');
  assert.equal(el.editor.hidden,false);
  assert.equal(el['begin-edit'].hidden,true);
  assert.equal(el.edit.value,'FROZEN NATIVE GOAL');
  assert.equal(el.edit.focused,true);
  assert.equal(el.save.disabled,true,'requires explicit acknowledgement');
  el.edit.value='UNSAVED CHANGE'; el.ack.checked=true;
  await el.ack.emit('change'); assert.equal(el.save.disabled,false);
  await el.cancel.emit('click');
  assert.equal(el.editor.hidden,true);
  assert.equal(el.original.textContent,'FROZEN NATIVE GOAL');
  assert.equal(writes.length,0,'cancel must not save');
  await el['begin-edit'].emit('click');
  assert.equal(el.edit.value,'FROZEN NATIVE GOAL','cancel must discard unsaved input');
  el.edit.value='DRAFT SAVED';el.ack.checked=true;await el.ack.emit('change');
  allowConfirm=false; await el.save.emit('click');
  assert.equal(writes.length,0,'confirmation cancel prevents write');
  assert.equal(el.editor.hidden,false);
  allowConfirm=true; await el.save.emit('click');
  assert.equal(writes.length,1);
  assert.equal(el.editor.hidden,true,'successful save returns to read mode');
  assert.equal(el['begin-edit'].hidden,false);
  assert.match(el.status.textContent,/已保存.*未生效/);
  assert.match(el.revision.textContent,/版本 1/);
  assert.equal(el.original.textContent,'FROZEN NATIVE GOAL','draft does not alter actual goal');
  await el['begin-edit'].emit('click');
  assert.equal(el.edit.value,'DRAFT SAVED','saved draft restored only after Edit');
  await el.cancel.emit('click');
  dialog.close();
  await document.emit('click',{target:opener});await tick();
  assert.equal(el.editor.hidden,true,'reopen starts in read-only state');
  assert.match(el.revision.textContent,/草稿版本 1/);
  dialog.close();
  remote={...remote,mode:'legacy',editable_as_draft_only:false,draft:null,
          objective:'OLD MODE ACTUAL PROMPT'};
  await document.emit('click',{target:opener});await tick();
  assert.equal(el['begin-edit'].hidden,true,'legacy prompt is noneditable');
  assert.equal(el.editor.hidden,true);
  assert.equal(el.original.textContent,'OLD MODE ACTUAL PROMPT');
  assert.equal(writes.length,1);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run(['node', '-e', harness, source], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_goal_dialog_retries_pending_current_cycle_input(self):
        source = modal('a'*16).split('<script>',1)[1].split('</script>',1)[0]
        harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
function element(){return {handlers:{},hidden:false,disabled:false,value:'',checked:false,textContent:'',isConnected:true,
 addEventListener(type,fn){(this.handlers[type]??=[]).push(fn);},
 async emit(type,arg={}){for(const fn of this.handlers[type]||[])await fn({target:this,...arg});},focus(){this.focused=true;}};}
const names=['edit','ack','save','begin-edit','editor','cancel','original','source','revision','status','retry'];
const el=Object.fromEntries(names.map(name=>[name,element()]));el.editor.hidden=true;el['begin-edit'].hidden=true;el.retry.hidden=true;
const dialog=element();dialog.querySelector=q=>{const name=/^\[data-goal-(.+)\]$/.exec(q)?.[1];return el[name]??null;};
dialog.showModal=()=>{dialog.open=true;};dialog.close=()=>{dialog.open=false;dialog.emit('close');};
const document=element();document.getElementById=name=>name==='tm-native-goal-dialog'?dialog:null;
const opener=element();opener.closest=q=>q==='[data-goal-open]'?opener:null;
let calls=0;
const fetch=async()=>{calls++;if(calls===1)return {ok:false,status:409,json:async()=>({code:'goal_input_pending',error:'not yet'})};
 return {ok:true,status:200,json:async()=>({mode:'native',editable_as_draft_only:true,cycle:'187',objective:'CURRENT CYCLE GOAL',sha256:'digest',recorded_at:'now',source:'runtime_events.jsonl',draft:null})};};
const window={confirm:()=>true,setTimeout:fn=>{setImmediate(fn);return 1;}};
vm.runInNewContext(process.argv[1],{document,window,fetch});const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{await document.emit('click',{target:opener});for(let i=0;i<8&&calls<2;i++)await tick();await tick();
 assert.equal(calls,2,'pending read must retry once a short delay passes');
 assert.equal(el.original.textContent,'CURRENT CYCLE GOAL');assert.equal(el['begin-edit'].hidden,false);
 assert.equal(el.editor.hidden,true);assert.equal(el.save.disabled,true,'draft stays disabled until explicit edit and acknowledgement');
 assert.equal(el.retry.hidden,true,'successful read hides retry button');dialog.close();
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result=subprocess.run(['node','-e',harness,source],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_html_and_js_have_reusable_dialog_outside_live_run(self):
        page=run_detail_body({'observer_id':'a'*16,'run_id':1,'task_name':'Synthetic',
                              'cycle':'cycle-0002','state':'RUNNING'})
        self.assertIn('data-goal-open',page)
        self.assertIn('id="tm-native-goal-dialog"',page)
        self.assertIn('保存未生效草稿',page)
        self.assertIn('不会修改当前 Goal',page)
        self.assertLess(page.index('id="tm-run-live"'),page.index('id="tm-native-goal-dialog"'))
        script=modal('a'*16).split('<script>')[1].split('</script>')[0]
        check=subprocess.run(['node','--check'],input=script,text=True,capture_output=True,timeout=5)
        self.assertEqual(check.returncode,0,check.stderr)


if __name__=='__main__': unittest.main()
