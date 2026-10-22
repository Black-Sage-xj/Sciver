"""Offline TUI fixture. No Codex, no Workflow, no files on disk."""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from theory_master.observability.models import blank_run_state

_PARA = (
    "For a freely jointed chain the end-to-end vector is the sum of N independent "
    "bond vectors. Cross terms vanish in the ensemble average, so <R^2> = N b^2. "
    "Each paragraph below is filler so the hypothesis pane can be scrolled. "
)


def _para(n: int, tag: str) -> str:
    return "\n".join(f"{tag} P{i+1}. {_PARA}" for i in range(n))


def _event_history() -> list[str]:
    lines = [
        "09-20T23:40:01  RUN_STARTED",
        "09-20T23:40:01  CYCLE_STARTED",
        "09-20T23:40:02  AGENT_STARTED  research_lead",
        "09-20T23:40:18  TOOL_STARTED  Read exploration.canvas",
        "09-20T23:40:19  TOOL_COMPLETED  Read exploration.canvas",
        "09-20T23:40:33  TOOL_STARTED  Read research_plan.md",
        "09-20T23:40:34  TOOL_COMPLETED  Read research_plan.md",
        "09-20T23:41:10  AGENT_COMPLETED  research_lead",
        "09-20T23:41:11  AGENT_STARTED  challenger",
        "09-20T23:41:40  TOOL_STARTED  Read identities/challenger.md",
        "09-20T23:41:41  TOOL_COMPLETED  Read identities/challenger.md",
        "09-20T23:42:08  AGENT_REASONING  challenger  c1 excluded-volume",
        "09-20T23:42:40  AGENT_COMPLETED  challenger",
        "09-20T23:42:41  AGENT_STARTED  research_lead",
        "09-20T23:43:02  TOOL_STARTED  Read branch.canvas",
        "09-20T23:43:03  TOOL_COMPLETED  Read branch.canvas",
        "09-20T23:43:20  HYPOTHESIS_LOCKED  research_lead",
        "09-20T23:43:21  AGENT_STARTED  investigator",
        "09-20T23:43:40  TOOL_STARTED  Read workspace/notes.md",
        "09-20T23:43:41  TOOL_COMPLETED  Read workspace/notes.md",
        "09-20T23:44:00  TOOL_STARTED  Read",
        "09-20T23:44:02  TOOL_COMPLETED  Read",
        "09-20T23:44:03  FILE_CHANGED  workspace/sketch.md",
    ]
    notes = [
        "expanding <R^2> as a double sum over bond indices",
        "dropping i!=j cross terms by independence",
        "checking <b_i · b_j> = 0 for i != j",
        "writing <R^2> = N b^2 after the diagonal remains",
        "comparing to a Gaussian chain with the same N, b",
        "noting Kuhn length vs raw bond length",
        "sketching the characteristic function of one bond",
        "product of N identical factors in Fourier space",
        "second moment from the k^2 coefficient",
        "finite-N 1/N end correction still pending",
        "excluded volume deferred to a later cycle",
        "checking the 1D vs 3D prefactor  d=3",
        "writing the radius of gyration as Nb^2/6",
        "confirming phantom-network affine stretch later",
        "storing a sympy notebook path in artifacts",
        "re-reading the challenge c2 finite-N note",
        "drafting the H1 title for the branch canvas",
        "switching methods inside one Investigator episode when needed",
    ]
    minute, second = 44, 10
    for i, note in enumerate(notes, start=1):
        second += 7
        if second >= 60:
            minute += 1
            second -= 60
        lines.append(f"09-20T23:{minute:02d}:{second:02d}  AGENT_REASONING  investigator  {i:02d} {note}")
        second += 3
        if second >= 60:
            minute += 1
            second -= 60
        lines.append(f"09-20T23:{minute:02d}:{second:02d}  TOOL_STARTED  Read workspace/draft_{i:02d}.md")
        second += 1
        if second >= 60:
            minute += 1
            second -= 60
        lines.append(f"09-20T23:{minute:02d}:{second:02d}  TOOL_COMPLETED  Read workspace/draft_{i:02d}.md")
    lines.append("09-20T23:46:12  AGENT_REASONING  investigator")
    return lines


def _hypothesis_history() -> list[dict[str, str]]:
    drafts = [
        ("2026-09-20T23:40:08+08:00", "H0: R^2 scales as N^2", "Rod-like guess. All bonds aligned. This is the wrong baseline."),
        ("2026-09-20T23:41:22+08:00", "H0b: keep bond correlations", "Tried a nearest-neighbor <b_i · b_{i+1}> term. Overfits a freely jointed chain."),
        ("2026-09-20T23:42:15+08:00", "H0c: 1D random walk only", "Worked in 1D first. Missed the 3D factor that still yields Nb^2, not Nb^2/3, for the vector square."),
        ("2026-09-20T23:43:05+08:00", "H0d: include self-avoidance", "Edwards-like excluded volume. Out of contract for this cycle; parked."),
        ("2026-09-20T23:43:55+08:00", "H0e: generating-function route", "Characteristic function of one bond, product over N, extract k^2. Same Nb^2."),
        ("2026-09-20T23:44:40+08:00", "H0f: radius of gyration mix-up", "Wrote Nb^2/6 by accident. That is <R_g^2>, not <R^2>."),
        ("2026-09-20T23:45:18+08:00", "H0g: Kuhn remapping", "Replace b by Kuhn length b_K. Contract asked for the ideal FJC with raw b."),
        ("2026-09-20T23:45:50+08:00", "H0h: drop the cross terms", "Almost the current claim. Still had a leftover 2 sum_{i<j} that should be zero."),
    ]
    return [
        {"ts": ts, "title": title, "body": f"{body}\n{_para(4, title.split(':', 1)[0])}"}
        for ts, title, body in drafts
    ]


def demo_state(*, elapsed_s: float = 372.0) -> dict[str, Any]:
    history = _event_history()
    state = blank_run_state()
    state.update({
        "run_id": "09-20-23:40-tui-demo",
        "goal_id": "tui-demo",
        "status": "RUNNING",
        "execution_active": True,
        "cycle_index": 1,
        "max_cycles": 1,
        "max_rounds": 12,
        "phase": "investigator:running",
        "current_agent": "investigator",
        "current_thread_id": "thr_demo_investigator",
        "current_turn_id": "turn_demo_07",
        "current_tool": None,
        "current_activity": "Agent reasoning / generating response",
        "tokens_in": 41200,
        "tokens_out": 9800,
        "tokens_total": 51000,
        "elapsed_s": elapsed_s,
        "started_at": "2026-09-20T23:40:01+08:00",
        "last_event": history[-1],
        "hypothesis_title": "Independent bond vectors give <R^2> = N b^2",
        "hypothesis": (
            "Current claim: a freely jointed chain has <R^2> = N b^2 because "
            "cross terms vanish.\n"
            + _para(8, "H1")
        ),
        "hypothesis_history": _hypothesis_history(),
        "branch_id": "H1",
        "round_count": 2,
        "rework_count": 0,
        "investigation_attempt": 1,
        "investigation_round_count": 2,
        "judge_rework_count": 0,
        "total_investigation_round_count": 2,
        "last_verdict": None,
        "last_target": None,
        "last_missing": None,
        "literature_requested": False,
        "branch_canvas": "/tmp/theory-master-tui-demo/branch.canvas",
        "notes_dir": "/tmp/theory-master-tui-demo/notes",
        "research_root": "/tmp/theory-master-tui-demo",
        "run_dir": "/tmp/theory-master-tui-demo/09-20-23:40-tui-demo",
        "task_root": "/tmp/theory-master-tui-demo/09-20-23:40-tui-demo",
        "report_path": None,
        "artifacts": [
            "/tmp/theory-master-tui-demo/workspace/sketch.md",
        ],
        "qualification_status": "qualified",
        "qualification_rationale": "The candidate is falsifiable and survives independent review.",
        "hypothesis_locked": True,
        "hypothesis_lock": {"candidate_id": "H1"},
        "investigation_rounds": [
            {"round": 1, "executor_role": "investigator", "state": "done"},
            {"round": 2, "executor_role": "investigator", "state": "running"},
        ],
        "event_history": history,
    })
    agents = {
        "research_lead": ("done", "thr_demo_research_lead", 3, 8200, 80.0, "gpt-6-sol", "high", "Codex"),
        "challenger": ("done", "thr_demo_challenger", 1, 4100, 89.0, "gpt-6-luna", "medium", "Codex"),
        "investigator": ("running", "thr_demo_investigator", 2, 27100, 171.0, "gpt-6-luna", "medium", "Codex"),
        "literature": ("idle", None, 0, None, 0.0, "gpt-6-luna", "low", "Codex"),
        "judge": ("waiting", None, 0, None, 0.0, "gpt-6-sol", "medium", "Codex"),
        "reporter": ("idle", None, 0, None, 0.0, "gpt-6-luna", "low", "Codex"),
    }
    for role, (agent_state, thread_id, turns, tokens, elapsed, model, effort, provider) in agents.items():
        item = state["agents"][role]
        item.update({
            "state": agent_state,
            "thread_id": thread_id,
            "model": model,
            "reasoning_effort": effort,
            "provider": provider,
            "turn_count": turns,
            "turn_id": "turn_demo_07" if role == "investigator" else None,
            "tokens_total": tokens,
            "elapsed_s": elapsed,
            "activity": "Agent reasoning / generating response" if role == "investigator" else None,
        })
    return state


def demo_events() -> list[dict[str, Any]]:
    events = []
    for line in _event_history():
        stamp, _, rest = line.partition("  ")
        kind, _, extra = rest.partition("  ")
        mapped = {"AGENT_STARTED": "LLM_TURN_STARTED", "AGENT_COMPLETED": "LLM_TURN_COMPLETED",
                  "AGENT_REASONING": "LLM_MESSAGE"}.get(kind, kind or rest)
        item: dict[str, Any] = {"ts": f"2026-{stamp}+08:00", "event": mapped}
        if extra:
            if kind == "FILE_CHANGED":
                item["path"] = extra
            elif kind.startswith("TOOL"):
                item["tool"] = extra
            else:
                item["agent"] = extra.split()[0]
                if mapped == "LLM_MESSAGE":
                    item["content"] = extra
        events.append(item)
    return events


def demo_logs() -> list[str]:
    lines = []
    for line in _event_history():
        stamp, _, rest = line.partition("  ")
        lines.append(f"2026-{stamp}+08:00 [INFO] {rest}")
    lines.append("2026-09-20T23:46:12+08:00 [WARN] demo fixture: no live Codex session")
    return lines


class DemoSource:
    """In-memory TUI source. Elapsed ticks; last/hypothesis text stays fixed."""

    def __init__(self) -> None:
        self._base_elapsed = 372.0
        self._started = time.monotonic()
        self._state = demo_state(elapsed_s=self._base_elapsed)
        self._events = demo_events()
        self._logs = demo_logs()

    def snapshot(self) -> dict[str, Any]:
        state = deepcopy(self._state)
        elapsed = self._base_elapsed + (time.monotonic() - self._started)
        state["elapsed_s"] = elapsed
        investigator = state["agents"]["investigator"]
        investigator["elapsed_s"] = 171.0 + (time.monotonic() - self._started)
        return state

    def runtime_events(self, limit: int | None = 400) -> list[dict[str, Any]]:
        return list(self._events if limit is None or limit <= 0 else self._events[-limit:])

    def logs(self, limit: int = 200) -> list[str]:
        return list(self._logs[-limit:])


def launch_demo_tui() -> int:
    from theory_master.tui.app import launch_tui

    launch_tui(DemoSource(), running_workflow=False)
    return 0
