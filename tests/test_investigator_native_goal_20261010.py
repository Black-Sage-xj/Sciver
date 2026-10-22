"""Goal schema/SDK stream and default Investigator integration regressions."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from theory_master.native_goal import (
    NativeGoalIncomplete, NativeGoalDeliveryError, NativeGoalResult, investigator_goal, read_goal_findings, run_native_goal,
)


SCIENCE = {"frozen_objective": {
    "hypothesis": "Hypothesis H: sign test",
    "scope": "scope S with fixed chemistry",
    "decisive_test": "compare signed energies in held-out networks",
}, "workspace_access": {"cycle_workspace": "/tmp/ISOLATED"}}


class NativeGoalTests(TestCase):
    def test_no_voluntary_scientific_block_across_goal_stages_and_layouts(self):
        context = dict(SCIENCE, judge_objections=[
            {"requested_resolution": "derive the missing comparison"},
        ])
        for layout in ("v1", "v2"):
            for stage in ("investigate", "judge_rework", "integrate_literature"):
                with self.subTest(layout=layout, stage=stage):
                    goal = investigator_goal(stage, context, layout=layout)
                    self.assertIn("blocked", goal)
                    self.assertTrue(
                        "Never self-terminate or request native blocked" in goal
                        or "must not voluntarily set this Native Goal to blocked" in goal
                    )
                    self.assertNotIn("blocked requests independent review", goal)
                    self.assertNotIn("blocked is only a request", goal)
                    self.assertIn("budget", goal.lower())
                    self.assertLessEqual(len(goal), 4000)

    def test_goal_contains_full_science_not_tool_paths(self):
        objective = investigator_goal("investigate", SCIENCE)
        for val in SCIENCE["frozen_objective"].values():
            self.assertIn(val, objective)
        self.assertNotIn("/tmp/ISOLATED", objective)
        self.assertIn("INDEPENDENT", objective.upper())

    def test_research_goal_targets_evidence_for_adjudication_not_executing_the_criterion(self):
        expected = ("Investigate the frozen Hypothesis until sufficient evidence is obtained "
                    "for independent adjudication under its decisive_test, rather than stopping at a local result.")
        old = "Execute the ENTIRE frozen decisive_test for this Hypothesis, not just a local result."
        for stage in ("investigate", "integrate_literature"):
            for layout in ("v1", "v2"):
                with self.subTest(stage=stage, layout=layout):
                    goal = investigator_goal(stage, SCIENCE, layout=layout)
                    self.assertTrue(goal.startswith(expected + "\n\n"))
                    self.assertNotIn(old, goal)
        context = dict(SCIENCE, judge_objections=[{"requested_resolution": "derive missing bound"}])
        self.assertTrue(investigator_goal("judge_rework", context, layout="v2").startswith("Resolve the Judge"))

    def test_v2_goal_layout_is_task_agnostic_and_contains_completion_chain(self):
        from theory_master.native_goal import investigator_goal
        original=investigator_goal("investigate",SCIENCE)
        explicit=investigator_goal("investigate",SCIENCE,layout="v1")
        self.assertEqual(original,explicit)
        orchestrated=investigator_goal("investigate",SCIENCE,layout="v2")
        # v2 keeps exact frozen science but compresses repeated workflow prose.
        self.assertLess(len(orchestrated),len(original))
        self.assertLess(len(orchestrated),3800)
        for phase in ("qualify inputs", "execute the complete decisive test",
                      "independent checks appropriate", "reproducible evidence"):
            self.assertIn(phase,orchestrated.lower())
        for value in SCIENCE["frozen_objective"].values():
            self.assertIn(value,orchestrated)
        self.assertNotIn("/tmp/ISOLATED",orchestrated)
        self.assertNotIn("36000",orchestrated)
        self.assertIn("NOT elapsed time",orchestrated)
        self.assertNotIn("H-0046", orchestrated)
        with self.assertRaises(ValueError):
            investigator_goal("investigate",SCIENCE,layout="experimental_unknown")

    def test_goal_stage_routing_includes_literature_integration_and_exact_rework(self):
        from theory_master.native_goal import investigator_goal
        integrated = investigator_goal("integrate_literature", SCIENCE)
        self.assertIn("complete", integrated.lower())
        context = dict(SCIENCE, judge_objections=[{"requested_resolution": "derive the missing bound"}])
        rework = investigator_goal("judge_rework", context)
        self.assertIn("derive the missing bound", rework)

    def test_codex_goal_objective_limit_fails_before_backend_invocation(self):
        from theory_master.native_goal import investigator_goal
        too_long = dict(frozen_objective={"hypothesis": "h", "scope": "s", "decisive_test": "x" * 5000})
        with self.assertRaisesRegex(ValueError, "4000-character"):
            investigator_goal("investigate", too_long)

    def test_v2_rework_targets_only_exact_judge_objection(self):
        ctx=dict(SCIENCE,judge_objections=[{"requested_resolution":"compute independent signed root-cut energy"}])
        goal=investigator_goal("judge_rework",ctx,layout="v2")
        self.assertIn("compute independent signed root-cut energy",goal)
        self.assertIn("JUDGE REWORK",goal)
        self.assertNotIn("SCIENTIFIC COMPLETION CHAIN",goal)
        self.assertIn(SCIENCE["frozen_objective"]["decisive_test"],goal)
        with self.assertRaisesRegex(ValueError,"requested_resolution"):
            investigator_goal("judge_rework",SCIENCE,layout="v2")

    def test_rework_is_judge_order_not_new_hypothesis(self):
        ctx = dict(SCIENCE, judge_objections=[{"requested_resolution": "run the missing signed test"}])
        message = investigator_goal("judge_rework", ctx)
        self.assertIn("run the missing signed test", message)
        self.assertIn("fixed chemistry", message)
        with self.assertRaises(ValueError):
            investigator_goal("judge_rework", SCIENCE)

    def test_incomplete_status_cannot_read_handoff(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            path=Path(tmp)/"findings.json"
            path.write_text('{"findings":[{"claim":"claim"}]}')
            for status in ("blocked", "paused", "usageLimited", "budgetLimited", "active", "unknown"):
                with self.subTest(status=status):
                    with self.assertRaises(NativeGoalIncomplete):
                        read_goal_findings(NativeGoalResult(status,"t",2,3),path)

    def test_sdk_maps_claim_without_llm_or_scientific_merging(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            path=Path(tmp)/"findings.json"
            source={"findings":[
                {"claim":"Claim A with its explicit conditions","method":"long proof","result":{"x":1},
                 "provenance":{"source":"benchmark_provided","split":"holdout","declaration_ref":"benchmark_manifest.json","frozen_before_current_cycle":True},
                 "limitations":["free-text research metadata, not delivery"]},
                {"statement":"Independent claim B"},
            ]}
            path.write_text(json.dumps(source))
            result=NativeGoalResult("complete","t",2,5)
            output=json.loads(read_goal_findings(result,path))
            self.assertEqual(output,{"findings":[
                {"statement":source["findings"][0]["claim"],
                 "method":"long proof","result":{"x":1},"provenance":source["findings"][0]["provenance"]},
                {"statement":"Independent claim B"}]})
            self.assertEqual(json.loads(path.read_text()),source)

    def test_native_goal_rejects_invalid_provenance_without_erasing_valid_declarations(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            path = Path(tmp) / "findings.json"
            path.write_text(json.dumps({"findings": [{
                "claim": "Scoped result",
                "provenance": {"source": "made_up", "split": "holdout"},
            }]}))
            with self.assertRaises(NativeGoalDeliveryError):
                read_goal_findings(NativeGoalResult("complete", "t", 1, 1), path)

    def test_scientific_result_supports_flexible_json_structure(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            path=Path(tmp)/"handoff.json"
            payload={"findings":[
                {"claim":"Claim with numerical results","method":"rigorous simulation","result":{"values":[1,2.5],"checked":True}},
                {"claim":"Theoretical identity","method":"symbolic derivation","result":"exact equality"},
                {"claim":"Null result","result":{"observed":[]}},
                {"claim":"Pure independent result"}]}
            path.write_text(json.dumps(payload))
            actual=json.loads(read_goal_findings(NativeGoalResult("complete","t",1,1),path))
            self.assertEqual(len(actual["findings"]),4)
            self.assertEqual(actual["findings"][0]["result"],payload["findings"][0]["result"])
            self.assertEqual(actual["findings"][1]["result"],"exact equality")
            self.assertNotIn("method",actual["findings"][2])
            self.assertNotIn("result",actual["findings"][3])

    def test_missing_invalid_or_conflicting_delivery_requires_attention(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            path=Path(tmp)/"findings.json"
            complete=NativeGoalResult("complete","t",2,5)
            with self.assertRaises(NativeGoalDeliveryError):
                read_goal_findings(complete,path)
            for payload in ('{}','{"findings":[]}','{"findings":[{"claim":""}]}',
                            '{"findings":[{"claim":"A","statement":"B"}]}'):
                path.write_text(payload)
                with self.subTest(payload=payload), self.assertRaises(NativeGoalDeliveryError):
                    read_goal_findings(complete,path)

    def test_completed_goal_reads_sdk_handoff_without_post_goal_turn(self):
        import tempfile
        from unittest.mock import MagicMock
        from theory_master.codex_backend import CodexBackend

        with tempfile.TemporaryDirectory() as tmp:
            handoff=Path(tmp)/"handoff.json"
            messages=[]
            class Client:
                def thread_goal_clear(self, thread_id):
                    messages.append(("clear",thread_id))
            thread=SimpleNamespace(id="thread-one",_client=Client(),
                                   run=lambda prompt: messages.append(("setup",prompt)))
            backend=CodexBackend.__new__(CodexBackend)
            backend.threads={"thread-one":thread}
            backend.ensure_thread=MagicMock(return_value="thread-one")
            backend._new_thread_ids=set()
            backend.collector=None
            backend.last_native_goal={}

            def completed_goal(inner_thread, objective, *, on_event):
                self.assertIs(inner_thread,thread)
                handoff.write_text(json.dumps({"findings":[
                    {"claim":"Exact inequality under frozen conditions."}]}))
                return NativeGoalResult("complete","thread-one",1,1)
            with patch("theory_master.native_goal.run_native_goal", side_effect=completed_goal):
                thread_id, raw=backend.invoke_goal(
                    "investigator", goal_objective="frozen scientific goal",
                    context_prompt="task context and handoff path",
                    handoff_path=str(handoff), thread_id="thread-one",
                    model="test", provider="test")
            self.assertEqual(thread_id,"thread-one")
            self.assertEqual(json.loads(raw),{"findings":[
                {"statement":"Exact inequality under frozen conditions."}]})
            self.assertEqual([x[0] for x in messages],["setup","clear"])

    def test_true_goal_operation_stream_and_state(self):
        from openai_codex.generated.v2_all import ThreadGoalStatus
        for status in (ThreadGoalStatus.complete,ThreadGoalStatus.blocked,ThreadGoalStatus.usage_limited,
                       ThreadGoalStatus.budget_limited,ThreadGoalStatus.paused):
            with self.subTest(status=status):
                state=SimpleNamespace(status=status,is_finished=lambda:True)
                event1=SimpleNamespace(method="turn/started",payload=SimpleNamespace())
                event2=SimpleNamespace(method="item/started",payload=SimpleNamespace(item=SimpleNamespace()))
                events=[]
                class FakeStream:
                    def __init__(self,**kwargs):events.append("STREAM_CONSTRUCTED")
                    def __iter__(self):return iter([event1,event2])
                    def close(self):events.append("CLOSED")
                class FakeClient:
                    def start_goal_operation(self,tid,objective):
                        events.append(("START_NATIVE_GOAL",tid,objective));return state,"logical-1"
                    def next_goal_notification(self,state): raise AssertionError("stream owns events")
                    def unregister_goal_operation(self,state): pass
                    def cancel_goal_operation(self,state): raise AssertionError("unexpected cancel")
                t=SimpleNamespace(id="thread-1",_client=FakeClient())
                with patch('openai_codex._goal._GoalNotificationStream',FakeStream):
                    result=run_native_goal(t,"scientific test")
                self.assertEqual(result.status,status.value)
                self.assertEqual(result.physical_turns,1)
                self.assertEqual(events[0],("START_NATIVE_GOAL","thread-1","scientific test"))
                self.assertIn("CLOSED",events)

    def test_native_goal_never_arms_wall_clock_timeout(self):
        import inspect
        from theory_master.native_goal import run_native_goal
        from theory_master.codex_backend import CodexBackend
        from theory_master.native_goal_trial import main
        self.assertNotIn('max_seconds', inspect.signature(run_native_goal).parameters)
        self.assertNotIn('max_seconds', inspect.signature(CodexBackend.invoke_goal).parameters)
        source = inspect.getsource(run_native_goal)
        self.assertNotIn('threading.Timer', source)
        self.assertNotIn('pause_on_timeout', source)
        self.assertNotIn('monotonic', source)
        self.assertNotIn('timeout=', inspect.getsource(main))
        self.assertNotIn('--max-seconds', inspect.getsource(main))

    def test_trial_refuses_unsandboxed_execution(self):
        import tempfile
        from theory_master.native_goal_trial import _guard
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'historical_first_investigator_context.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'host root not read-only'):
                _guard(p,p)

    def test_trial_will_not_fallback_without_bwrap(self):
        import tempfile
        from theory_master.native_goal_trial import main
        with tempfile.TemporaryDirectory() as tmp:
            frozen=Path(tmp)/'snapshot';frozen.mkdir()
            (frozen/'historical_first_investigator_context.json').write_text('{}')
            with patch('theory_master.native_goal_trial.shutil.which',return_value=None):
                with self.assertRaises(SystemExit) as err:
                    main(['--snapshot',str(frozen),'--output',str(Path(tmp)/'trial')])
                self.assertEqual(err.exception.code, 2)
                self.assertFalse((Path(tmp)/'trial').exists())

    def test_prior_artifact_continuation_is_opt_in_and_never_same_thread(self):
        from theory_master.native_goal_trial import main
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            snap=base/'snapshot';snap.mkdir()
            (snap/'historical_first_investigator_context.json').write_text('{}')
            old=base/'oldtrial';(old/'workspace').mkdir(parents=True)
            (old/'workspace'/'frozen_three_mode_protocol.json').write_text('{"status":"frozen"}')
            (old/'goal_events.json').write_text('[]')
            new=base/'newtrial'
            with patch('theory_master.native_goal_trial.shutil.which', return_value='/usr/bin/bwrap'), \
                 patch('theory_master.native_goal_trial.subprocess.run') as run:
                run.return_value.returncode=0
                self.assertEqual(main(['--snapshot',str(snap),'--output',str(new),
                                       '--goal-layout','v2','--resume-artifacts',str(old)]),0)
                args=run.call_args.args[0]
                self.assertIn('--goal-layout',args)
                self.assertEqual(args[args.index('--goal-layout')+1],'v2')
                self.assertEqual(args[args.index('--resume-artifacts')+1],str(old))
                self.assertNotIn('timeout',run.call_args.kwargs)
                self.assertFalse((old/'result.json').exists())
            no_old=base/'no-old'
            with self.assertRaises(SystemExit) as err:
                main(['--snapshot',str(snap),'--output',str(no_old),
                      '--resume-artifacts',str(base/'missing')])
            self.assertEqual(err.exception.code,2)
            self.assertFalse(no_old.exists())

    def test_real_frozen_goal_v2_with_continuation_fits_native_4000_char_limit(self):
        # The real H-0046 science is embedded in the previously frozen snapshot;
        # this fixture-free contract test still checks cue and generic limit.
        from theory_master.native_goal import investigator_goal
        from theory_master.native_goal_trial import _inside
        import inspect
        cue = ("\nCONTINUATION: use read-only prior science and PRECOMMITTED "
               "protocol from research_continuation context; never refit or "
               "reselect. Finish the missing frozen held-out test.")
        a=investigator_goal('investigate',SCIENCE,layout='v2')
        self.assertLessEqual(len(a+cue),4000)
        source=inspect.getsource(_inside)
        self.assertIn('len(goal_text) > 4000',source)
        self.assertIn('research_continuation',source)
