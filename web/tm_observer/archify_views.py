from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ARCHIFY_VERSION = "v3.0.1"
ARCHIFY_REPO = "https://github.com/tt-a1i/archify.git"

CURRENT_AGENT_STAGES: dict[str, tuple[str, ...]] = {
    "research_lead": (
        "frontier_proposal",
        "hypothesis_display",
        "candidate_selection",
        "selection_repair",
        "cycle_synthesis",
        "frontier_maintenance",
    ),
    "challenger": ("candidate_review", "repair_verification"),
    "investigator": ("investigate", "preserve_research", "integrate_literature", "judge_rework"),
    "literature": ("landscape", "targeted_source", "novelty_check"),
    "judge": ("terminal_assessment", "goal_completion"),
    "reporter": ("report",),
}

# STAGE_CARDS is the current runtime surface. Historical compatibility stages
# live outside this catalog and must not be rendered as active workflow stages.
HISTORICAL_COMPAT_STAGES: set[tuple[str, str]] = set()


def _stage_pairs_from_source(repo_root: Path) -> set[tuple[str, str]] | None:
    """Read Stage Card keys without importing TheoryMaster into Observatory."""
    path = next(
        (candidate for candidate in (
            repo_root / "theory_master" / "stage_registry.py",
            repo_root / "tool" / "theory_master" / "theory_master" / "stage_registry.py",
        ) if candidate.is_file()),
        repo_root / "theory_master" / "stage_registry.py",
    )
    if not path.is_file():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(target, ast.Name) and target.id == "STAGE_CARDS" and isinstance(node.value, ast.Dict):
            pairs: set[tuple[str, str]] = set()
            for key in node.value.keys:
                try:
                    value = ast.literal_eval(key)
                except (ValueError, TypeError):
                    continue
                if isinstance(value, tuple) and len(value) == 2:
                    pairs.add((str(value[0]), str(value[1])))
            return pairs
    return None


def _validate_workflow_stage_catalog(repo_root: Path) -> None:
    source = _stage_pairs_from_source(repo_root)
    if source is None:
        return
    expected = {
        (role, stage)
        for role, stages in CURRENT_AGENT_STAGES.items()
        for stage in stages
    } | HISTORICAL_COMPAT_STAGES
    if source != expected:
        missing = sorted(source - expected)
        stale = sorted(expected - source)
        raise RuntimeError(
            "Sciver workflow stage catalog drifted from runtime source: "
            f"unmapped_source_stages={missing}; stale_observer_stages={stale}"
        )


def default_state_dir() -> Path:
    return Path(os.environ.get("TM_OBSERVER_STATE_DIR", "~/.local/state/tm-observer")).expanduser()


def default_archify_root() -> Path:
    configured = os.environ.get("TM_OBSERVER_ARCHIFY_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(f"~/.local/share/tm-observer/archify-{ARCHIFY_VERSION}/archify").expanduser()


def archify_cli(root: Path) -> Path:
    return root / "bin" / "archify.mjs"


def node_executable() -> str:
    configured = (os.environ.get("TM_OBSERVER_NODE") or "").strip()
    if configured:
        return str(Path(configured).expanduser())
    return shutil.which("node") or "node"


def bootstrap_archify(target_root: Path | None = None) -> Path:
    """Install the pinned Archify release outside the TheoryMaster repository."""
    root = (target_root or default_archify_root()).expanduser().resolve()
    cli = archify_cli(root)
    if cli.is_file():
        return root

    checkout = root.parent
    checkout.parent.mkdir(parents=True, exist_ok=True)
    if checkout.exists():
        shutil.rmtree(checkout)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", ARCHIFY_VERSION, ARCHIFY_REPO, str(checkout)],
        check=True,
    )
    if not cli.is_file():
        raise RuntimeError(f"Archify checkout is missing CLI: {cli}")
    subprocess.run([node_executable(), str(cli), "doctor"], check=True)
    return root


def _meta(title: str, output: str, *, preset: str | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "title": title,
        "output": output,
        "quality_profile": "showcase",
        "animation": "trace",
    }
    if preset:
        meta["visual_preset"] = preset
    return meta


def architecture_ir(repo_root: Path) -> dict[str, Any]:
    """Master / Worker architecture backed by current runtime and Web controls."""
    return {
        "schema_version": 1,
        "diagram_type": "architecture",
        "meta": _meta("Sciver · Master / Multi-Worker Architecture", "architecture.html"),
        "components": [
            {"id": "operator", "type": "external", "label": "Human Operator", "sublabel": "Goal / Contract / Final Gate", "pos": [25, 370], "size": [170, 75]},
            {"id": "observer", "type": "frontend", "label": "Sciver Web", "sublabel": "Task Console / Observatory", "pos": [205, 125], "size": [170, 75], "tag": "monitor + controls"},
            {"id": "cli", "type": "frontend", "label": "CLI / TUI", "sublabel": "operator commands", "pos": [205, 565], "size": [170, 75]},
            {"id": "control", "type": "security", "label": "Operator Control", "sublabel": "operator_control.py", "pos": [425, 125], "size": [175, 75], "tag": "auth + safety checks"},
            {"id": "scheduler", "type": "backend", "label": "Worker Scheduler", "sublabel": "worker_runtime.py", "pos": [645, 125], "size": [185, 75], "tag": "dynamic allocation"},
            {"id": "claims", "type": "security", "label": "Branch Claims", "sublabel": "multi_worker.py", "pos": [865, 125], "size": [185, 75], "tag": "lease / fencing"},
            {"id": "worker", "type": "backend", "label": "Worker 1 ... N", "sublabel": "private Cycle + CODEX_HOME", "pos": [865, 365], "size": [185, 75], "tag": "one branch owner"},
            {"id": "runtime", "type": "backend", "label": "Legacy Runner", "sublabel": "runtime_manager.py", "pos": [425, 565], "size": [175, 75], "tag": "exclusive fallback"},
            {"id": "workflow", "type": "backend", "label": "Six-Agent Workflow", "sublabel": "workflow.py · per Worker", "pos": [1085, 365], "size": [175, 75]},
            {"id": "qualification", "type": "backend", "label": "Qualification", "sublabel": "Lead / Challenger", "pos": [1305, 125], "size": [175, 75]},
            {"id": "investigation", "type": "backend", "label": "Investigation", "sublabel": "Investigator / Literature", "pos": [1305, 365], "size": [175, 75]},
            {"id": "judge", "type": "security", "label": "Branch Judge", "sublabel": "frozen decisive_test", "pos": [1305, 565], "size": [175, 75], "tag": "independent verdict"},
            {"id": "reporter", "type": "backend", "label": "Reporter", "sublabel": "Cycle closeout / Canvas", "pos": [1515, 565], "size": [180, 75]},
            {"id": "science", "type": "database", "label": "Scientific Model", "sublabel": "shared synthesis / CAS", "pos": [1305, 790], "size": [175, 75]},
            {"id": "cycle", "type": "database", "label": "Cycle Evidence", "sublabel": "run_state / events / findings", "pos": [1515, 365], "size": [180, 75], "tag": "persisted per Worker"},
            {"id": "obs", "type": "messagebus", "label": "Telemetry", "sublabel": "runtime_events / Worker registry", "pos": [645, 565], "size": [185, 75]},
            {"id": "plan", "type": "database", "label": "Research Plan", "sublabel": "Human-owned Goal / Gate", "pos": [1085, 790], "size": [175, 75]},
            {"id": "canvas", "type": "database", "label": "Shared Canvas", "sublabel": "H / Decision / Finding", "pos": [865, 790], "size": [185, 75], "tag": "fenced commit"},
            {"id": "goal_gate", "type": "security", "label": "Master Goal Gate", "sublabel": "single locked completion", "pos": [1515, 790], "size": [180, 75], "tag": "OVER ≠ COMPLETE"},
        ],
        "boundaries": [
            {"kind": "region", "label": "Sciver runtime / shared scientific state", "wraps": [
                "control", "scheduler", "claims", "worker", "runtime", "workflow",
                "qualification", "investigation", "judge", "reporter", "science",
                "cycle", "obs", "plan", "canvas", "goal_gate",
            ]},
        ],
        "connections": [
            {"id": "operator-web", "from": "operator", "to": "observer"},
            {"id": "operator-cli", "from": "operator", "to": "cli"},
            {"id": "web-control", "from": "observer", "to": "control", "variant": "emphasis"},
            {"id": "control-scheduler", "from": "control", "to": "scheduler"},
            {"id": "cli-scheduler", "from": "cli", "to": "scheduler"},
            {"id": "cli-runtime", "from": "cli", "to": "runtime"},
            {"id": "runtime-workflow", "from": "runtime", "to": "workflow", "variant": "dashed"},
            {"id": "scheduler-claims", "from": "scheduler", "to": "claims", "variant": "emphasis"},
            {"id": "scheduler-worker", "from": "scheduler", "to": "worker"},
            {"id": "claims-worker", "from": "claims", "to": "worker", "variant": "emphasis"},
            {"id": "worker-workflow", "from": "worker", "to": "workflow", "variant": "emphasis"},
            {"id": "canvas-claims", "from": "canvas", "to": "claims"},
            {"id": "canvas-workflow", "from": "canvas", "to": "workflow"},
            {"id": "plan-workflow", "from": "plan", "to": "workflow"},
            {"id": "workflow-qualification", "from": "workflow", "to": "qualification"},
            {"id": "qualification-investigation", "from": "qualification", "to": "investigation"},
            {"id": "investigation-judge", "from": "investigation", "to": "judge", "variant": "emphasis"},
            {"id": "judge-investigation", "from": "judge", "to": "investigation", "variant": "dashed"},
            {"id": "judge-reporter", "from": "judge", "to": "reporter"},
            {"id": "judge-goal", "from": "judge", "to": "goal_gate", "variant": "dashed"},
            {"id": "reporter-canvas", "from": "reporter", "to": "canvas"},
            {"id": "reporter-science", "from": "reporter", "to": "science", "variant": "dashed"},
            {"id": "workflow-cycle", "from": "workflow", "to": "cycle"},
            {"id": "workflow-obs", "from": "workflow", "to": "obs", "variant": "dashed"},
            {"id": "cycle-observer", "from": "cycle", "to": "observer", "variant": "dashed"},
            {"id": "obs-observer", "from": "obs", "to": "observer", "variant": "dashed"},
        ],
        "cards": [
            {"dot": "cyan", "title": "One Master · multiple independent Workers", "items": [
                "One Canvas, Research Plan, Scientific Model and Master Goal Gate",
                "Each Worker repeats Lead / Challenger / Investigator / Literature / Judge / Reporter",
                "Concurrency requires independent active frontiers and verified resource slots",
            ]},
            {"dot": "emerald", "title": "SDK concurrency / scientific invariants", "items": [
                "Branch claim, fencing and canvas_write.lock; globally unique Cycle / H IDs",
                "Per-Worker state and account-pinned Codex; Scientific Model CAS",
                "Branch supported is not Master COMPLETE; Goal Gate uses one global lock",
            ]},
            {"dot": "amber", "title": "Operator and rollout boundary", "items": [
                "Sciver Web reads persisted evidence and sends authenticated guarded actions",
                "Legacy Runner and multi-Worker execution cannot own one Master together",
                "Real multi-Worker science audit pending; automatic Supervisor repair / quota switching remains disabled",
                f"Source: {repo_root.name} · worker_runtime.py / multi_worker.py",
            ]},
        ],
    }


def workflow_ir(repo_root: Path | None = None) -> dict[str, Any]:
    """Per-Worker scientific workflow, including conditional preservation stages."""
    if repo_root is not None:
        _validate_workflow_stage_catalog(repo_root.resolve())

    return {
        "schema_version": 2,
        "diagram_type": "workflow",
        "meta": _meta("Sciver Scientific Workflow", "workflow.html", preset="signal-flow"),
        "lanes": [
            {"id": "research_lead", "label": "Research Lead"},
            {"id": "challenger", "label": "Challenger"},
            {"id": "sdk", "label": "SDK / deterministic layer"},
            {"id": "investigator", "label": "Investigator"},
            {"id": "literature", "label": "Literature"},
            {"id": "judge", "label": "Judge"},
            {"id": "reporter", "label": "Reporter"},
        ],
        "phases": [
            {"id": "qualification", "label": "Qualification", "fromCol": 0, "toCol": 1},
            {"id": "investigation", "label": "Investigation", "fromCol": 2, "toCol": 4, "variant": "emphasis"},
            {"id": "post_judge", "label": "Post-Judge Closeout", "fromCol": 5, "toCol": 5, "variant": "dashed"},
        ],
        "groups": [
            {
                "id": "qualification_repair",
                "label": "Challenger revise → Lead selection_repair → Challenger review",
                "lane": "research_lead",
                "fromCol": 0,
                "toCol": 1,
                "variant": "dashed",
            },
            {
                "id": "specialist_routing",
                "label": "one locked H · context-selected specialist turns",
                "lane": "sdk",
                "fromCol": 2,
                "toCol": 4,
                "variant": "emphasis",
            },
            {
                "id": "post_judge_order",
                "label": "P1 → P7 static closeout order",
                "lane": "sdk",
                "fromCol": 5,
                "toCol": 5,
                "variant": "dashed",
            },
        ],
        "mainPath": [
            "lead_frontier_proposal",
            "challenger_candidate_review",
            "lead_candidate_selection",
            "sdk_hypothesis_lock",
            "sdk_investigation_router",
            "sdk_closure_readiness",
            "judge_terminal_assessment",
            "lead_post_judge",
        ],
        "nodes": [
            {
                "id": "lead_frontier_proposal",
                "lane": "research_lead",
                "col": 0,
                "type": "backend",
                "label": "Q1 frontier_proposal",
                "width": 180,
            },
            {
                "id": "challenger_candidate_review",
                "lane": "challenger",
                "col": 1,
                "type": "security",
                "label": "Q2 candidate_review",
                "width": 180,
            },
            {
                "id": "lead_candidate_selection",
                "lane": "research_lead",
                "col": 1,
                "type": "backend",
                "label": "Q3 candidate_selection",
                "sublabel": "selection_repair only on revise",
                "width": 190,
                "yOffset": 52,
            },
            {
                "id": "sdk_hypothesis_lock",
                "lane": "sdk",
                "col": 1,
                "type": "security",
                "label": "Q4 Hypothesis Lock",
                "width": 155,
            },
            {
                "id": "lead_hypothesis_display",
                "lane": "research_lead",
                "col": 2,
                "type": "backend",
                "label": "Q5 hypothesis_display",
                "sublabel": "optional copy for locked H",
                "width": 200,
            },
            {
                "id": "sdk_investigation_router",
                "lane": "sdk",
                "col": 2,
                "type": "messagebus",
                "label": "I1 Investigation Router",
                "width": 175,
                "yOffset": 52,
            },
            {
                "id": "investigator_investigation",
                "lane": "investigator",
                "col": 3,
                "type": "backend",
                "label": "I2 Investigator",
                "sublabel": "theory · numerical · local source",
                "width": 220,
            },
            {
                "id": "investigator_preserve",
                "lane": "investigator",
                "col": 4,
                "type": "database",
                "label": "Stop: preserve_research",
                "sublabel": "A findings + B display; no Judge",
                "width": 215,
                "yOffset": 55,
            },
            {
                "id": "literature_investigation",
                "lane": "literature",
                "col": 3,
                "type": "backend",
                "label": "I2 Literature",
                "sublabel": "external scholarly gap",
                "width": 155,
            },
            {
                "id": "sdk_closure_readiness",
                "lane": "sdk",
                "col": 4,
                "type": "database",
                "label": "I3–I4 Closure + Readiness",
                "sublabel": "Semantic Closure · Evidence · continue/ready",
                "width": 225,
            },
            {
                "id": "judge_terminal_assessment",
                "lane": "judge",
                "col": 4,
                "type": "security",
                "label": "J1 terminal_assessment",
                "sublabel": "terminal verdict or authorized rework",
                "width": 205,
            },
            {
                "id": "lead_post_judge",
                "lane": "research_lead",
                "col": 5,
                "type": "backend",
                "label": "P1 synthesis · P4 frontier?",
                "width": 195,
            },
            {
                "id": "lead_frontier_maintenance",
                "lane": "research_lead",
                "col": 5,
                "type": "backend",
                "label": "P4 frontier_maintenance",
                "sublabel": "active / parked / closed tips",
                "width": 205,
                "yOffset": 105,
            },
            {
                "id": "judge_goal_completion",
                "lane": "judge",
                "col": 5,
                "type": "security",
                "label": "P3 goal_completion",
                "width": 140,
            },
            {
                "id": "reporter_closeout",
                "lane": "reporter",
                "col": 5,
                "type": "backend",
                "label": "P5 report · P6 canvas_maintenance",
                "width": 230,
            },
            {
                "id": "sdk_preserve_closeout",
                "lane": "sdk",
                "col": 5,
                "type": "database",
                "label": "Preserve: SDK closeout",
                "sublabel": "Canvas archive → Cycle OVER",
                "width": 195,
                "yOffset": 105,
            },
            {
                "id": "sdk_post_judge",
                "lane": "sdk",
                "col": 5,
                "type": "database",
                "label": "P2 Model Commit · P7 Projection",
                "width": 220,
            },
        ],
        "edges": [
            {"id": "proposal-review", "from": "lead_frontier_proposal", "to": "challenger_candidate_review", "variant": "emphasis"},
            {"id": "review-selection", "from": "challenger_candidate_review", "to": "lead_candidate_selection", "variant": "emphasis"},
            {"id": "selection-lock", "from": "lead_candidate_selection", "to": "sdk_hypothesis_lock", "variant": "emphasis"},
            {"id": "lock-router", "from": "sdk_hypothesis_lock", "to": "sdk_investigation_router", "variant": "emphasis"},
            {"id": "display-optional", "from": "sdk_hypothesis_lock", "to": "lead_hypothesis_display", "variant": "dashed", "role": "branch"},
            {"id": "display-router", "from": "lead_hypothesis_display", "to": "sdk_investigation_router", "variant": "dashed", "role": "branch"},
            {"id": "router-investigator", "from": "sdk_investigation_router", "to": "investigator_investigation", "variant": "emphasis", "role": "branch"},
            {"id": "router-literature", "from": "sdk_investigation_router", "to": "literature_investigation", "variant": "dashed", "role": "branch"},
            {"id": "router-closure", "from": "sdk_investigation_router", "to": "sdk_closure_readiness", "variant": "emphasis"},
            {"id": "closure-judge", "from": "sdk_closure_readiness", "to": "judge_terminal_assessment", "variant": "emphasis"},
            {"id": "judge-closeout", "from": "judge_terminal_assessment", "to": "lead_post_judge", "variant": "emphasis"},
            {"id": "synthesis-frontiers", "from": "lead_post_judge", "to": "lead_frontier_maintenance", "variant": "dashed", "role": "branch"},
            {"id": "preserve-option", "from": "investigator_investigation", "to": "investigator_preserve", "variant": "dashed", "role": "branch"},
            {"id": "preserve-sdk", "from": "investigator_preserve", "to": "sdk_preserve_closeout", "variant": "dashed", "role": "branch"},
        ],
        "cards": [
            {"dot": "cyan", "title": "One workflow, many branch-owned Workers", "items": [
                "Each Worker follows the six-Agent Cycle with its own frozen Hypothesis and decisive_test",
                "Master Canvas, Research Plan, Scientific Model and final Goal Gate remain shared",
            ]},
            {"dot": "amber", "title": "Conditional stages", "items": [
                "hypothesis_display only formats the frozen H; frontier_maintenance labels existing tips after verdict",
                "Supervisor-authorized preserve_research archives existing findings and returns via SDK to OVER",
                "Preservation does not call Judge, Literature, Lead or Reporter downstream",
            ]},
        ],
    }


def lifecycle_ir() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "diagram_type": "lifecycle",
        "meta": {
            **_meta("Sciver Cycle Lifecycle", "lifecycle.html"),
            "viewBox": [1030, 630],
        },
        "lanes": [
            {"id": "main", "label": "Cycle main path"},
            {"id": "waiting", "label": "Qualification repair"},
            {"id": "exceptions", "label": "Judge rework"},
            {"id": "terminal", "label": "Safe stops"},
        ],
        "states": [
            {"id": "initialized", "type": "start", "label": "Initialized", "sublabel": "Cycle created", "lane": "main", "col": 0, "step": "01"},
            {"id": "qualification", "type": "active", "label": "Qualification", "sublabel": "select + lock", "lane": "main", "col": 1, "step": "02"},
            {"id": "investigation", "type": "active", "label": "Investigation", "sublabel": "locked-hypothesis evidence", "lane": "main", "col": 2, "step": "03"},
            {"id": "judging", "type": "decision", "label": "Judging", "sublabel": "independent verdict", "lane": "main", "col": 3, "step": "04"},
            {"id": "over", "type": "success", "label": "Over", "sublabel": "Cycle closed; Goal separate", "lane": "main", "col": 4, "step": "05"},
            {"id": "qual_repair", "type": "waiting", "label": "Qualification Repair", "sublabel": "bounded revision", "lane": "waiting", "col": 0, "tag": "bounded"},
            {"id": "judge_rework", "type": "active", "label": "Judge Rework", "sublabel": "authorized evidence work", "lane": "exceptions", "col": 2, "yOffset": 78, "tag": "bounded"},
            {"id": "attention", "type": "waiting", "label": "NEEDS_ATTENTION", "sublabel": "safe persisted stop", "lane": "terminal", "col": 0, "tag": "terminal"},
            {"id": "interrupted", "type": "failure", "label": "Interrupted / Failed", "sublabel": "recoverable persisted stop", "lane": "terminal", "col": 1, "tag": "terminal"},
        ],
        "transitions": [
            {"id": "init-qual", "from": "initialized", "to": "qualification", "variant": "emphasis"},
            {"id": "qual-investigation", "from": "qualification", "to": "investigation", "variant": "emphasis"},
            {"id": "investigation-judge", "from": "investigation", "to": "judging", "variant": "emphasis"},
            {"id": "judge-over", "from": "judging", "to": "over", "variant": "emphasis"},
            {"id": "preserve-over", "from": "investigation", "to": "over", "variant": "dashed"},
            {"id": "qual-repair", "from": "qualification", "to": "qual_repair", "variant": "dashed"},
            {"id": "repair-qual", "from": "qual_repair", "to": "qualification", "variant": "dashed"},
            {"id": "repair-attention", "from": "qual_repair", "to": "attention", "variant": "security"},
            {"id": "judge-rework", "from": "judging", "to": "judge_rework", "variant": "dashed"},
            {"id": "rework-investigation", "from": "judge_rework", "to": "investigation", "variant": "dashed"},
        ],
        "cards": [
            {"dot": "emerald", "title": "Main Cycle contract", "items": [
                "Qualification must finish before locked-hypothesis Investigation",
                "Judge terminal verdict closes through Reporter/Lead synthesis",
                "A new Main Hypothesis belongs to a new Cycle",
            ]},
            {"dot": "rose", "title": "Bounded exits and rework", "items": [
                "Qualification repair may end in persisted NEEDS_ATTENTION",
                "Judge rework returns to the same locked-hypothesis Investigation",
                "Interrupted/failed execution remains a visible recoverable terminal state",
            ]},
        ],
    }

def write_ir_set(output_dir: Path, repo_root: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        "architecture": architecture_ir(repo_root),
        "workflow": workflow_ir(repo_root),
        "lifecycle": lifecycle_ir(),
    }
    paths: dict[str, Path] = {}
    for kind, payload in payloads.items():
        path = output_dir / f"{kind}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths[kind] = path
    return paths


def _git_head(repo_root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


@dataclass
class StaticBuildResult:
    ok: bool
    rebuilt: bool
    head: str | None
    output_dir: Path
    warning: str | None = None


@dataclass
class ArchitectureDeltaResult:
    ok: bool
    rebuilt: bool
    base_head: str | None
    head: str | None
    output_path: Path
    receipt_path: Path
    warning: str | None = None


def _snapshot_index_path(state_dir: Path) -> Path:
    return state_dir / "architecture-snapshots.json"


def _load_snapshot_index(state_dir: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(_snapshot_index_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = payload.get("snapshots") if isinstance(payload, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _record_architecture_snapshot(state_dir: Path, head: str | None, source: Path) -> Path | None:
    if not head or not source.is_file():
        return None
    snapshots = state_dir / "snapshots" / "architecture"
    snapshots.mkdir(parents=True, exist_ok=True)
    target = snapshots / f"{head}.json"
    if not target.is_file():
        shutil.copy2(source, target)
    index = _load_snapshot_index(state_dir)
    if not any(item.get("git_head") == head for item in index):
        index.append(
            {
                "git_head": head,
                "path": str(target),
                "recorded_at": time.time(),
            }
        )
        _snapshot_index_path(state_dir).write_text(
            json.dumps(
                {
                    "schema": "tm-observer/architecture-snapshots/v1",
                    "snapshots": index,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return target


def build_architecture_delta(
    repo_root: Path,
    state_dir: Path,
    *,
    archify_root: Path | None = None,
    force: bool = False,
) -> ArchitectureDeltaResult:
    repo_root = repo_root.resolve()
    state_dir = state_dir.expanduser().resolve()
    root = (archify_root or default_archify_root()).expanduser().resolve()
    cli = archify_cli(root)
    output_path = state_dir / "static" / "changes.html"
    receipt_path = state_dir / "static" / "changes.receipt.json"
    marker = state_dir / "delta-build.json"
    head = _git_head(repo_root)

    if not cli.is_file():
        return ArchitectureDeltaResult(
            False, False, None, head, output_path, receipt_path, f"Archify CLI not found: {cli}"
        )

    index = _load_snapshot_index(state_dir)
    current_positions = [i for i, item in enumerate(index) if item.get("git_head") == head]
    if not current_positions:
        return ArchitectureDeltaResult(
            True,
            False,
            None,
            head,
            output_path,
            receipt_path,
            "Current Architecture has no validated snapshot yet.",
        )
    current_index = current_positions[-1]
    base = None
    for item in reversed(index[:current_index]):
        if item.get("git_head") and item.get("git_head") != head:
            base = item
            break
    if base is None:
        return ArchitectureDeltaResult(
            True,
            False,
            None,
            head,
            output_path,
            receipt_path,
            "No previous validated Architecture snapshot yet.",
        )

    base_head = str(base["git_head"])
    base_path = Path(str(base["path"]))
    head_path = Path(str(index[current_index]["path"]))
    if not base_path.is_file() or not head_path.is_file():
        return ArchitectureDeltaResult(
            False,
            False,
            base_head,
            head,
            output_path,
            receipt_path,
            "Architecture snapshot file is missing.",
        )

    old = {}
    try:
        old = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    if (
        not force
        and old.get("base_head") == base_head
        and old.get("git_head") == head
        and output_path.is_file()
        and receipt_path.is_file()
    ):
        return ArchitectureDeltaResult(True, False, base_head, head, output_path, receipt_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            node_executable(),
            str(cli),
            "compare",
            "architecture",
            str(base_path),
            str(head_path),
            str(output_path),
            "--receipt",
            str(receipt_path),
            "--json",
            "--quality",
            "standard",
            "--repo-root",
            str(repo_root),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stdout or proc.stderr or "Archify compare failed").strip()
        try:
            parsed = json.loads(proc.stdout)
            detail = str(parsed.get("error") or detail)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
        return ArchitectureDeltaResult(
            False,
            False,
            base_head,
            head,
            output_path,
            receipt_path,
            detail[:2000],
        )
    marker.write_text(
        json.dumps(
            {
                "schema": "tm-observer/delta-build/v1",
                "base_head": base_head,
                "git_head": head,
                "archify_version": ARCHIFY_VERSION,
                "built_at": time.time(),
                "output": str(output_path),
                "receipt": str(receipt_path),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return ArchitectureDeltaResult(True, True, base_head, head, output_path, receipt_path)


def build_static_views(
    repo_root: Path,
    state_dir: Path,
    *,
    archify_root: Path | None = None,
    force: bool = False,
) -> StaticBuildResult:
    repo_root = repo_root.resolve()
    state_dir = state_dir.expanduser().resolve()
    root = (archify_root or default_archify_root()).expanduser().resolve()
    cli = archify_cli(root)
    output_dir = state_dir / "static"
    ir_dir = state_dir / "ir"
    marker = state_dir / "static-build.json"
    head = _git_head(repo_root)

    if not cli.is_file():
        return StaticBuildResult(False, False, head, output_dir, f"Archify CLI not found: {cli}")

    old = {}
    try:
        old = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    # Preserve the previous validated IR before writing the new HEAD projection.
    old_head = old.get("git_head")
    old_architecture = ir_dir / "architecture.json"
    if old_head and old_head != head and old_architecture.is_file():
        _record_architecture_snapshot(state_dir, str(old_head), old_architecture)
    # Parent Git HEAD can remain unchanged while the TheoryMaster submodule is
    # edited. Track the actual rendered projection so Web does not go stale.
    projection_hash = hashlib.sha256(json.dumps(
        [architecture_ir(repo_root), workflow_ir(repo_root), lifecycle_ir()],
        sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    expected = [
        output_dir / "architecture.html",
        output_dir / "workflow.html",
        output_dir / "lifecycle.html",
    ]
    if (not force and old.get("git_head") == head
            and old.get("projection_hash") == projection_hash
            and old.get("archify_version") == ARCHIFY_VERSION
            and all(path.is_file() for path in expected)):
        _record_architecture_snapshot(state_dir, head, old_architecture)
        return StaticBuildResult(True, False, head, output_dir)

    paths = write_ir_set(ir_dir, repo_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    for artifact_name, input_path in paths.items():
        output_path = output_dir / f"{artifact_name}.html"
        diagram_kind = artifact_name
        quality = "standard" if artifact_name == "workflow" else "showcase"
        subprocess.run(
            [node_executable(), str(cli), "validate", diagram_kind, str(input_path), "--quality", quality, "--repo-root", str(repo_root)],
            check=True,
        )
        subprocess.run(
            [node_executable(), str(cli), "render", diagram_kind, str(input_path), str(output_path), "--quality", quality, "--repo-root", str(repo_root)],
            check=True,
        )

    state_dir.mkdir(parents=True, exist_ok=True)
    _record_architecture_snapshot(state_dir, head, paths["architecture"])
    marker.write_text(
        json.dumps(
            {
                "schema": "tm-observer/static-build/v1",
                "git_head": head,
                "projection_hash": projection_hash,
                "archify_version": ARCHIFY_VERSION,
                "built_at": time.time(),
                "outputs": {kind: str(output_dir / f"{kind}.html") for kind in paths},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return StaticBuildResult(True, True, head, output_dir)


class StaticViewManager:
    """Background projection watcher; failures degrade only the static views."""

    def __init__(
        self,
        repo_root: Path,
        state_dir: Path,
        *,
        archify_root: Path | None = None,
        interval_s: float = 30.0,
    ):
        self.repo_root = repo_root.resolve()
        self.state_dir = state_dir.expanduser().resolve()
        self.archify_root = archify_root or default_archify_root()
        self.interval_s = max(5.0, float(interval_s))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_result: StaticBuildResult | None = None
        self.last_delta_result: ArchitectureDeltaResult | None = None

    def refresh(self, force: bool = False) -> StaticBuildResult:
        try:
            result = build_static_views(
                self.repo_root,
                self.state_dir,
                archify_root=self.archify_root,
                force=force,
            )
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            result = StaticBuildResult(
                False,
                False,
                _git_head(self.repo_root),
                self.state_dir / "static",
                f"{type(exc).__name__}: {exc}",
            )
        self.last_result = result
        if result.ok:
            try:
                self.last_delta_result = build_architecture_delta(
                    self.repo_root,
                    self.state_dir,
                    archify_root=self.archify_root,
                    force=force,
                )
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                self.last_delta_result = ArchitectureDeltaResult(
                    False,
                    False,
                    None,
                    _git_head(self.repo_root),
                    self.state_dir / "static" / "changes.html",
                    self.state_dir / "static" / "changes.receipt.json",
                    f"{type(exc).__name__}: {exc}",
                )
        return result

    def start(self) -> None:
        if self._thread is not None:
            return
        self.refresh()

        def loop() -> None:
            while not self._stop.wait(self.interval_s):
                self.refresh()

        self._thread = threading.Thread(target=loop, name="tm-observer-static-views", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_s + 1.0))
            self._thread = None
