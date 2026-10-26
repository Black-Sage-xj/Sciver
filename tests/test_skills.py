from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from theory_master.core import FakeBackend, TaskSpec, Workflow, latest_cycle_dir
from theory_master.identity import BUDGET_ROLES, PATH_ANCHOR_ROLES, ROLES, identity_block, identity_files, identities_root
from theory_master.skills import (
    ROLE_SKILLS,
    developer_instructions,
    role_skills,
    resolve_skill,
    loaded_skill_for_path,
    skill_prompt_block,
    skills_root,
    thread_cycle_prefix,
    turn_input,
    write_rule,
)


class IdentityRegistryTest(unittest.TestCase):
    def test_every_role_has_identity_file(self):
        for role in ROLES:
            files = identity_files(role)
            names = [path.name for path in files]
            expected = [f"{role}.md"]
            expected.append("_investigator_handoff.md" if role == "investigator"
                            else "_invocation_handoff.md")
            if role in BUDGET_ROLES:
                expected.append("_round_budget.md")
            if role in PATH_ANCHOR_ROLES:
                expected.append("_paths.md")
            self.assertEqual(names, expected, role)
            for path in files:
                self.assertTrue(path.is_file(), path)
            self.assertIn("身份", identity_block(role))
        self.assertNotIn("最多使用 12 条终端命令", identity_block("investigator"))
        self.assertIn("权威工作流输入", identity_block("judge"))

    def test_workflow_budget_manual_is_not_auto_loaded_into_scientific_roles(self):
        self.assertEqual(BUDGET_ROLES, frozenset())
        budget = (identities_root() / "_round_budget.md").read_text(encoding="utf-8")
        for key in ("investigation_attempt", "investigation_round_count", "judge_rework_count", "THEORY_MASTER_INVESTIGATOR_BUDGET_TOKENS"):
            self.assertIn(key, budget)
        self.assertNotIn("_round_budget.md", [path.name for path in identity_files("investigator")])
        self.assertNotIn("_round_budget.md", [path.name for path in identity_files("literature")])
        self.assertNotIn("_round_budget.md", [path.name for path in identity_files("judge")])

    def test_validate_covers_every_identity_and_skill(self):
        """`validate` must fail before the run, not when the last role is finally reached."""
        from theory_master.__main__ import validate_roles
        report = validate_roles()
        self.assertEqual(set(report), set(ROLES))
        self.assertEqual(report["investigator"], ["theory-investigation", "numerical-investigation", "symbolic", "proof-writer", "analyze-results", "experiment-plan", "compute-dispatch", "plotting"])

    def test_legacy_canvas_reader_is_not_loaded_into_any_agent(self):
        for role in ROLES:
            self.assertNotIn("_canvas_read.md", [path.name for path in identity_files(role)])
        self.assertFalse((skills_root() / "canvas-read").exists())
        self.assertFalse((skills_root() / "research-context").exists())


class LiteratureStartupCheckTest(unittest.TestCase):
    def test_startup_check_uses_tiny_openalex_retrieval(self):
        from theory_master.__main__ import literature_startup_check
        with tempfile.TemporaryDirectory() as temp:
            paper = [{"title": "Polymer network topology", "source": "openalex"}]
            marker = Path(temp) / "marker_single"; marker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8"); marker.chmod(0o755)
            def fake_run(command, **kwargs):
                if "--output-dir" not in command:
                    self.assertEqual(command, [str(marker), "--help"])
                    return SimpleNamespace(returncode=0, stdout="marker help", stderr="")
                out = Path(command[command.index("--output-dir") + 1]); out.mkdir(parents=True, exist_ok=True)
                (out / "papers.json").write_text(json.dumps(paper), encoding="utf-8")
                self.assertEqual(command[command.index("--limit") + 1], "1")
                self.assertEqual(command[command.index("--sources") + 1], "openalex")
                self.assertNotIn("HTTP_PROXY", kwargs["env"]); self.assertNotIn("HTTPS_PROXY", kwargs["env"])
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            with unittest.mock.patch.dict("os.environ", {"SCHOLARLY_MARKER_BIN": str(marker)}, clear=False), unittest.mock.patch("theory_master.cli_runtime.tempfile.mkdtemp", return_value=temp), unittest.mock.patch("theory_master.cli_runtime.subprocess.run", side_effect=fake_run):
                result = literature_startup_check()
            self.assertEqual(result["status"], "ok"); self.assertEqual(result["retrieved"], 1)
            self.assertEqual(result["pdf_to_markdown"], "marker-ready")
            self.assertIn("tm-scholar", result["skills"])

    def test_startup_check_fails_when_retrieval_returns_no_papers(self):
        from theory_master.__main__ import literature_startup_check
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "marker_single"; marker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8"); marker.chmod(0o755)
            with unittest.mock.patch.dict("os.environ", {"SCHOLARLY_MARKER_BIN": str(marker)}, clear=False), unittest.mock.patch("theory_master.cli_runtime.tempfile.mkdtemp", return_value=temp), unittest.mock.patch("theory_master.cli_runtime.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")):
                with self.assertRaisesRegex(RuntimeError, "Literature startup check failed"):
                    literature_startup_check()


class SkillRegistryTest(unittest.TestCase):
    def test_every_listed_skill_exists(self):
        for role, names in ROLE_SKILLS.items():
            refs = role_skills(role)
            self.assertEqual([ref.directory for ref in refs], list(names), role)
            for ref in refs:
                self.assertTrue(ref.skill_md.is_file(), ref.skill_md)
                self.assertTrue(ref.description)

    def test_loaded_skill_path_matches_only_role_whitelist(self):
        symbolic = role_skills("investigator")[2]
        self.assertEqual(loaded_skill_for_path("investigator", symbolic.skill_md).directory, "symbolic")
        self.assertEqual(loaded_skill_for_path("investigator", "symbolic/SKILL.md").directory, "symbolic")
        self.assertIsNone(loaded_skill_for_path("judge", symbolic.skill_md))

    def test_task_local_skill_extends_only_the_selected_master(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "theory-master.config.json"
            names = ["network-graph", "polymer-numerical-policy"]
            config.write_text(json.dumps({"task_skills": {"investigator": names}}), encoding="utf-8")
            local_root = root / ".theory-master" / "skills"
            for name in names:
                local = local_root / name
                local.mkdir(parents=True)
                (local / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: task-local {name}\n---\n\n# {name}\n",
                    encoding="utf-8",
                )
            system_only = role_skills("investigator")
            combined = role_skills("investigator", task_config_path=config)
            self.assertTrue(all(name not in [ref.directory for ref in system_only] for name in names))
            self.assertEqual([ref.directory for ref in combined[-2:]], names)
            self.assertEqual([ref.scope for ref in combined[-2:]], ["task", "task"])
            prompt = skill_prompt_block("investigator", task_config_path=config)
            self.assertIn("System-level TheoryMaster skills", prompt)
            self.assertIn("Task-local Master skills", prompt)
            for name in names:
                local = local_root / name
                self.assertEqual(loaded_skill_for_path("investigator", local / "SKILL.md", task_config_path=config).scope, "task")
                self.assertIn(str(local / "SKILL.md"), prompt)

    def test_shared_cursor_skills_and_theorymaster_native_skills(self):
        root = skills_root()
        compute = root / "compute-dispatch"
        report_writing = root / "report-writing"
        scholarly = root / "tm-scholar"
        self.assertFalse(compute.is_symlink())
        self.assertFalse(report_writing.is_symlink())
        self.assertTrue((compute / "SKILL.md").is_file())
        self.assertTrue((report_writing / "SKILL.md").is_file())
        self.assertFalse(scholarly.is_symlink())
        self.assertTrue((scholarly / "SKILL.md").is_file())
        for path in root.rglob("*"):
            self.assertFalse(path.is_symlink(), f"skill must be copied: {path}")
            if path.is_file():
                self.assertEqual(path.stat().st_nlink, 1, f"skill must not be hard-linked: {path}")
        compute_text = (compute / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Investigator", compute_text)
        self.assertIn("TheoryMaster SDK", compute_text)
        self.assertIn("不调用 `canvas-console`", compute_text)
        self.assertNotIn("TheoryMaster Numerical", compute_text)
        self.assertNotIn("owner_agent:", compute_text)
        self.assertNotIn("next_action:", compute_text)
        self.assertNotIn("tmux-cron-agent", compute_text)

    def test_public_system_skills_do_not_embed_example_project_policy(self):
        # The packaged resources are the only authoritative public skills.
        # Do not restore an independent top-level skill mirror.
        source_root = Path(__file__).resolve().parents[1]
        self.assertFalse((source_root / "skills").exists())
        project_terms = ("M200K", "M400K", "Green/Schur", "root-cut", "root_001",
                         "network-graph", "polymer-numerical-policy", "GraphTools",
                         "gs_study", "example-project", "笔记区", "/home/example",
                         "polymer network fracture", "高分子", "凝胶", "ss_fit", "LAMMPS")
        for path in skills_root().rglob("*"):
            if path.is_file() and path.suffix in {".md", ".py", ".txt", ".json"}:
                with self.subTest(skill=str(path.relative_to(skills_root()))):
                    body = path.read_text(encoding="utf-8")
                    for term in project_terms:
                        self.assertNotIn(term, body, (str(path), term))

    def test_linked_skill_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("---\nname: sample\ndescription: sample\n---\n")
            (root / "linked").symlink_to(source, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "copy the skill instead"):
                resolve_skill("linked", root)

    def test_least_capability_optional_only(self):
        self.assertEqual(ROLE_SKILLS["research_lead"], ())
        self.assertEqual(ROLE_SKILLS["challenger"], ())
        self.assertEqual(ROLE_SKILLS["judge"], ())
        self.assertEqual(
            ROLE_SKILLS["investigator"],
            (
                "theory-investigation",
                "numerical-investigation",
                "symbolic",
                "proof-writer",
                "analyze-results",
                "experiment-plan",
                "compute-dispatch",
                "plotting",
            ),
        )
        self.assertEqual(ROLE_SKILLS["literature"], ("tm-scholar", "pdf-reading", "research-lit", "novelty-check"))
        self.assertEqual(ROLE_SKILLS["reporter"], ("report-writing",))

    def test_aris_capability_adaptations_preserve_theorymaster_authority(self):
        analyze = (skills_root() / "analyze-results" / "SKILL.md").read_text(encoding="utf-8")
        plan = (skills_root() / "experiment-plan" / "SKILL.md").read_text(encoding="utf-8")
        lit = (skills_root() / "research-lit" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("Observation -> Interpretation -> Implication", analyze)
        self.assertIn("possible discriminating follow-up evidence", analyze)
        self.assertIn("同一 Investigation Round 内应继续完成", analyze)

        self.assertIn("diagnostic/evidence criterion", plan)
        self.assertIn("当前 Investigator assignment", plan)
        self.assertIn("所有 routing 与 lifecycle 决策属于 TheoryMaster SDK", plan)

        self.assertIn("去重", lit)
        self.assertIn("verification", lit)
        self.assertIn("不得因为论文较多而自行 spawn subagents", lit)
        self.assertIn("所有外部 retrieval transport、Agent 调度和 lifecycle 权力属于 TheoryMaster SDK", lit)
        self.assertNotIn("research-wiki", lit)

    def test_literature_pdf_contract_keeps_materialization_sdk_owned(self):
        literature_identity = identity_block("literature")
        pdf_skill = (skills_root() / "pdf-reading" / "SKILL.md").read_text(encoding="utf-8")
        scholarly = (skills_root() / "tm-scholar" / "SKILL.md").read_text(encoding="utf-8")
        standalone = (skills_root() / "tm-scholar" / "references" / "standalone.md").read_text(encoding="utf-8")
        converter = skills_root() / "tm-scholar" / "scripts" / "pdf_to_markdown.py"
        self.assertTrue(converter.is_file())
        self.assertIn("scholarly_retrieval_root", literature_identity)
        self.assertIn("search_target", literature_identity)
        self.assertNotIn("scholarly_retrieval_access", literature_identity)
        self.assertNotIn("benchmark repository", literature_identity)
        self.assertIn("SDK/runner", literature_identity)
        self.assertNotIn("pdf_to_markdown.py", literature_identity)
        self.assertNotIn("pdf_to_markdown.py", pdf_skill)
        self.assertIn("SDK host runner", scholarly)
        self.assertNotIn("/home/example/miniconda3/bin/python", scholarly)
        self.assertNotIn("scripts/lit_retrieve.py", scholarly)
        self.assertIn("python scripts/lit_retrieve.py", standalone)
        self.assertIn("scripts/lit_retrieve.py", standalone)

    def test_imported_scientific_skills_keep_theorymaster_authority(self):
        novelty = (skills_root() / "novelty-check" / "SKILL.md").read_text(encoding="utf-8")
        proof_audit = (skills_root() / "proof-audit" / "SKILL.md").read_text(encoding="utf-8")
        result_to_claim = (skills_root() / "result-to-claim" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("must not", novelty)
        self.assertIn("must not", proof_audit)
        self.assertIn("must not", result_to_claim)
        self.assertNotIn("mcp__codex__codex", novelty)
        self.assertNotIn("mcp__codex__codex", proof_audit)
        self.assertNotIn("mcp__codex__codex", result_to_claim)
        self.assertNotIn("MAX_REVIEW_ROUNDS", proof_audit)
        self.assertNotIn("Route Based on Verdict", result_to_claim)
        self.assertIn("must not edit or rewrite the proof", proof_audit)
        self.assertIn("SDK/runner", novelty)

    def test_prompt_and_identity_injection(self):
        research_lead = developer_instructions("research_lead")
        self.assertIn("Hypothesis Lock", research_lead)
        self.assertNotIn("禁止把 `x/y`", research_lead)
        self.assertNotIn("_research_lead_protocol", research_lead)
        self.assertIn("Optional skills for role research_lead: none", research_lead)
        self.assertNotIn("compute-dispatch", research_lead)
        with_cycle = developer_instructions("research_lead", cycle_id="cycle-0023")
        self.assertTrue(with_cycle.startswith("TheoryMaster Cycle: cycle-0023\nYou are TheoryMaster Research Lead."))
        self.assertEqual(thread_cycle_prefix("cycle-0023", "research_lead"), "[C0023 · Research Lead]")
        numerical = skill_prompt_block("investigator")
        self.assertIn("compute-dispatch", numerical)
        self.assertIn("plotting", numerical)
        self.assertNotIn("canvas-read", numerical)
        literature = skill_prompt_block("literature")
        self.assertIn("tm-scholar", literature)
        self.assertIn("pdf-reading", literature)
        self.assertIn("novelty-check", literature)
        judge = skill_prompt_block("judge")
        self.assertIn("Optional skills for role judge: none", judge)
        self.assertNotIn("proof-audit", judge)
        self.assertNotIn("result-to-claim", judge)

    def test_turn_input_falls_back_to_prompt_without_sdk(self):
        with unittest.mock.patch.dict(sys.modules, {"openai_codex": None}):
            self.assertEqual(turn_input("judge", "return json"), "return json")

    def test_turn_input_does_not_eagerly_load_optional_skills(self):
        try:
            from openai_codex import SkillInput, TextInput  # type: ignore
        except ImportError:
            self.skipTest("Codex SDK not installed")
        judge = turn_input("judge", "return json")
        self.assertEqual([type(item) for item in judge], [TextInput])
        numerical = turn_input("investigator", "return json")
        self.assertEqual([type(item) for item in numerical], [TextInput])
        self.assertFalse(any(isinstance(item, SkillInput) for item in numerical))
        developer = developer_instructions("investigator")
        for skill in ("compute-dispatch", "plotting"):
            self.assertIn(skill, developer)
        self.assertNotIn("polymer-numerical-policy", developer)
        self.assertNotIn("network-graph", developer)

    def test_turn_input_omits_skills_for_continuation(self):
        try:
            from openai_codex import SkillInput, TextInput  # type: ignore
        except ImportError:
            self.skipTest("Codex SDK not installed")
        continuation = turn_input("investigator", "continue", include_skills=False)
        self.assertEqual([type(item) for item in continuation], [TextInput])
        self.assertFalse(any(isinstance(item, SkillInput) for item in continuation))

    def test_write_rules(self):
        self.assertIn("JSON only", write_rule("research_lead"))
        investigator_rule = write_rule("investigator")
        self.assertIn("current Cycle write root", investigator_rule)
        self.assertIn("cycle_workspace", investigator_rule)
        self.assertIn("Task scientific root as read/navigation only", investigator_rule)
        self.assertNotIn("task_root", investigator_rule)
        self.assertIn("without direct file writes", write_rule("reporter"))
        self.assertIn("regenerates the Canvas projection", write_rule("reporter"))
        self.assertNotIn("Canvas patches", write_rule("reporter"))
        literature_rule = write_rule("literature")
        self.assertIn("cycle_workspace/literature/", literature_rule)
        self.assertIn("your cwd", literature_rule)
        self.assertIn("scholarly_retrieval_root", literature_rule)
        # Literature receives no Task-root anchor and its cwd is not the Investigator write root.
        self.assertNotIn("Task scientific root", literature_rule)
        self.assertNotIn("Cycle write root", literature_rule)
        self.assertNotIn("task_root", literature_rule)
        self.assertIn("authoritative TheoryMaster handoff", literature_rule)

    def test_developer_instructions_keep_process_guard_for_writers(self):
        text = developer_instructions("investigator", extra="never pkill")
        self.assertIn("compute-dispatch", text)
        self.assertIn("never pkill", text)
        self.assertIn("经本次调用授权时，可在同一科学线程进行理论↔数值迭代", text)
        self.assertNotIn("never pkill", developer_instructions("judge"))


class SkillInjectionTest(unittest.TestCase):
    def test_backend_exposes_fixed_compute_python_to_agent_shells(self):
        from theory_master.backends import CodexBackend

        compute_python = __import__("sys").executable
        with unittest.mock.patch.dict("os.environ", {"THEORYMASTER_COMPUTE_PYTHON": compute_python}, clear=False):
            environment = CodexBackend._sdk_environment()
        resolved = str(Path(compute_python).absolute())
        self.assertEqual(environment["THEORYMASTER_COMPUTE_PYTHON"], resolved)
        self.assertEqual(environment["PYTHON"], resolved)
        self.assertTrue(environment["PATH"].startswith(str(Path(compute_python).absolute().parent) + ":"))

    @unittest.mock.patch.dict("os.environ", {"THEORYMASTER_COMPUTE_PYTHON": sys.executable})
    def test_backend_initializes_once_for_direct_and_precreated_threads(self):
        from theory_master.backends import CodexBackend

        sdk = SimpleNamespace(Sandbox=SimpleNamespace(workspace_write="write", read_only="read"))
        for precreated in (False, True):
            with self.subTest(precreated=precreated):
                backend = CodexBackend(Path("/tmp"))
                backend.cycle_id = "cycle-0023"
                thread = SimpleNamespace(id="test-thread", run=lambda payload: SimpleNamespace(final_response="{}"))
                client = unittest.mock.Mock()
                client.thread_start.return_value = thread
                client.thread_resume.return_value = thread
                with unittest.mock.patch.dict(sys.modules, {"openai_codex": sdk}), \
                     unittest.mock.patch.object(backend, "_ensure_client", return_value=client), \
                     unittest.mock.patch("theory_master.codex_backend.turn_input", side_effect=lambda role, prompt, **kw: prompt) as inputs:
                    identifier = backend.ensure_thread("investigator", None, "model", "provider") if precreated else None
                    identifier, _ = backend.invoke("investigator", "first", identifier, "model", "provider")
                    backend.invoke("investigator", "next", identifier, "model", "provider")
                    self.assertEqual([call.kwargs["include_skills"] for call in inputs.call_args_list], [True, False])
                    self.assertIn("developer_instructions", client.thread_start.call_args.kwargs)
                    self.assertTrue(
                        client.thread_start.call_args.kwargs["developer_instructions"].startswith(
                            "TheoryMaster Cycle: cycle-0023\nYou are TheoryMaster Investigator."
                        )
                    )
                    backend.drop_thread(identifier)
                    backend.ensure_thread("investigator", identifier, "model", "provider")
                    self.assertNotIn("developer_instructions", client.thread_resume.call_args.kwargs)

    @unittest.mock.patch.dict("os.environ", {"THEORYMASTER_COMPUTE_PYTHON": sys.executable})
    def test_investigator_thread_gets_task_read_anchor_without_widening_write_root(self):
        from theory_master.backends import CodexBackend

        sdk = SimpleNamespace(Sandbox=SimpleNamespace(workspace_write="write", read_only="read"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_root = root / "task"
            cycle_workspace = task_root / ".theory-master" / "research" / "1" / "workspace"
            cycle_workspace.mkdir(parents=True)
            config_path = task_root / "theory-master.config.json"
            config_path.write_text("{}\n", encoding="utf-8")
            backend = CodexBackend(cycle_workspace, task_config_path=config_path)
            backend.cycle_id = "1"
            thread = SimpleNamespace(id="investigator-thread")
            client = unittest.mock.Mock()
            client.thread_start.return_value = thread
            with unittest.mock.patch.dict(sys.modules, {"openai_codex": sdk}), \
                 unittest.mock.patch.object(backend, "_ensure_client", return_value=client):
                identifier = backend.ensure_thread("investigator", None, "model", "provider")
            self.assertEqual(identifier, "investigator-thread")
            kwargs = client.thread_start.call_args.kwargs
            self.assertEqual(Path(kwargs["cwd"]), cycle_workspace)
            instructions = kwargs["developer_instructions"]
            self.assertIn(f"Task scientific read root = {task_root.resolve()}", instructions)
            self.assertIn(f"Current Cycle write root = {cycle_workspace.resolve()}", instructions)
            # Concrete paths come from the backend; the navigation principle lives once in the Role Core.
            self.assertEqual(instructions.count("空的 Cycle Evidence Registry 不代表原始科研输入不存在"), 1)
            self.assertEqual(instructions.count("有目的地检索"), 1)
            self.assertNotIn("never proves that task-local scientific inputs are absent", instructions)

    @unittest.mock.patch.dict("os.environ", {"THEORYMASTER_COMPUTE_PYTHON": sys.executable})
    def test_account_threads_get_native_compaction_on_start_and_resume(self):
        from theory_master.backends import CodexBackend, RoleRoute

        sdk = SimpleNamespace(Sandbox=SimpleNamespace(workspace_write="write", read_only="read"))
        routes = {"investigator": RoleRoute("gpt-6-sol", "CodexHTTPS", "openai", "high")}
        backend = CodexBackend(Path("/tmp"), routes=routes)
        backend.cycle_id = "cycle-0023"
        backend.collector = unittest.mock.Mock()
        thread = SimpleNamespace(id="compact-thread")
        client = unittest.mock.Mock()
        client.thread_start.return_value = thread
        client.thread_resume.return_value = thread
        with unittest.mock.patch.dict(sys.modules, {"openai_codex": sdk}), \
             unittest.mock.patch.object(backend, "_ensure_client", return_value=client):
            identifier = backend.ensure_thread("investigator", None, "gpt-6-sol", "CodexHTTPS")
            start_config = client.thread_start.call_args.kwargs["config"]
            self.assertEqual(start_config["model_reasoning_effort"], "high")
            self.assertEqual(start_config["model_auto_compact_token_limit"], 220000)
            self.assertEqual(start_config["model_auto_compact_token_limit_scope"], "total")
            backend.drop_thread(identifier)
            backend.ensure_thread("investigator", identifier, "gpt-6-sol", "CodexHTTPS")
            resume_config = client.thread_resume.call_args.kwargs["config"]
            self.assertEqual(resume_config["model_auto_compact_token_limit"], 220000)
            self.assertEqual(resume_config["model_auto_compact_token_limit_scope"], "total")
            self.assertNotIn("developer_instructions", client.thread_resume.call_args.kwargs)
            config_events = [call.args[0] for call in backend.collector.on_codex.call_args_list
                             if call.args[0].get("event") == "THREAD_CONFIG"]
            self.assertEqual([event["thread_action"] for event in config_events], ["start", "resume"])
            self.assertTrue(all(event["native_compaction_enabled"] for event in config_events))

    @unittest.mock.patch.dict("os.environ", {"THEORYMASTER_COMPUTE_PYTHON": sys.executable})
    def test_literature_uses_scoped_workspace_write_cwd(self):
        from theory_master.backends import CodexBackend

        sdk = SimpleNamespace(Sandbox=SimpleNamespace(workspace_write="write", read_only="read"))
        with tempfile.TemporaryDirectory() as temp:
            backend = CodexBackend(Path(temp))
            backend.cycle_id = "cycle-0023"
            thread = SimpleNamespace(id="literature-thread")
            client = unittest.mock.Mock()
            client.thread_start.return_value = thread
            with unittest.mock.patch.dict(sys.modules, {"openai_codex": sdk}), \
                 unittest.mock.patch.object(backend, "_ensure_client", return_value=client):
                identifier = backend.ensure_thread("literature", None, "model", "provider")
            self.assertEqual(identifier, "literature-thread")
            kwargs = client.thread_start.call_args.kwargs
            self.assertEqual(kwargs["sandbox"], "write")
            self.assertEqual(Path(kwargs["cwd"]), Path(temp) / "literature")
            self.assertTrue((Path(temp) / "literature").is_dir())
            self.assertIn("tm-scholar", kwargs["developer_instructions"])
            self.assertNotIn("task_root/literature", kwargs["developer_instructions"])
            self.assertIn("cycle_workspace/literature/", write_rule("literature"))

    def test_workflow_injects_identity_and_optional_skills(self):
        class CapturingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.prompts = {}

            def invoke(self, role, prompt, thread_id, model, provider):
                self.prompts.setdefault(role, []).append(prompt)
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = root / "research_plan.md"
            plan.write_text(
                "# Goal\n\ng\n\n# Research Contract\n\nc\n\n"
                "# Final Gate\n\nEstablish reproducible result.\n\n"
                "# Required Context\n\n- r\n\n"
                "# Scientific Model\n\nNo prior findings.\n",
                encoding="utf-8",
            )
            spec = TaskSpec.load(plan)
            config = json.loads(spec.config_path.read_text(encoding="utf-8"))
            config["max_cycles"] = 1
            spec.config_path.write_text(json.dumps(config), encoding="utf-8")
            spec = TaskSpec.from_config(spec.config_path)
            backend = CapturingBackend()
            Workflow(spec, backend).run()
            research_lead = backend.prompts["research_lead"][0]
            def _payload(prompt: str):
                if "Context:\n" not in prompt:
                    return None
                return json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
            lead_contexts = [item for item in (_payload(prompt) for prompt in backend.prompts["research_lead"]) if item]
            agent_graph = lead_contexts[0]["agent_graph"]
            self.assertIn("Stage: frontier_proposal", research_lead)
            self.assertIn("Current objective:", research_lead)
            self.assertIn("SQLite 为唯一权威数据源", developer_instructions("research_lead"))
            self.assertIn("injected agent_graph or same-stage agent_graph_reuse", research_lead)
            self.assertIn("Frontier labels describe opportunities independently of graph leaves", research_lead)
            self.assertEqual(lead_contexts[0]["goal"], "g")
            self.assertEqual(lead_contexts[0]["research_contract"], "c")
            self.assertIn("schema", agent_graph)
            self.assertIn("hypotheses", agent_graph)
            self.assertIn("current_frontiers", agent_graph)
            self.assertNotIn("canvas_access", lead_contexts[0])
            self.assertNotIn("branch_canvas", lead_contexts[0])
            for removed in (
                "research_reading", "required_context", "hypothesis_catalog", "prior_cycle_summary",
                "canvas_graph_overview", "current_node_neighborhood", "lead_literature_detour_used",
            ):
                self.assertTrue(all(removed not in item for item in lead_contexts))
            # Later Lead stages reuse thread memory and receive only the stage delta.
            finalization = [item for item in lead_contexts if item.get("phase") == "finalization"]
            if finalization:
                self.assertTrue(any("candidate_reviews" in item for item in finalization))
            numerical = backend.prompts["investigator"][0]
            reporter = backend.prompts["reporter"][0]
            cycle_dir = latest_cycle_dir(spec.research_root)
            self.assertIsNotNone(cycle_dir)
            self.assertTrue(research_lead.startswith(thread_cycle_prefix(cycle_dir.name, "research_lead") + " "))
            self.assertTrue(numerical.startswith(thread_cycle_prefix(cycle_dir.name, "investigator") + " "))
            self.assertTrue(reporter.startswith(thread_cycle_prefix(cycle_dir.name, "reporter") + " "))
            # Identity and skill definitions are installed on the SDK thread;
            # they are intentionally absent from the user turn prompt.
            self.assertIn("Do not select or lock a Candidate before Challenger", research_lead)
            self.assertIn("SDK owns identity, lineage persistence, qualification routing and Hypothesis Lock", research_lead)
            self.assertNotIn("禁止用 `x/y`", research_lead)
            self.assertNotIn("compute-dispatch", research_lead)
            self.assertIn("JSON only", research_lead)
            self.assertNotIn("Optional skills for role investigator", numerical)
            self.assertNotIn("compute-dispatch", numerical)
            self.assertIn("current Cycle write root", numerical)
            self.assertIn("cycle_workspace", numerical)
            self.assertIn("Task scientific root as read/navigation only", numerical)
            self.assertNotIn("task_root", numerical)
            self.assertNotIn("Optional skills for role reporter", reporter)
            events = [json.loads(line) for line in (cycle_dir / "events.jsonl").read_text().splitlines()]
            started = {event["payload"]["role"]: event["payload"] for event in events if event["type"] == "role.started"}
            self.assertEqual(started["research_lead"]["skills"], [])
            self.assertEqual(started["research_lead"]["identities"], ["research_lead.md", "_invocation_handoff.md", "_paths.md"])
            self.assertEqual(started["investigator"]["skills"], [
                "theory-investigation", "numerical-investigation", "symbolic", "proof-writer",
                "analyze-results", "experiment-plan",
                "compute-dispatch", "plotting",
            ])
            if "literature" in started:
                self.assertEqual(started["literature"]["skills"], ["tm-scholar", "pdf-reading", "research-lit", "novelty-check"])
            self.assertEqual(started["reporter"]["skills"], ["report-writing"])
            self.assertEqual(started["judge"]["identities"], ["judge.md", "_invocation_handoff.md", "_paths.md"])
            self.assertEqual(started["judge"]["skills"], [])
            self.assertNotIn("director", started)
            self.assertNotIn("defense", started)


if __name__ == "__main__":
    unittest.main()
