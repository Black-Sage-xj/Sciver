"""Stage Conversation read-only fixture, provenance, Rework and safety tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tm_observer.stage_conversation import overview, transcript, cycles, _index
from tm_observer.run_stage_ui import shell, script


def write_lines(folder, name, records):
    (folder/name).write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in records),
                             encoding="utf-8")


def record(kind, role=None, thread=None, invocation=None, stage="investigate",
           content=None, ts="2026-10-11T12:00:00+08:00", **other):
    x={"ts":ts,"event":kind,"stage":stage}
    if role is not None:x["role"]=role
    if thread is not None:x["thread_id"]=thread
    if invocation is not None:x["invocation_id"]=invocation
    if content is not None:x["content"]=content
    x.update(other)
    return x


class StageConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.cycle=root/"4"
        self.cycle.mkdir()
        self.row={"research_root":str(root),"cycle":"4"}
        self.a="inv-4-investigator-aa"
        self.j="inv-4-judge-bb"
        self.b="inv-4-investigator-cc"
        self.runtime=[
            record("AGENT_STARTED","investigator","thread-one",self.a),
            record("AGENT_INPUT","investigator",content="冻结目标、decisive_test及原始 Prompt"),
            record("LLM_MESSAGE","investigator","thread-one",content="初步科研回答",turn_id="turn-one"),
            record("TOOL_ACTIVITY","investigator","thread-one",detail="python simulate.py",tool="command",turn_id="turn-one"),
            record("AGENT_COMPLETED","investigator",output={"findings":[1]},ts="2026-10-11T12:02:00+08:00"),
            record("AGENT_STARTED","judge","thread-judge",self.j,stage="terminal_assessment",ts="2026-10-11T12:03:00+08:00"),
            record("AGENT_INPUT","judge",content="Judge frozen question",stage="terminal_assessment",ts="2026-10-11T12:03:00+08:00"),
            record("AGENT_COMPLETED","judge",stage="terminal_assessment",ts="2026-10-11T12:04:00+08:00"),
            record("AGENT_STARTED","investigator","thread-one",self.b,ts="2026-10-11T12:05:00+08:00",
                   resume_thread_id="thread-one"),
            record("AGENT_INPUT","investigator",content="Rework Prompt containing requested_resolution",
                   ts="2026-10-11T12:05:00+08:00"),
            record("LLM_MESSAGE","investigator","thread-one",content="<script>alert(1)</script> scientific words",
                   ts="2026-10-11T12:06:00+08:00"),
        ]
        self.ledger=[
            {"event":"invocation.prepared","invocation_id":self.a,"role":"investigator","stage":"investigate",
             "state":"prepared","recorded_at":"2026-10-11T12:00:00+08:00"},
            {"event":"invocation.committed","invocation_id":self.a,"role":"investigator","stage":"investigate",
             "state":"committed","commit_state":"committed","recorded_at":"2026-10-11T12:02:00+08:00"},
            {"event":"invocation.prepared","invocation_id":self.j,"role":"judge","stage":"terminal_assessment",
             "state":"prepared","recorded_at":"2026-10-11T12:03:00+08:00"},
            {"event":"invocation.committed","invocation_id":self.j,"role":"judge","stage":"terminal_assessment",
             "state":"committed","commit_state":"committed","recorded_at":"2026-10-11T12:04:00+08:00"},
            {"event":"invocation.prepared","invocation_id":self.b,"role":"investigator","stage":"investigate",
             "state":"prepared","recorded_at":"2026-10-11T12:05:00+08:00"},
            {"event":"invocation.running","invocation_id":self.b,"role":"investigator","state":"running",
             "execution_mode":"resume_thread","thread_id":"thread-one","recorded_at":"2026-10-11T12:05:00+08:00"},
        ]
        self.sdk=[
            {"seq":1,"at":"2026-10-11T12:00:00+08:00","type":"role.started",
             "payload":{"role":"investigator","invocation_id":self.a}},
            {"seq":2,"at":"2026-10-11T12:04:00+08:00","type":"role.completed",
             "payload":{"role":"judge","stage":"terminal_assessment","output":{
                 "objection":{"requested_resolution":"独立网络复核必须真正执行，而不是提交局部Finding"}}}},
            {"seq":3,"at":"2026-10-11T12:04:01+08:00","type":"judge.rework_requested",
             "payload":{"requested_resolution":"完整完成冻结检验"}},
        ]
        self.persist()

    def persist(self):
        write_lines(self.cycle,"runtime_events.jsonl",self.runtime)
        write_lines(self.cycle,"invocations.jsonl",self.ledger)
        write_lines(self.cycle,"events.jsonl",self.sdk)

    def test_stage_sequence_rework_and_thread_continuation_from_actual_ids(self):
        index=overview(self.row)
        self.assertEqual([(s["role"],s["round"]) for s in index["stages"]],
                         [("investigator",1),("judge",1),("investigator",2)])
        self.assertEqual(index["stages"][0]["state"],"committed")
        self.assertEqual(index["stages"][2]["state"],"running")
        self.assertEqual(index["stages"][2]["context_mode"],"same_thread")
        self.assertEqual(index["stages"][2]["previous_stage"],self.a)
        self.assertTrue(index["stages"][2]["rework"]["confirmed_rework"])
        self.assertEqual(index["stages"][2]["rework"]["requested_resolution"],"完整完成冻结检验")
        self.assertEqual(index["transitions"][1]["kind"],"judge.rework_requested")
        self.assertEqual(index["transitions"][1]["seq"],3)
        self.assertIsNone(index["stages"][1]["rework"])

    def test_prompt_reply_tool_and_original_fields_not_invented(self):
        first=transcript(self.row,cycle="4",stage_id=self.a)
        self.assertEqual(first["total"],5)
        self.assertEqual([x["side"] for x in first["messages"]],
                         ["system","prompt","agent","agent","system"])
        self.assertEqual(first["messages"][1]["content"],"冻结目标、decisive_test及原始 Prompt")
        self.assertEqual(first["messages"][2]["turn_id"],"turn-one")
        self.assertEqual(first["messages"][3]["content"],"python simulate.py")
        self.assertIn("may omit stdout/stderr",first["tool_output_warning"])
        second=transcript(self.row,cycle="4",stage_id=self.b)
        self.assertIn("<script>",second["messages"][-1]["content"]) # JSON source is literal, UI escapes it.
        self.assertEqual(second["stage"]["thread_id"],"thread-one")

    def test_native_goal_input_layers_are_displayed_without_relabeling_legacy(self):
        self.runtime[1]["input_kind"] = "native_goal_objective"
        self.persist()
        first = transcript(self.row, cycle="4", stage_id=self.a)
        prompt = first["messages"][1]
        self.assertEqual(prompt["meta"]["input_kind"], "native_goal_objective")
        self.assertTrue(prompt["content"].startswith("[实际发送：Investigator Native Goal]"))
        self.assertEqual(prompt["content_chars"], len("冻结目标、decisive_test及原始 Prompt"))
        self.runtime[1]["input_kind"] = "continuation"
        self.persist()
        legacy = transcript(self.row, cycle="4", stage_id=self.a)["messages"][1]
        self.assertTrue(legacy["content"].startswith("[历史逻辑 Stage Prompt"))
        self.assertNotIn("实际发送：Investigator Native Goal", legacy["content"])


    def test_new_thread_handoff_distinct_from_continuation(self):
        self.runtime[8]["thread_id"]="thread-two"
        self.runtime[8].pop("resume_thread_id")
        self.runtime[10]["thread_id"]="thread-two"
        self.ledger[-1]["execution_mode"]="new_thread"
        self.ledger[-1]["thread_id"]="thread-two"
        self.persist()
        stage=overview(self.row)["stages"][2]
        self.assertEqual(stage["context_mode"],"new_thread")
        self.assertFalse(stage["context_mode"]=="same_thread")
        self.assertEqual(stage["previous_stage"],self.a)

    def test_repeated_agent_is_not_necessarily_formal_rework(self):
        self.sdk=self.sdk[:1]
        self.persist()
        stage=overview(self.row)["stages"][2]
        self.assertIsNone(stage["rework"])

    def test_decisive_judge_objection_without_formal_rework_event_is_labeled_unconfirmed(self):
        self.sdk=self.sdk[:2]
        self.persist()
        rework=overview(self.row)["stages"][2]["rework"]
        self.assertFalse(rework["confirmed_rework"])
        self.assertIn("独立网络",rework["requested_resolution"])

    def test_paging_large_stage_and_rejecting_cycle_escape(self):
        for idx in range(100):
            self.runtime.insert(3,record("TOOL_ACTIVITY","investigator","thread-one",
                                          detail="tool-"+str(idx),turn_id="turn-one"))
        self.persist()
        first=transcript(self.row,cycle="4",stage_id=self.a)
        second=transcript(self.row,cycle="4",stage_id=self.a,offset=first["next_cursor"])
        self.assertEqual(len(first["messages"]),45)
        self.assertEqual(first["next_cursor"],45)
        self.assertEqual(len(second["messages"]),45)
        with self.assertRaises(ValueError):
            transcript(self.row,cycle="../secret",stage_id=self.a)
        with self.assertRaises(ValueError):
            transcript(self.row,cycle="4",stage_id="../../etc/passwd")
        with self.assertRaises(ValueError):
            transcript(self.row,cycle="4",stage_id=self.a,offset=-1)
        with self.assertRaises(ValueError):
            transcript(self.row,cycle="4",stage_id="nonexistent")
        self.assertEqual(cycles(self.row),["4"])

    def test_missing_runtime_records_visible_as_ledger_only_stage(self):
        self.runtime=[]
        self.persist()
        info=overview(self.row)
        self.assertEqual(len(info["stages"]),3)
        self.assertTrue(all(s["missing_runtime_events"] for s in info["stages"]))
        self.assertEqual(transcript(self.row,cycle="4",stage_id=self.a)["messages"],[])

    def test_symlinked_log_not_followed(self):
        (self.cycle/"runtime_events.jsonl").unlink()
        target=Path(self.temp.name)/"outside.jsonl"
        target.write_text(json.dumps({"content":"secret"})+"\n")
        (self.cycle/"runtime_events.jsonl").symlink_to(target)
        info=overview(self.row)
        self.assertTrue(any("rejected" in x for x in info["warnings"]))
        self.assertNotIn("secret",str(info))

    def test_frontend_script_node_valid_and_prompt_right_tool_left(self):
        import subprocess,shutil
        self.assertIn('data-stage-cycle',shell())
        self.assertIn('data-stage-density',shell())
        self.assertIn('data-stage-timeline',shell())
        self.assertIn('data-stage-refresh',shell())
        self.assertIn('data-stage-current-notice',shell())
        self.assertIn('data-stage-switch-current',shell())
        self.assertIn('window.tmStageConversationNotifyCycle',script("0123456789abcdef"))
        self.assertIn('tm-stage-prompt',script("0123456789abcdef"))
        self.assertIn('tm-stage-agent-message',script("0123456789abcdef"))
        if shutil.which("node"):
            js=script("0123456789abcdef").split("<script>")[1].split("</script>")[0]
            checked=subprocess.run(["node","--check","--input-type=commonjs"],
                                   input=js,text=True,capture_output=True,timeout=8)
            self.assertEqual(checked.returncode,0,checked.stderr)


class SemanticStagePresentationTests(unittest.TestCase):
    """Browser rendering is checked with real JS, not just substring tests."""

    @unittest.skipUnless(__import__("shutil").which("node"), "Node.js unavailable")
    def test_lifecycle_prompts_canvas_updates_followup_are_semantically_distinct(self):
        import subprocess
        snippet = script("8dd38a9977eef7e5").split("<script>", 1)[1].split("</script>", 1)[0]
        anchor = "      function mount(root){"
        assert snippet.count(anchor) == 1
        snippet = snippet.replace(anchor,
            "      window.testHooks={eventCard,continuation,canvasUpdates,jsonViewer,dialogueBlocks};\n"+anchor)
        verify = r"""
const vm=require('vm'),assert=require('assert');
const source=process.argv[1];
const env={window:{},document:{querySelector(){return null;}},URLSearchParams,
           location:{search:''}};
vm.runInNewContext(source,env);
const {eventCard,continuation,canvasUpdates,jsonViewer}=env.window.testHooks;
const s={role:'research_lead',stage:'cycle_synthesis',round:2,
  previous_stage_name:'frontier_proposal',previous_stage:'inv-before',
  context_mode:'new_thread',rework:null};
assert.equal(continuation(s),''); // unrelated stage is not Rework
const turn=eventCard({kind:'LLM_TURN_STARTED',side:'agent',content:'',
  time:'2026-10-11T19:48:58+08:00',turn_id:'real-turn-one',
  meta:{role:'research_lead'}},'normal',s);
assert(turn.includes('Research Lead · cycle_synthesis'));
assert(turn.includes('模型 Turn 开始'));
assert(turn.includes('real-turn-on'));
assert(!turn.includes('事件没有持久化正文'));
assert(!turn.includes('AGENT · LLM_TURN_STARTED'));
const sdk=eventCard({kind:'AGENT_STARTED',side:'system',content:'',
  time:'2026-10-11T19:48:58+08:00',meta:{role:'research_lead'}},'normal',s);
assert(sdk.includes('Agent 调用开始'));
assert(!sdk.includes('SDK / 生命周期'));
const tokens=eventCard({kind:'TOKEN_USAGE',side:'system',content:'',
  time:'2026-10-11T19:50:00+08:00',meta:{tokens_in:16080,tokens_out:73,tokens_total:16153}},
 'normal',s);
assert.equal(tokens,''); // Frequent TOKEN_USAGE is hidden even in direct raw rendering.
const rawTokens=eventCard({kind:'TOKEN_USAGE',side:'system',content:'',
  meta:{tokens_in:7266964,tokens_out:31083,tokens_total:7298047},
  turn_id:'01a12fbc-171'},'raw',s);
assert.equal(rawTokens,'');
const prompt=eventCard({kind:'AGENT_INPUT',side:'prompt',content:'Frozen decisive_test',
  time:'2026-10-11T19:48:00+08:00',meta:{}},'normal',s);
assert(prompt.includes('tm-stage-prompt')&&prompt.includes('Frozen decisive_test'));
const changes=JSON.stringify({updates:[{node_id:'3-hypothesis',
   frontier_status:'parked',reason:'实验不足 <script>alert(1)</script>'}]});
const canvas=eventCard({kind:'LLM_MESSAGE',side:'agent',content:changes,
  time:'2026-10-11T20:26:00+08:00',meta:{}},'normal',s);
assert(!canvas.includes('模型回复'));
assert(canvas.includes('Canvas 节点维护 · 1 项更新'));
assert(canvas.includes('3-hypothesis')&&canvas.includes('parked'));
assert(canvas.includes('实验不足 &lt;script&gt;alert(1)&lt;/script&gt;'));
assert(!canvas.includes('<script>alert(1)</script>'));
assert(canvas.includes('查看完整原始 JSON'));
const original={short:'ok',long:'A'.repeat(230)+'<script>',nested:{values:Array.from({length:24},(_,i)=>i)}};
const pretty=jsonViewer(JSON.stringify(original));
assert(pretty.includes('tm-stage-json-toolbar'));
assert(pretty.includes('data-stage-json-toggle aria-expanded="false">全部'));
assert(pretty.includes('data-stage-json-preview'));
assert(pretty.includes('data-stage-json-full hidden'));
assert(pretty.includes('&lt;script&gt;')&&!pretty.includes('<script>'));
const preview=pretty.split('data-stage-json-preview')[1].split('</pre>')[0];
const full=pretty.split('data-stage-json-full hidden')[1].split('</pre>')[0];
assert(preview.includes('…')&&!preview.includes('A'.repeat(230)));
assert(full.includes('A'.repeat(230))&&full.includes('23'));
assert(pretty.includes(String.fromCharCode(10))); // indentation uses line breaks
assert(!jsonViewer('not JSON'));
assert(!canvas.includes('tm-stage-prompt'));
const tool=eventCard({kind:'TOOL_ACTIVITY',side:'agent',content:'python compute.py',
  time:'2026-10-11T19:50:00+08:00',meta:{tool:'command'}},'normal',s);
assert(tool.includes('tm-stage-tool-message'));
assert(tool.includes('>command</strong>'));
assert(!tool.includes('工具执行与结果来源'));
assert(!tool.includes('Research Lead · 工具调用'));
assert(prompt.includes('Research Lead · Prompt 输入'));
const investigator={...s,role:'investigator',stage:'investigate'};
const finalReply=eventCard({kind:'LLM_MESSAGE',side:'agent',content:'完整 Findings: first_shell/findings.json:1',
  time:'2026-10-11T21:44:52+08:00',meta:{}},'normal',investigator);
assert(!finalReply.includes('模型回复'));
assert(finalReply.includes('完整 Findings: first_shell/findings.json:1'));
assert(finalReply.includes('tm-stage-reply-time'));
const delivery=eventCard({kind:'AGENT_COMPLETED',side:'system',
  content:JSON.stringify({findings:[{statement:'scientific result'}]}),
  time:'2026-10-11T21:45:41+08:00',meta:{}},'normal',investigator);
assert(delivery.includes('Investigator · 结构化交付'));
const unknown=eventCard({kind:'LLM_MESSAGE',side:'agent',content:'Unknown event',
  time:'2026-10-11T21:45:41+08:00',meta:{}},'normal');
assert(!unknown.includes('模型回复'));
assert(unknown.includes('Unknown event'));
const fallback=eventCard({kind:'LLM_TURN_STARTED',side:'agent',content:'',
  time:'2026-10-11T19:48:58+08:00',meta:{role:'research_lead',stage:'frontier_proposal'}},
 'normal');
assert(fallback.includes('Research Lead · frontier_proposal'));
assert(!fallback.includes('undefined'));

assert(!tool.includes('工具执行与结果来源'));
assert(!tool.includes('tm-stage-warning'));
const {dialogueBlocks}=env.window.testHooks;
const folded=dialogueBlocks([
  {kind:'TOOL_ACTIVITY',seq:1,time:'2026-10-13T00:19:49+08:00',content:'sed -n 1,65p a.py',meta:{tool:'command'}},
  {kind:'TOOL_ACTIVITY',seq:2,time:'2026-10-13T00:21:17+08:00',content:'ls evaluation',meta:{tool:'command'}}
], 'normal', investigator);
assert(folded.includes('工具调用 · 2 次 · 用时 1分28秒'));
assert(!folded.split('<summary>')[1].split('</summary>')[0].includes('sed -n'));
const split=dialogueBlocks([
  {kind:'LLM_MESSAGE',seq:3,time:'2026-10-13T00:22:00+08:00',content:'继续'},
  {kind:'TOOL_ACTIVITY',seq:4,time:'2026-10-13T00:22:02+08:00',content:'pwd',meta:{tool:'command'}}
], 'normal', investigator);
assert(split.includes('工具调用 · 1 次'));
assert(split.includes('继续'));
const skills=dialogueBlocks([
  {kind:'TOOL_ACTIVITY',seq:5,time:'2026-10-13T15:10:03+08:00',content:'sed a.py',meta:{tool:'command'}},
  {kind:'SKILL_LOADED',seq:6,time:'2026-10-13T15:10:18+08:00',content:'',
   meta:{skill:'compute-dispatch',skill_path:'/skills/compute-dispatch/SKILL.md',load_index:1,detection:'observed_file_read'},
   turn_id:'01a13413-8ac0'},
  {kind:'SKILL_LOADED',seq:7,time:'2026-10-13T15:10:20+08:00',content:'',
   meta:{skill:'data-plotting',load_index:2}},
  {kind:'TOOL_ACTIVITY',seq:8,time:'2026-10-13T15:12:37+08:00',content:'ls',meta:{tool:'command'}}
], 'normal', investigator);
assert(skills.includes('工具调用 · 1 次 · 用时 &lt;1秒'));
assert(skills.includes('Skill 调用 · 2 次 · 用时 2秒'));
const skillHead=skills.split('Skill 调用 · 2 次')[1].split('</summary>')[0];
assert.equal(skillHead.includes('Investigator'), false);
assert(!skills.includes('tm-stage-milestone'));
assert(skills.includes('>compute-dispatch</strong>'));
assert(skills.includes('/skills/compute-dispatch/SKILL.md'));
assert(skills.includes('load #1'));
assert(skills.includes('observed file read'));
assert(!skills.split('Skill 调用')[1].split('工具调用')[0].includes('Turn 01a13413'));
assert(skills.includes('>command</strong>'));
const rework={...s,stage:'investigate',previous_stage_name:'investigate',
  rework:{confirmed_rework:true,kind:'judge.rework_requested',requested_resolution:'finish frozen test'}};
assert(continuation(rework).includes('finish frozen test'));
assert(continuation(rework).includes('Judge Rework'));
"""
        result = subprocess.run(["node", "-e", verify, snippet], capture_output=True,
                                text=True, timeout=12)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_css_role_inheritance_two_rails_and_blue_prompt_bubble(self):
        from tm_observer.dashboard_ui import STYLE
        self.assertIn(".tm-role-research_lead { --role-accent:#60a5fa;", STYLE)
        self.assertIn(".tm-stage-section.tm-role-research_lead { --role-accent:#60a5fa;", STYLE)
        for role in ("research_lead","challenger","investigator","literature","judge","reporter"):
            self.assertIn(".tm-role-"+role+" { --role-accent:",STYLE)
        self.assertIn("border-left:6px solid var(--role-accent,#a1a1aa)",STYLE)
        self.assertIn("border-right:6px solid var(--role-accent,#a1a1aa)",STYLE)
        self.assertNotIn(".tm-stage-section { --role-accent:#a1a1aa;",STYLE)
        for role in ("research_lead","challenger","investigator","literature","judge","reporter"):
            self.assertIn(".tm-stage-section.tm-role-"+role+" { --role-accent:",STYLE)
        self.assertIn(".tm-stage-prompt { margin-left:auto; background:#19365b;",STYLE)
        self.assertIn("border:1px solid #3b82f6;",STYLE)
        self.assertIn(".tm-stage-tool-message",STYLE)
        self.assertIn(".tm-stage-tool-message pre",STYLE)
        self.assertIn(".tm-stage-agent-message pre { color:#fafafa; }",STYLE)
        self.assertIn("color:#7d7d88!important; font-size:11px!important; font-weight:400;",STYLE)
        self.assertIn(".tm-stage-tool-note { margin-top:2px; color:#6f6f7a; font-size:11px; }",STYLE)
        self.assertIn(".tm-stage-jump { position:fixed; right:16px; bottom:24px;",STYLE)
        self.assertIn(".tm-focus-latest { display:block; margin-top:10px;",STYLE)

    def test_structured_completion_output_is_valid_json_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cycle=root/"58"
            cycle.mkdir()
            row={"research_root":str(root),"cycle":"58"}
            events=[
                record("AGENT_STARTED","research_lead","th1","inv-58-lead-first",stage="frontier_proposal"),
                record("AGENT_COMPLETED","research_lead","th1","inv-58-lead-first",stage="frontier_proposal",
                       output={"updates":[{"node_id":"H-0001","frontier_status":"parked","reason":"pending"}]}),
                record("AGENT_STARTED","research_lead","th2","inv-58-lead-second",stage="cycle_synthesis"),
                record("AGENT_INPUT","research_lead","th2",content="Synthesis prompt",stage="cycle_synthesis")
            ]
            write_lines(cycle,"runtime_events.jsonl",events)
            write_lines(cycle,"invocations.jsonl",[])
            write_lines(cycle,"events.jsonl",[])
            o=overview(row)
            self.assertEqual(o["stages"][1]["previous_stage_name"],"frontier_proposal")
            self.assertEqual(o["stages"][1]["context_mode"],"new_thread")
            self.assertIsNone(o["stages"][1]["rework"])
            first=transcript(row,cycle="58",stage_id=o["stages"][0]["id"])
            completed=first["messages"][-1]
            decoded=json.loads(completed["content"])
            self.assertEqual(decoded["updates"][0]["node_id"],"H-0001")
            self.assertEqual(completed["content_chars"],len(completed["content"]))
            self.assertFalse(completed["content_truncated"])


class StageDialogueEndToEndTests(unittest.TestCase):
    """Exercise real showStage + lazy-paged eventCard, not the detached card helper."""

    @unittest.skipUnless(__import__("shutil").which("node"),"Node.js unavailable")
    def test_actual_stage_expansion_renders_prompt_agent_tool_lifecycle_and_page_two(self):
        import subprocess
        source=script("8dd38a9977eef7e5").split("<script>",1)[1].split("</script>",1)[0]
        # Test-only hooks enter the actual stage closure with an intentionally
        # minimal fake DOM and fake read-only backend. This catches mistakes
        # in the showStage -> eventCard argument list (past production failure).
        needle="        function draw(options){"
        self.assertEqual(source.count(needle),1)
        source=source.replace(needle,
            '        window.__testShowStage=showStage; overview={cycle:"58"};\n'+needle)
        self.assertIn("IntersectionObserver", source)
        self.assertIn("跳到本对话最前", source)
        self.assertIn("跳到本对话最后", source)
        self.assertIn("滚动到底自动加载下一批（每次 45 条）", source)
        self.assertIn("滚动到顶自动加载更早一批（每次 45 条）", source)
        self.assertIn("前面还有", source)
        self.assertIn("function lastDialogueItem", source)
        self.assertNotIn(".tm-stage-event-item:last-child", source)
        self.assertIn("params().get('jump')==='latest'", source)
        needle="        fetchOverview(params().get('cycle')||'');"
        self.assertEqual(source.count(needle),1)
        source=source.replace(needle,"        // test: initial overview is supplied directly")
        verification=r'''
const vm=require('vm'),assert=require('assert');
const source=process.argv[1];
const make=()=>({dataset:{},hidden:false,innerHTML:'',
  addEventListener(){},querySelector(){return null},textContent:''});
const roleFilter=make(),box=make(),root=make();
const selectors={
 '[data-stage-timeline]':make(),'[data-stage-cycle]':make(),
 '[data-stage-density]':Object.assign(make(),{value:'normal'}),
 '[data-stage-role]':roleFilter,'[data-stage-search]':make(),
 '.tm-stage-status':make(),'[data-stage-current-notice]':make(),
 '[data-stage-current-text]':make(),
 '[data-stage-switch-current]':make(),'[data-stage-refresh]':make()
};
box.querySelector=x=>selectors[x]||null;
root.querySelector=x=>x==='.tm-stage-reader'?box:null;
let requests=0;
const first=[
 {kind:'AGENT_STARTED',side:'system',time:'2026-10-11T19:48:00+08:00',
   meta:{role:'research_lead'}},
 {kind:'AGENT_INPUT',side:'prompt',time:'2026-10-11T19:48:01+08:00',
   content:'Frozen decisive_test',meta:{}},
 {kind:'TOKEN_USAGE',side:'system',seq:20,time:'2026-10-11T19:48:04+08:00',
   turn_id:'01a12fbc-171',meta:{tokens_in:7266964,tokens_out:31083,tokens_total:7298047}},
];
const second=[
 {kind:'LLM_MESSAGE',side:'agent',time:'2026-10-11T19:48:05+08:00',
   content:'Scientific research answer',meta:{}},
 {kind:'TOKEN_USAGE',side:'system',seq:21,time:'2026-10-11T19:48:07+08:00',
   meta:{tokens_in:7298047,tokens_out:200,tokens_total:7298247}},
 {kind:'TOOL_ACTIVITY',side:'agent',time:'2026-10-11T19:48:08+08:00',
   content:'python compute.py',meta:{tool:'command'}}
];
const env={
  URL,URLSearchParams,
  window:{},history:{replaceState(){}},
  document:{querySelector(){return root}},
  location:{search:'?tab=events&cycle=58',origin:'https://observer.local',
            href:'https://observer.local/run/id/?tab=events&cycle=58'},
  fetch:async _url=>({ok:true,
     json:async()=>requests++===0?
       {messages:first,total:6,next_cursor:3,warnings:[]}:
       {messages:second,total:6,next_cursor:null,warnings:[]}})
};
vm.runInNewContext(source,env);
assert.equal(typeof env.window.__testShowStage,'function');
const stage={id:'inv-58-research_lead-1ff63a1e34e4',
  role:'research_lead',stage:'frontier_proposal',round:1};
const body=make(),section=make();
let onMore=null;
body.querySelector=(selector)=>{
  if(selector==='[data-stage-more]'&&body.innerHTML.includes('data-stage-more')){
    const btn=make();
    btn.addEventListener=(name,fn)=>{if(name==='click')onMore=fn};
    return btn;
  }
  return null;
};
section.querySelector=x=>x==='.tm-stage-messages'?body:null;
(async()=>{
 await env.window.__testShowStage(stage,section);
 assert(body.innerHTML.includes('Frozen decisive_test'),body.innerHTML.slice(0,700));
 assert(body.innerHTML.includes('tm-stage-prompt'));
 assert(body.innerHTML.includes('模型 Turn')===false);
 assert(body.innerHTML.includes('Research Lead · frontier_proposal'));
 assert(body.innerHTML.includes('已加载 3 / 6 条原始事件 · 显示 2 条'));
 assert(body.innerHTML.includes('已省略 1 条 Token 用量记录'));
 assert(!body.innerHTML.includes('Token · 输入'));
 assert(!body.innerHTML.includes('01a12fbc-171'));
 assert.equal(typeof onMore,'function');
 await onMore();
 assert(body.innerHTML.includes('Scientific research answer'));
 assert(body.innerHTML.includes('python compute.py'));
 assert(body.innerHTML.includes('tm-stage-agent-message'));
 assert(body.innerHTML.includes('tm-stage-tool-message'));
 assert(body.innerHTML.includes('已加载 6 / 6 条原始事件 · 显示 4 条'));
 assert(body.innerHTML.includes('已省略 2 条 Token 用量记录'));
 assert(!body.innerHTML.includes('Token · 输入'));
 assert(!body.innerHTML.includes('7,298,047'));
 assert(body.innerHTML.includes('Frozen decisive_test'));
 assert(body.innerHTML.includes('Scientific research answer'));
 assert(!body.innerHTML.includes('无法读取 Stage 对话'));
 assert.equal(requests,2);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        outcome=subprocess.run(["node","-e",verification,source],text=True,
                               capture_output=True,timeout=15)
        self.assertEqual(outcome.returncode,0,outcome.stderr)

    @unittest.skipUnless(__import__("shutil").which("node"),"Node.js unavailable")
    def test_tail_deeplink_can_load_earlier_pages(self):
        import subprocess
        source=script("8dd38a9977eef7e5").split("<script>",1)[1].split("</script>",1)[0]
        needle="        function draw(options){"
        source=source.replace(needle,
            '        window.__testShowStage=showStage; overview={cycle:"72"};\n'+needle,1)
        source=source.replace(
            "        fetchOverview(params().get('cycle')||'');",
            "        // test: initial overview is supplied directly",1)
        verification=r'''
const vm=require('vm'),assert=require('assert');
const source=process.argv[1];
const make=()=>({dataset:{},hidden:false,innerHTML:'',
  addEventListener(){},querySelector(){return null},querySelectorAll(){return []},textContent:''});
const roleFilter=make(),box=make(),root=make();
const selectors={
 '[data-stage-timeline]':make(),'[data-stage-cycle]':make(),
 '[data-stage-density]':Object.assign(make(),{value:'normal'}),
 '[data-stage-role]':roleFilter,'[data-stage-search]':make(),
 '.tm-stage-status':make(),'[data-stage-current-notice]':make(),
 '[data-stage-current-text]':make(),
 '[data-stage-switch-current]':make(),'[data-stage-refresh]':make()
};
box.querySelector=x=>selectors[x]||null;
root.querySelector=x=>x==='.tm-stage-reader'?box:null;
const env={
  URL,URLSearchParams,
  window:{},history:{replaceState(){}},
  document:{querySelector(){return root}},
  location:{search:'?tab=events&cycle=72&jump=latest',origin:'https://observer.local',
            href:'https://observer.local/run/id/?tab=events&cycle=72&jump=latest'},
  fetch:async url=>({ok:true,json:async()=>{
    const cursor=new URL(url).searchParams.get('cursor');
    if(cursor==='315') return {messages:[
      {kind:'TOKEN_USAGE',side:'system',seq:1,time:'2026-10-13T02:03:00+08:00',meta:{}},
      {kind:'LLM_MESSAGE',side:'agent',seq:2,time:'2026-10-13T02:03:29+08:00',content:'tail reply',meta:{}}
    ],total:323,next_cursor:null,warnings:[]};
    if(cursor==='270') return {messages:[
      {kind:'TOOL_ACTIVITY',side:'agent',seq:9,time:'2026-10-13T01:52:50+08:00',
       content:'python earlier.py',meta:{tool:'command'}}
    ],total:323,next_cursor:315,warnings:[]};
    throw new Error('unexpected cursor '+cursor);
  }})
};
vm.runInNewContext(source,env);
const stage={id:'inv-72-investigator-cc7c234fdb9b',role:'investigator',stage:'investigate',round:3};
const body=make(),section=make();
let onPrev=null;
body.querySelector=(selector)=>{
  if(selector==='[data-stage-prev]'&&body.innerHTML.includes('data-stage-prev')){
    const btn=make();
    btn.addEventListener=(name,fn)=>{if(name==='click')onPrev=fn};
    return btn;
  }
  return null;
};
section.querySelector=x=>x==='.tm-stage-messages'?body:null;
(async()=>{
 await env.window.__testShowStage(stage,section,{stage_offset:320,target:999});
 assert(body.innerHTML.includes('已加载 2 / 323 条原始事件'),body.innerHTML.slice(0,500));
 assert(body.innerHTML.includes('前面还有 315 条'));
 assert(body.innerHTML.includes('后面已到头'));
 assert(!body.innerHTML.includes('当前 Stage 已加载完毕'));
 assert.equal(typeof onPrev,'function');
 await onPrev();
 assert(body.innerHTML.includes('python earlier.py'));
 assert(body.innerHTML.includes('tail reply'));
 assert(body.innerHTML.includes('已加载 3 / 323 条原始事件'));
 assert(body.innerHTML.includes('前面还有 270 条'));
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        outcome=subprocess.run(["node","-e",verification,source],text=True,
                               capture_output=True,timeout=15)
        self.assertEqual(outcome.returncode,0,outcome.stderr)

    @unittest.skipUnless(__import__("shutil").which("node"),"Node.js unavailable")
    def test_live_refresh_appends_new_tail_without_wiping_loaded_messages(self):
        import subprocess
        source=script("8dd38a9977eef7e5").split("<script>",1)[1].split("</script>",1)[0]
        needle="        function draw(options){"
        source=source.replace(needle,
            '        window.__testShowStage=showStage;\n'
            '        overview={cycle:"83",warnings:[],transitions:[],stages:[{id:"inv-83-challenger-live",'
            'role:"challenger",stage:"review",count:1,round:1,state:"running"}]};\n'+needle,1)
        source=source.replace(
            "        fetchOverview(params().get('cycle')||'');",
            "        // test: initial overview is supplied directly",1)
        verification=r'''
const vm=require('vm'),assert=require('assert');
const source=process.argv[1];
const make=()=>({dataset:{},hidden:false,innerHTML:'',
  addEventListener(){},querySelector(){return null},querySelectorAll(){return []},
  textContent:'',remove(){}});
const roleFilter=make(),box=make(),root=make(),timeline=make();
const selectors={
 '[data-stage-timeline]':timeline,'[data-stage-cycle]':make(),
 '[data-stage-density]':Object.assign(make(),{value:'normal'}),
 '[data-stage-role]':roleFilter,'[data-stage-search]':make(),
 '.tm-stage-status':make(),'[data-stage-current-notice]':make(),
 '[data-stage-current-text]':make(),
 '[data-stage-switch-current]':make(),'[data-stage-refresh]':make()
};
box.querySelector=x=>selectors[x]||null;
root.querySelector=x=>x==='.tm-stage-reader'?box:null;
const cursors=[];
const env={
  URL,URLSearchParams,
  window:{},history:{replaceState(){}},
  document:{querySelector(){return root},hidden:false},
  location:{search:'?tab=events&cycle=83',origin:'https://observer.local',
            href:'https://observer.local/run/id/?tab=events&cycle=83'},
  fetch:async url=>({ok:true,json:async()=>{
    const parsed=new URL(url);
    const mode=parsed.searchParams.get('mode');
    const cursor=parsed.searchParams.get('cursor');
    cursors.push(mode+':'+cursor);
    if(mode==='index') return {cycle:'83',warnings:[],transitions:[],stages:[{
      id:'inv-83-challenger-live',role:'challenger',stage:'review',count:2,round:1,state:'running'}]};
    if(cursor==='1') return {messages:[{kind:'LLM_MESSAGE',side:'agent',seq:2,time:'t2',
      content:'second reply',meta:{}}],total:2,next_cursor:null,warnings:[]};
    return {messages:[{kind:'LLM_MESSAGE',side:'agent',seq:1,time:'t1',
      content:'first reply',meta:{}}],total:1,next_cursor:null,warnings:[]};
  }})
};
vm.runInNewContext(source,env);
const stage={id:'inv-83-challenger-live',role:'challenger',stage:'review',round:1,count:1,state:'running'};
const dialogue={innerHTML:'',insertAdjacentHTML(pos,html){if(pos==='beforeend')this.innerHTML+=html;}};
const body=make(),section=make(),countEl=make();
body.hidden=false;
body.querySelector=selector=>{
  if(selector==='.tm-stage-dialogue'&&body.innerHTML.includes('tm-stage-dialogue'))return dialogue;
  if(selector==='.tm-stage-coverage')return {set outerHTML(_v){}};
  return null;
};
section.dataset.stageId='inv-83-challenger-live';
section.querySelector=selector=>{
  if(selector==='.tm-stage-messages')return body;
  if(selector==='[data-stage-count]')return countEl;
  if(selector==='[data-stage-state]')return make();
  return null;
};
timeline.querySelectorAll=()=>[section];
(async()=>{
  await env.window.__testShowStage(stage,section);
  assert(body.innerHTML.includes('first reply'),body.innerHTML.slice(0,400));
  assert.equal(section.__atEnd,true);
  assert.equal(section.__nextOffset,1);
  const frozen=body.innerHTML;
  await env.window.tmStageConversationRefresh();
  assert(frozen.includes('first reply'));
  assert.equal(body.innerHTML,frozen,'loaded bubbles must stay mounted');
  assert(dialogue.innerHTML.includes('second reply'),dialogue.innerHTML);
  assert(cursors.includes('stage:1'),cursors.join(','));
  assert.equal(countEl.textContent,'2 events');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        outcome=subprocess.run(["node","-e",verification,source],text=True,
                               capture_output=True,timeout=15)
        self.assertEqual(outcome.returncode,0,outcome.stderr or outcome.stdout)


class LeadChallengerActualStageTurnTests(unittest.TestCase):
    def test_real_stage_inputs_labeled_without_changing_raw_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cycle=root/"8"
            cycle.mkdir()
            record_row={"event":"AGENT_INPUT","role":"challenger",
                        "input_kind":"task",
                        "content":"Stage: candidate_review\nOutput contract: v4\nContext: {}",
                        "ts":"2026-10-12T20:00:00+08:00"}
            # Observer index associates an input with a started invocation.
            with (cycle/"runtime_events.jsonl").open("w") as f:
                f.write(json.dumps({"event":"AGENT_STARTED","role":"challenger",
                                    "thread_id":"thread-x",
                                    "invocation_id":"inv-8-challenger-aa",
                                    "ts":"2026-10-12T19:59:59+08:00"})+"\n")
                f.write(json.dumps(record_row)+"\n")
            (cycle/"invocations.jsonl").write_text(json.dumps({
                "event":"invocation.prepared", "invocation_id":"inv-8-challenger-aa",
                "role":"challenger","stage":"candidate_review",
                "state":"prepared","recorded_at":"2026-10-12T19:59:59+08:00"})+"\n")
            run={"research_root":str(root),"cycle":"8"}
            item=overview(run)["stages"][0]
            data=transcript(run,cycle="8",stage_id=item["id"])
            events=[m for m in data["messages"] if m["kind"]=="AGENT_INPUT"]
            self.assertEqual(len(events),1)
            self.assertTrue(events[0]["content"].startswith("[实际发送：普通 Stage Turn"))
            self.assertEqual(events[0]["content_chars"],len(record_row["content"]))
            self.assertNotIn("实际发送：",json.dumps(record_row,ensure_ascii=False))


class RecentProgressExactSourceTests(unittest.TestCase):
    def test_same_second_skills_and_agent_start_are_resolved_without_guessing(self):
        from tm_observer.stage_conversation import recent_event_detail
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cyc = root / "61"
            cyc.mkdir()
            stamp = "2026-10-11T21:53:26+08:00"
            start = "2026-10-11T21:53:07+08:00"
            rows = [
                record("AGENT_STARTED", "investigator",
                       ts=start, invocation_id="inv-61-investigator-test"),
                record("SKILL_LOADED", "investigator", ts=stamp,
                       skill="numerical-investigation", load_index=2),
                record("SKILL_LOADED", "investigator", ts=stamp,
                       skill="network-graph", load_index=1),
                record("SKILL_LOADED", "investigator", ts=stamp,
                       skill="network-graph", load_index=2),
                record("TOOL_ACTIVITY", "investigator", ts=stamp,
                       detail="secret=topsecret", tool="command"),
            ]
            write_lines(cyc, "runtime_events.jsonl", rows)
            run = {"research_root": str(root), "cycle": "61"}
            for hint, expected in (
                ("Investigator · skill loaded · numerical-investigation · load #2", 1),
                ("Investigator · skill loaded · network-graph", 2),
                ("Investigator · skill loaded · network-graph · load #2", 3),
            ):
                outcome = recent_event_detail(
                    run, timestamp=stamp, kind="SKILL_LOADED",
                    role="investigator", hint=hint)
                self.assertTrue(outcome["found"], hint)
                self.assertEqual(outcome["target"], expected, hint)
                match = next(e for e in outcome["events"]
                             if e["index"] == outcome["target"])
                self.assertIn(hint.split("skill loaded · ")[1].split(" · ")[0],
                              match["content"])
                self.assertLessEqual(len(outcome["events"]), 7)
            started = recent_event_detail(run,timestamp=start,kind="AGENT_STARTED",
                                          role="investigator")
            self.assertEqual(started["target"], 0)
            unknown = recent_event_detail(run,timestamp=stamp,kind="SKILL_LOADED",
                                          role="investigator",hint="Investigator · skill loaded · unlisted")
            self.assertFalse(unknown["found"])
            secret = recent_event_detail(run,timestamp=stamp,kind="TOOL_ACTIVITY",
                                         role="investigator")
            self.assertTrue(secret["found"])
            self.assertNotIn("topsecret",repr(secret))
            self.assertIn("[redacted]",repr(secret))
            with self.assertRaises(ValueError):
                recent_event_detail(run,timestamp="../",kind="AGENT_STARTED",role="investigator")

    def test_ambiguous_same_second_event_is_never_arbitrarily_selected(self):
        from tm_observer.stage_conversation import recent_event_detail
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cyc=root/"61"
            cyc.mkdir()
            stamp="2026-10-11T21:53:26+08:00"
            write_lines(cyc, "runtime_events.jsonl",[
                record("SKILL_LOADED","investigator",ts=stamp,skill="network-graph",load_index=1),
                record("SKILL_LOADED","investigator",ts=stamp,skill="network-graph",load_index=1),
            ])
            outcome=recent_event_detail({"research_root":str(root),"cycle":"61"},
                timestamp=stamp,kind="SKILL_LOADED",role="investigator",
                hint="Investigator · skill loaded · network-graph")
            self.assertFalse(outcome["found"])
            self.assertIn("无法确认唯一",outcome["reason"])


class SourceToStageDeepLinkTests(unittest.TestCase):
    def test_deeplink_resolves_exact_stage_offset_past_initial_page(self):
        from tm_observer.stage_conversation import recent_event_detail, transcript
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cycle=root/"61"
            cycle.mkdir()
            inv="inv-61-investigator-scroll"
            thread="thread-scroll"
            entries=[record("AGENT_STARTED","investigator",thread,inv,ts="2026-10-11T21:53:07+08:00")]
            for i in range(100):
                entries.append(record("LLM_MESSAGE","investigator",thread,content=f"科研事件 #{i}",
                                      ts=f"2026-10-11T21:{54+i//60:02}:{i%60:02}+08:00"))
            entries.append(record("SKILL_LOADED","investigator",thread,ts="2026-10-11T22:01:10+08:00",
                                  skill="network-graph",load_index=1))
            write_lines(cycle,"runtime_events.jsonl",entries)
            answer=recent_event_detail({"research_root":str(root),"cycle":"61"},
                timestamp="2026-10-11T22:01:10+08:00",kind="SKILL_LOADED",
                role="investigator",hint="Investigator · skill loaded · network-graph")
            self.assertTrue(answer["found"])
            self.assertEqual(answer["stage_id"],inv)
            self.assertEqual(answer["stage_offset"],101)
            self.assertEqual(answer["target"],101)
            data=transcript({"research_root":str(root),"cycle":"61"},
                            cycle="61",stage_id=inv,offset=90)
            self.assertEqual(data["messages"][11]["seq"],101)
            self.assertEqual(data["messages"][11]["kind"],"SKILL_LOADED")

    def test_original_events_reader_has_source_navigation_and_highlight(self):
        from tm_observer.run_stage_ui import script
        from tm_observer.run_views import run_detail_body
        import subprocess
        page=run_detail_body({"observer_id":"a"*16,"run_id":1,
            "task_name":"Mock","cycle":"61","state":"OVER"},tab="events")
        self.assertIn("Stage Conversation",page)
        self.assertIn("data-stage-timeline",page)
        self.assertIn("pendingSource=params().has('at')",page)
        self.assertIn("async function followEventLink()",page)
        self.assertIn("async function refreshLive()",page)
        self.assertIn("section.__tailResume=true",page)
        self.assertIn("window.__tmStageLive=setInterval",page)
        self.assertIn("draw({reuse,savedSdk,live:true})",page)
        self.assertIn("Math.floor(sourceTarget.stage_offset/45)*45",page)
        self.assertIn("tm-stage-jump-target",page)
        self.assertIn("data-stage-event-seq",page)
        self.assertIn("section.scrollIntoView",page)
        self.assertNotIn("tm-recent-event-scroll",page)
        if subprocess.run(["which","node"],capture_output=True).returncode==0:
            js=script("a"*16).split("<script>")[1].split("</script>")[0]
            checked=subprocess.run(["node","--check","--input-type=commonjs"],
                input=js,text=True,capture_output=True,timeout=15)
            self.assertEqual(checked.returncode,0,checked.stderr)
