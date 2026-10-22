from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .archify_views import archify_cli, default_archify_root, node_executable

ROLE_LABELS = {
    "research_lead": "Research Lead",
    "challenger": "Challenger",
    "theorist": "Theorist",
    "numerical": "Numerical",
    "literature": "Literature",
    "judge": "Judge",
    "reporter": "Reporter",
    "semantic_closure": "Semantic Closure",
}
ROLE_TYPES = {
    "challenger": "security",
    "judge": "security",
    "semantic_closure": "messagebus",
}

SEQUENCE_PROJECTION_VERSION = 2


@dataclass
class SequenceBuildResult:
    ok: bool
    rebuilt: bool
    observer_id: str
    output_path: Path
    source_path: Path | None
    warning: str | None = None


def _safe_id(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    return value or "unknown"


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return events
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def _event_signature(path: Path) -> dict[str, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _event_field(event: dict[str, Any], key: str) -> Any:
    if key in event:
        return event.get(key)
    return _event_payload(event).get(key)


def _event_kind(event: dict[str, Any]) -> str:
    raw = str(event.get("event") or event.get("type") or "")
    aliases = {
        "role.started": "AGENT_STARTED",
        "role.completed": "AGENT_COMPLETED",
        "role.failed": "AGENT_FAILED",
    }
    return aliases.get(raw, raw.upper().replace(".", "_"))


def _event_role(event: dict[str, Any]) -> str:
    return str(_event_field(event, "role") or _event_field(event, "agent") or "").strip()


def _sequence_source(cycle_dir: Path) -> Path | None:
    # events.jsonl is the canonical workflow ledger. runtime_events.jsonl is an
    # observability projection retained as a compatibility fallback for older runs.
    for name in ("events.jsonl", "runtime_events.jsonl"):
        candidate = cycle_dir / name
        if candidate.is_file():
            return candidate
    return None


def _thread_mode(event: dict[str, Any], thread_actions: dict[str, str]) -> str:
    if _event_field(event, "resume_thread_id"):
        return "resume"
    thread_id = str(_event_field(event, "thread_id") or "")
    action = thread_actions.get(thread_id) or ""
    if action == "resume":
        return "resume"
    if action == "start":
        return "fresh"
    return "thread"


def _completion_details(output: Any) -> list[str]:
    if not isinstance(output, dict):
        return []
    details: list[str] = []
    if "ready_for_judge" in output:
        details.append(f"ready={str(bool(output.get('ready_for_judge'))).lower()}")
    next_cap = output.get("requested_next_capability") or output.get("next_capability")
    if next_cap:
        details.append(f"next={next_cap}")
    verdict = output.get("verdict")
    if isinstance(verdict, str) and verdict:
        details.append(f"verdict={verdict}")
    return details


def run_sequence_ir(run: dict[str, Any], events: list[dict[str, Any]], source_path: Path) -> dict[str, Any]:
    roles: list[str] = []
    seen: set[str] = set()
    start_totals: dict[str, int] = {}
    for event in events:
        if _event_kind(event) != "AGENT_STARTED":
            continue
        role = _event_role(event)
        if role == "semantic_closure":
            continue
        if role:
            start_totals[role] = start_totals.get(role, 0) + 1
        if role and role not in seen:
            seen.add(role)
            roles.append(role)

    participants: list[dict[str, Any]] = [
        {"id": "artifacts", "type": "database", "label": "Artifacts", "sublabel": "runtime files"},
        {"id": "sdk", "type": "backend", "label": "TM SDK", "sublabel": "controller"},
    ]
    role_ids: dict[str, str] = {}
    for role in roles:
        pid = f"role-{_safe_id(role)}"
        role_ids[role] = pid
        participants.append(
            {
                "id": pid,
                "type": ROLE_TYPES.get(role, "backend"),
                "label": ("Closure" if role == "semantic_closure" else ROLE_LABELS.get(role, role.replace("_", " ").title())),
                "sublabel": "role" if role != "semantic_closure" else "classifier",
            }
        )
    thread_actions: dict[str, str] = {}
    latest_tokens: dict[str, int] = {}
    invocation_stage: dict[str, str] = {}
    active_stage: dict[str, str] = {}
    call_index: dict[str, int] = {}
    active_call: dict[str, int] = {}
    messages: list[dict[str, Any]] = []
    y = 175

    def add(frm: str, to: str, label: str, variant: str = "default") -> None:
        nonlocal y
        messages.append({"from": frm, "to": to, "y": y, "label": label[:72], "variant": variant})
        y += 44

    add("sdk", "artifacts", f"read {source_path.name}", "dashed")

    for event in events:
        kind = _event_kind(event)
        role = _event_role(event)
        participant = role_ids.get(role, "sdk")

        if kind == "THREAD_CONFIG":
            thread_id = str(_event_field(event, "thread_id") or "")
            if thread_id:
                thread_actions[thread_id] = str(_event_field(event, "thread_action") or "")
            continue

        if kind == "TOKEN_USAGE" and role:
            value = _event_field(event, "tokens_total")
            if isinstance(value, (int, float)):
                latest_tokens[role] = int(value)
            continue

        if "COMPACTION" in kind.upper() and role in role_ids:
            label = kind.lower().replace("_", " ")
            add("sdk", participant, label, "dashed")
            continue

        if kind == "AGENT_STARTED" and role:
            stage = str(_event_field(event, "stage") or "agent turn")
            invocation_id = str(_event_field(event, "invocation_id") or "")
            if invocation_id:
                invocation_stage[invocation_id] = stage
            active_stage[role] = stage
            call_index[role] = call_index.get(role, 0) + 1
            active_call[role] = call_index[role]
            mode = _thread_mode(event, thread_actions)
            attempt = _event_field(event, "investigation_attempt")
            round_count = _event_field(event, "investigation_round_count")
            suffix = [mode]
            if start_totals.get(role, 0) > 1:
                suffix.append(f"call {active_call[role]}/{start_totals[role]}")
            if isinstance(attempt, int) and attempt > 0:
                suffix.append(f"A{attempt}")
            if isinstance(round_count, int) and round_count > 0:
                suffix.append(f"R{round_count}")
            add("sdk", participant, f"{stage} · {' · '.join(suffix)}", "emphasis")
            latest_tokens.pop(role, None)
            continue

        if kind in {"AGENT_COMPLETED", "AGENT_FAILED", "INVESTIGATION_ROUND_COMPLETED", "JUDGE_COMPLETED"} and role:
            stage = str(_event_field(event, "stage") or active_stage.get(role) or "agent turn")
            failed = kind == "AGENT_FAILED"
            pieces = [f"{'failed' if failed else 'done'} {stage}"]
            if role in latest_tokens:
                pieces.append(f"tok={latest_tokens[role]}")
            pieces.extend(_completion_details(_event_field(event, "output")))
            add(participant, "sdk", " · ".join(pieces), "security" if failed else "return")
            continue

        if kind in {"RUN_COMPLETED", "RUN_FAILED", "RUN_INTERRUPTED", "QUALIFICATION_NEEDS_ATTENTION", "WORKFLOW_STOPPED"}:
            add("sdk", "artifacts", kind.lower().replace("_", " "), "security" if "FAILED" in kind or "ATTENTION" in kind else "return")

    if len(messages) == 1:
        add("sdk", "artifacts", "no agent lifecycle events found", "dashed")

    return {
        "schema_version": 1,
        "diagram_type": "sequence",
        "meta": {
            "title": f"Sciver Run Sequence — {run.get('task_name') or run.get('observer_id')}",
            "output": "sequence.html",
            "animation": "trace",
            "visual_preset": "signal-flow",
            "quality_profile": "showcase",
            "column_fit": "spread",
        },
        "participants": participants,
        "messages": messages,
        "cards": [
            {
                "dot": "cyan",
                "title": "Source",
                "items": [
                    f"Cycle: {run.get('cycle') or 'unknown'}",
                    f"{source_path.name}: {source_path}",
                    "Full scientific prompts/messages are intentionally not copied into this view.",
                ],
            },
            {
                "dot": "emerald",
                "title": "Thread semantics",
                "items": [
                    "fresh/resume is shown only from persisted THREAD_CONFIG / resume_thread_id evidence",
                    "token labels use persisted TOKEN_USAGE values observed inside that role episode",
                    "compaction appears only when an explicit compaction event exists",
                ],
            },
        ],
    }


def build_run_sequence(
    run: dict[str, Any],
    state_dir: Path,
    *,
    archify_root: Path | None = None,
    force: bool = False,
) -> SequenceBuildResult:
    observer_id = str(run.get("observer_id") or "")
    if not observer_id:
        return SequenceBuildResult(False, False, "", state_dir / "runs" / "unknown" / "sequence.html", None, "run has no observer_id")

    cycle_dir = run.get("cycle_dir")
    source = _sequence_source(Path(str(cycle_dir))) if cycle_dir else None
    out_dir = state_dir.expanduser().resolve() / "runs" / observer_id
    output = out_dir / "sequence.html"
    marker = out_dir / "sequence-build.json"
    ir_path = out_dir / "sequence.json"

    if source is None or not source.is_file():
        return SequenceBuildResult(False, False, observer_id, output, source, "events.jsonl/runtime_events.jsonl not found")

    signature = _event_signature(source)
    old: dict[str, Any] = {}
    try:
        old = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    if (
        not force
        and old.get("projection_version") == SEQUENCE_PROJECTION_VERSION
        and old.get("event_signature") == signature
        and output.is_file()
        and ir_path.is_file()
    ):
        return SequenceBuildResult(True, False, observer_id, output, source)

    root = (archify_root or default_archify_root()).expanduser().resolve()
    cli = archify_cli(root)
    if not cli.is_file():
        return SequenceBuildResult(False, False, observer_id, output, source, f"Archify CLI not found: {cli}")

    events = _read_events(source)
    ir = run_sequence_ir(run, events, source)
    out_dir.mkdir(parents=True, exist_ok=True)
    ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    subprocess.run(
        [node_executable(), str(cli), "validate", "sequence", str(ir_path), "--quality", "showcase"],
        check=True,
    )
    subprocess.run(
        [node_executable(), str(cli), "render", "sequence", str(ir_path), str(output), "--quality", "showcase"],
        check=True,
    )
    marker.write_text(
        json.dumps(
            {
                "schema": "tm-observer/run-sequence-build/v2",
                "projection_version": SEQUENCE_PROJECTION_VERSION,
                "observer_id": observer_id,
                "cycle": run.get("cycle"),
                "source": str(source),
                "event_signature": signature,
                "built_at": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return SequenceBuildResult(True, True, observer_id, output, source)
