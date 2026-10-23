from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from tm_observer.archify_views import StaticBuildResult, StaticViewManager, architecture_ir, build_architecture_delta, build_static_views, lifecycle_ir, workflow_ir
from tm_observer.al_public import caddy_site, render_tunnel_service
from tm_observer.auth import BasicAuthCredentials, ObservatoryAuth, ensure_auth_file, load_auth_file
from tm_observer.collector import collect_state
from tm_observer.sequence_views import _sequence_source, run_sequence_ir
from tm_observer.service import render_user_service
from tm_observer.stage_flow import SDK_OWNERSHIP_FIELDS, STAGE_CONTEXT_FIELDS, field_note_catalog, stage_contract_inventory, stage_detail_catalog, stage_workflow_mermaid, stage_workflow_overview_mermaid
from tm_observer.workflow_versions import load_workflow_snapshots
from tm_observer.web import StateCache, ThreadingHTTPServer, current_flow_html, make_handler, stage_workflow_guide_html, stage_workflow_html, workflow_html


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def make_task(
    root: Path,
    name: str,
    *,
    status: str,
    pid: int | None = None,
    execution_active: bool = False,
    phase: str = "investigation",
    active_agent: str | None = "numerical",
    master_id: int = 1,
    attention: str | None = None,
) -> Path:
    task = root / name
    write_json(task / ".theory-master" / "task.json", {"schema": "theory-master/task/v2"})
    runtime = {"schema": "theory-master/runtime/v1", "state": status, "master_id": master_id, "heartbeat_at": stamp()}
    if pid is not None:
        runtime["runner_pid"] = pid
    write_json(task / ".theory-master" / "runtime.json", runtime)
    cycle = task / "cycle-0001"
    run_state = {
        "master_id": master_id,
        "status": status,
        "phase": phase,
        "current_agent": active_agent,
        "owner_pid": pid,
        "execution_active": execution_active,
        "project_tokens_total": 1234,
        "investigation_round_count": 3,
        "started_at": stamp(),
    }
    if attention:
        run_state["human_attention"] = attention
    write_json(cycle / "run_state.json", run_state)
    write_json(
        cycle / "research_state.json",
        {
            "status": status.lower(),
            "phase": phase,
            "active_role": active_agent,
            "execution_active": execution_active,
            "owner_pid": pid,
        },
    )
    (cycle / "runtime_events.jsonl").write_text(
        json.dumps(
            {
                "ts": stamp(),
                "event": "LLM_TURN_STARTED" if execution_active else "LLM_TURN_COMPLETED",
                "role": active_agent,
                "tokens_total": 1234,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return task


def tree_signature(root: Path) -> dict[str, tuple[str, int, int]]:
    result = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        data = path.read_bytes()
        st = path.stat()
        result[str(path.relative_to(root))] = (
            hashlib.sha256(data).hexdigest(),
            st.st_mtime_ns,
            st.st_size,
        )
    return result


class CollectorTests(unittest.TestCase):
    def test_live_run_uses_persisted_state_and_proc_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root, "live-task", status="RUNNING", pid=os.getpid(), execution_active=True, master_id=7)
            state = collect_state(root)
            run = state["runs"][0]
            self.assertEqual(run["run_id"], 7)
            self.assertEqual(run["state"], "LIVE")
            self.assertTrue(run["pid_alive"])
            self.assertEqual(run["active_agent"], "numerical")
            self.assertEqual(run["tokens_total"], 1234)
            self.assertEqual(run["current_round"], 3)
            self.assertEqual(run["provenance"]["active_agent"]["field"], "current_agent")

    def test_orphaned_run_is_derived_not_guessed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root, "orphan", status="RUNNING", pid=99999999, execution_active=True)
            run = collect_state(root)["runs"][0]
            self.assertEqual(run["state"], "ORPHANED")
            self.assertFalse(run["pid_alive"])
            self.assertEqual(run["warnings"][0]["code"], "ORPHANED")
            self.assertEqual(run["provenance"]["state"]["method"], "derived")

    def test_terminal_states(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root, "stopped", status="STOPPED", pid=None, execution_active=False, master_id=2)
            make_task(root, "attention", status="NEEDS_ATTENTION", pid=None, execution_active=False, master_id=3, attention="qualification repair exhausted")
            make_task(root, "over", status="OVER", pid=None, execution_active=False, master_id=4)
            make_task(root, "legacy-completed", status="COMPLETED", pid=None, execution_active=False, master_id=5)
            runs = {run["task_name"]: run for run in collect_state(root)["runs"]}
            self.assertEqual(runs["stopped"]["state"], "STOPPED")
            self.assertEqual(runs["attention"]["state"], "NEEDS_ATTENTION")
            self.assertEqual(runs["over"]["state"], "OVER")
            self.assertEqual(runs["legacy-completed"]["state"], "OVER")
            self.assertEqual(runs["legacy-completed"]["raw_status"], "COMPLETED")
            self.assertEqual(runs["legacy-completed"]["provenance"]["state"]["method"], "legacy_cycle_status_normalization")
            self.assertEqual(runs["attention"]["warnings"][0]["code"], "NEEDS_ATTENTION")
            self.assertIn("qualification", runs["attention"]["warnings"][0]["message"])

    def test_current_numbered_cycle_discovery_and_tui_projection(self):
        from tm_observer.run_monitor import data, tail_lines
        from tm_observer.run_views import run_detail_body

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = make_task(root, "numbered", status="RUNNING", pid=os.getpid(), execution_active=True)
            for number in (2, 10):
                cycle = task / str(number)
                write_json(cycle / "run_state.json", {
                    "master_id": 1,
                    "cycle_id": str(number),
                    "status": "RUNNING" if number == 10 else "OVER",
                    "phase": "investigator:running" if number == 10 else "complete",
                    "current_agent": "investigator",
                    "owner_pid": os.getpid(),
                    "execution_active": number == 10,
                    "project_tokens_total": number * 1000,
                    "agents": {"investigator": {"state": "running", "model": "mock-model"}},
                })
                write_json(cycle / "research_state.json", {
                    "status": "active" if number == 10 else "over",
                    "execution_active": number == 10,
                    "active_role": "investigator",
                })
                (cycle / "runtime_events.jsonl").write_text(
                    json.dumps({"event": "LLM_TURN_STARTED", "role": "investigator"}) + "\n",
                    encoding="utf-8",
                )
            write_json(task / "cycle-0010" / "run_state.json", {
                "master_id": 1,
                "status": "OVER",
                "phase": "complete",
                "project_tokens_total": 1,
            })
            (task / "00-invalid").mkdir()
            (task / "cycle-other").mkdir()
            before = tree_signature(task)
            run = collect_state(root)["runs"][0]
            self.assertEqual(run["cycle"], "10")
            self.assertEqual(run["state"], "LIVE")
            self.assertEqual(run["active_agent"], "investigator")
            self.assertEqual(run["tokens_total"], 10000)
            self.assertFalse(any(item["code"] == "NO_CYCLE" for item in run["warnings"]))
            projected = data(run)
            self.assertEqual(projected["cycle_id"], "10")
            self.assertEqual(projected["phase"], "investigator:running")
            self.assertEqual(projected["project_tokens_total"], 10000)
            detail = run_detail_body(run, tab="usage")
            self.assertIn("mock-model", detail)
            self.assertIn("10,000", detail)
            self.assertIn("investigator:running", detail)
            self.assertEqual(len(tail_lines(run, "runtime_events.jsonl")), 1)
            self.assertEqual(tree_signature(task), before)

    def test_missing_or_malformed_state_remains_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = root / "bad-task"
            write_json(task / ".theory-master" / "task.json", {"schema": "theory-master/task/v2"})
            cycle = task / "cycle-0001"
            cycle.mkdir(parents=True)
            (cycle / "run_state.json").write_text("{bad json", encoding="utf-8")
            run = collect_state(root)["runs"][0]
            self.assertEqual(run["state"], "UNKNOWN")
            self.assertIsNone(run["phase"])
            self.assertTrue(any(w["code"] == "MISSING_RUN_STATE" for w in run["warnings"]))

    def test_every_core_field_has_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root, "task", status="RUNNING", pid=os.getpid(), execution_active=True)
            run = collect_state(root)["runs"][0]
            for key in [
                "observer_id",
                "run_id",
                "task_name",
                "task_root",
                "research_root",
                "cycle",
                "state",
                "raw_status",
                "phase",
                "active_agent",
                "pid",
                "pid_alive",
                "started_at",
                "last_activity_at",
                "tokens_total",
                "current_round",
            ]:
                self.assertIn(key, run["provenance"], key)
                self.assertIn("source", run["provenance"][key], key)
                self.assertIn("method", run["provenance"][key], key)

    def test_collection_is_read_only_for_task_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = make_task(root, "readonly", status="RUNNING", pid=os.getpid(), execution_active=True)
            before = tree_signature(task)
            collect_state(root)
            after = tree_signature(task)
            self.assertEqual(before, after)

    def test_nested_internal_research_binding_is_not_double_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = make_task(root, "task", status="STOPPED", execution_active=False)
            write_json(task / ".theory-master" / "research" / ".theory-master" / "task.json", {"schema": "theory-master/task/v2"})
            self.assertEqual(len(collect_state(root)["runs"]), 1)

    def test_alias_bindings_to_same_research_root_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            canonical = make_task(root, "canonical", status="STOPPED", execution_active=False, master_id=5)
            alias = root / "notes" / "alias"
            config = alias / "theory-master.config.json"
            write_json(
                alias / ".theory-master" / "task.json",
                {"schema": "theory-master/task/v2", "config_path": str(config)},
            )
            write_json(
                config,
                {
                    "schema": "theory-master/system/v1",
                    "task_id": 5,
                    "research_root": str(canonical),
                },
            )
            runs = collect_state(root)["runs"]
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["task_root"], str(canonical.resolve()))

    def test_configured_research_root_is_followed_for_tmlab_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = root / "experiment" / "runs" / "run-001" / "task"
            research = task / ".theory-master" / "research"
            config = task / "theory-master.config.json"
            write_json(
                task / ".theory-master" / "task.json",
                {"schema": "theory-master/task/v2", "config_path": str(config)},
            )
            write_json(
                config,
                {
                    "schema": "theory-master/system/v1",
                    "task_id": 42,
                    "research_root": str(research),
                },
            )
            write_json(
                research / ".theory-master" / "runtime.json",
                {"state": "STOPPED", "master_id": 42, "heartbeat_at": stamp()},
            )
            write_json(
                research / "cycle-0003" / "run_state.json",
                {
                    "master_id": 42,
                    "status": "STOPPED",
                    "phase": "complete",
                    "execution_active": False,
                    "project_tokens_total": 88,
                },
            )
            write_json(
                research / "cycle-0003" / "research_state.json",
                {"status": "stopped", "phase": "complete", "execution_active": False},
            )
            run = collect_state(root)["runs"][0]
            self.assertEqual(run["run_id"], 42)
            self.assertEqual(run["cycle"], "cycle-0003")
            self.assertEqual(run["research_root"], str(research.resolve()))
            self.assertEqual(run["task_name"], "experiment/run-001")
            self.assertFalse(any(w["code"] == "NO_CYCLE" for w in run["warnings"]))

    def test_no_eager_theory_master_runtime_imports(self):
        # Web read paths must be import-safe. Explicit, authorized HTTP
        # actions and read-only DB access can lazy-import the SDK inside
        # their handlers, but may not load it at module import time.
        import ast
        package = Path(__file__).resolve().parents[1] / "tm_observer"
        for path in package.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in tree.body:
                if isinstance(node, ast.Import):
                    self.assertFalse(any(alias.name.startswith("theory_master") for alias in node.names), path.name)
                elif isinstance(node, ast.ImportFrom):
                    self.assertFalse((node.module or "").startswith("theory_master"), path.name)


class SequenceViewTests(unittest.TestCase):
    def test_observer_id_is_unique_even_when_legacy_run_ids_collide(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root, "a", status="STOPPED", execution_active=False, master_id=0)
            make_task(root, "b", status="STOPPED", execution_active=False, master_id=0)
            runs = collect_state(root)["runs"]
            self.assertEqual({run["run_id"] for run in runs}, {0})
            self.assertEqual(len({run["observer_id"] for run in runs}), 2)
            self.assertTrue(all(len(run["observer_id"]) == 16 for run in runs))

    def test_sequence_uses_only_lifecycle_facts_and_explicit_thread_semantics(self):
        run = {
            "observer_id": "0123456789abcdef",
            "task_name": "fixture",
            "cycle": "cycle-0001",
        }
        events = [
            {
                "event": "THREAD_CONFIG",
                "role": "research_lead",
                "thread_id": "thr-lead",
                "thread_action": "start",
            },
            {
                "event": "AGENT_STARTED",
                "role": "research_lead",
                "stage": "frontier_proposal",
                "thread_id": "thr-lead",
                "resume_thread_id": None,
                "invocation_id": "inv-1",
            },
            {
                "event": "AGENT_INPUT",
                "role": "research_lead",
                "content": "SECRET SCIENTIFIC PROMPT THAT MUST NOT APPEAR",
            },
            {
                "event": "TOKEN_USAGE",
                "role": "research_lead",
                "tokens_total": 1200,
            },
            {
                "event": "AGENT_COMPLETED",
                "role": "research_lead",
                "stage": "frontier_proposal",
                "output": {"ready_for_judge": False, "requested_next_capability": "challenger"},
            },
            {
                "event": "AGENT_STARTED",
                "role": "research_lead",
                "stage": "selection_repair",
                "thread_id": "thr-lead",
                "resume_thread_id": "thr-lead",
            },
            {
                "event": "NATIVE_COMPACTION_STARTED",
                "role": "research_lead",
            },
        ]
        ir = run_sequence_ir(run, events, Path("/tmp/runtime_events.jsonl"))
        labels = [msg["label"] for msg in ir["messages"]]
        joined = "\n".join(labels)
        self.assertIn("frontier_proposal · fresh", joined)
        self.assertIn("done frontier_proposal · tok=1200 · ready=false · next=challenger", joined)
        self.assertIn("selection_repair · resume", joined)
        self.assertIn("native compaction started", joined)
        self.assertNotIn("SECRET SCIENTIFIC PROMPT", json.dumps(ir))
        self.assertEqual(ir["diagram_type"], "sequence")

    def test_cycle7_style_repeated_judge_invocations_are_not_collapsed(self):
        run = {"observer_id": "cycle7fixture000", "task_name": "cycle-7", "cycle": "cycle-0007"}
        events = []
        for index in range(1, 4):
            events.extend(
                [
                    {
                        "type": "role.started",
                        "payload": {
                            "role": "judge",
                            "invocation_id": f"inv-judge-{index}",
                            "thread_id": f"judge-thread-{index}",
                        },
                    },
                    {
                        "type": "role.completed",
                        "payload": {
                            "role": "judge",
                            "output": {"verdict": "needs_more_evidence" if index < 3 else "open"},
                        },
                    },
                ]
            )

        ir = run_sequence_ir(run, events, Path("/tmp/events.jsonl"))
        starts = [msg for msg in ir["messages"] if msg["to"] == "role-judge" and msg["variant"] == "emphasis"]
        returns = [msg for msg in ir["messages"] if msg["from"] == "role-judge" and msg["variant"] == "return"]
        self.assertEqual(len(starts), 3)
        self.assertEqual(len(returns), 3)
        self.assertIn("call 1/3", starts[0]["label"])
        self.assertIn("call 2/3", starts[1]["label"])
        self.assertIn("call 3/3", starts[2]["label"])
        self.assertIn("verdict=open", returns[-1]["label"])

    def test_cycle8_style_idle_roles_do_not_become_sequence_participants(self):
        run = {"observer_id": "cycle8fixture000", "task_name": "cycle-8", "cycle": "cycle-0008"}
        events = [
            {"type": "run.failed", "payload": {"error": "name 'tempfile' is not defined"}},
            {"type": "role.snapshot", "payload": {"role": "literature", "state": "idle"}},
        ]

        ir = run_sequence_ir(run, events, Path("/tmp/events.jsonl"))
        participant_ids = {item["id"] for item in ir["participants"]}
        self.assertNotIn("role-literature", participant_ids)

    def test_cycle9_style_state_projection_does_not_duplicate_reporter(self):
        run = {"observer_id": "cycle9fixture000", "task_name": "cycle-9", "cycle": "cycle-0009"}
        events = [
            {"type": "role.snapshot", "payload": {"role": "reporter", "source": "project_agents"}},
            {"type": "role.snapshot", "payload": {"role": "reporter", "source": "agents"}},
            {"type": "role.started", "payload": {"role": "theorist", "invocation_id": "inv-theorist-1"}},
            {
                "type": "role.completed",
                "payload": {
                    "role": "theorist",
                    "output": {"ready_for_judge": False, "requested_next_capability": "numerical"},
                },
            },
        ]

        ir = run_sequence_ir(run, events, Path("/tmp/events.jsonl"))
        participant_ids = [item["id"] for item in ir["participants"]]
        self.assertNotIn("role-reporter", participant_ids)
        self.assertEqual(participant_ids.count("role-theorist"), 1)

    def test_sequence_source_prefers_canonical_events_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            cycle = Path(temp)
            (cycle / "runtime_events.jsonl").write_text("{}\n", encoding="utf-8")
            self.assertEqual(_sequence_source(cycle), cycle / "runtime_events.jsonl")
            (cycle / "events.jsonl").write_text("{}\n", encoding="utf-8")
            self.assertEqual(_sequence_source(cycle), cycle / "events.jsonl")


class ArchifyViewTests(unittest.TestCase):
    def test_static_ir_types_and_key_contracts(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            architecture = architecture_ir(repo)
            workflow = workflow_ir()
            lifecycle = lifecycle_ir()
            self.assertEqual(architecture["diagram_type"], "architecture")
            self.assertEqual(workflow["diagram_type"], "workflow")
            self.assertEqual(lifecycle["diagram_type"], "lifecycle")
            component_ids = {item["id"] for item in architecture["components"]}
            self.assertIn("observer", component_ids)
            self.assertIn("cycle", component_ids)
            self.assertTrue({"scheduler", "claims", "worker", "control", "canvas", "goal_gate"} <= component_ids)
            edges = {(edge["from"], edge["to"]): edge for edge in architecture["connections"]}
            for required in [("observer", "control"), ("control", "scheduler"),
                             ("worker", "workflow"), ("canvas", "claims"),
                             ("judge", "goal_gate")]:
                self.assertIn(required, edges)
            self.assertFalse(any(edge["from"] == "observer" and edge["to"] != "control"
                                 for edge in architecture["connections"]),
                             "TM Web must not bypass authenticated Operator Control.")
            workflow_ids = {item["id"] for item in workflow["nodes"]}
            self.assertTrue({
                "lead_frontier_proposal",
                "challenger_candidate_review",
                "lead_candidate_selection",
                "sdk_hypothesis_lock",
                "sdk_investigation_router",
                "investigator_investigation",
                "literature_investigation",
                "sdk_closure_readiness",
                "judge_terminal_assessment",
                "lead_post_judge",
                "judge_goal_completion",
                "reporter_closeout",
                "sdk_post_judge",
            } <= workflow_ids)
            lane_ids = {item["id"] for item in workflow["lanes"]}
            self.assertEqual(
                lane_ids,
                {"research_lead", "challenger", "investigator", "literature", "judge", "reporter", "sdk"},
            )
            lifecycle_ids = {item["id"] for item in lifecycle["states"]}
            self.assertTrue({"qualification", "investigation", "judging", "over", "attention"} <= lifecycle_ids)
            self.assertNotIn("completed", lifecycle_ids)

    def test_workflow_stage_catalog_matches_current_theory_master_source(self):
        repo_root = Path(__file__).resolve().parents[3]
        workflow = workflow_ir(repo_root)
        lane_ids = {item["id"] for item in workflow["lanes"]}
        self.assertEqual(
            lane_ids,
            {"research_lead", "challenger", "investigator", "literature", "judge", "reporter", "sdk"},
        )
        self.assertNotIn("theorist", lane_ids)
        self.assertNotIn("numerical", lane_ids)

    def test_delta_missing_archify_degrades_without_touching_tm(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = make_task(root, "readonly-delta", status="STOPPED", execution_active=False)
            before = tree_signature(task)
            result = build_architecture_delta(
                root,
                root / "observer-state",
                archify_root=root / "missing-archify",
                force=True,
            )
            self.assertFalse(result.ok)
            self.assertIn("Archify CLI not found", result.warning or "")
            self.assertEqual(before, tree_signature(task))

    def test_missing_archify_degrades_without_touching_tm(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = make_task(root, "readonly-static", status="STOPPED", execution_active=False)
            before = tree_signature(task)
            result = build_static_views(
                root,
                root / "observer-state",
                archify_root=root / "missing-archify",
                force=True,
            )
            self.assertFalse(result.ok)
            self.assertIn("Archify CLI not found", result.warning or "")
            self.assertEqual(before, tree_signature(task))


class AuthTests(unittest.TestCase):
    def test_auth_file_is_private_and_stable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "credentials.json"
            first = ensure_auth_file(path, username="observer")
            creds1 = load_auth_file(first)
            self.assertEqual(creds1.username, "observer")
            self.assertGreaterEqual(len(creds1.password), 30)
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
            second = ensure_auth_file(path, username="changed")
            creds2 = load_auth_file(second)
            self.assertEqual(creds1, creds2)

    def test_basic_auth_protects_all_get_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root, "auth-task", status="STOPPED", execution_active=False, master_id=10)
            cache = StateCache(root, ttl_s=0.2)
            credentials = BasicAuthCredentials("observer", "secret-value")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache, auth=credentials))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(base + "/healthz", timeout=3)
                self.assertEqual(ctx.exception.code, 401)
                self.assertIn("Basic", ctx.exception.headers.get("WWW-Authenticate", ""))
                token = base64.b64encode(b"observer:secret-value").decode("ascii")
                req = Request(base + "/healthz", headers={"Authorization": f"Basic {token}"})
                with urlopen(req, timeout=3) as response:
                    health = json.loads(response.read())
                self.assertTrue(health["ok"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_cycle_page_api_is_authenticated_task_scoped_and_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root, "cycle-page-test", status="STOPPED",
                      execution_active=False, master_id=42)
            cache = StateCache(root, ttl_s=0.2)
            run = cache.get()["runs"][0]
            task_root = Path(run["research_root"])
            for cid in range(1, 26):
                folder = task_root / str(cid)
                folder.mkdir(exist_ok=True)
                write_json(folder / "run_state.json", {"cycle_id": str(cid),
                    "action_history": [{"event": "CYCLE_STARTED",
                        "at": f"2026-10-21T10:{cid:02d}:00+08:00",
                        "text": f"Cycle started · {cid}"}]})
            cred = BasicAuthCredentials("observer", "secret-value")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache, auth=cred))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            prefix = f"/api/run/{run['observer_id']}/cycle-page"
            token = base64.b64encode(b"observer:secret-value").decode("ascii")
            try:
                def req(uri, authorized=True):
                    headers = {"Authorization": "Basic " + token} if authorized else {}
                    return urlopen(Request(base + uri, headers=headers), timeout=5)
                with req(prefix + "?before=16") as response:
                    self.assertEqual(response.headers.get_content_type(), "application/json")
                    page = json.load(response)
                self.assertEqual([c["id"] for c in page["cycles"]],
                                 [str(i) for i in range(6, 16)])
                self.assertTrue(page["has_more"])
                self.assertEqual(page["total"], 25)
                with req(prefix + "?before=6") as response:
                    self.assertFalse(json.load(response)["has_more"])
                with req(prefix + "?before=16", authorized=False) as response:
                    self.assertIn("Sciver Observatory Login", response.read().decode())
                with self.assertRaises(HTTPError) as invalid:
                    req(prefix + "?before=../15")
                self.assertEqual(invalid.exception.code, 400)
                with self.assertRaises(HTTPError) as unknown:
                    req("/api/run/" + "0" * 16 + "/cycle-page?before=16")
                self.assertEqual(unknown.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_stage_conversation_api_requires_auth_and_limits_stage_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root, "stage-auth-task", status="STOPPED", execution_active=False, master_id=29)
            cache = StateCache(root, ttl_s=0.2)
            state = cache.get()
            run = state["runs"][0]
            ident = run["observer_id"]
            folder = Path(run["research_root"]) / str(run["cycle"])
            folder.mkdir(exist_ok=True)
            (folder/"runtime_events.jsonl").write_text(
                json.dumps({"event":"AGENT_STARTED","role":"investigator","stage":"investigate",
                            "invocation_id":"inv-stage-mock","thread_id":"thread-stage"})+"\n"+
                json.dumps({"event":"AGENT_INPUT","role":"investigator",
                            "content":"Prompt for mock scientific work"})+"\n",encoding="utf-8")
            credentials = BasicAuthCredentials("observer", "secret-value")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache, auth=credentials))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            stem=f"/api/run/{ident}/conversation"
            token=base64.b64encode(b"observer:secret-value").decode("ascii")
            def get(url, authorized=True):
                headers={"Authorization":"Basic "+token} if authorized else {}
                return urlopen(Request(base+url,headers=headers),timeout=6)
            try:
                with get(stem+"?mode=index",False) as unauthorized:
                    # Browser navigation follows the auth redirect to login;
                    # it must not return Stage data without credentials.
                    self.assertIn("Sciver Observatory Login", unauthorized.read().decode())
                with get(stem+"?mode=index") as response:
                    index=json.load(response)
                self.assertEqual(len(index["stages"]),1)
                invocation=index["stages"][0]["id"]
                with get(stem+"?mode=stage&stage="+invocation+"&cycle="+str(run["cycle"])) as response:
                    stage=json.load(response)
                self.assertEqual(stage["messages"][-1]["content"],"Prompt for mock scientific work")
                with self.assertRaises(HTTPError) as bad:
                    get(stem+"?mode=stage&stage=../../etc/passwd")
                self.assertEqual(bad.exception.code,400)
                with self.assertRaises(HTTPError) as bad_cycle:
                    get(stem+"?mode=index&cycle=../../etc")
                self.assertEqual(bad_cycle.exception.code,400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_html_login_sets_secure_session_cookie(self):
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root, "login-task", status="STOPPED", execution_active=False, master_id=11)
            cache = StateCache(root, ttl_s=0.2)
            credentials = BasicAuthCredentials("observer", "secret-value")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache, auth=credentials))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            opener = build_opener(NoRedirect)
            try:
                with self.assertRaises(HTTPError) as ctx:
                    opener.open(base + "/", timeout=3)
                self.assertEqual(ctx.exception.code, 303)
                self.assertEqual(ctx.exception.headers.get("Location"), "/login")
                with urlopen(base + "/favicon.svg", timeout=3) as response:
                    self.assertEqual(response.headers.get_content_type(), "image/svg+xml")
                    favicon = response.read()
                self.assertIn(b"<svg", favicon)
                self.assertIn(b"Sciver software app icon", favicon)
                with urlopen(base + "/login", timeout=3) as response:
                    page = response.read().decode("utf-8")
                self.assertIn("Sign in", page)

                bad = Request(
                    base + "/login",
                    data=urlencode({"username": "observer", "password": "wrong"}).encode(),
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(bad, timeout=3)
                self.assertEqual(ctx.exception.code, 401)

                good = Request(
                    base + "/login",
                    data=urlencode({"username": "observer", "password": "secret-value"}).encode(),
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx:
                    opener.open(good, timeout=3)
                self.assertEqual(ctx.exception.code, 303)
                cookie = ctx.exception.headers.get("Set-Cookie", "")
                self.assertIn("tm_observer_session=", cookie)
                self.assertIn("HttpOnly", cookie)
                self.assertIn("Secure", cookie)
                self.assertIn("SameSite=Strict", cookie)
                cookie_pair = cookie.split(";", 1)[0]
                req = Request(base + "/", headers={"Cookie": cookie_pair})
                with urlopen(req, timeout=3) as response:
                    body = response.read().decode("utf-8")
                self.assertIn("Research Observatory", body)
                self.assertIn('aria-current="page"', body)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_viewer_login_sees_the_same_run_as_primary(self):
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            auth_path = root / "auth.json"
            auth_path.write_text(
                json.dumps({
                    "username": "owner",
                    "password": "owner-secret",
                    "viewers": [{"username": "123", "password": "123"}],
                }),
                encoding="utf-8",
            )
            realm = load_auth_file(auth_path)
            self.assertIsInstance(realm, ObservatoryAuth)
            make_task(root, "shared-view", status="STOPPED", execution_active=False, master_id=12)
            cache = StateCache(root, ttl_s=0.2)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache, auth=realm))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            opener = build_opener(NoRedirect)

            def session_for(username, password):
                request = Request(
                    base + "/login",
                    data=urlencode({"username": username, "password": password}).encode(),
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx:
                    opener.open(request, timeout=3)
                self.assertEqual(ctx.exception.code, 303)
                return ctx.exception.headers.get("Set-Cookie", "").split(";", 1)[0]

            try:
                owner_body = urlopen(Request(base + "/", headers={"Cookie": session_for("owner", "owner-secret")}), timeout=3).read()
                viewer_body = urlopen(Request(base + "/", headers={"Cookie": session_for("123", "123")}), timeout=3).read()
                self.assertEqual(owner_body, viewer_body)
                self.assertIn(b"shared-view", viewer_body)
                token = base64.b64encode(b"123:123").decode("ascii")
                with urlopen(Request(base + "/healthz", headers={"Authorization": f"Basic {token}"}), timeout=3) as response:
                    self.assertTrue(json.loads(response.read())["ok"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


class ServiceTests(unittest.TestCase):
    def test_user_service_is_localhost_only_and_read_only_surface(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "tool" / "tm_observer").mkdir(parents=True)
            unit = render_user_service(
                repo,
                python_executable=Path("/usr/bin/python3"),
                state_dir=repo / "observer-state",
                archify_root=repo / "archify",
                node_path=Path("/usr/bin/node"),
                port=8787,
            )
            self.assertIn('"--host" "127.0.0.1"', unit)
            self.assertNotIn("0.0.0.0", unit)
            self.assertIn("NoNewPrivileges=true", unit)
            self.assertIn("ProtectSystem=full", unit)
            self.assertIn("Restart=on-failure", unit)
            self.assertIn("TM_OBSERVER_NODE=/usr/bin/node", unit)
            self.assertNotIn("theory_master", unit)

    def test_user_service_can_enable_origin_basic_auth_without_exposing_secret(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "tool" / "tm_observer").mkdir(parents=True)
            auth_file = repo / "private-auth.json"
            auth_file.write_text('{"username":"observer","password":"not-in-unit"}', encoding="utf-8")
            unit = render_user_service(
                repo,
                python_executable=Path("/usr/bin/python3"),
                state_dir=repo / "observer-state",
                archify_root=repo / "archify",
                node_path=Path("/usr/bin/node"),
                auth_file=auth_file,
            )
            self.assertIn('"--basic-auth-file"', unit)
            self.assertIn(str(auth_file.resolve()), unit)
            self.assertNotIn("not-in-unit", unit)

    def test_tunnel_service_uses_dedicated_user_and_loopback_remote_forward(self):
        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / "tm_observer_key"
            key.write_text("placeholder", encoding="utf-8")
            unit = render_tunnel_service(key_path=key, known_hosts=Path("/tmp/known_hosts"), remote_host="198.51.100.10")
            self.assertIn("tmobserver@198.51.100.10", unit)
            self.assertIn("127.0.0.1:8453:127.0.0.1:8787", unit)
            self.assertNotIn("root@198.51.100.10", unit)
            self.assertIn("StrictHostKeyChecking=yes", unit)
            self.assertIn("ExitOnForwardFailure=yes", unit)
            self.assertIn("Restart=always", unit)

            site = caddy_site()
            self.assertIn("127.0.0.1", site)
            self.assertIn("127.0.0.1:8453", site)
            self.assertNotIn("8452", site)


class WebTests(unittest.TestCase):
    def test_workflow_page_contains_exactly_one_scientific_workflow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = StaticViewManager(root, root / "observer-state")
            static_dir = manager.state_dir / "static"
            static_dir.mkdir(parents=True, exist_ok=True)
            (static_dir / "workflow.html").write_text("<html>workflow</html>", encoding="utf-8")
            (static_dir / "lifecycle.html").write_text("<html>lifecycle</html>", encoding="utf-8")
            (static_dir / "control-flow.html").write_text("<html>legacy control flow</html>", encoding="utf-8")
            body = workflow_html(manager)
            self.assertIn("Scientific Workflow", body)
            self.assertEqual(body.count("<iframe"), 1)
            self.assertIn("/artifacts/workflow.html", body)
            self.assertNotIn("/artifacts/control-flow.html", body)
            self.assertNotIn("/artifacts/lifecycle.html", body)

    def test_current_flow_page_renders_the_current_mermaid_source(self):
        body = current_flow_html()
        self.assertIn('class="mermaid"', body)
        self.assertIn("cdn.jsdelivr.net/npm/mermaid@11", body)
        self.assertIn("flowchart TD", body)
        self.assertIn("Research Lead&lt;br/&gt;frontier_proposal", body)
        self.assertIn("optional lineage_anchor_node_id", body)
        self.assertNotIn("hypothesis_relation = new / continue", body)
        self.assertNotIn("H_REL", body)
        self.assertIn("只写一个 cycle criterion：C-1", body)
        self.assertIn("same-H 已裁决 scientific outcomes", body)
        self.assertIn("active_hypothesis", body)
        self.assertIn("scientific_access", body)
        self.assertIn("Scientific Model path", body)
        self.assertIn("Evidence Manifest path", body)
        self.assertIn("conclusions[] → Established", body)
        self.assertIn("open_questions[] → Unresolved", body)
        self.assertIn("previous_scientific_delta", body)
        self.assertIn("不注入 hypothesis_ref / C-1 / Evidence refs", body)
        self.assertIn("Task root = scientific read anchor", body)
        self.assertIn("Cycle workspace = write root", body)
        self.assertIn("空 Evidence Registry ≠ 输入缺失", body)
        self.assertIn("materialize Judge packet", body)
        self.assertIn("已 Lock 的 stable H id", body)
        self.assertIn("hypothesis_ref + cycle_id + criterion_id", body)
        self.assertNotIn("previous_investigation_delta（若有）", body)
        self.assertIn("criterion-bindable", body)
        self.assertIn("hypothesis_display", body)
        self.assertNotIn("reporter__canvas_maintenance", body)
        self.assertIn("NEXT_CTX --&gt; LEAD", body)
        self.assertNotIn("frontier_planning", body)
        self.assertNotIn("goal_gate", body)

    def test_stage_workflow_preview_uses_runtime_stage_contracts(self):
        inventory = stage_contract_inventory()
        self.assertIn(
            ("literature", "novelty_check"),
            {(role, stage) for role, stage, _ref, _fields in inventory},
        )
        self.assertIn("literature__novelty_check", stage_workflow_mermaid())
        self.assertEqual(len(inventory), len(STAGE_CONTEXT_FIELDS))
        self.assertEqual(len({(role, stage) for role, stage, _ref, _fields in inventory}), len(STAGE_CONTEXT_FIELDS))
        stage_pairs = {(role, stage) for role, stage, _ref, _fields in inventory}
        self.assertEqual(set(STAGE_CONTEXT_FIELDS), stage_pairs)
        self.assertEqual(set(SDK_OWNERSHIP_FIELDS), stage_pairs)
        self.assertEqual(
            STAGE_CONTEXT_FIELDS[("judge", "goal_completion")],
            ("research_plan_access", "agent_graph", "agent_graph_reuse"),
        )
        self.assertEqual(
            STAGE_CONTEXT_FIELDS[("research_lead", "frontier_proposal")],
            (
                "goal", "research_contract", "final_gate", "scientific_model_summary",
                "agent_graph", "agent_graph_reuse", "science_graph_tools", "running_hypotheses",
                "operator_guidance", "qualification_feedback", "lead_literature_result", "workspace_access",
            ),
        )
        self.assertEqual(
            STAGE_CONTEXT_FIELDS[("investigator", "investigate")],
            ("frozen_objective", "qualification_handoff", "previous_scientific_delta", "workspace_access"),
        )
        self.assertEqual(
            STAGE_CONTEXT_FIELDS[("literature", "landscape")],
            ("search_target", "scholarly_retrieval_root"),
        )
        self.assertEqual(
            STAGE_CONTEXT_FIELDS[("literature", "targeted_source")],
            ("search_target", "source_lock", "scholarly_retrieval_root"),
        )
        self.assertEqual(
            STAGE_CONTEXT_FIELDS[("literature", "novelty_check")],
            ("search_target", "scholarly_retrieval_root"),
        )
        self.assertEqual(
            STAGE_CONTEXT_FIELDS[("research_lead", "candidate_selection")],
            ("qualified_candidates",),
        )
        self.assertEqual(
            STAGE_CONTEXT_FIELDS[("research_lead", "selection_repair")],
            ("repair_options",),
        )
        self.assertEqual(
            STAGE_CONTEXT_FIELDS[("research_lead", "cycle_synthesis")],
            ("active_hypothesis", "judge_decision", "scientific_access"),
        )
        # Overview is the navigation-first default. Full view is the contract
        # detail mode where individual Agent/SDK field chips must appear.
        overview = stage_workflow_html()
        self.assertIn('aria-current="page">流程概览', overview)
        self.assertNotIn('id="change-requests"', overview)
        body = stage_workflow_html(display_mode="full")
        self.assertIn("<h1>Workflow Details</h1>", body)
        self.assertIn("workspace_access", body)
        notes = field_note_catalog()
        self.assertGreater(len(notes), 100)
        self.assertEqual(len(notes), len({item["key"] for item in notes}))
        self.assertTrue(all(item["description"].strip() for item in notes))
        self.assertTrue(all(any("一" <= ch <= "龥" for ch in item["default_description"]) for item in notes))
        note_by_key = {item["key"]: item for item in notes}
        evolution = note_by_key["research_lead::frontier_proposal::output::candidates[].evolution_operator"]
        self.assertEqual(evolution["field_kind"], "choice")
        self.assertEqual(evolution["choice_mode"], "enum")
        self.assertEqual(
            [item["value"] for item in evolution["choice_options"]],
            ["refine", "specialize", "generalize", "alternative", "combine", "derive", "counter_hypothesis"],
        )
        self.assertIn("细化", evolution["choice_options"][0]["description"])
        selected = note_by_key["research_lead::candidate_selection::output::selected_candidate_id"]
        self.assertEqual(selected["field_kind"], "choice")
        self.assertEqual(selected["choice_mode"], "dynamic")
        self.assertEqual(selected["choice_source"], "qualified_candidates[].id")
        free_answer = note_by_key["research_lead::frontier_proposal::output::candidates[].hypothesis"]
        self.assertEqual(free_answer["field_kind"], "free")
        for item in notes:
            if item["section"] == "output" and "|" in item.get("contract_hint", ""):
                self.assertEqual(item["field_kind"], "choice", item["key"])
        self.assertIn('id="field-note-popover"', body)
        self.assertIn("点击查看/编辑备注", body)
        self.assertIn("workflow-field-notes/update", body)
        self.assertIn("field-note-history-list", body)
        self.assertIn("人工修改", body)
        self.assertIn("机器自动更新", body)
        self.assertIn("token.addEventListener('click'", body)
        self.assertNotIn("token.addEventListener('dblclick'", body)
        self.assertNotIn("恢复默认说明", body)
        self.assertIn("field-token", body)
        self.assertIn("Current · live", body)
        self.assertIn("Workflow 版本", body)
        self.assertNotIn("独立预览页，不替换旧", body)
        self.assertNotIn("旧 Workflow", body)
        self.assertIn("research_lead__frontier_proposal", body)
        self.assertIn("challenger__candidate_review", body)
        self.assertIn("investigator__integrate_literature", body)
        self.assertIn("literature__novelty_check", body)
        self.assertIn("scholarly_retrieval_root", body)
        self.assertNotIn("scholarly_retrieval_access", body)
        self.assertIn("judge__terminal_assessment", body)
        self.assertIn("research_lead__hypothesis_display", body)
        self.assertNotIn("reporter__canvas_maintenance", body)
        self.assertIn("candidates[].hypothesis", body)
        self.assertIn("blocking_issues", body)
        self.assertIn("required_change", body)
        self.assertNotIn("theory-master/role/", body)
        self.assertIn("qualified_candidates", body)
        self.assertIn("selection_reason", body)
        self.assertIn("scientific_synthesis.conclusions[]", body)
        self.assertIn("scientific_synthesis.open_questions[]", body)
        self.assertIn("scientific_access", body)
        self.assertEqual(body.count("(From Research Plan)"), 4)
        self.assertIn("agent_graph", body)
        self.assertNotIn("scientific_synthesis.learned[]", body)
        self.assertNotIn("scientific_model_update.reason", body)
        self.assertIn("repair_options", body)
        self.assertNotIn("previous_selection", body)
        self.assertNotIn("repair_candidates", body)
        self.assertNotIn("why_blockers_resolved", body)
        self.assertIn("findings[].statement", body)
        self.assertNotIn("findings[].evidence_kind", body)
        self.assertNotIn("local_source → source_refs", body)
        # V2 Investigator submits concise findings; scope and limitations
        # remain in frozen objective/evidence, not duplicate Finding fields.
        self.assertNotIn("findings[].scope", body)
        self.assertNotIn("findings[].limitations", body)
        self.assertIn("findings[].provenance.source", body)
        self.assertIn("◌", body)
        self.assertIn("☑", body)
        self.assertIn("☑ 待选项目", body)
        self.assertIn("确定性字段 · 固定枚举", body)
        self.assertIn("自由回答字段", body)
        self.assertIn("field-note-choice-list", body)
        self.assertIn("frozen_objective", body)
        self.assertIn("previous_scientific_delta", body)
        self.assertIn("objection.requested_resolution", body)
        self.assertIn("objection.needs_external_scholarship", body)
        self.assertNotIn("objection.scientific_gap.kind", body)
        self.assertIn("C-1 binding + objection reason from rationale", body)
        self.assertNotIn("decision.title", body)
        self.assertIn("1 · Agent receives / Context", body)
        self.assertIn("2 · Agent must return", body)
        self.assertIn("3 · ◆ SDK critical controls (not Agent input/output)", body)
        self.assertIn("agent_graph", body)
        self.assertNotIn("canvas_access", body)
        self.assertIn("workspace_access", body)
        self.assertIn("scholarly_retrieval_root", body)
        self.assertNotIn("research_reading", body)
        self.assertNotIn("canvas_graph_overview", body)
        self.assertNotIn("current_node_neighborhood", body)
        self.assertNotIn("prior_cycle_summary", body)
        self.assertNotIn("existing-ID reference only", body)
        self.assertIn("existing hypothesis_id", body)
        self.assertIn("candidates[].id", body)
        self.assertIn("reviews[].candidate_id", body)
        self.assertIn("Evidence ID registration + criterion binding", body)
        self.assertIn("objection_id / executor_role / source_gap_id / Literature task / rework_plan", body)
        self.assertNotIn("Decision parent_id / evidence_refs integrity", body)
        self.assertIn("frontier authority remains outside Reporter", body)
        self.assertNotIn("Cycle scientific-state digest", body)
        self.assertIn("does not inject prior-cycle continuation science or a goal-gap planner", body)
        self.assertIn("PHASE_CONTRACTS", body)
        self.assertIn("prompt_stages.STAGE_CONTEXT_FIELDS", body)
        self.assertIn("其它普通 SDK bookkeeping 不显示", body)
        self.assertIn('data-view-action="height"', body)
        self.assertIn('data-view-action="fullscreen"', body)
        self.assertIn("stage-height-focus", body)
        self.assertIn("stage-fullscreen-fallback", body)
        self.assertIn("stage-zoom-space", body)
        self.assertIn("requestFullscreen", body)
        self.assertIn("zoomSpace.style.width", body)
        self.assertIn("zoomSpace.style.height", body)
        self.assertNotIn("transform-origin:top center", body)
        mermaid = stage_workflow_mermaid()
        self.assertIn('QUALIFY -- "all rejected" --> QUALIFY_RETRY', mermaid)
        # A resolved Lead repair gets exactly one bounded Challenger verification.
        # A confirmed repair may qualify; unresolved/cannot_repair must not lock.
        self.assertIn('research_lead__selection_repair -- "resolved + SDK validation passed" --> challenger__repair_verification', mermaid)
        self.assertIn('challenger__repair_verification -- "confirmed; eligible for Investigation" --> LOCK', mermaid)
        self.assertIn('challenger__repair_verification -- "unresolved blocker / fatal design flaw" --> QUALIFY_RETRY', mermaid)
        self.assertIn('research_lead__selection_repair -- "cannot_repair" --> QUALIFY_RETRY', mermaid)
        self.assertIn('QUALIFY_RETRY -- "proposal rounds remain (max 3)" --> research_lead__frontier_proposal', mermaid)
        self.assertIn('QUALIFY_RETRY -- "limit exhausted" --> STOP', mermaid)
        self.assertIn('research_lead__selection_repair -- "invalid contract / frozen-boundary validation failure" --> STOP', mermaid)
        self.assertIn("LOCK --> CANVAS_H", mermaid)
        self.assertIn('CANVAS_H -- "identity conflict: fail closed" --> STOP', mermaid)
        self.assertIn('SEMANTIC -- "accepted criterion-bound Evidence" --> judge__terminal_assessment', mermaid)
        self.assertIn("Task-root scientific read anchor + Cycle workspace write boundary", mermaid)
        self.assertIn("empty Evidence Registry ≠ missing task-local input", mermaid)
        self.assertIn('SEMANTIC -- "no adjudicable Evidence / true blocker" --> NONSCI', mermaid)
        self.assertIn("NONSCI --> OPERATIONAL_CLOSED", mermaid)
        self.assertNotIn("NONSCI --> CLOSED", mermaid)
        self.assertNotIn('INV_ROUTE -- "internal scientific gap"', mermaid)
        self.assertIn("remaining_gap.kind", mermaid)
        self.assertIn(
            'SEMANTIC -. "remaining_gap.kind theoretical / numerical / local_source_review: Cycle-internal next-executor = investigator, not next-Cycle science" .-> investigator__investigate',
            mermaid,
        )
        self.assertIn(
            'SEMANTIC -. "remaining_gap.kind literature_landscape: Cycle-internal next-executor = literature landscape, not next-Cycle science" .-> literature__landscape',
            mermaid,
        )
        self.assertIn(
            'SEMANTIC -. "remaining_gap.kind literature_targeted: Cycle-internal next-executor = literature targeted_source, not next-Cycle science" .-> literature__targeted_source',
            mermaid,
        )
        self.assertIn("not a next-Cycle scientific fact", mermaid)
        self.assertIn('literature__targeted_source -- "Judge-authorized scholarship" --> investigator__integrate_literature', mermaid)
        self.assertIn('REWORK_TARGET -- "external scholarship: targeted_source only" --> literature__targeted_source', mermaid)
        self.assertIn('JUDGE_ROUTE -- "rework denied / budget exhausted" --> FORCED_OPEN', mermaid)
        self.assertIn("MODEL_COMMIT --> research_lead__frontier_maintenance", mermaid)
        self.assertLess(
            mermaid.index("MODEL_COMMIT --> research_lead__frontier_maintenance"),
            mermaid.index("research_lead__frontier_maintenance --> reporter__report"),
        )
        self.assertLess(
            mermaid.index("research_lead__frontier_maintenance --> reporter__report"),
            mermaid.index("reporter__report --> CANVAS_CLOSE"),
        )
        self.assertLess(
            mermaid.index("reporter__report --> CANVAS_CLOSE"),
            mermaid.index("CANVAS_CLOSE --> CLOSED"),
        )
        self.assertIn('CANVAS_CLOSE["SDK / Canvas commit"]', mermaid)
        self.assertNotIn("H_STATE", mermaid)
        self.assertNotIn("remaining_goal_gap", mermaid)
        self.assertIn("remaining_gap.kind", mermaid)
        self.assertIn("frontier_maintenance", mermaid)
        self.assertIn('GOAL_TRIGGER -- "scientific verdict = supported" --> judge__goal_completion', mermaid)
        self.assertIn('GOAL_TRIGGER -. "otherwise" .-> NEXT_CYCLE', mermaid)
        self.assertIn('judge__goal_completion -- "passed" --> GOAL_DONE', mermaid)
        self.assertIn("goal_status=passed; Cycle remains OVER", mermaid)
        self.assertIn('judge__goal_completion -- "not_passed" --> NEXT_CYCLE', mermaid)
        self.assertIn("verdict + rationale only", mermaid)
        self.assertIn('LEAD_LIT_KIND -- "novelty_check"', mermaid)
        self.assertIn('LEAD_LIT_GUARD -- "already used → needs_attention" --> STOP', mermaid)
        self.assertNotIn("stage mode / cycle-end routing bookkeeping", body)
        for _role, _stage, _ref, fields in inventory:
            for field in fields:
                self.assertIn(field, mermaid)
        notes_by_key = {item["key"]: item for item in notes}
        self.assertIn("hypothesis", notes_by_key["investigator::investigate::input::frozen_objective"]["structure"])
        self.assertIn("hypotheses[]", notes_by_key["research_lead::frontier_proposal::input::agent_graph"]["structure"])
        details = {item["node"]: item for item in stage_detail_catalog()}
        self.assertTrue(details["judge__terminal_assessment"]["contract_rules"])
        self.assertTrue(details["investigator__investigate"]["role_guidance"])
        self.assertIn('id="stage-detail-popover"', body)
        self.assertIn("Contract 条件规则", body)
        self.assertIn("实际结构", body)
        self.assertIn("显示规则 / 维护说明", body)
        self.assertIn("◆", body)
        self.assertLess(
            body.index('<div class="stage-canvas-shell">'),
            body.index('<details class="stage-meta stage-policy" id="workflow-guide">'),
        )
        self.assertIn(".stage-toolbar { position:absolute; top:10px; left:10px;", body)
        self.assertNotIn('<div class="card stage-policy">', body)
        self.assertIn('class="stage-layout"', body)
        self.assertNotIn('id="change-requests"', body)
        self.assertNotIn('class="stage-splitter"', body)
        self.assertNotIn("stageSplitter.addEventListener('pointermove'", body)
        self.assertIn("findStageNode", body)
        self.assertNotIn("<code>mode</code>", body)
        self.assertIn("user-select:text", body)
        self.assertIn("event.target.closest('g.node')", body)
        self.assertIn("if (onNode && !spaceHeld) return", body)
        self.assertIn("event.code === 'Space'", body)
        self.assertIn("Select node text normally", body)
        current_flow = current_flow_html()
        self.assertIn("compact prior Cycle scientific state", current_flow)
        self.assertIn("Cycle scientific state digest", current_flow)
        self.assertIn("Established / Unresolved / Boundary", current_flow)
        self.assertIn("Cycle N OVER", current_flow)
        self.assertIn("goal_status = active | passed", current_flow)

    def test_compact_stage_workflow_preserves_scientific_edges_and_full_detail(self):
        detailed = stage_workflow_mermaid()
        compact = stage_workflow_overview_mermaid(detailed, stage_detail_catalog())
        import re
        def routing_lines(source):
            return [line for line in source.splitlines() if re.search(r'(-->|\.->)', line)]
        self.assertEqual(routing_lines(compact), routing_lines(detailed))
        self.assertLess(len(compact), len(detailed) // 2)
        self.assertNotIn('Stage goal', compact)
        self.assertIn('research_lead__frontier_proposal', compact)
        overview_page = stage_workflow_html()
        full_page = stage_workflow_html(display_mode="full")
        self.assertIn('aria-current="page">流程概览</a>', overview_page)
        self.assertIn('aria-current="page">字段详情</a>', full_page)
        self.assertIn('research_lead__frontier_proposal', full_page)
        self.assertIn('Agent must return', full_page)
        self.assertIn('field-note-popover', overview_page)
        self.assertIn('stage-detail-popover', overview_page)
        self.assertIn('stage-detail-popover', full_page)
        self.assertIn('data-zoom-action="fit"', overview_page)
        self.assertIn('renderZoom(false)', overview_page)
        self.assertEqual(overview_page.count('class="stage-canvas"'), 1)
        # A retired three-column layout must not leave 2 empty columns.
        self.assertIn('.stage-layout { display:block; width:100%; min-width:0; }', overview_page)
        self.assertNotIn('href="/change-requests/"', overview_page)
        with self.assertRaises(ValueError):
            stage_workflow_html(display_mode="invalid")

    def test_workflow_history_is_frozen_and_read_only(self):
        snapshots = load_workflow_snapshots()
        self.assertTrue(snapshots)
        snapshot = next(item for item in snapshots if item["version"] == "2026-10-07-pre-audit-fix")
        self.assertIn('INV_ROUTE -- "ready" --> judge__terminal_assessment', snapshot["mermaid"])
        self.assertIn("NONSCI --> CLOSED", snapshot["mermaid"])
        self.assertNotIn("OPERATIONAL_CLOSED", snapshot["mermaid"])
        historical = stage_workflow_html(workflow_version="2026-10-07-pre-audit-fix")
        self.assertIn("历史只读", historical)
        self.assertIn("workflowReadOnly = true", historical)
        self.assertIn("2026-10-07 · 审计修正前", historical)
        self.assertIn("NONSCI --&gt; CLOSED", historical)
        self.assertNotIn("NONSCI --&gt; OPERATIONAL_CLOSED", historical)
        with self.assertRaises(ValueError):
            stage_workflow_html(workflow_version="missing-history-version")

    def test_stage_workflow_guide_documents_display_boundary(self):
        body = stage_workflow_guide_html()
        self.assertIn("Agent receives / Context", body)
        self.assertIn("Agent must return", body)
        self.assertIn("◆ SDK critical controls", body)
        self.assertIn("mode", body)
        self.assertIn("不能列在 Agent Input", body)
        self.assertIn("返回 Stage Preview", body)

    def test_workflow_field_notes_persist_edits_with_timestamp_and_reject_unknown_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_dir = root / "observer-state"
            make_task(root, "field-note-task", status="STOPPED", execution_active=False, master_id=12)
            cache = StateCache(root, ttl_s=0.2)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                make_handler(cache, field_notes_state_dir=state_dir),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            key = "research_lead::frontier_proposal::input::goal"
            try:
                with urlopen(base + "/workflow-field-notes.json", timeout=3) as response:
                    initial = json.loads(response.read())
                initial_item = next(item for item in initial["items"] if item["key"] == key)
                self.assertIn("总研究目标", initial_item["description"])
                self.assertTrue(initial_item["edited_at"])
                self.assertEqual(initial_item["actor"], "machine")
                self.assertFalse(initial_item["is_override"])
                self.assertEqual(len(initial_item["history"]), 1)
                self.assertEqual(initial_item["history"][0]["actor"], "machine")

                edited_text = "这是人工编辑后的 Goal 说明，用于测试持久化。"
                req = Request(
                    base + "/workflow-field-notes/update",
                    data=json.dumps({"key": key, "description": edited_text}, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req, timeout=3) as response:
                    saved = json.loads(response.read())
                self.assertTrue(saved["ok"])
                self.assertEqual(saved["item"]["description"], edited_text)
                self.assertTrue(saved["item"]["edited_at"])
                self.assertTrue(saved["item"]["is_override"])
                self.assertEqual(saved["item"]["actor"], "human")
                self.assertEqual([entry["actor"] for entry in saved["item"]["history"]], ["machine", "human"])

                edited_text_2 = "这是第二次人工修改，用于确认完整历史不会丢失。"
                req2 = Request(
                    base + "/workflow-field-notes/update",
                    data=json.dumps({"key": key, "description": edited_text_2}, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req2, timeout=3) as response:
                    saved2 = json.loads(response.read())
                self.assertEqual(saved2["item"]["description"], edited_text_2)
                self.assertEqual([entry["actor"] for entry in saved2["item"]["history"]], ["machine", "human", "human"])

                with urlopen(base + "/workflow-stage-preview/", timeout=3) as response:
                    page = response.read().decode("utf-8")
                self.assertIn(edited_text_2, page)

                bad = Request(
                    base + "/workflow-field-notes/update",
                    data=json.dumps({"key": "made-up::field", "description": "不应写入"}, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(bad, timeout=3)
                self.assertEqual(ctx.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

            cache2 = StateCache(root, ttl_s=0.2)
            server2 = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                make_handler(cache2, field_notes_state_dir=state_dir),
            )
            thread2 = threading.Thread(target=server2.serve_forever, daemon=True)
            thread2.start()
            base2 = f"http://127.0.0.1:{server2.server_address[1]}"
            try:
                with urlopen(base2 + "/workflow-field-notes.json", timeout=3) as response:
                    reloaded = json.loads(response.read())
                reloaded_item = next(item for item in reloaded["items"] if item["key"] == key)
                self.assertEqual(reloaded_item["description"], edited_text_2)
                self.assertTrue(reloaded_item["edited_at"])
                self.assertEqual([entry["actor"] for entry in reloaded_item["history"]], ["machine", "human", "human"])
            finally:
                server2.shutdown()
                server2.server_close()
                thread2.join(timeout=3)

    def test_get_pages_and_json_are_read_only_and_post_is_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = make_task(root, "web-task", status="RUNNING", pid=os.getpid(), execution_active=True, master_id=9)
            before = tree_signature(task)
            cache = StateCache(root, ttl_s=0.2)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                class NoRedirect(HTTPRedirectHandler):
                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        return None

                no_redirect = build_opener(NoRedirect)
                with self.assertRaises(HTTPError) as ctx:
                    no_redirect.open(base + "/", timeout=3)
                self.assertEqual(ctx.exception.code, 303)
                live_id = cache.get()["runs"][0]["observer_id"]
                self.assertEqual(ctx.exception.headers.get("Location"), f"/run/{live_id}/")
                with urlopen(base + "/workflow-stage-preview/", timeout=3) as response:
                    stage_preview = response.read().decode("utf-8")
                self.assertIn('<nav class="tm-nav"', stage_preview)
                nav = stage_preview.split('<nav class="tm-nav"', 1)[1].split('</nav>', 1)[0]
                self.assertEqual(nav.count('class="tm-nav-link"'), 8)
                for route in ("/run/", "/", "/research-plan/", "/workflow-stage-preview/", "/architecture/", "/services/", "/settings/"):
                    self.assertIn(f'href="{route}"', nav)
                self.assertIn("Task Console", nav)
                self.assertNotIn("Codex Usage", nav)
                self.assertNotIn("Change Requests", nav)
                self.assertIn('aria-current="page"', nav)
                with urlopen(base + "/run/", timeout=3) as response:
                    run_index = response.read().decode("utf-8")
                self.assertIn('class="tm-run-task-link"', run_index)
                # Keep the row keyboard/click navigable while preserving the
                # explicit task-name link; the row handler ignores nested controls.
                self.assertIn('class="tm-run-clickable"', run_index)
                self.assertIn('data-task-url="/run/', run_index)
                # /run/ has no redundant action column; row and task-name link
                # are two access paths to the same task detail page.
                self.assertNotIn('class="tm-run-open"', run_index)
                self.assertNotIn('<th>操作</th>', run_index)
                self.assertIn('web-task', run_index)
                self.assertIn('Master 9', run_index)
                self.assertIn('任务清单', run_index)
                self.assertIn('href="/run/', run_index)
                task_id = cache.get()["runs"][0]["observer_id"]
                with urlopen(base + "/run/" + task_id + "/", timeout=3) as response:
                    run_detail = response.read().decode("utf-8")
                self.assertIn('Task monitoring', run_detail)
                self.assertIn('class="tm-tui-screen"', run_detail)
                self.assertIn('Task ID 9', run_detail)
                self.assertIn('Explore map', run_detail)
                self.assertIn('当前任务暂未提供可阅读的科研画布', run_detail)
                self.assertNotIn('/api/canvas-md/', run_detail)
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(base + "/run/" + "f" * 16 + "/", timeout=3)
                self.assertEqual(ctx.exception.code, 404)
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(base + "/api/canvas-md/tasks/" + task_id + "/", timeout=3)
                self.assertEqual(ctx.exception.code, 404)
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(Request(base + "/run/" + task_id + "/", data=b"x", method="POST"), timeout=3)
                self.assertEqual(ctx.exception.code, 405)
                self.assertIn('<h1>Workflow</h1>', stage_preview)
                self.assertNotIn("TheoryMaster Workflow · Stage Preview", stage_preview)
                self.assertIn('aria-label="Workflow 显示模式"', stage_preview)
                self.assertIn('流程概览', stage_preview)
                self.assertIn('字段详情', stage_preview)
                self.assertNotIn('class="tm-workflow-diagnostics"', stage_preview)
                self.assertLess(stage_preview.index('class="stage-view-toggle"'), stage_preview.index('class="stage-canvas"'))
                self.assertIn('class="stage-legend-details"', stage_preview)
                self.assertIn('&lt;strong&gt;frontier_proposal&lt;/strong&gt;', stage_preview)
                self.assertNotIn('&lt;b&gt;Stage goal&lt;/b&gt;', stage_preview)
                with urlopen(base + "/workflow-stage-preview/?view=full", timeout=3) as response:
                    full_workflow = response.read().decode("utf-8")
                self.assertIn('&lt;b&gt;Stage goal&lt;/b&gt;', full_workflow)
                self.assertIn('Agent receives / Context', full_workflow)
                self.assertIn('&lt;strong&gt;frontier_proposal&lt;/strong&gt;', full_workflow)
                self.assertIn("research_lead__frontier_proposal", stage_preview)
                self.assertIn("research_lead__hypothesis_display", stage_preview)
                self.assertNotIn("reporter__canvas_maintenance", stage_preview)
                self.assertIn("Agent receives / Context", full_workflow)
                self.assertIn("Agent must return", full_workflow)
                self.assertIn("◆ SDK critical controls", full_workflow)
                self.assertIn('id="workflow-guide"', stage_preview)
                self.assertNotIn('id="change-requests"', stage_preview)
                self.assertNotIn('class="stage-splitter"', stage_preview)
                self.assertNotIn('href="/change-requests/"', stage_preview)
                self.assertIn("const dark = true;", stage_preview)
                with urlopen(base + "/architecture/", timeout=3) as response:
                    architecture = response.read().decode("utf-8")
                self.assertIn('aria-current="page"', architecture)
                self.assertIn("Agent Workflow Architecture", architecture)
                self.assertNotIn("Static view unavailable", architecture)
                with urlopen(base + "/services/", timeout=3) as response:
                    services = response.read().decode("utf-8")
                for removed in ("Observer details", "Architecture view", "Discovered tasks", "Git revision", "Last observation", "Snapshot updated", "Last checked:", "Used: ", "<h2>Quota</h2>"):
                    self.assertNotIn(removed, services)
                self.assertIn("updateUsageAge()", services)
                self.assertIn('id="usage-updated"', services)
                self.assertIn("setInterval(updateUsageAge, 1000)", services)
                self.assertIn('class="tm-refresh-button"', services)
                self.assertIn('aria-label="立即刷新 Codex 额度"', services)
                self.assertEqual(services.count('id="usage-updated"'), 1)
                self.assertEqual(services.count('onclick="loadUsage({force:true})"'), 1)
                self.assertLess(services.index('id="usage-updated"'), services.index("id='usage'"))
                self.assertLess(services.index('class="tm-update-status"'), services.index("id='usage'"))
                self.assertIn("Codex Usage", services)
                self.assertNotIn("Active account", services)
                self.assertNotIn("Use Codex 1", services)
                self.assertNotIn("Use Codex 2", services)
                self.assertIn("tm-active-indicator", services)
                self.assertIn("切换到此账号", services)
                self.assertIn("id='usage'", services)
                self.assertIn("id='switch-message'", services)
                self.assertIn("id='reset-message'", services)
                self.assertIn("resetConfirmationCount(snapshot)", services)
                self.assertIn("window.confirm", services)
                self.assertIn("/codex-usage.json", services)
                self.assertIn("/services-selection/switch", services)
                self.assertIn("/codex-account/reset-credit", services)
                self.assertNotIn('href="/codex-usage/"', services)
                for removed in (
                    "/runs/", "/runs/9/", "/codex-usage/", "/change-requests/",
                    "/changes/", "/current-flow/", "/workflow-stage-preview-guide/",
                ):
                    with self.subTest(removed=removed):
                        with self.assertRaises(HTTPError) as ctx:
                            urlopen(base + removed, timeout=3)
                        self.assertEqual(ctx.exception.code, 404)
                with urlopen(base + "/observer_state.json", timeout=3) as response:
                    payload = json.loads(response.read())
                self.assertEqual(payload["runs"][0]["run_id"], 9)
                with urlopen(base + "/healthz", timeout=3) as response:
                    health = json.loads(response.read())
                self.assertTrue(health["ok"])
                self.assertEqual(health["task_count"], 1)
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(Request(base + "/", data=b"x", method="POST"), timeout=3)
                self.assertEqual(ctx.exception.code, 405)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
            self.assertEqual(before, tree_signature(task))


if __name__ == "__main__":
    unittest.main()
