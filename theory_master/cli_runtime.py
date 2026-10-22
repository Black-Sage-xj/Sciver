from __future__ import annotations

"""CLI runtime operations, TUI attachment, smoke checks, and projections."""

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .core import CanonicalFakeBackend, CodexBackend, DEFAULT_ROUTES, FakeBackend, TaskSpec, Workflow, latest_cycle_dir, write_json
from .identity import ROLES, identity_block
from .load_snapshots import create_load_snapshot
from .scientific_model import load as load_scientific_model
from .skills import role_skills
from .task import bootstrap

def validate_roles(task_config_path: Path | None = None) -> dict[str, list[str]]:
    """Fail fast on a missing identity file or broken system/task skill.

    Without this the first unreachable role aborts mid-run, and Reporter is last: a typo there
    would only surface after every other role (and its HPC work) has already been paid for.
    """
    report: dict[str, list[str]] = {}
    for role in ROLES:
        identity_block(role)
        report[role] = [ref.directory for ref in role_skills(role, task_config_path=task_config_path)]
    return report


def literature_startup_check(routes=None) -> dict[str, object]:
    """Opt-in live startup check for the Literature skill; never runs by default."""
    from .skills import role_skills, skills_root

    names = [ref.directory for ref in role_skills("literature")]
    if "tm-scholar" not in names:
        raise RuntimeError("Literature is missing tm-scholar")
    skill_scripts = skills_root() / "tm-scholar" / "scripts"
    script = skill_scripts / "lit_retrieve.py"
    converter = skill_scripts / "pdf_to_markdown.py"
    if not script.is_file():
        raise RuntimeError(f"Literature retrieval script missing: {script}")
    if not converter.is_file():
        raise RuntimeError(f"Literature PDF-to-Markdown interface missing: {converter}")
    marker_bin = Path((os.environ.get("SCHOLARLY_MARKER_BIN") or str(Path.home() / "miniconda3" / "envs" / "marker_env" / "bin" / "marker_single"))).expanduser()
    if not marker_bin.is_file():
        raise RuntimeError(f"Literature Marker converter missing: {marker_bin}")
    root = Path(tempfile.mkdtemp(prefix="theory-master-literature-check-"))
    output = root / "retrieval"
    command = [sys.executable, str(script), "--query", "polymer network topology spatial embedding", "--sources", "openalex", "--limit", "1", "--output-dir", str(output), "--report-style", "brief"]
    env = os.environ.copy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    proc = subprocess.run(command, text=True, capture_output=True, timeout=60, env=env, check=False)
    papers_path = output / "papers.json"
    papers = []
    if papers_path.is_file():
        try:
            papers = json.loads(papers_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            papers = []
    if proc.returncode != 0 or not papers:
        detail = (proc.stderr or proc.stdout or "no paper returned").strip()
        raise RuntimeError(f"Literature startup check failed: {detail[:800]}")
    marker_probe = subprocess.run([str(marker_bin), "--help"], text=True, capture_output=True, timeout=30, check=False)
    if marker_probe.returncode != 0:
        detail = (marker_probe.stderr or marker_probe.stdout or "marker_single --help failed").strip()
        raise RuntimeError(f"Literature PDF-to-Markdown startup check failed: {detail[:800]}")
    first = papers[0] if isinstance(papers[0], dict) else {}
    return {"status": "ok", "skills": names, "retrieved": len(papers), "source": first.get("source") or first.get("sources"), "sample_title": first.get("title"), "pdf_to_markdown": "marker-ready", "marker_bin": str(marker_bin), "artifacts": str(output)}


def smoke(live: bool) -> int:
    root = Path(tempfile.mkdtemp(prefix="theory-master-smoke-"))
    plan = root / "research_plan.md"
    plan.write_text("# Smoke\n\n## Goal\n\nExercise isolated workflow.\n\n## Research Contract\n\nNo scientific claim or external file access.\n\n## Required Context\n\n- TM-SMOKE\n", encoding="utf-8")
    binding = bootstrap(plan)
    spec = TaskSpec.from_config(binding.config_path)
    backend = CodexBackend(root, routes=DEFAULT_ROUTES) if live else CanonicalFakeBackend()
    state = Workflow(spec, backend, DEFAULT_ROUTES).run()
    if state["branches"][-1]["status"] != "supported":
        raise RuntimeError("smoke verdict failed")
    print(root)
    return 0


def _env_flag(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _collector(spec: TaskSpec, *, cycle_audit: bool | None = None, error_repair: bool | None = None, attention_takeover: bool | None = None):
    from theory_master.observability.collector import Collector
    if cycle_audit is None:
        cycle_audit = _env_flag("THEORY_MASTER_CYCLE_AUDIT", default=False)
    if error_repair is None:
        error_repair = _env_flag("THEORY_MASTER_ERROR_REPAIR", default=True)
    if attention_takeover is None:
        from .attention_takeover import attention_takeover_enabled
        attention_takeover = attention_takeover_enabled(spec.research_root)
    return Collector(
        spec,
        system_failure_actions=bool(error_repair),
        email_notifications=True,
        cycle_audit_actions=bool(cycle_audit),
        attention_takeover_actions=bool(attention_takeover),
    )


def _schedule_close_current_tmux_window() -> bool:
    """Remove only the tmux window that hosted the failed live TUI."""
    pane = (os.environ.get("TMUX_PANE") or "").strip()
    if not pane:
        return False
    probe = subprocess.run(
        ["tmux", "display-message", "-p", "-t", pane, "#{window_id}"],
        text=True,
        capture_output=True,
        check=False,
    )
    window_id = (probe.stdout or "").strip()
    if probe.returncode != 0 or not window_id.startswith("@"):
        return False
    subprocess.Popen(
        ["sh", "-c", "sleep 0.25; tmux kill-window -t \"$1\"", "sh", window_id],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True


def run_with_tui(workflow: Workflow, collector, operation=None) -> int:
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            (operation or workflow.run)()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker, name="theory-master-workflow", daemon=False)
    thread.start()
    from theory_master.tui.app import launch_tui
    launch_tui(collector, workflow=workflow, close_on_repair_handoff=True, interrupt_on_exit=True)
    thread.join()
    if errors:
        handoff = collector.snapshot().get("repair_handoff") or {}
        if handoff.get("status") == "started":
            _schedule_close_current_tmux_window()
        cycle = latest_cycle_dir(workflow.spec.research_root, task_mode=workflow.spec.task_mode)
        if cycle is not None:
            if workflow.spec.task_mode:
                print(f"TheoryMaster stopped. Resume with: TheoryMaster resume {workflow.spec.master_id}")
            else:
                print(f"TheoryMaster stopped. Resume with: theory-master resume {cycle.name}")
        raise errors[0]
    print("over")
    return 0


def print_status(spec: TaskSpec) -> int:
    from theory_master.observability.collector import FileSource
    from theory_master.tui.render import _fmt_cost
    state = FileSource(spec.research_root).snapshot()
    print(
        f"{state.get('run_id') or '-'}  run={state.get('status') or '-'}  "
        f"cycle={state.get('cycle_index') or 0}/{state.get('max_cycles') or 1}  "
        f"phase={state.get('phase') or '-'}  agent={state.get('current_agent') or '-'}  "
        f"cost={_fmt_cost(state.get('cost'))}"
    )
    return 0


def print_inspect(spec: TaskSpec) -> int:
    from theory_master.observability.collector import FileSource
    from theory_master.tui.render import (
        render_activity,
        render_challenges,
        render_header,
        render_hypothesis,
        render_investigation,
        render_judge,
        render_last,
        render_thread,
        render_workflow,
    )
    source = FileSource(spec.research_root)
    state = source.snapshot()
    print(render_header(state))
    print()
    print(render_hypothesis(state, full=True))
    print()
    print(render_workflow(state))
    print()
    print(render_investigation(state))
    print()
    print(render_activity(state))
    print()
    print(render_last(state))
    print()
    print(render_challenges(state))
    print()
    print(render_judge(state))
    print()
    print(render_thread(state))
    return 0


def attach_tui(spec: TaskSpec) -> int:
    """Attach the TUI to the task's real workflow control plane.

    Display state is read from the persisted run projection, while control
    requests are sent to the same task/Cycle through Workflow.request_control.
    The standalone TUI is therefore a controller, not a read-only observer.
    """
    from theory_master.observability.collector import FileSource
    from theory_master.tui.app import launch_tui

    create_load_snapshot(spec, reason="tui")
    source = FileSource(spec.research_root)
    state = source.snapshot()
    if not state.get("run_id") and latest_cycle_dir(spec.research_root, task_mode=spec.task_mode) is None:
        raise ValueError("no TheoryMaster workflow exists to attach TUI")
    control_workflow = Workflow(spec, FakeBackend())
    launch_tui(source, running_workflow=True, workflow=control_workflow)
    return 0


def project_control(spec: TaskSpec) -> int:
    """Refresh the Canvas projection from committed SQLite state without replaying pending writes."""
    create_load_snapshot(spec, reason="project")
    cycle = latest_cycle_dir(spec.research_root, task_mode=spec.task_mode)
    if cycle is None:
        raise ValueError("no cycle directory under research_root")
    state_path = cycle / "research_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    projection_state = dict(state)

    # Projection-only runs must not replay a pending Cycle Outcome command.
    # Rebuild visual projections without reading obsolete global scientific pointers.
    projection_state.pop("frontier_state", None)
    projection_state["next_start_node_id"] = None

    workflow = Workflow(spec, FakeBackend())
    workflow._bind_cycle(cycle, create=False)
    model = load_scientific_model(spec.research_root, goal_id=spec.goal_id)
    projection_state["scientific_model_current_state"] = workflow._model_current_state(
        model, projection_state.get("current_state", "")
    )
    projection_state["current_state"] = (
        projection_state["scientific_model_current_state"]
        or projection_state.get("current_state", "")
    )
    workflow._render_canvas(projection_state)

    # Persist only derived projection metadata/current-model text. Do not
    # rewrite the Cycle historical semantic patch or its scientific frontier.
    state["scientific_model_current_state"] = projection_state["scientific_model_current_state"]
    state["current_state"] = projection_state["current_state"]
    state["canvas_projection"] = projection_state.get("canvas_projection", state.get("canvas_projection", {}))
    write_json(state_path, state)
    print("projected")
    return 0
