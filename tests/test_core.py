from __future__ import annotations
import io, json, os, re, subprocess, sys, tempfile, unittest
from datetime import datetime
from unittest import mock
from pathlib import Path
from types import SimpleNamespace
from theory_master.core import CYCLE_MARKER, CONTEXT_VALUE_MAX_CHARS, CodexBackend, DEFAULT_ROUTE, DEFAULT_ROUTES, FakeBackend, KNOWN_ROUTES, MODEL_PROFILES, REWORK_LIMIT, TaskSpec, Workflow, approval_decision, bound_json_value, bound_prompt_context, compact_ts, direction_text, find_cycle, is_cycle_dir, is_history_rewriting_command, is_process_control_command, is_runtime_current_state, json_object, latest_cycle_dir, list_cycle_dirs, make_approval_decision, make_run_id, master_run_id, migration_preview, parse_model_spec, resolve_routes, sticky_direction
from theory_master.__main__ import print_runs, resumable_runs, spec_from_resume_identifier
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.task import bootstrap


class OpenJudgeBackend(FakeBackend):
    """Deterministic backend for tests that specifically need a terminal-open Cycle."""
    def invoke(self, role, prompt, thread_id, model, provider):
        if role == "judge" and "Current task: terminal_assessment" in prompt:
            thread = self.ensure_thread(role, thread_id, model, provider)
            return thread, json.dumps({"verdict": "open", "rationale": "the bounded smoke result remains inconclusive"})
        return super().invoke(role, prompt, thread_id, model, provider)


class TheoryMasterTest(unittest.TestCase):
    def test_ls_sorts_by_latest_started_at_first(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, started_at in (
                ("run-older", "2026-09-20T10:00:00+08:00"),
                ("run-newer", "2026-09-28T10:00:00+08:00"),
            ):
                cycle = root / name
                cycle.mkdir(parents=True)
                (cycle / "run_state.json").write_text(json.dumps({
                    "master_id": 1,
                    "status": "FAILED",
                    "resumable": True,
                    "started_at": started_at,
                }), encoding="utf-8")

            runs = resumable_runs(root)
            self.assertEqual([item["started_at"] for item in runs], [
                "2026-09-28T10:00:00+08:00",
                "2026-09-20T10:00:00+08:00",
            ])

    def test_ls_compact_columns_and_minute_precision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle = root / "1"
            cycle.mkdir()
            (cycle / "run_state.json").write_text(json.dumps({
                "status": "FAILED", "resumable": True, "phase": "failed",
                "started_at": "2026-09-23T16:30:45+08:00", "goal_id": "hidden-goal",
                "execution_active": False,
            }), encoding="utf-8")
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                self.assertEqual(print_runs(root), 0)
            lines = output.getvalue().splitlines()
            self.assertEqual(lines[0].split(), ["TASK_ID", "CYCLE", "STARTED", "STATE", "PHASE"])
            self.assertNotIn("PID", lines[0])
            self.assertNotIn("GOAL", lines[0])
            self.assertIn(datetime.fromisoformat("2026-09-23T16:30:45+08:00").astimezone().strftime("%m-%d %H:%M"), lines[1])
            self.assertNotIn("16:30:45", lines[1])
            self.assertNotIn("hidden-goal", lines[1])

    def test_ls_shows_checkpoint_reached_even_when_run_projection_is_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle = root / "cycle-0001"
            cycle.mkdir()
            (cycle / "run_state.json").write_text(json.dumps({
                "master_id": 45,
                "status": "RUNNING",
                "phase": "pre_hypothesis_lock",
                "resumable": False,
                "execution_active": False,
                "owner_pid": 99999999,
            }), encoding="utf-8")
            (cycle / "research_state.json").write_text(json.dumps({
                "master_id": 45,
                "status": "checkpoint_reached",
                "phase": "pre_hypothesis_lock",
                "resumable": False,
                "execution_active": False,
                "hypothesis_locked": False,
            }), encoding="utf-8")
            listed = resumable_runs(root)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["status"], "CHECKPOINT_REACHED")
            self.assertFalse(listed[0]["live"])
            self.assertIsNone(listed[0]["pid"])

    def test_ls_hides_legacy_tm_run_but_exact_resume_id_still_resolves_canvas(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec, _ = self.make(root)
            spec.branch_canvas.write_text(json.dumps({'nodes': [{
                'id': 'entry', 'type': 'text',
                'text': f'<!-- theory-master-task-document: {root / "research_plan.md"} -->\ntype: TheoryMasterEntry\nstatus: active\n',
            }], 'edges': []}), encoding='utf-8')
            cycle = spec.research_root / 'tm-0007-test-goal'
            cycle.mkdir(parents=True)
            (cycle / CYCLE_MARKER).write_text('', encoding='utf-8')
            (cycle / 'run_state.json').write_text(json.dumps({
                'master_id': 7, 'status': 'FAILED', 'phase': 'failed', 'resumable': True,
                'goal_id': 'test-goal', 'plan_path': str(spec.document),
                'execution_active': False,
            }), encoding='utf-8')
            runs = resumable_runs(root)
            self.assertEqual(runs, [])
            self.assertEqual(spec_from_resume_identifier('tm-0007-test-goal', root).goal_id, spec.goal_id)
            self.assertTrue((spec.branch_canvas.parent / 'science_graph.sqlite3').is_file())

    def test_unregistered_legacy_run_cannot_bootstrap_a_new_task_on_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = root / "research_plan.md"
            plan.write_text(
                "# Goal\n\nHistorical goal\n\n# Research Contract\n\nEvidence\n\n"
                "# Final Gate\n\nResult\n\n# Required Context\n\n- old notes\n",
                encoding="utf-8",
            )
            cycle = root / "tm-0007-historical-goal"
            cycle.mkdir()
            (cycle / "run_state.json").write_text(json.dumps({
                "master_id": 7, "status": "FAILED", "resumable": True,
                "goal_id": "historical-goal", "plan_path": str(plan),
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unregistered legacy run.*cannot be resumed"):
                spec_from_resume_identifier("tm-0007-historical-goal", root)
            self.assertFalse((root / ".theory-master").exists())
            self.assertFalse((root / "theory-master.config.json").exists())
            self.assertFalse((root / "science_graph.sqlite3").exists())

    def test_ls_and_resume_ignore_tmlab_archived_overrun_cycles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / '.tmlab' / 'overrun_cycles' / 'cycle-0005'
            archive.mkdir(parents=True)
            plan = root / 'research_plan.md'
            plan.write_text('# archived only\n', encoding='utf-8')
            (archive / 'run_state.json').write_text(json.dumps({
                'master_id': 99,
                'status': 'FAILED',
                'phase': 'failed',
                'resumable': True,
                'goal_id': 'archived',
                'plan_path': str(plan),
                'execution_active': False,
            }), encoding='utf-8')
            self.assertEqual(resumable_runs(root), [])
            with self.assertRaisesRegex(ValueError, 'cannot find Task ID/Cycle'):
                spec_from_resume_identifier('cycle-0005', root)

    def test_ls_and_resume_ignore_interrupted_run_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "experiment" / "interrupted_runs" / "run-004-aborted" / "task" / "research" / "cycle-0001"
            archive.mkdir(parents=True)
            plan = root / "research_plan.md"
            plan.write_text("# archived only\n", encoding="utf-8")
            stale = {
                "master_id": 38,
                "status": "RUNNING",
                "phase": "research_lead:running",
                "resumable": True,
                "execution_active": True,
                "owner_pid": 99999999,
                "plan_path": str(plan),
            }
            (archive / "run_state.json").write_text(json.dumps(stale), encoding="utf-8")
            (archive / "research_state.json").write_text(json.dumps(stale), encoding="utf-8")
            self.assertEqual(resumable_runs(root), [])
            with self.assertRaisesRegex(ValueError, "cannot find Task ID/Cycle"):
                spec_from_resume_identifier("cycle-0001", root)

    def test_workspace_resume_prefers_task_binding_for_task_local_master_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec, _ = self.make(root)
            task_root = root / 'task'
            binding_dir = task_root / '.theory-master'
            binding_dir.mkdir(parents=True)
            branch = task_root / 'branch.canvas'
            branch.write_text('{"nodes": [], "edges": []}', encoding='utf-8')
            plan = task_root / 'research_plan.md'
            plan.write_text((root / 'research_plan.md').read_text(encoding='utf-8'), encoding='utf-8')
            from theory_master.task import bootstrap
            with mock.patch.dict(os.environ, {"THEORY_MASTER_SEARCH_ROOT": str(task_root)}):
                binding = bootstrap(plan)
            self.assertEqual(binding.master_id, 0)
            resolved = spec_from_resume_identifier(str(binding.master_id), task_root)
            self.assertTrue(resolved.task_mode)
            self.assertEqual(resolved.master_id, binding.master_id)
            cycle = resolved.research_root / '1'
            cycle.mkdir()
            (cycle / CYCLE_MARKER).write_text('', encoding='utf-8')
            (cycle / 'run_state.json').write_text(json.dumps({
                'master_id': binding.master_id, 'status': 'FAILED', 'phase': 'failed', 'resumable': True,
                'goal_id': 'test-goal', 'plan_path': str(plan),
                'started_at': '2026-09-23T16:30:00+08:00',
            }), encoding='utf-8')
            (cycle / 'research_state.json').write_text(json.dumps({
                'master_id': binding.master_id, 'status': 'FAILED', 'phase': 'failed', 'resumable': True,
                'goal_id': 'test-goal', 'plan_path': str(plan), 'execution_active': False,
            }), encoding='utf-8')
            listed = resumable_runs(task_root)
            self.assertEqual([item['resume_id'] for item in listed], [str(binding.master_id)])
            self.assertEqual(listed[0]['cycle'], '1')
            self.assertEqual(listed[0]['started_at'], '2026-09-23T16:30:00+08:00')

    def test_ls_normalizes_registered_legacy_completed_to_over_and_hides_unbound_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_root = root / "task"
            task_root.mkdir(parents=True)
            plan = task_root / "research_plan.md"
            plan.write_text(
                "# Goal\n\nTest a claim.\n\n"
                "# Research Contract\n\nUse evidence.\n\n"
                "# Final Gate\n\nThe requested claim is conclusively adjudicated.\n\n"
                "# Required Context\n\n- none\n\n"
                "# Scientific Model\n\nNo established findings yet.\n",
                encoding="utf-8",
            )
            from theory_master.task import bootstrap
            with mock.patch.dict(os.environ, {"THEORY_MASTER_SEARCH_ROOT": str(root)}):
                binding = bootstrap(plan)

            cycle = binding.task_root / "cycle-0001"
            cycle.mkdir()
            state = {
                "master_id": binding.master_id,
                "status": "COMPLETED",
                "phase": "complete",
                "resumable": False,
                "execution_active": False,
                "goal_id": binding.goal_id,
                "plan_path": str(plan),
                "started_at": "2026-10-04T12:56:16+08:00",
            }
            (cycle / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
            (cycle / "research_state.json").write_text(json.dumps({**state, "status": "completed"}), encoding="utf-8")

            legacy = root / "legacy" / "cycle-0001"
            legacy.mkdir(parents=True)
            (legacy / "run_state.json").write_text(json.dumps({
                "master_id": 999,
                "status": "COMPLETED",
                "phase": "complete",
                "resumable": False,
                "execution_active": False,
                "goal_id": "legacy",
                "started_at": "2026-10-04T13:00:00+08:00",
            }), encoding="utf-8")

            listed = resumable_runs(root)
            self.assertEqual([item["resume_id"] for item in listed], [str(binding.master_id)])
            self.assertEqual(listed[0]["status"], "OVER")
            self.assertEqual(listed[0]["cycle"], "1")

    def test_ls_projects_goal_passed_task_as_complete_while_cycle_remains_over(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_root = root / "task-complete"
            task_root.mkdir(parents=True)
            plan = task_root / "research_plan.md"
            plan.write_text(
                "# Goal\n\nTest a claim.\n\n"
                "# Research Contract\n\nUse evidence.\n\n"
                "# Final Gate\n\nThe requested claim is conclusively adjudicated.\n\n"
                "# Required Context\n\n- none\n\n"
                "# Scientific Model\n\nNo established findings yet.\n",
                encoding="utf-8",
            )
            from theory_master.task import bootstrap
            with mock.patch.dict(os.environ, {"THEORY_MASTER_SEARCH_ROOT": str(root)}):
                binding = bootstrap(plan)

            cycle = binding.task_root / "cycle-0001"
            cycle.mkdir()
            common = {
                "master_id": binding.master_id,
                "phase": "goal_complete",
                "resumable": False,
                "execution_active": False,
                "goal_id": binding.goal_id,
                "plan_path": str(plan),
                "started_at": "2026-10-07T21:30:00+08:00",
            }
            (cycle / "run_state.json").write_text(json.dumps({**common, "status": "OVER"}), encoding="utf-8")
            (cycle / "research_state.json").write_text(json.dumps({
                **common,
                "status": "over",
                "goal_status": "passed",
                "goal_completion": {"verdict": "passed", "rationale": "final gate passed"},
            }), encoding="utf-8")

            listed = resumable_runs(root)
            self.assertEqual([item["resume_id"] for item in listed], [str(binding.master_id)])
            self.assertEqual(listed[0]["status"], "COMPLETE")
            persisted = json.loads((cycle / "research_state.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], "over")

    def test_task_ids_allocate_compactly_and_duplicate_ids_fail_closed(self):
        from theory_master.task import SYSTEM_CONFIG_NAME, TaskBinding, bootstrap, master_id_for_root

        plan_text = (
            "# Goal\n\nTest a claim.\n\n"
            "# Research Contract\n\nUse evidence.\n\n"
            "# Final Gate\n\nA reproducible result is established.\n\n"
            "# Required Context\n\n- none\n\n"
            "# Scientific Model\n\nA test model.\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bindings = []
            with mock.patch.dict(os.environ, {"THEORY_MASTER_SEARCH_ROOT": str(root)}):
                for index in (1, 2):
                    workspace = root / f"run-{index:03d}" / "task"
                    workspace.mkdir(parents=True)
                    plan = workspace / "research_plan.md"
                    plan.write_text(plan_text, encoding="utf-8")
                    binding = bootstrap(plan)
                    bindings.append(binding)
                    cycle = binding.task_root / "cycle-0001"
                    cycle.mkdir()
                    (cycle / "run_state.json").write_text(json.dumps({
                        "master_id": binding.master_id,
                        "status": "FAILED",
                        "phase": "failed",
                        "resumable": True,
                        "goal_id": binding.goal_id,
                        "plan_path": str(plan),
                        "execution_active": False,
                    }), encoding="utf-8")

            self.assertEqual([binding.master_id for binding in bindings], [0, 1])
            listed = resumable_runs(root)
            self.assertEqual(sorted(item["resume_id"] for item in listed), ["0", "1"])
            for binding in bindings:
                resolved = spec_from_resume_identifier(str(binding.master_id), root)
                self.assertEqual(resolved.research_root, binding.task_root)
                legacy_hash = str(master_id_for_root(binding.task_root))
                self.assertEqual(spec_from_resume_identifier(legacy_hash, root).research_root, binding.task_root)

            second_config = bindings[1].config_path
            payload = json.loads(second_config.read_text(encoding="utf-8"))
            payload["task_id"] = 0
            second_config.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(TaskBinding.load(bindings[1].task_root).master_id, 0)
            with self.assertRaisesRegex(ValueError, "task ID '0' is ambiguous"):
                spec_from_resume_identifier("0", root)

    def test_resume_task_id_wins_over_same_named_cycle_directories(self):
        plan_text = (
            "# Goal\n\n- goal\n"
            "# Research Contract\n\n- contract\n"
            "# Final Gate\n\nA reproducible result is established.\n"
            "# Required Context\n\n- none\n"
            "# Scientific Model\n\nA test model.\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bindings = []
            with mock.patch.dict(os.environ, {"THEORY_MASTER_SEARCH_ROOT": str(root)}):
                for index in range(3):
                    workspace = root / f"task-{index}"
                    workspace.mkdir(parents=True)
                    plan = workspace / "research_plan.md"
                    plan.write_text(plan_text, encoding="utf-8")
                    binding = bootstrap(plan)
                    bindings.append(binding)
                    (binding.task_root / "1").mkdir()
            self.assertEqual([binding.master_id for binding in bindings], [0, 1, 2])
            resolved = spec_from_resume_identifier("1", root)
            self.assertEqual(resolved.research_root, bindings[1].task_root)

    def test_sqlite_graph_detail_is_authoritative_and_does_not_read_canvas(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec, _ = self.make(root)
            database = spec.branch_canvas.parent / "science_graph.sqlite3"
            with ScienceGraphStore(database) as store:
                store.add_hypothesis("H-0090", title="Frontier", summary="SQLite claim")
                store.append_outcome(outcome_id="outcome-h90", hypothesis_id="H-0090", cycle_id="90", verdict="open")
                store.set_frontiers_batch([{"hypothesis_id": "H-0090", "status": "active", "reason": "test"}])
                detail = store.agent_hypothesis_detail("H-0090")
                self.assertEqual(detail["id"], "H-0090")
                self.assertEqual(detail["claim"], "SQLite claim")
                self.assertEqual(store.frontier_view()["H-0090"]["frontier_status"], "active")
            spec.branch_canvas.write_text("not-json", encoding="utf-8")
            with ScienceGraphStore(database, readonly=True) as store:
                self.assertEqual(store.agent_hypothesis_detail("H-0090")["claim"], "SQLite claim")

    def test_registered_investigator_evidence_reaches_judge(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            state = Workflow(spec, FakeBackend(reworks=0)).run()
            attempt = state["investigation_attempts"]["attempt-0001"]
            self.assertEqual(attempt["readiness"]["decision"], "send_to_judge")
            self.assertTrue(attempt["readiness"]["ready"])
            self.assertTrue(state["evidence_refs"])
            events = [json.loads(line) for line in (self.cycle(spec) / "events.jsonl").read_text().splitlines()]
            routing = next(row for row in reversed(events) if row.get("type") == "investigation.routing")
            self.assertEqual(routing["payload"]["selected_executor"], "judge")
            self.assertEqual({item["executor_role"] for item in attempt["outputs"]}, {"investigator"})

    def test_json_object_repairs_stray_backslash_in_model_string(self):
        value = json_object(r'{"hypothesis":"use \\delta_g and C:\\temp\\x","title":"ok"}')
        self.assertIn("\\delta_g", value["hypothesis"])
        self.assertEqual(value["title"], "ok")

    def test_json_object_merges_safe_premature_top_level_close(self):
        raw = '{"mode":"cycle_end","scientific_synthesis":{"learned":[]}},"evolution_candidates":[{"id":"c1"}],"next_frontier_candidate_id":"c1"}'
        value = json_object(raw)
        self.assertEqual(value["mode"], "cycle_end")
        self.assertEqual(value["evolution_candidates"], [{"id": "c1"}])
        self.assertEqual(value["next_frontier_candidate_id"], "c1")

    def test_json_object_rejects_duplicate_fields_in_premature_close_tail(self):
        with self.assertRaisesRegex(ValueError, "repeats top-level fields"):
            json_object('{"mode":"cycle_end"},"mode":"other"}')

    def make(self, root: Path, reworks: int = 1, max_cycles: int = 1):
        plan=root/'research_plan.md'; control=root/'control.canvas'; research=root/'research'
        control.write_text(json.dumps({'nodes':[{'id':'human','type':'text','text':f'<!-- theory-master-task-document: {plan} -->\nkeep me','x':9,'y':8,'width':1,'height':1}],'edges':[]}),encoding='utf-8')
        plan.write_text('# Goal\n\ng\n\n# Research Contract\n\nc\n\n# Final Gate\n\nA reproducible scientific result is established.\n\n# Required Context\n\n- r\n\n# Scientific Model\n\nA test-only scientific model.\n', encoding='utf-8')
        spec = TaskSpec.load(plan)
        config = json.loads(spec.config_path.read_text(encoding='utf-8'))
        config['max_cycles'] = max_cycles
        config['max_judge_reworks_per_cycle'] = reworks
        spec.config_path.write_text(json.dumps(config), encoding='utf-8')
        spec = TaskSpec.from_config(spec.config_path)
        spec.branch_canvas.write_text(json.dumps({'nodes':[{'id':'human','type':'text','text':'keep me','x':9,'y':8,'width':1,'height':1}],'edges':[]}), encoding='utf-8')
        with ScienceGraphStore(spec.branch_canvas.parent / "science_graph.sqlite3"):
            pass
        return spec, FakeBackend(reworks)

    def cycle(self, spec: TaskSpec) -> Path:
        found = latest_cycle_dir(spec.research_root)
        self.assertIsNotNone(found)
        return found


    def test_legacy_cycle_without_canonical_sqlite_hypothesis_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            canvas_path = Path(temp) / "exploration.canvas"
            canvas_path.write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
            workflow = Workflow.__new__(Workflow)
            workflow.branch_canvas = canvas_path
            workflow.spec = SimpleNamespace(branch_canvas=canvas_path, branch_anchor_id=None)
            state = {"hypothesis_lock": {"status": "locked"}}
            branch = {"id": "1", "title": "Legacy H", "hypothesis": "legacy claim", "source_node_id": ""}
            selected = {"title": "Legacy H", "hypothesis": "legacy claim", "why_now": "historical record"}
            with self.assertRaisesRegex(FileNotFoundError, "Canonical SQLite graph is required"):
                workflow._ensure_locked_hypothesis(state, branch, selected)

    def test_same_hypothesis_identity_survives_milestone_into_next_cycle(self):
        class ContinuingBackend(FakeBackend):
            def __init__(self):
                from theory_master.backend_fakes import _fake_sqlite_lineage, _prompt_stage
                self._fake_sqlite_lineage = _fake_sqlite_lineage
                self._prompt_stage = _prompt_stage
                super().__init__(reworks=0)
                self.frontier_calls = 0
                self.judge_calls = 0
                self.frontier_contexts = []

            def invoke(self, role, prompt, thread_id, model, provider):
                context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0] if "Context:\n" in prompt else {}
                if role == "research_lead" and self._prompt_stage(prompt) == "frontier_proposal":
                    self.frontier_calls += 1
                    self.frontier_contexts.append(json.loads(json.dumps(context)))
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    continuing_same_h = self.frontier_calls > 1
                    decisive_test = "DT-A" if self.frontier_calls == 1 else "DT-B"
                    candidate = {
                        "hypothesis": (
                            "one scientific claim spanning two bounded adjudications"
                            if self.frontier_calls == 1
                            else "the same scientific claim, refined for the second bounded adjudication"
                        ),
                        "scope": "the same scientific scope, refined for DT-B" if continuing_same_h else "the same scientific scope",
                        "why_now": "the next bounded discriminant is executable",
                        "goal_advance": "accumulate decisive evidence for the same scientific claim",
                        "decisive_test": decisive_test,
                        **self._fake_sqlite_lineage(context),
                    }
                    return thread, json.dumps({"candidates": [candidate]})
                thread, raw = super().invoke(role, prompt, thread_id, model, provider)
                if role == "judge":
                    self.judge_calls += 1
                    value = json.loads(raw)
                    if self.judge_calls == 1:
                        value["verdict"] = "milestone_advanced"
                        value["objection"] = None
                    return thread, json.dumps(value)
                return thread, raw

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), max_cycles=2)
            backend = ContinuingBackend()
            state = Workflow(spec, backend).run()
            self.assertEqual(backend.frontier_calls, 2)
            self.assertTrue(all("hypothesis_catalog" not in item for item in backend.frontier_contexts))
            self.assertTrue(all("agent_graph" in item for item in backend.frontier_contexts))
            self.assertNotIn("canvas_access", backend.frontier_contexts[1])
            self.assertNotIn("prior_cycle_summary", backend.frontier_contexts[1])

            first = json.loads((spec.research_root / "1" / "research_state.json").read_text(encoding="utf-8"))
            second = json.loads((spec.research_root / "2" / "research_state.json").read_text(encoding="utf-8"))
            self.assertEqual(first["hypothesis_lock"]["id"], "H-0001")
            self.assertEqual(second["hypothesis_lock"]["id"], "H-0002")
            self.assertNotIn("hypothesis_relation", first["hypothesis_lock"])
            self.assertNotIn("hypothesis_relation", second["hypothesis_lock"])
            self.assertEqual(second["hypothesis_lock"]["hypothesis"], "the same scientific claim, refined for the second bounded adjudication")
            self.assertEqual(first["branches"][-1]["status"], "milestone_advanced")
            self.assertEqual(second["branches"][-1]["status"], "supported")
            with ScienceGraphStore(spec.branch_canvas.parent / "science_graph.sqlite3", readonly=True) as store:
                self.assertEqual({row["id"] for row in store.graph_view()["nodes"] if row.get("type") == "Hypothesis"}, {"H-0001", "H-0002"})

            canvas = json.loads(spec.branch_canvas.read_text(encoding="utf-8"))
            node_ids = [str(node.get("id") or "") for node in canvas["nodes"]]
            self.assertEqual(node_ids.count("H-0001"), 1)
            self.assertEqual(node_ids.count("H-0002"), 1)
            self.assertFalse(any(re.fullmatch(r"\\d+-decision", node_id) for node_id in node_ids))

    def test_happy_path_events_and_sqlite_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp))
            state = Workflow(spec, backend).run()
            self.assertEqual(state['branches'][-1]['status'], 'supported')
            self.assertIn('research_lead', state['threads'])
            self.assertNotIn('director', state['threads'])
            self.assertNotIn('defense', state['threads'])
            self.assertEqual(state['hypothesis_lock']['status'], 'locked')
            cycle = latest_cycle_dir(spec.research_root)
            self.assertIsNotNone(cycle)
            events = [json.loads(line) for line in (cycle / 'events.jsonl').read_text().splitlines()]
            self.assertEqual([event['seq'] for event in events], list(range(1, len(events) + 1)))
            projection = json.loads(spec.branch_canvas.read_text(encoding='utf-8'))
            self.assertTrue(spec.branch_canvas.is_file())
            self.assertTrue((cycle / 'workspace').is_dir())
            self.assertTrue((cycle / 'notes').is_dir())
            self.assertFalse(any(re.fullmatch(r'\d+-decision', str(node.get('id', ''))) for node in projection['nodes']))
            self.assertEqual(state['hypothesis_lock']['id'], 'H-0001')
            self.assertIn('H-0001', {str(node.get('id', '')) for node in projection['nodes']})
            self.assertFalse(any(node.get('id') == 'tm-scientific-model' for node in projection['nodes']))
            plan = spec.document.read_text(encoding='utf-8')
            self.assertIn('# Scientific Model\n\nDeterministic smoke fixture completed its controlled evidence path.', plan)
            self.assertNotIn('## 当前科学图景', plan)
            ids = {node.get('id') for node in projection['nodes']}
            self.assertNotIn('tm-v2-next-action', ids)
            self.assertNotIn('tm-v2-current-gate', ids)
            self.assertTrue((cycle / 'run_report.md').is_file())
            with ScienceGraphStore(spec.branch_canvas.parent / 'science_graph.sqlite3', readonly=True) as store:
                graph = store.graph_view()
                self.assertEqual([node['id'] for node in graph['nodes'] if node.get('type') == 'Hypothesis'], ['H-0001'])
                self.assertEqual(graph['nodes'][0]['type'], 'Hypothesis')

    def test_pre_hypothesis_lock_checkpoint_stops_before_investigator(self):
        class CountingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.roles = []

            def invoke(self, role, prompt, thread_id, model, provider):
                self.roles.append(role)
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), max_cycles=3)
            backend = CountingBackend()
            state = Workflow(spec, backend, stop_before="hypothesis_lock").run()
            self.assertEqual(state["status"], "checkpoint_reached")
            self.assertEqual(state["checkpoint"]["name"], "pre_hypothesis_lock")
            self.assertEqual(state["scientific_phase"], "qualification")
            self.assertTrue(state["checkpoint"]["selected_candidate_id"])
            self.assertEqual(state["hypothesis_lock"]["status"], "unlocked")
            self.assertFalse(state["hypothesis_locked"])
            self.assertIn("research_lead", backend.roles)
            self.assertIn("challenger", backend.roles)
            self.assertNotIn("investigator", backend.roles)
            self.assertNotIn("judge", backend.roles)
            self.assertEqual(len(list_cycle_dirs(spec.research_root)), 1)
            cycle = latest_cycle_dir(spec.research_root)
            events = [json.loads(line) for line in (cycle / "events.jsonl").read_text().splitlines()]
            self.assertTrue(any(row.get("type") == "workflow.checkpoint_reached" for row in events))

    def test_pre_hypothesis_lock_resume_consumes_checkpoint_without_requalification(self):
        class CountingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.roles = []
                self.lead_prompts = []

            def invoke(self, role, prompt, thread_id, model, provider):
                self.roles.append(role)
                if role == "research_lead":
                    self.lead_prompts.append(prompt)
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), max_cycles=1)
            first = CountingBackend()
            stopped = Workflow(spec, first, stop_before="hypothesis_lock").run()
            self.assertEqual(stopped["checkpoint"]["name"], "pre_hypothesis_lock")
            resumed_backend = CountingBackend()
            resumed = Workflow(spec, resumed_backend, stop_before="judge").resume(0)
            self.assertEqual(resumed["checkpoint"]["name"], "pre_judge")
            self.assertEqual(len(resumed_backend.lead_prompts), 1)
            self.assertIn("Current task: hypothesis_display", resumed_backend.lead_prompts[0])
            self.assertNotIn("challenger", resumed_backend.roles)
            self.assertIn("investigator", resumed_backend.roles)
            self.assertNotIn("judge", resumed_backend.roles)

    def test_qualification_closure_never_calls_semantic_backend(self):
        class ForbiddenSemanticBackend:
            name = "forbidden"

            def decide(self, **_kwargs):
                raise AssertionError("qualification closure must be deterministic")

        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), max_cycles=1)
            state = Workflow(
                spec,
                backend,
                semantic_backend=ForbiddenSemanticBackend(),
                stop_before="hypothesis_lock",
            ).run()
            self.assertEqual(state["status"], "checkpoint_reached")

    def test_nonterminal_failed_cycle_does_not_project_decision(self):
        class FailBeforeTerminalJudge(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "judge":
                    raise RuntimeError("synthetic provider failure before terminal Judge")
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            with self.assertRaisesRegex(RuntimeError, "before terminal Judge"):
                Workflow(spec, FailBeforeTerminalJudge(reworks=0)).run()
            cycle = latest_cycle_dir(spec.research_root)
            self.assertIsNotNone(cycle)
            branch = json.loads(spec.branch_canvas.read_text(encoding="utf-8"))
            self.assertFalse(any(
                re.fullmatch(r"\d{4}-decision", str(node.get("id", "")))
                for node in branch.get("nodes", [])
            ))

    def test_rework_cap_pauses_without_scientific_open(self):
        with tempfile.TemporaryDirectory() as temp:
            from dataclasses import replace
            spec, backend = self.make(Path(temp), reworks=9)
            spec = replace(spec, max_judge_reworks_per_cycle=3)
            state = Workflow(spec, backend).run()
            self.assertEqual(state['judge_rework_count'], 3)
            self.assertEqual(state['investigation_attempt'], 4)
            self.assertEqual(state['status'], 'needs_attention')
            self.assertTrue(state['resumable'])
            self.assertIsNone(state['scientific_verdict'])
            self.assertEqual(state['assessment_state'], 'rework_requested')
            self.assertEqual(state['closure_reason'], 'judge_rework_budget_exhausted')
            self.assertEqual(state['resource_disposition'], 'budget_exhausted')
            self.assertEqual(state['next_disposition'], 'rework_blocked')
            self.assertEqual(state['attention']['code'], 'judge_rework_budget_exhausted')
            self.assertNotEqual(state['runtime_state'], 'over')
            terminal = state['role_outputs']['judge']
            self.assertIsNone(terminal['verdict'])
            self.assertEqual(len(terminal['objections']), 1)
            self.assertIsInstance(terminal['rework_plan'], dict)
            original = state['rework_authorization']['judge_request']
            self.assertEqual(len(original['objections']), 1)
            self.assertIsInstance(original['rework_plan'], dict)
            branch_canvas = json.loads(spec.branch_canvas.read_text(encoding='utf-8'))
            self.assertFalse(any(str(n.get('id', '')).endswith('-decision') for n in branch_canvas.get('nodes', [])))

    def test_rejected_judge_rework_pauses_and_preserves_request_for_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), reworks=1)
            workflow = Workflow(spec, backend)
            workflow._authorize_judge_rework = lambda *_args, **_kwargs: (False, 'synthetic_rework_denial')
            state = workflow.run()
            terminal = state['role_outputs']['judge']
            self.assertEqual(state['closure_reason'], 'judge_rework_not_authorized')
            self.assertIsNone(terminal['verdict'])
            self.assertEqual(terminal['assessment_state'], 'rework_requested')
            self.assertEqual(len(terminal['objections']), 1)
            self.assertIsInstance(terminal['rework_plan'], dict)
            self.assertEqual(state['status'], 'needs_attention')
            self.assertTrue(state['resumable'])
            self.assertIsNone(state['scientific_verdict'])
            self.assertEqual(state['attention']['code'], 'judge_rework_not_authorized')
            authorization = state['rework_authorization']
            self.assertFalse(authorization['authorized'])
            self.assertEqual(authorization['reason'], 'synthetic_rework_denial')
            self.assertEqual(len(authorization['judge_request']['objections']), 1)
            self.assertIsInstance(authorization['judge_request']['rework_plan'], dict)

    def test_max_cycles_two_open_cycles(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0, max_cycles=2)
            state = Workflow(spec, OpenJudgeBackend(reworks=0)).run()
            cycles = list_cycle_dirs(spec.research_root)
            self.assertEqual(len(cycles), 2)
            self.assertEqual(state['branches'][-1]['status'], 'open')
            self.assertEqual(Path(state['cycle_dir']).resolve(), cycles[0].resolve())
            for cycle in cycles:
                self.assertTrue((cycle / CYCLE_MARKER).is_file())
                sci = json.loads((cycle / 'research_state.json').read_text())
                self.assertEqual(sci['branches'][-1]['status'], 'open')
                self.assertTrue((cycle / 'notes').is_dir())
                self.assertTrue(spec.branch_canvas.is_file())

    def test_hot_max_cycles_update_stops_before_next_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), max_cycles=5)
            workflow = Workflow(spec, backend)
            calls = []

            def one_cycle():
                workflow.invocation_cycle_index += 1
                calls.append(workflow.invocation_cycle_index)
                if workflow.invocation_cycle_index == 1:
                    self.assertEqual(workflow.set_max_cycles(1), 1)
                return {
                    "status": "over",
                    "operational_only_closure": False,
                }

            with mock.patch.object(workflow, "_run_cycle", side_effect=one_cycle):
                state = workflow.run()
            self.assertEqual(calls, [1])
            self.assertEqual(state["status"], "over")
            self.assertEqual(workflow.current_max_cycles(), 1)
            payload = json.loads(Path(spec.config_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["max_cycles"], 1)

    def test_run_stops_remaining_cycle_budget_after_non_over_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), max_cycles=4)
            for terminal_status in ("interrupted", "paused", "failed", "cancelled", "needs_attention"):
                with self.subTest(status=terminal_status):
                    workflow = Workflow(spec, backend)
                    calls = []
                    def one_cycle():
                        calls.append(terminal_status)
                        return {"status": terminal_status}
                    workflow._run_cycle = one_cycle
                    state = workflow.run()
                    self.assertEqual(state["status"], terminal_status)
                    self.assertEqual(calls, [terminal_status])

    def test_cycle_budget_continuation_policy_is_shared_by_run_and_resume(self):
        self.assertTrue(Workflow._can_continue_cycle_budget({
            "status": "over", "next_start_node_id": "H-next",
        }))
        self.assertTrue(Workflow._can_continue_cycle_budget({
            "status": "over", "next_start_node_id": "H-next",
        }, require_next_frontier=True))
        self.assertFalse(Workflow._can_continue_cycle_budget({
            "status": "over", "next_start_node_id": None,
        }, require_next_frontier=True))
        operationally_closed = {
            "status": "over", "next_start_node_id": "H-same",
            "operational_only_closure": {"kind": "external_artifact_source_unavailable"},
        }
        self.assertFalse(Workflow._can_continue_cycle_budget(operationally_closed))
        self.assertFalse(Workflow._can_continue_cycle_budget(
            operationally_closed, require_next_frontier=True,
        ))
        for status in ("interrupted", "paused", "failed", "cancelled", "needs_attention", "active"):
            with self.subTest(status=status):
                self.assertFalse(Workflow._can_continue_cycle_budget({
                    "status": status, "next_start_node_id": "H-next",
                }))

    def test_each_cycle_gets_fresh_conversations_for_every_role(self):
        class CapturingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = []

            def invoke(self, role, prompt, thread_id, model, provider):
                result = super().invoke(role, prompt, thread_id, model, provider)
                context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0] if "Context:\n" in prompt else {}
                self.calls.append((role, str(context.get("cycle_dir") or ""), result[0]))
                return result

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), max_cycles=2)
            backend = CapturingBackend()
            Workflow(spec, backend).run()
            by_role = {}
            for role, _cycle_dir, thread_id in backend.calls:
                by_role.setdefault(role, []).append(thread_id)
            self.assertGreaterEqual(len(by_role), 1)
            for role, thread_ids in by_role.items():
                self.assertGreaterEqual(len(thread_ids), 2, role)
                self.assertGreaterEqual(len(set(thread_ids)), 2, f"{role} must use fresh conversations across Cycle/stage contexts")

    def test_max_cycles_is_per_invocation_and_ids_continue_history(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0, max_cycles=2)
            Workflow(spec, OpenJudgeBackend(reworks=0)).run()
            names = {path.name for path in list_cycle_dirs(spec.research_root)}
            self.assertEqual(len(names), 2)
            state = Workflow(spec, OpenJudgeBackend(reworks=0)).run()
            cycles = list_cycle_dirs(spec.research_root)
            self.assertEqual(len(cycles), 4)
            self.assertEqual(names, {path.name for path in cycles[2:]})
            self.assertEqual(Path(state['cycle_dir']).resolve(), cycles[0].resolve())
            self.assertEqual(state['branches'][-1]['status'], 'open')

    def test_terminal_branch_closure_uses_cycle_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), reworks=0, max_cycles=2)
            state = Workflow(spec, backend).run()
            self.assertEqual(state['branches'][-1]['status'], 'supported')
            self.assertEqual(len(list_cycle_dirs(spec.research_root)), 2)

    def test_rejected_path_does_not_implicitly_select_sibling_frontier(self):
        from theory_master.multi_worker import frontier_snapshot
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'science_graph.sqlite3'
            with ScienceGraphStore(db) as store:
                for h in ('H-0001', 'H-0002'):
                    store.add_hypothesis(h, title=h)
                    store.append_outcome(outcome_id=f'outcome-{h}', hypothesis_id=h, cycle_id=h[-1], verdict='open')
                store.set_frontiers_batch([
                    {'hypothesis_id':'H-0001','status':'closed','reason':'rejected path is terminal'},
                    {'hypothesis_id':'H-0002','status':'active','reason':'explicitly selected by Research Lead'},
                ])
            _, eligible = frontier_snapshot(db)
            self.assertEqual([row['hypothesis_id'] for row in eligible], ['H-0002'])

    def test_open_cycle_outcome_links_report_from_its_hypothesis(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec, _ = self.make(root, reworks=0)
            state = Workflow(spec, OpenJudgeBackend(reworks=0)).run()
            item = state['branches'][-1]
            self.assertEqual(item['status'], 'open')
            note = Path(item['note_path'])
            self.assertTrue(note.is_file())
            projection = json.loads(spec.branch_canvas.read_text(encoding='utf-8'))
            self.assertFalse(any(re.fullmatch(r'\d+-decision', str(node.get('id', ''))) for node in projection['nodes']))
            with ScienceGraphStore(spec.branch_canvas.parent / 'science_graph.sqlite3', readonly=True) as store:
                outcome = store.db.execute("SELECT verdict,report_ref FROM outcomes WHERE hypothesis_id=?", (state['hypothesis_lock']['id'],)).fetchone()
                self.assertIsNotNone(outcome)
                self.assertEqual(outcome['verdict'], 'open')
                self.assertTrue(outcome['report_ref'])
                self.assertIn(note.stem, outcome['report_ref'])
    def test_migration_preview_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'old.json'; path.write_text('{"goal_id":"old"}'); before=path.read_bytes(); preview=migration_preview(path)
            self.assertEqual(path.read_bytes(),before); self.assertEqual(preview['writes'],[])

    def test_cancelled_run_is_reset_and_lock_is_forwarded(self):
        class CapturingBackend(FakeBackend):
            def __init__(self): super().__init__(reworks=0); self.prompts = {}
            def invoke(self, role, prompt, thread_id, model, provider):
                self.prompts.setdefault(role, []).append(prompt)
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), max_cycles=2); backend = CapturingBackend(); workflow = Workflow(spec, backend)
            workflow.initialize(); workflow.cancel("operator timeout")
            state = workflow.run()
            self.assertEqual(state['status'], 'over')
            self.assertNotIn('operator timeout', backend.prompts['research_lead'][0])
            self.assertNotIn('defense', backend.prompts)
            investigator = json.loads(backend.prompts['investigator'][0].split('Context:\n', 1)[1])
            self.assertEqual(
                investigator['frozen_objective']['hypothesis'],
                state['hypothesis_lock']['hypothesis'],
            )
            self.assertNotIn('hypothesis_lock', investigator)
            self.assertNotIn('hypothesis_id', investigator['frozen_objective'])
            self.assertNotIn('cycle_criteria', investigator['frozen_objective'])
            self.assertNotIn('branch', investigator)

    def test_qualification_order_and_lock_payload(self):
        class OrderedBackend(FakeBackend):
            def __init__(self): super().__init__(reworks=0); self.calls = []
            def invoke(self, role, prompt, thread_id, model, provider):
                context = json.JSONDecoder().raw_decode(prompt.split('Context:\n', 1)[1])[0]
                self.calls.append((role, context.get('phase') or context.get('mode')))
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp)); backend = OrderedBackend()
            state = Workflow(spec, backend).run()
            roles = [role for role, _ in backend.calls]
            self.assertEqual(roles[:2], ['research_lead', 'challenger'])
            self.assertEqual(roles[2], 'research_lead')  # post-lock display copy, not qualification
            self.assertEqual(state['hypothesis_display']['status'], 'generated')
            self.assertNotIn('literature', roles)
            lock = state['hypothesis_lock']
            for key in ('candidate_id', 'title', 'hypothesis', 'scope',
                        'initial_investigation_intent', 'candidate_provenance'):
                self.assertIn(key, lock)
            self.assertNotIn('critical_issues', lock)
            self.assertNotIn('investigation_requirements', lock)
            self.assertNotIn('investigation_requirements', state)
            self.assertEqual(lock['cycle_criteria'], [{'id': 'C-1', 'criterion': lock['decisive_test']}])
            for legacy_field in ('success_criteria', 'failure_criteria', 'cycle_progress_criteria', 'hypothesis_terminal_criteria'):
                self.assertNotIn(legacy_field, lock)
            self.assertEqual(state['initial_executor'], 'investigator')
            self.assertTrue(any('hypothesis-lock' in path.name for path in (self.cycle(spec) / 'checkpoints').iterdir()))

    def test_single_challenger_audit_is_followed_by_one_frozen_repair(self):
        class AlwaysReviseBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = []
                self.prompts = []
                self.repairs = 0

            def invoke(self, role, prompt, thread_id, model, provider):
                context = json.JSONDecoder().raw_decode(prompt.split('Context:\n', 1)[1])[0] if 'Context:\n' in prompt else {}
                self.calls.append((role, context.get('phase') or context.get('mode')))
                self.prompts.append((role, prompt))
                if role == 'challenger' and context.get('repaired_candidate'):
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    return thread, json.dumps({'candidate_id': context['repaired_candidate']['id'],
                                               'verdict': 'confirmed',
                                               'reason': 'original frozen blocker resolved in the revised decisive test'})
                if role == 'research_lead' and context.get('repair_options'):
                    self.repairs += 1
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    parent = context['repair_options'][0]['candidate_id']
                    return thread, json.dumps({'selected_parent_candidate_id': parent, 'outcome': 'resolved', 'repair': {
                        'hypothesis_clarification': 'the frozen qualification predicate is made explicit',
                        'repaired_decisive_test': 'certify that frozen qualification predicate operationally',
                        'estimand_or_operational_definition': 'use the parent scientific target with an explicit operational predicate',
                        'blocker_closures': [{'blocker_id': f'{parent}:block-1', 'resolution': 'make the existing predicate operational'}],
                    }})
                thread, raw = super().invoke(role, prompt, thread_id, model, provider)
                value = json.loads(raw)
                if role == 'challenger':
                    for review in value.get('reviews', []):
                        review.update({
                            'verdict': 'revise',
                            'reason': 'qualification preparation is incomplete',
                            'blocking_issues': [{
                                'problem': 'the qualification predicate is not yet operational',
                                'required_change': 'make the existing predicate operational without changing its scope',
                            }],
                        })
                return thread, json.dumps(value)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = AlwaysReviseBackend()
            state = Workflow(spec, backend).run()
            self.assertEqual(state['status'], 'over')
            self.assertEqual(state['qualification_status'], 'qualified')
            self.assertEqual(state['qualification_repair_count'], 1)
            self.assertEqual(backend.repairs, 1)
            self.assertEqual(sum(role == 'challenger' for role, _ in backend.calls), 2)
            review = state['hypothesis_lock']['qualification']['review']
            self.assertEqual(review['qualification_basis'], 'repair_verified')

    def test_selection_repair_unresolved_is_terminal_for_each_candidate(self):
        class PersistentRepairBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.repairs = 0

            def invoke(self, role, prompt, thread_id, model, provider):
                context = json.JSONDecoder().raw_decode(prompt.split('Context:\n', 1)[1])[0] if 'Context:\n' in prompt else {}
                if role == 'challenger' and context.get('repaired_candidate'):
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    return thread, json.dumps({'candidate_id': context['repaired_candidate']['id'],
                                               'verdict': 'unresolved',
                                               'reason': 'original preparation blocker remains unresolved'})
                if role == 'research_lead' and context.get('repair_options'):
                    self.repairs += 1
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    parent = context['repair_options'][0]['candidate_id']
                    return thread, json.dumps({'selected_parent_candidate_id': parent, 'outcome': 'resolved', 'repair': {
                        'hypothesis_clarification': 'the frozen response-blind preparation claim is unchanged',
                        'repaired_decisive_test': 'test the explicitly specified preparation protocol',
                        'estimand_or_operational_definition': 'define the existing preparation predicate as an executable protocol',
                        'blocker_closures': [{'blocker_id': f'{parent}:block-1', 'resolution': 'make one minimal executable repair'}],
                    }})
                thread, raw = super().invoke(role, prompt, thread_id, model, provider)
                value = json.loads(raw)
                if role == 'challenger':
                    for review in value.get('reviews', []):
                        review.update({
                            'verdict': 'revise',
                            'reason': 'preparation is still incomplete',
                            'blocking_issues': [{
                                'problem': 'the preparation protocol remains under-specified',
                                'required_change': 'make one more minimal executable repair',
                            }],
                        })
                return thread, json.dumps(value)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = PersistentRepairBackend()
            state = Workflow(spec, backend).run()
            self.assertEqual(state['qualification_status'], 'no_qualified_candidate')
            self.assertFalse(state['hypothesis_locked'])
            self.assertEqual(backend.repairs, 3)
            self.assertEqual(state['attention']['code'], 'no_qualified_candidate')

    def test_selection_repair_cannot_repair_stops_without_locking(self):
        class CannotRepairBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = []

            def invoke(self, role, prompt, thread_id, model, provider):
                context = json.JSONDecoder().raw_decode(prompt.split('Context:\n', 1)[1])[0] if 'Context:\n' in prompt else {}
                self.calls.append(role)
                if role == 'research_lead' and context.get('repair_options'):
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    parent = context['repair_options'][0]['candidate_id']
                    return thread, json.dumps({
                        'selected_parent_candidate_id': parent,
                        'outcome': 'cannot_repair',
                        'cannot_repair_reason': 'no consequential executable decisive test remains inside the frozen scope',
                    })
                thread, raw = super().invoke(role, prompt, thread_id, model, provider)
                value = json.loads(raw)
                if role == 'challenger':
                    for review in value.get('reviews', []):
                        review.update({
                            'verdict': 'revise',
                            'reason': 'the proposed test only repeats an already adjudicated result',
                            'blocking_issues': [{
                                'problem': 'no unresolved consequential prediction remains',
                                'required_change': 'provide a consequential unresolved test inside the same scope',
                            }],
                        })
                return thread, json.dumps(value)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = CannotRepairBackend()
            state = Workflow(spec, backend).run()
            self.assertEqual(state['status'], 'no_qualified_candidate')
            self.assertEqual(state['qualification_status'], 'no_qualified_candidate')
            self.assertFalse(state['hypothesis_locked'])
            self.assertNotIn('investigator', backend.calls)
            # Each of the three bounded different-angle proposals is initially
            # reviewed once; cannot_repair itself triggers no second review.
            self.assertEqual(sum(role == 'challenger' for role in backend.calls), 3)
            self.assertEqual(state['attention']['code'], 'no_qualified_candidate')
            review = state['frontier_review']['reviews'][0]
            self.assertEqual(review['verdict'], 'revise')
            self.assertEqual(len(review['blocking_issues']), 1)
            events = [json.loads(line) for line in (self.cycle(spec) / 'events.jsonl').read_text().splitlines()]
            self.assertTrue(any(event.get('type') == 'qualification.cannot_repair_unqualified' for event in events))
            self.assertFalse(any(event.get('type') == 'qualification.single_candidate_selected' for event in events))

    def test_resume_discards_cannot_repair_candidate_with_stale_lineage_anchor(self):
        with tempfile.TemporaryDirectory() as temp:
            canvas_path = Path(temp) / 'branch.canvas'
            canvas_path.write_text(json.dumps({'nodes': [], 'edges': []}), encoding='utf-8')
            workflow = Workflow.__new__(Workflow)
            workflow.branch_canvas = canvas_path
            workflow.spec = SimpleNamespace(branch_canvas=canvas_path, branch_anchor_id=None)
            with ScienceGraphStore(canvas_path.with_name("science_graph.sqlite3")) as store:
                store.add_hypothesis("H-0001", title="Known source")
            state = {
                'qualification_status': 'pending',
                'role_outputs': {
                    'research_lead': {
                        'outcome': 'cannot_repair',
                        'selected_parent_candidate_id': 'candidate-1',
                        'reason': 'preserve unresolved blockers',
                    },
                    'research_lead_frontier': {'candidates': [{
                        'id': 'candidate-1',
                        'lineage_anchor_node_id': 'H-9999',
                        'evolution_operator': 'derive',
                    }]},
                    'challenger': {'reviews': [{'candidate_id': 'candidate-1', 'verdict': 'revise'}]},
                },
                'frontier_review': {'reviews': [{
                    'candidate_id': 'candidate-1', 'verdict': 'revise',
                    'blocking_issues': [{'problem': 'blocked', 'required_change': 'resolve'}],
                }]},
                'qualification_working_set': {'candidates': [{'id': 'candidate-1'}]},
            }

            self.assertTrue(workflow._restart_unqualified_qualification(state))
            self.assertNotIn('research_lead_frontier', state['role_outputs'])
            self.assertNotIn('research_lead', state['role_outputs'])
            self.assertNotIn('qualification_working_set', state)
            self.assertEqual(state['qualification_status'], 'pending')
            self.assertTrue(state['recovery']['qualification_restart'])

    def test_rejected_candidates_close_without_locking_or_passing_goal(self):
        class FatalBackend(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                thread, raw = super().invoke(role, prompt, thread_id, model, provider)
                value = json.loads(raw)
                if role == 'challenger' and value.get('reviews'):
                    value['reviews'][0] = {**value['reviews'][0], 'verdict': 'reject',
                                           'reason': 'contradicts established evidence'}
                return thread, json.dumps(value)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            workflow = Workflow(spec, FatalBackend(reworks=0))
            workflow.supervisor_enabled = lambda: False
            state = workflow.run()
            self.assertEqual(state['status'], 'no_qualified_candidate')
            self.assertEqual(state['qualification_status'], 'no_qualified_candidate')
            self.assertFalse(state['hypothesis_locked'])
            self.assertEqual(state['hypothesis_lock']['status'], 'unlocked')
            self.assertTrue(state['role_outputs']['candidate_review']['reviews'])
            self.assertEqual(state['attention']['code'], 'no_qualified_candidate')

    def test_revise_creates_replacement_after_one_challenger_repair_confirmation(self):
        class ReviseBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.repair_context = {}
                self.selection_ids = []
                self.challenger_threads = []

            def invoke(self, role, prompt, thread_id, model, provider):
                context = json.JSONDecoder().raw_decode(prompt.split('Context:\n', 1)[1])[0] if 'Context:\n' in prompt else {}
                if role == 'research_lead' and context.get('repair_options'):
                    self.repair_context = context
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    parent = context['repair_options'][0]['candidate_id']
                    return thread, json.dumps({"selected_parent_candidate_id": parent, "outcome": "resolved", "repair": {
                        "hypothesis_clarification": "the parent predicate claim remains unchanged",
                        "repaired_decisive_test": "certify the predicate protocol only",
                        "estimand_or_operational_definition": "define the parent predicate certification procedure explicitly",
                        "blocker_closures": [{"blocker_id": f"{parent}:block-1", "resolution": "separate definition from execution"}],
                    }})
                if role == 'challenger' and context.get('repaired_candidate'):
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    self.challenger_threads.append(thread)
                    return thread, json.dumps({'candidate_id': context['repaired_candidate']['id'],
                                               'verdict': 'confirmed',
                                               'reason': 'original definition/instance coupling removed in complete revised test'})
                thread, raw = super().invoke(role, prompt, thread_id, model, provider)
                value = json.loads(raw)
                if role == 'challenger':
                    self.challenger_threads.append(thread)
                    candidates = context.get('candidate') or []
                    if candidates and candidates[0].get('hypothesis') == 'smoke-h' and value.get('reviews'):
                        value['reviews'][0].update({'verdict': 'revise',
                                                   'reason': 'candidate combines definition and instance search',
                                                   'blocking_issues': [{
                                                       'problem': 'definition and instance search are coupled',
                                                       'required_change': 'separate the existing predicate definition from its execution without changing the claim',
                                                   }]})
                if role == 'research_lead' and context.get('qualified_candidates'):
                    self.selection_ids = [item.get('id') for item in context.get('qualified_candidates') or []]
                return thread, json.dumps(value)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = ReviseBackend()
            state = Workflow(spec, backend).run()
            self.assertNotIn('repair_budget', backend.repair_context)
            option = backend.repair_context['repair_options'][0]
            parent_id = option['candidate_id']
            self.assertTrue(parent_id.startswith('cand-'))
            self.assertEqual(option['blockers'][0]['id'], f'{parent_id}:block-1')
            self.assertNotIn('candidate_reviews', backend.repair_context)
            self.assertEqual(backend.selection_ids, [])
            self.assertNotEqual(state['hypothesis_lock']['candidate_id'], parent_id)
            self.assertTrue(state['hypothesis_lock']['candidate_id'].startswith('cand-'))
            self.assertEqual(len(backend.challenger_threads), 2)
            expected_test = (
                'certify the predicate protocol only\n\n'
                'Binding hypothesis clarification:\n'
                'the parent predicate claim remains unchanged\n\n'
                'Binding operational definition:\n'
                'define the parent predicate certification procedure explicitly'
            )
            self.assertEqual(state['hypothesis_lock']['decisive_test'], expected_test)
            self.assertNotIn('cycle_milestone', state['hypothesis_lock'])
            self.assertEqual(state['branches'][-1]['decisive_test'], expected_test)
            self.assertNotIn('cycle_milestone', state['branches'][-1])
            self.assertEqual(state['hypothesis_lock']['hypothesis'], 'smoke-h')
            repaired_candidate = next(
                item for item in state['cycle_start_candidates']
                if item['id'] == state['hypothesis_lock']['candidate_id']
            )
            self.assertNotIn('evolution_operator', repaired_candidate)
            self.assertEqual(repaired_candidate['lineage_anchor_node_ids'], [])
            self.assertEqual(repaired_candidate['goal_advance'], 'advance the deterministic fixture Goal from definition to registered evidence')

    def test_cancel_reconciles_interrupted_event_sequence(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp)); workflow = Workflow(spec, backend)
            state = workflow.initialize()
            workflow._emit(state, 'role.started', {'role': 'numerical'})
            workflow._emit(state, 'role.completed', {'role': 'numerical'})
            # Simulate a process death before its state checkpoint, then an old
            # cancellation command that wrote a duplicated sequence number.
            state['last_seq'] = 0
            workflow._emit(state, 'run.cancelled', {'reason': 'timeout'})
            from theory_master.core import write_json
            write_json(workflow._state_path(), state)
            reconciled = workflow.initialize()
            events = [json.loads(line) for line in workflow.events_path.read_text().splitlines()]
            self.assertEqual([event['seq'] for event in events], [1, 2, 3])
            self.assertEqual(reconciled['last_seq'], 3)

    def test_emit_reconciles_stale_in_memory_sequence_before_append(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            workflow = Workflow(spec, FakeBackend(reworks=0))
            state = workflow.initialize()
            workflow._emit(state, 'role.started', {'role': 'challenger'})
            stale = dict(state)
            stale['last_seq'] = 0
            workflow._emit(stale, 'role.completed', {'role': 'challenger'})
            events = [json.loads(line) for line in workflow.events_path.read_text().splitlines()]
            self.assertEqual([event['seq'] for event in events], [1, 2])
            self.assertEqual(stale['last_seq'], 2)

    def test_process_control_commands_are_declined(self):
        self.assertTrue(is_process_control_command('pkill -f "python -u /tmp/tmnum/ball"'))
        self.assertTrue(is_process_control_command('kill -TERM 123'))
        self.assertTrue(is_process_control_command('/bin/killall python'))
        self.assertFalse(is_process_control_command('python /tmp/tmnum/ball.py'))
        declined = approval_decision('item/commandExecution/requestApproval', {'command': 'pkill -f ball'})
        accepted = approval_decision('item/commandExecution/requestApproval', {'command': 'python /tmp/tmnum/ball.py'})
        self.assertEqual(declined['decision'], 'decline')
        self.assertEqual(accepted['decision'], 'accept')

    def test_history_rewriting_commands_are_declined(self):
        for command in ('git reset --hard HEAD~1', 'git push origin main', 'git -C /repo rebase main',
                        'git clean -fd', 'git commit -m x', 'git checkout --force other'):
            self.assertTrue(is_history_rewriting_command(command), command)
        for command in ('git status', 'git log --oneline', 'git diff HEAD', 'python train.py'):
            self.assertFalse(is_history_rewriting_command(command), command)
        decide = make_approval_decision()
        self.assertEqual(decide('item/commandExecution/requestApproval', {'command': 'git reset --hard'})['decision'], 'decline')
        self.assertEqual(decide('item/commandExecution/requestApproval', {'command': 'git status'})['decision'], 'accept')

    def test_writes_outside_cycle_workspace_are_declined(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / 'workspace'
            (workspace / 'result').mkdir(parents=True)
            decide = make_approval_decision((workspace,))
            inside = decide('item/fileChange/requestApproval', {'changes': [{'path': str(workspace / 'result' / 'a.tsv')}]})
            outside = decide('item/fileChange/requestApproval', {'changes': [{'path': '/tmp/example-project/gs_study/x/result/a.tsv'}]})
            self.assertEqual(inside['decision'], 'accept')
            self.assertEqual(outside['decision'], 'decline')
            # An unparsable payload must degrade to accept rather than stall the role.
            self.assertEqual(decide('item/fileChange/requestApproval', {'summary': 'edited a file'})['decision'], 'accept')
            keep = decide('item/commandExecution/requestApproval', {'command': f'rm -rf {workspace}/result/tmp'})
            drop = decide('item/commandExecution/requestApproval', {'command': 'rm -rf /tmp/example-project/gs_study'})
            escape = decide('item/commandExecution/requestApproval', {'command': 'rm -rf ../../gs_study'})
            self.assertEqual(keep['decision'], 'accept')
            self.assertEqual(drop['decision'], 'decline')
            self.assertEqual(escape['decision'], 'decline')
            self.assertEqual(decide('item/commandExecution/requestApproval', {'command': 'sbatch SLURM_script'})['decision'], 'accept')

    def test_locked_criteria_and_budget_reach_downstream_roles(self):
        class CapturingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.prompts: dict[str, list[str]] = {}
            def invoke(self, role, prompt, thread_id, model, provider):
                self.prompts.setdefault(role, []).append(prompt)
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = CapturingBackend()
            state = Workflow(spec, backend).run()
            criteria = state['hypothesis_lock']['cycle_criteria']
            self.assertTrue(any('register deterministic smoke evidence' in row['criterion'] for row in criteria))
            investigator = json.loads(backend.prompts['investigator'][0].split('Context:\n', 1)[1])
            self.assertEqual(
                investigator['frozen_objective']['decisive_test'],
                'register deterministic smoke evidence',
            )
            self.assertNotIn('hypothesis_lock', investigator)
            self.assertNotIn('cycle_criteria', investigator['frozen_objective'])
            for runtime_key in (
                'investigation_attempt', 'round', 'internal_turn', 'max_rounds', 'rework_left',
                'router_feedback', 'routing_source', 'judge_feedback', 'recent_gap_history',
            ):
                self.assertNotIn(runtime_key, investigator)
            judge = json.loads(backend.prompts['judge'][0].split('Context:\n', 1)[1])
            self.assertEqual(judge['hypothesis_lock']['decisive_test'], 'register deterministic smoke evidence')
            self.assertNotIn('cycle_criteria', judge['hypothesis_lock'])
            self.assertNotIn('success_criteria', judge['hypothesis_lock'])
            for runtime_key in ('rework_left', 'max_rounds', 'budgets', 'readiness', 'investigation_attempt', 'rounds'):
                self.assertNotIn(runtime_key, judge)
            self.assertIn('findings', backend.prompts['investigator'][0])
            self.assertNotIn('remaining_scientific_question', backend.prompts['investigator'][0])
            self.assertNotIn('criterion_assessments', backend.prompts['investigator'][0])
            self.assertNotIn('objection_responses', backend.prompts['investigator'][0])
            self.assertNotIn('remaining_gap', backend.prompts['investigator'][0])
            self.assertNotIn('critical_issue_updates', backend.prompts['investigator'][0])
            self.assertNotIn('requested_next_capability', backend.prompts['investigator'][0])
            self.assertNotIn('ready_for_judge', backend.prompts['investigator'][0])

    def test_locked_candidate_is_the_investigated_branch(self):
        class SplitBackend(FakeBackend):
            def __init__(self): super().__init__(reworks=0); self.prompts = {}
            def invoke(self, role, prompt, thread_id, model, provider):
                self.prompts.setdefault(role, []).append(prompt)
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = SplitBackend()
            state = Workflow(spec, backend).run()
            branch = state['branches'][-1]
            self.assertEqual(branch['title'], state['hypothesis_lock']['title'])
            self.assertEqual(branch['hypothesis'], state['hypothesis_lock']['hypothesis'])
            investigator = json.loads(backend.prompts['investigator'][0].split('Context:\n', 1)[1])
            self.assertEqual(
                investigator['frozen_objective']['hypothesis'],
                state['hypothesis_lock']['hypothesis'],
            )
            self.assertNotIn('hypothesis_lock', investigator)
            self.assertNotIn('hypothesis_id', investigator['frozen_objective'])
            self.assertNotIn('cycle_criteria', investigator['frozen_objective'])
            self.assertNotIn('branch', investigator)

    def test_candidates_hand_off_to_the_next_cycle(self):
        class CapturingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.prompts: dict[str, list[str]] = {}
            def invoke(self, role, prompt, thread_id, model, provider):
                self.prompts.setdefault(role, []).append(prompt)
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), max_cycles=2)
            first = Workflow(spec, FakeBackend(reworks=0)).run()
            candidate_ids = [item['id'] for item in first['candidates']]
            self.assertGreaterEqual(len(candidate_ids), 1)
            self.assertTrue(all(item.startswith('cand-') for item in candidate_ids))
            self.assertEqual(len(candidate_ids), len(set(candidate_ids)))
            self.assertIsNone(first.get('next_start_node_id'))
            backend = CapturingBackend()
            Workflow(spec, backend).run()
            lead = json.loads(backend.prompts['research_lead'][0].split('Context:\n', 1)[1])
            self.assertNotIn('prior_cycle_summary', lead)
            self.assertIn('agent_graph', lead)
            self.assertNotIn('canvas_access', lead)

    def test_cycle_dir_uses_incrementing_master_run_id(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp))
            Workflow(spec, backend).run()
            cycle = self.cycle(spec)
            self.assertEqual(cycle.name, '1')
            self.assertIsNone(master_run_id(cycle.name))
            self.assertTrue((cycle / CYCLE_MARKER).is_file())
            # A later run must not reuse the directory and clobber the previous state.
            second = make_run_id(spec.goal_id, spec.research_root, task_mode=True)
            self.assertEqual(second, '2')

    def test_task_roots_sharing_the_name_pattern_are_not_cycles(self):
        """gs_study task roots match <mm-dd-HH:MM>-<abbr> too; only the marker makes a cycle."""
        with tempfile.TemporaryDirectory() as temp:
            research = Path(temp) / 'research'
            decoy = research / '09-17-22:53-代际能量差推导'
            decoy.mkdir(parents=True)
            (decoy / 'research_plan.md').write_text('# plan\n', encoding='utf-8')
            self.assertFalse(is_cycle_dir(decoy))
            self.assertIsNone(latest_cycle_dir(research))
            (decoy / CYCLE_MARKER).write_text('marked\n', encoding='utf-8')
            self.assertTrue(is_cycle_dir(decoy))

    def test_path_anchors_make_required_context_resolvable(self):
        class CapturingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.prompts: dict[str, list[str]] = {}
            def invoke(self, role, prompt, thread_id, model, provider):
                self.prompts.setdefault(role, []).append(prompt)
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.git').mkdir()
            vault = root / '笔记区' / '探索台账' / 'q'
            vault.mkdir(parents=True)
            spec, _ = self.make(root)
            spec = spec.__class__(**{**spec.__dict__, 'branch_canvas': vault / 'exploration.canvas', 'notes_mirror_dir': vault / 'notes'})
            with ScienceGraphStore(vault / "science_graph.sqlite3"):
                pass
            spec.branch_canvas.write_text('{"nodes": [], "edges": []}', encoding='utf-8')
            spec.notes_mirror_dir.mkdir()
            backend = CapturingBackend()
            Workflow(spec, backend).run()
            lead = json.loads(backend.prompts['research_lead'][0].split('Context:\n', 1)[1])
            self.assertNotIn('workspace_dir', lead)
            self.assertNotIn('notes_vault_root', lead)
            access = lead['workspace_access']
            self.assertEqual(access['repo_root'], str(root))
            self.assertIn('cycle_dir', access)
            self.assertIn('agent_graph', lead)
            self.assertNotIn('canvas_access', lead)

    def test_first_cycle_has_empty_prior_handoff(self):
        class CapturingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.prompts: dict[str, list[str]] = {}
            def invoke(self, role, prompt, thread_id, model, provider):
                self.prompts.setdefault(role, []).append(prompt)
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = CapturingBackend()
            Workflow(spec, backend).run()
            lead = json.loads(backend.prompts['research_lead'][0].split('Context:\n', 1)[1])
            self.assertNotIn('prior_cycle_summary', lead)
            self.assertIn('agent_graph', lead)
            self.assertNotIn('canvas_access', lead)

    def test_role_started_persists_thread_before_invoke(self):
        class Boom(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == 'investigator':
                    raise RuntimeError('interrupted')
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                Workflow(spec, Boom(reworks=0)).run()
            state = json.loads((self.cycle(spec) / 'research_state.json').read_text())
            self.assertIn('attempt-0001:investigator', state['threads'])
            events = [json.loads(line) for line in (self.cycle(spec) / 'events.jsonl').read_text().splitlines()]
            started = [event for event in events if event['type'] == 'role.started' and event['payload']['role'] == 'investigator']
            self.assertEqual(started[-1]['payload']['thread_id'], state['threads']['attempt-0001:investigator'])
            self.assertTrue(started[-1]['payload']['thread_id'])

    def test_epistemic_episode_thread_policy_for_lead_judge_and_reporter(self):
        class CapturingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=1)
                self.live_threads: dict[str, list[str]] = {}
            def invoke(self, role, prompt, thread_id, model, provider):
                thread, raw = super().invoke(role, prompt, thread_id, model, provider)
                self.live_threads.setdefault(role, []).append(thread)
                return thread, raw
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=1)
            backend = CapturingBackend()
            Workflow(spec, backend).run()
            self.assertEqual(len(set(backend.live_threads['research_lead'])), 2)
            # Judge adjudication and task-level Goal Completion are separate SDK stages.
            self.assertEqual(len(set(backend.live_threads['judge'])), 2)
            self.assertEqual(len(set(backend.live_threads['reporter'])), 1)
            self.assertEqual(len(set(backend.live_threads['investigator'])), 1)
            self.assertEqual(len(set(backend.live_threads['investigator'])), 1)




    def test_qualification_resume_never_autolocks_unverified_legacy_replacement(self):
        class CapturingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.reviewed_candidate_ids = []
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "challenger" and "Context:\n" in prompt:
                    context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
                    self.reviewed_candidate_ids.append([str(item.get("id")) for item in context.get("candidates") or []])
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            backend = CapturingBackend()
            workflow = Workflow(spec, backend)
            state = workflow.initialize()
            # Compatibility fixture: resume one persisted pre-vNext qualification working set.
            with ScienceGraphStore(spec.branch_canvas.parent / "science_graph.sqlite3") as store:
                store.add_hypothesis("H-0001", title="Registered source", summary="Existing source H")
            original = {
                "id": "cand-original", "title": "original", "hypothesis": "original H",
                "why_now": "original why", "goal_advance": "advance goal", "decisive_test": "original test",
                "evidence_requirements": [
                    {"id": "ER-1", "kind": "local_source_review", "requirement": "inspect original source"},
                    {"id": "ER-2", "kind": "numerical_evidence", "requirement": "test the original scientific claim"},
                ],
                "scope": "", "parent_hypothesis_ids": ["H-0001"], "evolution_operator": "derive",
                "lineage_anchor_node_id": "H-0001", "lineage_anchor_node_ids": ["H-0001"],
                "rationale": "original why", "evidence_refs": [], "status": "provisional", "source_gap_id": "",
            }
            replacement = {
                "id": "cand-replacement", "title": "replacement", "hypothesis": "replacement H",
                "why_now": "replacement is narrower", "goal_advance": "advance goal", "decisive_test": "replacement test",
                "evidence_requirements": [
                    {"id": "ER-1", "kind": "local_source_review", "requirement": "inspect replacement source"},
                    {"id": "ER-2", "kind": "numerical_evidence", "requirement": "test the replacement scientific claim"},
                ],
                "scope": "", "parent_hypothesis_ids": ["H-0001"], "evolution_operator": "specialize",
                "lineage_anchor_node_id": "H-0001", "lineage_anchor_node_ids": ["H-0001"],
                "rationale": "replacement is narrower", "evidence_refs": [], "status": "provisional", "source_gap_id": "",
            }
            state["role_outputs"]["research_lead_frontier"] = {
                "candidates": [original], "recommended_candidate_id": "cand-original",
            }
            state["qualification_repair_count"] = 1
            state["qualification_working_set"] = {
                "schema": "theory-master/qualification-working-set/v1",
                "candidates": [original, replacement],
                "reviews": [{
                    "candidate_id": "cand-original", "verdict": "revise", "reason": "too broad",
                    "blocking_issues": [{"problem": "too broad", "required_change": "narrow it"}],
                }],
                "pending_ids": ["cand-replacement"],
                "recommended_id": "cand-original",
                "repair_count": 1,
            }
            from theory_master.workflow_support import RecoveryStop
            with self.assertRaisesRegex(RecoveryStop, "original Challenger blocker ledger"):
                workflow._qualify_and_lock(state, "2", {})
            self.assertEqual(backend.reviewed_candidate_ids, [])
            self.assertNotEqual(state.get("hypothesis_lock", {}).get("status"), "locked")

    def test_empty_judge_packet_stops_before_judge_call(self):
        from theory_master.workflow import RecoveryStop

        class CountingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = []

            def invoke(self, role, prompt, thread_id, model, provider):
                self.calls.append(role)
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            backend = CountingBackend()
            workflow = Workflow(spec, backend)
            state = workflow.initialize()
            branch = {
                "id": "cycle-empty-judge-packet", "title": "Existing evidence review",
                "hypothesis": "H", "status": "testing",
                "cycle_progress_criteria": {
                    "success": [{"id": "M-S1", "criterion": "existing evidence supports H"}],
                    "failure": [{"id": "M-F1", "criterion": "existing evidence contradicts H"}],
                },
            }
            state["branches"] = [branch]
            state["hypothesis_lock"] = {
                "status": "locked", "title": branch["title"], "hypothesis": "H",
                "cycle_progress_criteria": branch["cycle_progress_criteria"],
            }
            state["next_executor"] = "judge"
            with self.assertRaisesRegex(RecoveryStop, "judge investigation result not ready"):
                workflow._investigate_and_judge(state, branch, {"next_executor": "judge"})
            self.assertEqual(state["attention"]["code"], "judge_investigation_result_not_ready")
            self.assertNotIn("judge", backend.calls)



    def test_qualification_always_hands_new_hypothesis_to_unified_investigator(self):
        class CaptureBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = []
            def invoke(self, role, prompt, thread_id, model, provider):
                context = json.JSONDecoder().raw_decode(prompt.split('Context:\n', 1)[1])[0] if 'Context:\n' in prompt else {}
                self.calls.append((role, context))
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            backend = CaptureBackend()
            state = Workflow(spec, backend).run()
            self.assertEqual(state['initial_executor'], 'investigator')
            specialist = [role for role, _ in backend.calls if role in {'investigator', 'literature'}]
            self.assertTrue(specialist)
            self.assertEqual(specialist[0], 'investigator')

    def test_duplicate_external_literature_gap_without_new_lead_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), reworks=0)
            workflow = Workflow(spec, backend)
            state = {}
            task = {
                'gap_id': 'EXT-source-1', 'task_type': 'targeted_source',
                'search_target': 'original source X', 'why_external_needed': 'not present locally',
                'expected_external_evidence': 'DOI and primary-source quote', 'new_search_lead': '',
                'triggered_by': 'investigator',
            }
            queued, _ = workflow._queue_literature_task(state, task)
            self.assertTrue(queued)
            workflow._complete_literature_task(state, {'evidence_records': []})
            queued, reason = workflow._queue_literature_task(state, task)
            self.assertFalse(queued)
            self.assertEqual(reason, 'duplicate_external_gap_without_new_search_lead')
            task['new_search_lead'] = 'new DOI 10.1234/example'
            queued, _ = workflow._queue_literature_task(state, task)
            self.assertTrue(queued)

    def test_judge_literature_rework_reuses_scientific_gap_id_not_objection_id(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), reworks=0)
            workflow = Workflow(spec, backend)
            state = {}
            objection = {'objection_id': 'J-0001', 'criterion_id': 'M-S1',
                         'reason': 'primary source still missing',
                         'source_gap_id': 'source-gap-primary-definition'}
            self.assertEqual(workflow._judge_source_gap_id(state, objection), 'source-gap-primary-definition')

            first = {
                'gap_id': 'source-gap-primary-definition', 'source_gap_id': 'source-gap-primary-definition', 'task_type': 'targeted_source',
                'search_target': 'original source', 'why_external_needed': 'not local',
                'expected_external_evidence': 'primary-source quote', 'new_search_lead': '',
                'triggered_by': 'research_lead',
            }
            queued, _ = workflow._queue_literature_task(state, first)
            self.assertTrue(queued)
            workflow._complete_literature_task(state, {'evidence_records': []})
            judge_task = {**first, 'objection_id': 'J-0001', 'triggered_by': 'judge'}
            queued, reason = workflow._queue_literature_task(state, judge_task)
            self.assertFalse(queued)
            self.assertEqual(reason, 'duplicate_external_gap_without_new_search_lead')

    def test_recovery_resume_reuses_cycle_and_skips_completed_roles(self):
        class Boom(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0); self.calls = []
            def invoke(self, role, prompt, thread_id, model, provider):
                self.calls.append(role)
                if role == 'investigator':
                    raise RuntimeError('temporary provider failure')
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            with self.assertRaisesRegex(RuntimeError, 'temporary provider failure'):
                Workflow(spec, Boom()).run()
            cycle = self.cycle(spec)
            failed = json.loads((cycle / 'research_state.json').read_text())
            self.assertTrue(failed['resumable'])
            self.assertTrue(failed['last_safe_checkpoint'])
            class ResumeBackend(FakeBackend):
                def __init__(self):
                    super().__init__(reworks=0); self.calls = []
                def invoke(self, role, prompt, thread_id, model, provider):
                    self.calls.append((role, prompt, thread_id))
                    return super().invoke(role, prompt, thread_id, model, provider)
            resume_backend = ResumeBackend()
            resumed = Workflow(spec, resume_backend).resume(0)
            self.assertEqual(resumed['status'], 'over')
            self.assertNotIn('challenger', [role for role, _, _ in resume_backend.calls])
            resumed_lead_prompts = [prompt for role, prompt, _ in resume_backend.calls if role == 'research_lead']
            resumed_lead_threads = [thread_id for role, _, thread_id in resume_backend.calls if role == 'research_lead']
            self.assertTrue(resumed_lead_prompts)
            self.assertTrue(any('cycle_synthesis' in prompt for prompt in resumed_lead_prompts))
            self.assertFalse(any('frontier_planning' in prompt for prompt in resumed_lead_prompts))
            self.assertTrue(resumed_lead_threads)
            self.assertTrue(all(thread_id != failed['threads']['research_lead'] for thread_id in resumed_lead_threads))
            self.assertNotEqual(resumed['threads']['research_lead'], failed['threads']['research_lead'])
            self.assertEqual(len(list_cycle_dirs(spec.research_root)), 1)
            self.assertEqual(resumed['master_id'], spec.master_id)
            self.assertEqual(resumed['cycle_id'], cycle.name)
            projected = json.loads((cycle / 'run_state.json').read_text())
            self.assertEqual(projected['status'], 'OVER')
            self.assertFalse(projected['execution_active'])

    def test_resume_allows_precheckpoint_research_lead_interruption(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0, max_cycles=1)
            workflow = Workflow(spec, FakeBackend(reworks=0))
            workflow.initialize()
            cycle = self.cycle(spec)
            state_path = cycle / 'research_state.json'
            state = json.loads(state_path.read_text(encoding='utf-8'))
            state.update({
                'status': 'active',
                'phase': 'research_lead:running',
                'execution_active': True,
                'owner_pid': 99999999,
                'resumable': False,
                'last_safe_checkpoint': None,
                'role_outputs': {},
                'hypothesis_lock': {'status': 'unlocked'},
                'hypothesis_locked': False,
                'qualification_status': 'pending',
                'threads': {'research_lead': 'thr-precheckpoint'},
            })
            state_path.write_text(json.dumps(state), encoding='utf-8')
            resumed = Workflow(spec, FakeBackend(reworks=0)).resume(0)
            self.assertEqual(Path(resumed['cycle_dir']).resolve(), cycle.resolve())
            self.assertEqual(resumed['status'], 'over')
            self.assertTrue(resumed['recovery'].get('precheckpoint_restart'))

    def test_invalid_current_investigator_output_repairs_before_evidence_registration(self):
        class RepairingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.numerical_threads = []
                self.numerical_calls = 0
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == 'investigator':
                    self.numerical_calls += 1
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    self.numerical_threads.append(thread)
                    if self.numerical_calls == 1:
                        return thread, json.dumps({
                            'findings': [{
                                'statement': 'invalid current output', 'scope': 'test', 'limitations': '',
                                'artifacts': [], 'evidence_kind': 'literature',
                            }],
                            'remaining_scientific_question': '',
                        })
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            backend = RepairingBackend()
            state = Workflow(spec, backend).run()
            self.assertEqual(state['contract_repair_count'], 1)
            self.assertGreaterEqual(backend.numerical_calls, 2)
            self.assertEqual(len(set(backend.numerical_threads[:2])), 1)
            manifest = (self.cycle(spec) / 'workspace' / 'evidence_manifest.json').read_text(encoding='utf-8')
            self.assertNotIn('invalid current output', manifest)
            self.assertNotIn('partially_resolved', manifest)

    def test_recovery_resume_continues_remaining_cycle_budget(self):
        class Boom(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == 'investigator':
                    raise RuntimeError('temporary provider failure')
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0, max_cycles=2)
            with self.assertRaisesRegex(RuntimeError, 'temporary provider failure'):
                Workflow(spec, Boom()).run()
            resumed = Workflow(spec, FakeBackend(reworks=0)).resume(0)
            cycles = list_cycle_dirs(spec.research_root)
            self.assertEqual(len(cycles), 2)
            self.assertEqual(resumed['status'], 'over')
            self.assertEqual(resumed['branches'][-1]['status'], 'supported')

    def test_resume_links_abandoned_running_invocation(self):
        class Boom(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == 'investigator':
                    raise RuntimeError('temporary provider failure')
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            with self.assertRaisesRegex(RuntimeError, 'temporary provider failure'):
                Workflow(spec, Boom()).run()
            cycle = self.cycle(spec)
            from theory_master.invocations import InvocationLedger
            ledger = InvocationLedger(cycle / 'invocations.jsonl')
            from theory_master.contracts import role_contract
            ledger.prepare(invocation_id='abandoned', kind='agent', input_payload={'round': 1},
                           contract_ref=role_contract('investigator', phase='investigate').descriptor())
            ledger.transition('abandoned', 'running', role='investigator', thread_id='old-thread')
            state_path = cycle / 'research_state.json'
            state = json.loads(state_path.read_text(encoding='utf-8'))
            state['active_invocation_id'] = 'abandoned'
            state_path.write_text(json.dumps(state), encoding='utf-8')
            Workflow(spec, FakeBackend(reworks=0)).resume(0)
            old = ledger.latest('abandoned')
            self.assertEqual(old['state'], 'interrupted')
            successor = next(record for record in ledger.records()
                             if record.get('invocation_id') == old['successor_invocation_id']
                             and record.get('state') == 'running')
            self.assertEqual(successor['predecessor_invocation_id'], 'abandoned')
            self.assertIn(successor['execution_mode'], {'thread_continuation', 'new_thread'})

    def test_recovery_control_pause_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), reworks=0)
            workflow = Workflow(spec, backend)
            workflow.initialize()
            state = workflow.request_control('pause', reason='operator pause')
            self.assertEqual(state['status'], 'paused')
            persisted = json.loads((self.cycle(spec) / 'research_state.json').read_text())
            self.assertEqual(persisted['recovery']['reason'], 'operator pause')
            run_state = json.loads((self.cycle(spec) / 'run_state.json').read_text())
            self.assertTrue(run_state['resumable'])

    def test_resume_rejects_live_owner_during_pending_pause(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), reworks=0)
            workflow = Workflow(spec, backend)
            workflow.initialize()
            workflow.request_control('pause', reason='operator pause')
            cycle = self.cycle(spec)
            path = cycle / 'research_state.json'
            state = json.loads(path.read_text())
            state.update(owner_pid=os.getpid(), execution_active=True)
            path.write_text(json.dumps(state), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'already has an active execution'):
                workflow.resume(spec.master_id)
            self.assertTrue((cycle / 'recovery_control.json').is_file())

    def test_recovery_control_rejects_stopped_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), reworks=0)
            workflow = Workflow(spec, backend)
            workflow.initialize()
            cycle = self.cycle(spec)
            state = json.loads((cycle / 'research_state.json').read_text())
            for status in ('completed', 'failed', 'interrupted', 'paused', 'needs_attention'):
                state['status'] = status
                state['execution_active'] = False
                (cycle / 'research_state.json').write_text(json.dumps(state), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'terminal/stopped Cycle cannot receive recovery control|only an inactive PAUSED Cycle can be killed'):
                    workflow.request_control('interrupt')

    def test_find_cycle_accepts_id_and_full_name(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), reworks=0)
            Workflow(spec, backend).run()
            self.assertEqual(find_cycle(spec.research_root, '1').name, '1')
            self.assertEqual(find_cycle(spec.research_root, 1).name, '1')

    def test_control_line_current_state_is_direction_not_runtime(self):
        self.assertTrue(is_runtime_current_state("challenger is running"))
        self.assertTrue(is_runtime_current_state("cycle 09-20-22:17-x started"))
        self.assertTrue(is_runtime_current_state("Codex process closed stdout. stderr_tail="))
        self.assertFalse(is_runtime_current_state("axisymmetric dipole closure"))
        text = direction_text("Smoke hypothesis", "smoke-h")
        self.assertEqual(text, "Smoke hypothesis\n\nsmoke-h")
        self.assertNotIn("…", direction_text("H", "x" * 800))
        prior = {"current_state": "challenger is running", "branches": [{"title": "Keep this line", "hypothesis": "sticky H"}]}
        self.assertIn("Keep this line", sticky_direction(prior))
        class Boom(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "investigator":
                    raise RuntimeError("interrupted")
                return super().invoke(role, prompt, thread_id, model, provider)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            leftover = json.loads(spec.branch_canvas.read_text())
            leftover["nodes"].extend([
                {"id": "tm-v2-next-action", "type": "text", "x": 0, "y": 0, "width": 1, "height": 1, "text": "# Next Action\n\nwait for challenger"},
                {"id": "tm-v2-current-gate", "type": "text", "x": 0, "y": 1, "width": 1, "height": 1, "text": "# Current Gate\n\nold gate"},
            ])
            spec.branch_canvas.write_text(json.dumps(leftover), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                Workflow(spec, Boom(reworks=0)).run()
            canvas = json.loads(spec.branch_canvas.read_text())
            self.assertFalse(any(node.get("id") == "tm-scientific-model" for node in canvas["nodes"]))
            plan = (Path(temp) / "research_plan.md").read_text(encoding="utf-8")
            self.assertIn("# Scientific Model\n\nA test-only scientific model.", plan)
            ids = {node.get("id") for node in canvas["nodes"]}
            self.assertNotIn("tm-v2-next-action", ids)
            self.assertNotIn("tm-v2-current-gate", ids)

    def test_final_canvas_projection_failure_persists_resumable_authority_state(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            workflow = Workflow(spec, FakeBackend(reworks=0))
            original_projection = workflow._render_canvas
            projection_calls = {"count": 0}
            def fail_only_final_projection(state):
                projection_calls["count"] += 1
                if projection_calls["count"] == 2:
                    raise ValueError("projection boom")
                return original_projection(state)
            with mock.patch.object(workflow, "_render_canvas", side_effect=fail_only_final_projection):
                with self.assertRaisesRegex(ValueError, "projection boom"):
                    workflow.run()
            cycle = self.cycle(spec)
            state = json.loads((cycle / "research_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["phase"], "failed")
            self.assertTrue(state["resumable"])
            self.assertFalse(state["execution_active"])
            self.assertEqual(state["recovery"]["message"], "projection boom")
            self.assertEqual(state["protocol_health"], "valid")
            self.assertEqual(state["recovery"]["category"], "runtime_validation")
            events = [json.loads(line) for line in (cycle / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["type"], "run.failed")
            self.assertEqual(events[-1]["payload"].get("stage"), "final_canvas_projection")

    def test_from_canvas_rejects_legacy_frontmatter_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            control = root / "control.canvas"
            plan = root / "research_plan.md"
            plan.write_text("---\nschema: theory-master/v2\ngoal_id: legacy\n---\n# Goal\nold\n", encoding="utf-8")
            control.write_text(json.dumps({"nodes":[{"id":"entry","type":"text","text":f"<!-- theory-master-task-document: {plan} -->\ntype: TheoryMasterEntry\n"}],"edges":[]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "legacy frontmatter research_plan format is unsupported"):
                TaskSpec.from_canvas(control)

    def test_missing_sqlite_for_external_branch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            branch = Path(temp) / "exploration.canvas"
            branch.write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
            workflow = Workflow.__new__(Workflow)
            workflow.branch_canvas = branch
            workflow.spec = SimpleNamespace(branch_canvas=branch)
            with self.assertRaisesRegex(FileNotFoundError, "Canonical SQLite graph is required"):
                workflow._graph_database_path()

    def test_reporter_cannot_mutate_scientific_lineage_through_canvas_patch(self):
        from theory_master.science_canvas_export import canvas_projection
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "science_graph.sqlite3"
            with ScienceGraphStore(db) as store:
                store.add_hypothesis("H-0001", title="Parent")
                store.add_hypothesis("H-0002", title="Child")
                store.add_evolution(edge_id="e1", source="H-0001", target="H-0002", operator="derive")
                projected = canvas_projection(store)
                self.assertEqual(len(projected["edges"]), 1)
            self.assertFalse(hasattr(Workflow, "_apply_canvas_patch"))
            with ScienceGraphStore(db, readonly=True) as store:
                self.assertEqual(store.agent_hypothesis_detail("H-0002")["lineage"][0][0], "H-0001")

    def test_external_branch_projection_uses_sqlite_hypothesis_identity(self):
        from theory_master.science_canvas_export import canvas_projection
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "science_graph.sqlite3"
            with ScienceGraphStore(db) as store:
                store.add_hypothesis("H-0001", title="Scientific origin", summary="Original H")
                store.append_outcome(outcome_id="outcome-1", hypothesis_id="H-0001", cycle_id="1", verdict="open")
                store.db.execute(
                    "INSERT INTO external_records(id,content_type,source,title,content,created_at) VALUES (?,?,?,?,?,?)",
                    ("E-0001", "paper", "source", "External source", "supporting context", "test"),
                )
                store.db.execute(
                    "INSERT INTO external_edges(id,hypothesis_id,external_id,edge_type) VALUES (?,?,?,?)",
                    ("ref-1", "H-0001", "E-0001", "external_reference"),
                )
                projected = canvas_projection(store)
                self.assertEqual({node["id"] for node in projected["nodes"]}, {"H-0001", "E-0001"})
                self.assertFalse(any(node.get("type") == "Decision" for node in projected["nodes"]))
                detail = store.agent_external_detail("E-0001")
                self.assertEqual(detail["id"], "E-0001")
                self.assertEqual(detail["hypothesis_references"][0][0], "H-0001")

    def test_projection_does_not_synthesize_decision_from_report_path(self):
        from theory_master.science_canvas_export import canvas_projection
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "science_graph.sqlite3"
            with ScienceGraphStore(db) as store:
                store.add_hypothesis("H-0001", title="Scientific origin", summary="Frozen scientific claim")
                store.append_outcome(outcome_id="outcome-1", hypothesis_id="H-0001", cycle_id="1", verdict="open", summary="Still unresolved", report_ref="notes/report.md")
                projected = canvas_projection(store)
            self.assertFalse(any(re.fullmatch(r"\d+-decision", str(node.get("id", ""))) for node in projected["nodes"]))
            self.assertEqual({node["id"] for node in projected["nodes"]}, {"H-0001"})

    def test_projection_summary_comes_from_sqlite_hypothesis_and_outcome(self):
        from theory_master.science_canvas_export import canvas_projection
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "science_graph.sqlite3"
            with ScienceGraphStore(db) as store:
                store.add_hypothesis("H-0001", title="Readable claim", summary="The claim remains bounded")
                store.append_outcome(outcome_id="outcome-1", hypothesis_id="H-0001", cycle_id="1", verdict="supported", summary="A controlled test supports the local prediction")
                projected = canvas_projection(store)
            node = projected["nodes"][0]
            self.assertEqual(node["id"], "H-0001")
            self.assertIn("Readable claim", node["text"])
            self.assertIn("A controlled test supports the local prediction", node["text"])

    def test_canvas_projection_is_stable_for_unchanged_sqlite(self):
        from theory_master.science_canvas_export import canvas_projection
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "science_graph.sqlite3"
            with ScienceGraphStore(db) as store:
                store.add_hypothesis("H-0001", title="Claim", summary="A scientific claim")
                store.append_outcome(outcome_id="outcome-1", hypothesis_id="H-0001", cycle_id="1", verdict="open")
                first = canvas_projection(store)
                second = canvas_projection(store)
            self.assertEqual(first, second)

    def test_default_routes_are_gpt_lunar(self):
        self.assertEqual(DEFAULT_ROUTE.model, "gpt-6-luna")
        self.assertEqual(DEFAULT_ROUTE.provider, "CodexHTTPS")
        self.assertEqual(DEFAULT_ROUTE.reasoning_effort, "medium")
        self.assertEqual(KNOWN_ROUTES["gpt-6-luna"].provider, "Codex")
        for route in DEFAULT_ROUTES.values():
            self.assertEqual(route, DEFAULT_ROUTE)

    def test_resolve_routes_overlays_without_touching_code_default(self):
        isolated = resolve_routes(env={})
        self.assertEqual(isolated, MODEL_PROFILES["test"])
        self.assertEqual(set(MODEL_PROFILES), {"test", "codex", "advanced"})
        test_profile = resolve_routes(profile="test", env={})
        self.assertTrue(all(route.model == "gpt-6-luna" for route in test_profile.values()))
        self.assertTrue(all(route.reasoning_effort == "medium" for route in test_profile.values()))
        self.assertEqual(resolve_routes(profile="1", env={}), test_profile)
        codex = resolve_routes(profile="codex", env={})
        self.assertEqual(resolve_routes(profile="2", env={}), codex)
        self.assertEqual(codex["research_lead"].model, "gpt-6-sol")
        self.assertEqual(codex["research_lead"].reasoning_effort, "high")
        self.assertEqual(codex["challenger"].model, "gpt-6-sol")
        self.assertEqual(codex["challenger"].reasoning_effort, "medium")
        self.assertEqual(codex["investigator"].model, "gpt-6-sol")
        self.assertEqual(codex["investigator"].reasoning_effort, "low")
        routed = resolve_routes(profile="codex", env={"THEORYMASTER_MODEL_PROVIDER": "codex"})
        self.assertEqual(routed["investigator"].model, "gpt-6-sol")
        self.assertEqual(routed["investigator"].reasoning_effort, "low")
        self.assertEqual(routed["research_lead"].model, "gpt-6-sol")
        self.assertEqual(codex["judge"].model, "gpt-6-sol")
        self.assertEqual(codex["judge"].reasoning_effort, "medium")
        self.assertEqual(codex["literature"].model, "gpt-6-luna")
        self.assertEqual(codex["literature"].reasoning_effort, "low")
        self.assertEqual(codex["reporter"].model, "gpt-6-luna")
        self.assertEqual(codex["reporter"].reasoning_effort, "low")
        advanced = resolve_routes(profile="advanced", env={})
        self.assertEqual(resolve_routes(profile="3", env={}), advanced)
        self.assertEqual(advanced["research_lead"].reasoning_effort, "xhigh")
        self.assertEqual(advanced["investigator"].reasoning_effort, "xhigh")
        self.assertEqual(advanced["judge"].reasoning_effort, "high")
        self.assertEqual(advanced["challenger"].reasoning_effort, "high")
        self.assertEqual(advanced["literature"].model, "gpt-6-luna")
        self.assertEqual(advanced["literature"].reasoning_effort, "medium")
        self.assertEqual(advanced["reporter"].model, "gpt-6-luna")
        self.assertEqual(advanced["reporter"].reasoning_effort, "medium")
        luna = KNOWN_ROUTES["gpt-6-luna"]
        deepseek = KNOWN_ROUTES["deepseek-flash"]
        all_luna = resolve_routes(model="gpt-6-luna", env={"THEORYMASTER_MODEL": "deepseek-flash"})
        self.assertTrue(all(route == luna for route in all_luna.values()))
        mixed = resolve_routes(model="deepseek-flash", role_specs=["investigator=gpt-6-luna", "judge=gpt-6-luna"], env={})
        self.assertEqual(mixed["investigator"], luna)
        self.assertEqual(mixed["judge"], luna)
        self.assertEqual(mixed["literature"], deepseek)
        from_env = resolve_routes(env={"THEORYMASTER_MODEL": "gpt-6-luna", "THEORYMASTER_ROUTE": "investigator=deepseek-flash"})
        self.assertEqual(from_env["investigator"], deepseek)
        self.assertEqual(from_env["literature"], luna)
        sol = parse_model_spec("gpt-6-sol@Codex")
        with tempfile.TemporaryDirectory() as task_file:
            task_env = Path(task_file) / "env"
            task_env.write_text(
                "THEORYMASTER_MODEL=gpt-6-luna@Codex\n"
                "THEORYMASTER_ROUTE=investigator=gpt-6-sol@Codex\n",
                encoding="utf-8",
            )
            with mock.patch.object(CodexBackend, "_env_file_values", return_value={"THEORYMASTER_MODEL": "deepseek-flash"}):
                with mock.patch.dict("os.environ", {}, clear=True):
                    from_task = resolve_routes(task_env_file=task_env)
            self.assertEqual(from_task["investigator"], sol)
            self.assertEqual(from_task["literature"], luna)
        with self.assertRaisesRegex(ValueError, "unknown model"):
            parse_model_spec("gpt-5.6-terra")
        terra = parse_model_spec("gpt-5.6-terra@FixtureProvider")
        self.assertEqual(terra.model, "gpt-5.6-terra")
        self.assertEqual(terra.provider, "FixtureProvider")
        luna6 = parse_model_spec("gpt-6-luna")
        self.assertEqual(luna6.model, "gpt-6-luna")
        self.assertEqual(luna6.provider, "Codex")
        self.assertEqual(parse_model_spec("gpt-6-luna@Codex"), luna6)



    def test_codex_account_route_does_not_force_api_key_auth(self):
        routes = resolve_routes(model="gpt-6-luna", env={})
        overrides, env = CodexBackend._runtime_config(routes)
        joined = "\n".join(overrides)
        self.assertNotIn('preferred_auth_method="apikey"', joined)
        self.assertNotIn('forced_login_method="api"', joined)
        self.assertNotIn("model_catalog_json=", joined)
        self.assertEqual(env, {})

    @mock.patch.dict("os.environ", {"THEORYMASTER_COMPUTE_PYTHON": sys.executable})
    def test_codex_account_thread_uses_chatgpt_auth_and_medium_reasoning(self):
        class FakeThread:
            id = "thread-account"
        class FakeTransport:
            def request(self, method, payload, response_model=None):
                self.method = method
                self.payload = payload
                return SimpleNamespace(data=[SimpleNamespace(
                    name="theory_master_gateway",
                    runtime_status=SimpleNamespace(value="connected"),
                    tools=["read", "output_search", "output_slice"],
                )])
        class FakeClient:
            def __init__(self):
                self.kwargs = None
                self._client = FakeTransport()
            def thread_start(self, **kwargs):
                self.kwargs = kwargs
                return FakeThread()
        routes = resolve_routes(model="gpt-6-luna", env={})
        backend = CodexBackend(Path("."), routes=routes)
        fake = FakeClient()
        backend.client = fake
        identifier = backend.ensure_thread("research_lead", None, "gpt-6-luna", "Codex")
        self.assertEqual(identifier, "thread-account")
        self.assertIsNone(fake.kwargs["model_provider"])
        self.assertEqual(fake.kwargs["model"], "gpt-6-luna")
        self.assertEqual(fake.kwargs["config"]["model_reasoning_effort"], "medium")
        self.assertEqual(fake.kwargs["config"]["model_auto_compact_token_limit"], 220000)
        self.assertEqual(fake.kwargs["config"]["model_auto_compact_token_limit_scope"], "total")

    def test_codex_https_route_injects_tls12_openssl_config(self):
        routes = resolve_routes(model="gpt-6-luna@CodexHTTPS", env={})
        overrides, env = CodexBackend._runtime_config(routes)
        joined = "\n".join(overrides)
        self.assertIn('model_providers.openai_https.supports_websockets=false', joined)
        self.assertNotIn('forced_login_method="api"', joined)
        path = Path(env["OPENSSL_CONF"])
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("MinProtocol = TLSv1.2", text)
        self.assertIn("MaxProtocol = TLSv1.2", text)

    def test_codex_https_provider_is_persisted_in_theorymaster_runtime_for_thread_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            daily_config = home / ".codex" / "config.toml"
            daily_config.parent.mkdir(parents=True)
            daily_config.write_text('model = "gpt-6-luna"\n', encoding="utf-8")
            runtime_config = home / ".codex-theory-master" / "config.toml"
            with mock.patch("theory_master.codex_backend.Path.home", return_value=home), \
                 mock.patch("theory_master.codex_accounts.Path.home", return_value=home):
                first = CodexBackend._ensure_openai_https_provider_registered()
                second = CodexBackend._ensure_openai_https_provider_registered()
            self.assertEqual(first, runtime_config)
            self.assertEqual(second, runtime_config)
            text = runtime_config.read_text(encoding="utf-8")
            self.assertEqual(text.count("[model_providers.openai_https]"), 1)
            self.assertIn('base_url = "https://chatgpt.com/backend-api/codex"', text)
            self.assertIn("requires_openai_auth = true", text)
            self.assertIn("supports_websockets = false", text)
            self.assertEqual(daily_config.read_text(encoding="utf-8"), 'model = "gpt-6-luna"\n')
            self.assertNotIn("[model_providers.openai_https]", daily_config.read_text(encoding="utf-8"))

    def test_codex_network_guard_applies_selected_proxy(self):
        routes = resolve_routes(model="gpt-6-luna@CodexHTTPS", env={})
        backend = CodexBackend(Path("."), routes=routes)
        payload = {"ok": True, "proxy": "http://127.0.0.1:7890", "changed": True,
                   "selector": "GLOBAL", "selection": "HK"}
        completed = subprocess.CompletedProcess(["codex-net"], 0, stdout=json.dumps(payload), stderr="")
        with mock.patch("theory_master.codex_backend.shutil.which", return_value="/tmp/example-bin/codex-net"), \
             mock.patch("theory_master.codex_backend.subprocess.run", return_value=completed), \
             mock.patch.dict(os.environ, {}, clear=True):
            result = backend._run_network_guard("test", required=True)
            self.assertTrue(result["changed"])
            self.assertEqual(os.environ["HTTP_PROXY"], "http://127.0.0.1:7890")
            self.assertEqual(os.environ["HTTPS_PROXY"], "http://127.0.0.1:7890")
            self.assertEqual(os.environ["CODEX_PROXY"], "http://127.0.0.1:7890")


    def test_codex_sdk_environment_keeps_historical_incremental_behavior(self):
        routes = resolve_routes(model="gpt-6-luna@CodexHTTPS", env={})
        parent = {
            "THEORYMASTER_COMPUTE_PYTHON": sys.executable,
            "HTTP_PROXY": "http://127.0.0.1:7890",
            "HTTPS_PROXY": "http://127.0.0.1:7890",
            "CODEX_PROXY": "http://127.0.0.1:7890",
        }
        with mock.patch.dict(os.environ, parent, clear=True):
            environment = CodexBackend._sdk_environment(routes)
            self.assertEqual(os.environ["HTTP_PROXY"], "http://127.0.0.1:7890")
            self.assertEqual(os.environ["HTTPS_PROXY"], "http://127.0.0.1:7890")
            self.assertEqual(os.environ["CODEX_PROXY"], "http://127.0.0.1:7890")
        self.assertNotIn("HTTP_PROXY", environment)
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertNotIn("CODEX_PROXY", environment)

    def test_compact_ts_drops_year_and_offset(self):
        self.assertEqual(compact_ts("2026-09-20T22:07:35+08:00"), "09-20T22:07:35")
        self.assertEqual(compact_ts("09-20T22:07:35"), "09-20T22:07:35")
        self.assertEqual(compact_ts(""), "")

    def test_write_roles_do_not_pass_untrusted_approval_policy(self):
        src = Path(__file__).resolve().parents[1] / "theory_master" / "core.py"
        self.assertNotIn('{"approval_policy": "untrusted"}', src.read_text(encoding="utf-8"))

    def test_bound_prompt_context_preserves_user_authored_fields(self):
        long_goal = "G" * (CONTEXT_VALUE_MAX_CHARS + 1500)
        notes = "N" * (CONTEXT_VALUE_MAX_CHARS + 1500)
        clipped = bound_json_value(notes)
        self.assertEqual(clipped, notes)
        ctx = bound_prompt_context({
            "goal": long_goal,
            "research_contract": "C" * (CONTEXT_VALUE_MAX_CHARS + 10),
            "required_context": "R" * (CONTEXT_VALUE_MAX_CHARS + 10),
            "notes": notes,
        })
        self.assertEqual(ctx["goal"], long_goal)
        self.assertEqual(len(ctx["research_contract"]), CONTEXT_VALUE_MAX_CHARS + 10)
        self.assertEqual(len(ctx["required_context"]), CONTEXT_VALUE_MAX_CHARS + 10)
        self.assertEqual(ctx["notes"], notes)

    def test_evidence_projections_and_fresh_rework_thread(self):
        class CapturingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=1)
                self.prompts: dict[str, list[str]] = {}
                self.threads_by_role: dict[str, list[str]] = {}
            def invoke(self, role, prompt, thread_id, model, provider):
                self.prompts.setdefault(role, []).append(prompt)
                thread, raw = super().invoke(role, prompt, thread_id, model, provider)
                self.threads_by_role.setdefault(role, []).append(thread)
                return thread, raw
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = CapturingBackend()
            workflow = Workflow(spec, backend)
            workflow.run()
            cycle = self.cycle(spec)
            legacy_packet_path = cycle / "workspace" / "evidence_packet.json"
            self.assertFalse(legacy_packet_path.exists())
            manifest_path = cycle / "workspace" / "evidence_manifest.json"
            coverage_path = cycle / "workspace" / "coverage_matrix.json"
            coverage = json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.is_file() else {}
            judge_packet_path = cycle / "workspace" / "judge_packet.json"
            self.assertTrue(manifest_path.is_file())
            self.assertTrue(coverage_path.is_file())
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            self.assertEqual(set(coverage["criteria"]), {"C-1"})
            # Investigator findings do not self-assign adjudicative criterion IDs.
            # Criterion attribution is a separate Judge/SDK decision.
            self.assertEqual(coverage["criteria"]["C-1"], [])
            self.assertNotIn("C-S1", coverage["criteria"])
            self.assertNotIn("C-F1", coverage["criteria"])
            self.assertFalse(judge_packet_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(manifest["records"])
            self.assertTrue(all(item.get("evidence_id") for item in manifest["records"]))
            legacy_packet_path.write_text("{not valid json", encoding="utf-8")
            evidence_context = workflow._evidence_context()
            self.assertEqual(set(evidence_context), {"evidence_manifest", "coverage_matrix"})
            self.assertEqual(legacy_packet_path.read_text(encoding="utf-8"), "{not valid json")
            # Judge receives only the compact scientific Investigation Result.
            # Registry/coverage/artifact packets stay SDK-owned and are not replayed.
            for prompt in backend.prompts["judge"]:
                if "Current task: goal_completion" in prompt:
                    # Final Gate gets the canonical SQLite graph, not the current-Cycle Judge payload.
                    continue
                self.assertNotIn('"judge_packet"', prompt)
                self.assertNotIn("prior_role_outputs", prompt)
                ctx = json.loads(prompt.split("Context:\n", 1)[1])
                self.assertNotIn("evidence_packet", ctx)
                self.assertNotIn("evidence_manifest", ctx)
                self.assertNotIn("coverage_matrix", ctx)
                self.assertNotIn("judge_packet", ctx)
                self.assertNotIn("prior_role_outputs", ctx)
                self.assertIn("investigation_result", ctx)
                self.assertTrue(ctx["investigation_result"]["findings"])
                blob = json.dumps(ctx["investigation_result"])
                self.assertNotIn("artifact_pointers", blob)
                self.assertNotIn("judge_assessment", blob)
            self.assertEqual(len(backend.prompts["reporter"]), 1)
            report_ctx = json.loads(backend.prompts["reporter"][0].split("Context:\n", 1)[1])
            self.assertNotIn("evidence_packet", report_ctx)
            self.assertNotIn("evidence_refs", report_ctx)
            self.assertIn("evidence_scope_digest", report_ctx)
            self.assertNotIn("authorized_frontier", report_ctx)
            self.assertNotIn("authorized_next_frontier_candidate_id", report_ctx)
            self.assertNotIn("evolution_candidates", report_ctx)
            self.assertNotIn("next_frontier_recommendation",
                             report_ctx.get("scientific_synthesis") or {})
            for retired_field in ("canvas_access", "canvas_focus", "canvas_maintenance",
                                  "canvas_patch", "branch_canvas", "terminal_decision", "mode"):
                self.assertNotIn(retired_field, report_ctx)
            final_state = json.loads((cycle / "research_state.json").read_text(encoding="utf-8"))
            self.assertEqual(final_state["scientific_verdict"], "supported")
            from theory_master.science_graph_store import ScienceGraphStore
            with ScienceGraphStore(spec.branch_canvas.with_name("science_graph.sqlite3"), readonly=True) as store:
                h_id = str((final_state.get("hypothesis_lock") or {}).get("id") or "")
                outcomes = store.cycle_history(h_id)
                current_outcome = next(row for row in outcomes if row.get("cycle_id") == cycle.name)
                self.assertTrue(str(current_outcome.get("id") or "").startswith("cycle-outcome-"))
                outcome_refs = current_outcome.get("evidence_refs") or []
                manifest_ids = {str(item.get("evidence_id")) for item in manifest["records"]}
                self.assertTrue(outcome_refs)
                self.assertEqual(len(outcome_refs), len(set(outcome_refs)))
                self.assertTrue(set(outcome_refs).issubset(manifest_ids))
            reporter_ids = backend.threads_by_role["reporter"]
            self.assertEqual(len(reporter_ids), 1)
            numerical_ids = backend.threads_by_role["investigator"]
            self.assertGreaterEqual(len(numerical_ids), 2)
            self.assertEqual(numerical_ids[0], numerical_ids[1])
            attempt = json.loads((cycle / "investigation_attempts" / "attempt-0001" / "state.json").read_text(encoding="utf-8"))
            # The Investigator does not assign criterion IDs or decide coverage.
            # The SDK submits the complete scientific result to Judge, which alone adjudicates it.
            self.assertEqual(attempt["criterion_coverage"], {})
            readiness = attempt["readiness"]
            self.assertEqual(readiness["decision"], "send_to_judge")
            self.assertTrue(readiness["ready"])
            self.assertNotIn("criterion_coverage", readiness)

    def test_judge_rework_reopens_investigator_without_specialist_handoff(self):
        class CapturingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=1)
                self.executor_calls = []
                self.branch_judge_contexts = []

            def invoke(self, role, prompt, thread_id, model, provider):
                context = json.JSONDecoder().raw_decode(prompt.split('Context:\n', 1)[1])[0]
                if role in {'investigator', 'literature'}:
                    self.executor_calls.append((role, thread_id))
                if role == 'judge':
                    self.branch_judge_contexts.append(context)
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = CapturingBackend()
            state = Workflow(spec, backend).run()
            self.assertEqual(state['investigation_attempt'], 2)
            self.assertEqual(state['judge_rework_count'], 1)
            self.assertEqual(state['total_investigation_round_count'], len(backend.executor_calls))
            self.assertEqual({role for role, _ in backend.executor_calls}, {'investigator'})
            self.assertEqual(len(backend.executor_calls), 2)
            self.assertEqual(backend.executor_calls[0][1], backend.executor_calls[1][1])
            self.assertTrue(all('research_contract' not in context
                                for context in backend.branch_judge_contexts))
            self.assertTrue(all('adjudication_scope' not in context for context in backend.branch_judge_contexts))
            self.assertTrue(all('rule' not in context for context in backend.branch_judge_contexts))

    def test_codex_backend_drop_thread_discards_cache(self):
        backend = CodexBackend(Path("/tmp"))
        backend.threads["thr-old"] = object()
        backend.drop_thread("thr-old")
        self.assertNotIn("thr-old", backend.threads)
        backend.drop_thread(None)
        backend.drop_thread("missing")
