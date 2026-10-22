from __future__ import annotations

"""Workflow state binding, persistence, recovery controls, and Evidence projection plumbing."""

import json
import hashlib
import fcntl
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .cycle_criteria import criterion_rows

from .backend_config import RoleRoute
from .runtime import atomic, now, write_json
from .specification import CYCLE_MARKER, SCHEMA, cycle_number, latest_cycle_dir, make_run_id, master_run_id
from .workflow_support import RecoveryStop

class WorkflowStateMixin:
    @staticmethod
    def _replay_code_fingerprint() -> str:
        package_root = Path(__file__).resolve().parent
        rows = [(p.relative_to(package_root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
                for p in sorted(package_root.rglob("*"))
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc" and "build" not in p.parts]
        return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _copy_replay_tree(source: Path, destination: Path) -> None:
        if any(path.is_symlink() for path in source.rglob("*")):
            raise ValueError(f"replay snapshot contains symlinks and cannot be frozen safely: {source}")
        shutil.copytree(source, destination, dirs_exist_ok=True)

    def _write_replay_checkpoint(self, state: dict[str, Any], name: str) -> None:
        """Best-effort replay snapshot: never interrupt active scientific research.

        An incomplete optional snapshot is explicitly unavailable, not a valid
        replay checkpoint. Real Workflow state/Canvas remain authoritative.
        """
        try:
            WorkflowStateMixin._materialize_replay_checkpoint(self, state, name)
        except (OSError, ValueError) as exc:
            root = self.cycle_dir / "replay_checkpoints"
            root.mkdir(parents=True, exist_ok=True)
            write_json(root / f"{name}.unavailable.json", {
                "schema": "theory-master/replay-checkpoint-unavailable/v1",
                "reason": f"{type(exc).__name__}: {exc}",
                "boundary": name, "at": now(),
            })

    def _materialize_replay_checkpoint(self, state: dict[str, Any], name: str) -> None:
        """Record immutable, provenance-bound state at a real stage boundary."""
        import hashlib
        from .runtime import atomic
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        state_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        source_digests: dict[str, str] = {}
        spec = getattr(self, "spec", None)
        graph_path = None
        if spec is not None:
            graph_resolver = getattr(self, "_graph_database_path", None)
            if callable(graph_resolver):
                graph_path = graph_resolver()
            elif getattr(spec, "branch_canvas", None) is not None:
                graph_path = Path(spec.branch_canvas).resolve().parent / "science_graph.sqlite3"
        candidates = {
            "research_plan": getattr(spec, "document", None),
            "task_config": getattr(spec, "config_path", None),
            "branch_canvas": getattr(self, "branch_canvas", None),
            "science_graph": graph_path,
            "scientific_model_state": Path(self.spec.research_root) / "scientific_model_state.json" if spec is not None else None,
        }
        checkpoint_root = self.cycle_dir / "replay_checkpoints"
        # A historical boundary is immutable. Never destroy or silently replace it
        # during same-Cycle recovery, even if the in-memory state has evolved.
        if (checkpoint_root / f"{name}.json").exists():
            return
        snapshot_root = checkpoint_root / f"{name}.inputs"
        if snapshot_root.exists():
            raise ValueError("prior incomplete checkpoint inputs exist; refusing overwrite")
        # Do not copy arbitrarily large calculation outputs at every Cycle
        # boundary. Large benchmark inputs are referenced by hash, not copied.
        dirs = (
            (Path(spec.config_path).parent / ".theory-master" / "skills" if spec and spec.config_path else None),
            getattr(spec, "notes_mirror_dir", None),
            getattr(self, "cycle_workspace", None) if name != "cycle_start" else None,
        )
        total = sum(c.stat().st_size for c in candidates.values() if c is not None and Path(c).is_file())
        for directory in dirs:
            if directory is None or not Path(directory).is_dir():
                continue
            for child in Path(directory).rglob("*"):
                if child.is_symlink():
                    raise ValueError(f"checkpoint contains symlink: {child}")
                if child.is_file():
                    total += child.stat().st_size
        limit = 512 * 1024 * 1024
        free = shutil.disk_usage(self.cycle_dir).free
        if total > limit or total + (2 * 1024**3) > free:
            raise ValueError(f"checkpoint needs {total} bytes but size/free-space safety cap forbids copying")
        snapshot_root.mkdir(parents=True, exist_ok=False)
        for label, candidate in candidates.items():
            if candidate is None:
                continue
            source = Path(candidate)
            if source.is_file():
                target = snapshot_root / f"{label}{source.suffix}"
                if label == "science_graph":
                    source_db = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
                    target_db = sqlite3.connect(str(target))
                    try:
                        source_db.backup(target_db)
                        # The frozen input is a standalone snapshot, not a live WAL
                        # database; make its bytes deterministic before hashing/copying.
                        target_db.execute("PRAGMA journal_mode=DELETE")
                        target_db.commit()
                    finally:
                        target_db.close()
                        source_db.close()
                    digest = hashlib.sha256(target.read_bytes()).hexdigest()
                else:
                    snapshot_bytes = source.read_bytes()
                    target.write_bytes(snapshot_bytes)
                    digest = hashlib.sha256(snapshot_bytes).hexdigest()
                source_digests[label] = digest
        external_references: dict[str, str] = {}
        if spec is not None and getattr(spec, "config_path", None) is not None:
            config = json.loads(Path(spec.config_path).read_text(encoding="utf-8"))
            home = Path(config.get("task_home") or spec.document.parent)
            for label, source_dir in (("task_skills", home / ".theory-master" / "skills"),
                                      ("notes_mirror", Path(spec.notes_mirror_dir) if spec.notes_mirror_dir else None),
                                      ("benchmark", Path(config["benchmark_root"]) if config.get("benchmark_root") else None)):
                if source_dir is None or not source_dir.is_dir():
                    continue
                if label == "benchmark":
                    # A benchmark can be many GB. Check a content digest before
                    # replay and after execution; read through the existing
                    # sandbox as a read-only external scientific input.
                    from .cycle_replay import _fingerprint_tree
                    external_references[label] = str(source_dir.resolve())
                    source_digests[label] = _fingerprint_tree(source_dir)
                    continue
                dest = snapshot_root / label
                self._copy_replay_tree(source_dir, dest)
                source_digests[label] = hashlib.sha256(json.dumps(
                    [(p.relative_to(dest).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
                     for p in sorted(dest.rglob("*")) if p.is_file()], separators=(",", ":")
                ).encode()).hexdigest()
        # Investigator/Judge checkpoints depend on the exact qualification or
        # investigation workspace. Preserve that boundary alongside state.
        workspace_digest = None
        workspace = getattr(self, "cycle_workspace", None)
        if name != "cycle_start" and workspace is not None and Path(workspace).is_dir():
            target_workspace = snapshot_root / "workspace"
            self._copy_replay_tree(Path(workspace), target_workspace)
            rows = []
            for item in sorted(p for p in target_workspace.rglob("*") if p.is_file()):
                rows.append((item.relative_to(target_workspace).as_posix(), hashlib.sha256(item.read_bytes()).hexdigest()))
            workspace_digest = hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
        root = self.cycle_dir / "replay_checkpoints"
        root.mkdir(parents=True, exist_ok=True)
        record = {
            "schema": "theory-master/replay-checkpoint/v2",
            "logical_cycle": int(state.get("cycle_number") or 0),
            "attempt_id": str(state.get("attempt_id") or ""),
            "name": name,
            "boundary": {"cycle_start": "before_research_lead", "investigator_start": "after_frozen_h_lock_before_investigator", "judge_start": "after_complete_findings_before_judge"}[name],
            "state_sha256": state_digest,
            "source_sha256": source_digests,
            "external_references": external_references,
            "sdk_code_sha256": WorkflowStateMixin._replay_code_fingerprint(),
            "routes": {role: {"model": route.model, "provider": route.provider,
                              "model_vendor": route.model_vendor, "reasoning_effort": route.reasoning_effort}
                       for role, route in sorted(getattr(self, "routes", {}).items())},
            "inputs_dir": snapshot_root.name,
            "workspace_sha256": workspace_digest,
            "state": json.loads(payload),
        }
        record["routes_sha256"] = hashlib.sha256(json.dumps(record["routes"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        atomic(root / f"{name}.json", json.dumps(record, ensure_ascii=False, indent=2) + "\n")

    def current_max_cycles(self) -> int:
        """Read the live task Cycle budget so TUI/config edits apply at Cycle boundaries."""
        value = max(1, int(self.spec.max_cycles or 1))
        path = getattr(self.spec, "config_path", None)
        if path is None:
            return value
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            configured = int(payload.get("max_cycles", value))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return value
        return max(1, configured)

    def set_max_cycles(self, value: int) -> int:
        """Persist a new Cycle budget; a live Runner observes it at the next Cycle boundary."""
        try:
            budget = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_cycles must be an integer >= 1") from exc
        if budget < 1:
            raise ValueError("max_cycles must be an integer >= 1")
        path = getattr(self.spec, "config_path", None)
        if path is None:
            raise ValueError("hot max_cycles update requires task-mode theory-master.config.json")
        config_path = Path(path)
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        payload["max_cycles"] = budget
        atomic(config_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        cycle = latest_cycle_dir(self.spec.research_root, task_mode=self.spec.task_mode)
        if cycle is not None:
            run_state_path = cycle / "run_state.json"
            if run_state_path.is_file():
                try:
                    run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    run_state = None
                if isinstance(run_state, dict):
                    run_state["max_cycles"] = budget
                    write_json(run_state_path, run_state)
        return budget

    def submit_attention_guidance(
        self, text: str, *, source: str = "human",
        authorize_prequalification_restart: bool = False,
    ) -> dict[str, Any]:
        """Persist one-shot operator guidance for the next Agent turn after resume."""
        from .attention_guidance import write_attention_guidance

        cycle = latest_cycle_dir(self.spec.research_root, task_mode=self.spec.task_mode)
        if cycle is None:
            raise ValueError("no Cycle available for attention guidance")
        state_path = cycle / "research_state.json"
        if not state_path.is_file():
            raise ValueError("Cycle has no research_state.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if str(state.get("status") or "").lower() != "needs_attention":
            raise ValueError("attention guidance is accepted only while the Cycle is NEEDS_ATTENTION")
        attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
        prequalification_authorized = False
        if authorize_prequalification_restart:
            lock = state.get("hypothesis_lock") if isinstance(state.get("hypothesis_lock"), dict) else {}
            if (str(state.get("phase") or "") != "runtime_failure:no_recoverable_checkpoint"
                    or state.get("last_safe_checkpoint") or state.get("role_outputs")
                    or str(lock.get("status") or "unlocked").lower() == "locked"
                    or state.get("hypothesis_locked")):
                raise ValueError("prequalification restart authorization requires an untouched, unlocked runtime failure")
            state["prequalification_restart_authorized"] = True
            prequalification_authorized = True
        payload = write_attention_guidance(
            cycle, text, source=source, attention_code=str(attention.get("code") or "") or None,
        )
        if prequalification_authorized:
            payload["prequalification_restart_authorized"] = True
        state["attention_guidance"] = payload
        state["attention"] = {**attention, "guidance_status": "provided", "guidance_source": source}
        write_json(state_path, state)
        run_state_path = cycle / "run_state.json"
        if run_state_path.is_file():
            try:
                run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                run_state = {}
            if isinstance(run_state, dict):
                run_state["attention_guidance"] = payload
                run_state["attention"] = dict(state["attention"])
                write_json(run_state_path, run_state)
        return payload

    def supervisor_enabled(self) -> bool:
        """Read the authoritative Supervisor switch, including legacy settings."""
        from .supervisor_intervention import supervisor_enabled
        return supervisor_enabled(self.spec.research_root)

    def attention_takeover_enabled(self) -> bool:
        """Legacy API alias; there is no separate Attention Takeover switch."""
        return self.supervisor_enabled()

    def set_attention_takeover(self, enabled: bool) -> dict[str, Any]:
        """Compatibility alias; all authority belongs to the Supervisor switch."""
        return self.set_supervisor(enabled)

    def set_supervisor(self, enabled: bool) -> dict[str, Any]:
        """Hot-toggle the single Supervisor switch (runtime and science reviews)."""
        from .supervisor_intervention import write_supervisor_setting

        enabled = bool(enabled)
        write_supervisor_setting(self.spec.research_root, enabled)
        cycle = latest_cycle_dir(self.spec.research_root, task_mode=self.spec.task_mode)
        result: dict[str, Any] = {"enabled": enabled, "handoff": None}
        if cycle is None:
            return result
        run_state_path = cycle / "run_state.json"
        run_state: dict[str, Any] = {}
        if run_state_path.is_file():
            try:
                loaded = json.loads(run_state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                run_state = loaded
        run_state["attention_takeover_enabled"] = enabled
        run_state["supervisor_enabled"] = enabled
        run_state["system_failure_actions_enabled"] = enabled
        run_state.setdefault("run_dir", str(cycle))
        run_state.setdefault("cycle_dir", str(cycle))
        run_state.setdefault("cycle_id", cycle.name)
        run_state.setdefault("run_id", cycle.name)
        if enabled and str(run_state.get('status') or '').upper() == 'NEEDS_ATTENTION':
            # A stopped Cycle has no owning SDK Runner. Never start an external
            # repair process from a hot setting toggle; normal managed resume
            # enters the internal reviewer/recovery path.
            result['handoff'] = 'sdk:pending_managed_resume'
            run_state['attention_takeover_handoff'] = {
                'status': 'sdk_pending_resume', 'target': None, 'error': None,
            }
        if run_state:
            write_json(run_state_path, run_state)
        return result

    def quota_auto_adjust_enabled(self) -> bool:
        from .quota_auto_adjust import quota_auto_adjust_enabled
        return quota_auto_adjust_enabled(self.spec.research_root)

    def set_quota_auto_adjust(self, enabled: bool) -> dict[str, Any]:
        """Persist the quota controller switch; Supervisor owns switching/resume."""
        from .quota_auto_adjust import (
            read_controller_state,
            write_controller_state,
            write_quota_auto_adjust_setting,
        )

        enabled = bool(enabled)
        write_quota_auto_adjust_setting(self.spec.research_root, enabled)
        prior = read_controller_state(self.spec.research_root)
        status = str(prior.get("status") or "idle") if enabled else "disabled"
        if enabled and status == "disabled":
            status = "idle"
        controller = write_controller_state(self.spec.research_root, status=status)
        collector = getattr(self, "collector", None)
        collector_state = getattr(collector, "state", None)
        if isinstance(collector_state, dict):
            lock = getattr(collector, "_lock", None)
            if lock is not None:
                with lock:
                    collector_state["quota_auto_adjust_enabled"] = enabled
                    collector_state["quota_auto_adjust"] = dict(controller)
            else:
                collector_state["quota_auto_adjust_enabled"] = enabled
                collector_state["quota_auto_adjust"] = dict(controller)
        cycle = latest_cycle_dir(self.spec.research_root, task_mode=self.spec.task_mode)
        result: dict[str, Any] = {"enabled": enabled, "controller": controller, "quota_paused": False}
        if cycle is None:
            return result
        research_state_path = cycle / "research_state.json"
        if research_state_path.is_file():
            try:
                research_state = json.loads(research_state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                research_state = {}
            recovery = research_state.get("recovery") if isinstance(research_state, dict) and isinstance(research_state.get("recovery"), dict) else {}
            result["quota_paused"] = (
                str(research_state.get("status") or "").lower() == "paused"
                and recovery.get("code") == "provider_quota_exhausted"
            ) if isinstance(research_state, dict) else False
        run_state_path = cycle / "run_state.json"
        if run_state_path.is_file():
            try:
                run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                run_state = {}
            if isinstance(run_state, dict):
                run_state["quota_auto_adjust_enabled"] = enabled
                run_state["quota_auto_adjust"] = controller
                write_json(run_state_path, run_state)
        return result

    def _master_id_for_cycle(self, cycle_name: str | None = None) -> int | None:
        """Task mode owns one fixed Master ID; legacy mode keeps old IDs readable."""
        if self.spec.task_mode:
            return self.spec.master_id
        return master_run_id(cycle_name or (self.cycle_dir.name if self.cycle_dir is not None else ""))

    def _empty_canvas(self) -> dict[str, Any]:
        return {"nodes": [], "edges": []}

    def _bind_cycle(self, cycle: Path, *, create: bool) -> None:
        cycle = cycle.expanduser().resolve()
        cycle.mkdir(parents=True, exist_ok=True)
        # Cycle directories retain only execution provenance.  A shared Obsidian
        # branch must write its sole authoritative narrative into the vault.
        (cycle / "notes").mkdir(parents=True, exist_ok=True)
        (cycle / "workspace").mkdir(parents=True, exist_ok=True)
        marker = cycle / CYCLE_MARKER
        if create and not marker.exists():
            atomic(marker, f"{SCHEMA}\n{self.spec.goal_id}\n{now()}\n")
        self.cycle_dir = cycle
        self.notes_dir = self.spec.notes_mirror_dir or (cycle / "notes")
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.cycle_workspace = cycle / "workspace"
        self.events_path = cycle / "events.jsonl"
        if self.spec.branch_canvas is None:
            raise ValueError("Task configuration must define the Canvas projection path")
        self.branch_canvas = self.spec.branch_canvas
        graph_path = self._graph_database_path()
        if create and not self.branch_canvas.exists():
            from .science_graph_store import ScienceGraphStore
            from .science_canvas_export import canvas_projection
            with ScienceGraphStore(graph_path, readonly=True) as store:
                write_json(self.branch_canvas, canvas_projection(store))
        if hasattr(self.backend, "cwd"):
            self.backend.cwd = self.cycle_workspace  # type: ignore[attr-defined]
            if hasattr(self.backend, "cycle_id"):
                self.backend.cycle_id = cycle.name  # type: ignore[attr-defined]
            if getattr(self.backend, "client", None) is not None:
                self.backend.client = None  # type: ignore[attr-defined]
                if hasattr(self.backend, "threads"):
                    self.backend.threads = {}  # type: ignore[attr-defined]

    def _ensure_cycle(self, run_id: str | None = None, *, create: bool = True) -> str:
        if self.cycle_dir is not None:
            return self.cycle_dir.name
        if run_id is None and not create:
            latest = latest_cycle_dir(self.spec.research_root, task_mode=self.spec.task_mode)
            if latest is None:
                raise ValueError("no cycle directory under research_root")
            self._bind_cycle(latest, create=False)
            return latest.name
        run_id = run_id or make_run_id(
            self.spec.goal_id,
            self.spec.research_root,
            task_mode=self.spec.task_mode,
            legacy_research_root=self.spec.legacy_research_root,
        )
        self._bind_cycle(self.spec.research_root / run_id, create=True)
        return run_id

    def _new_state(self, seed: dict[str, Any] | None = None) -> dict[str, Any]:
        state = {"schema_version": 3, "goal_id": self.spec.goal_id, "goal_revision": self.spec.revision, "goal_sha256": self.spec.goal_sha256,
                 "status": "active", "phase": "idle", "last_seq": 0, "threads": {}, "branches": [], "active_branch_id": "",
                 "scientific_phase": "qualification", "scientific_verdict": None,
                 "assessment_state": "pending", "closure_reason": None,
                 "resource_disposition": None, "next_disposition": "investigate",
                 "runtime_state": "running", "protocol_health": "valid",
                 "investigation_attempt": 0, "investigation_round_count": 0,
                 "judge_rework_count": 0, "scientific_rework_count": 0,
                 "json_parse_repair_count": 0, "json_normalization_count": 0, "contract_repair_count": 0,
                 "minor_anomaly_count": 0,
                 "total_investigation_round_count": 0, "prompt_metrics": [],
                 "investigation_attempts": {}, "candidates": [], "current_state": "initialized",
                 "next_start_node_id": None,
                 "cycle_dir": str(self.cycle_dir) if self.cycle_dir is not None else "",
                 "master_id": self._master_id_for_cycle(),
                 "cycle_id": self.cycle_dir.name if self.cycle_dir is not None else "",
                 "cycle_number": cycle_number(self.cycle_dir.name) if self.cycle_dir is not None else None,
                 "active_role": None, "investigation_round": 0, "current_turn_id": None,
                 "last_safe_checkpoint": None, "resumable": False, "recovery": {}, "role_outputs": {},
                 "next_executor": None, "owner_pid": None, "execution_active": False,
                 "human_attention": None, "attention": {}, "attention_guidance": {},
                 "scientific_model_version_in": 0, "scientific_model_version_out": 0,
                 "scientific_model_patch_status": "not_started", "scientific_model_update_status": "not_started",
                 "research_lead_thread_id": None,
                 "frontier_review": {"status": "pending", "generated_candidates": [], "selected_candidate_id": None,
                                     "reviews": []},
                 "literature_need": {"status": "not_evaluated", "reason": ""},
                 "hypothesis_lock": {"status": "unlocked"},
                 "hypothesis_locked": False, "qualification_status": "pending", "qualification_rationale": "",
                 "scientific_model_current_state": ""}
        if seed: state.update({key: value for key, value in seed.items() if key not in {"last_seq", "threads"}})
        return state

    @staticmethod
    def _normalize_state_axes(state: dict[str, Any]) -> None:
        """Maintain the current orthogonal runtime/scientific state axes."""
        state.setdefault("total_investigation_round_count", 0)
        state.setdefault("minor_anomaly_count", 0)
        state.setdefault("next_executor", None)
        state.setdefault("investigation_attempt", 0)
        state.setdefault("investigation_round_count", 0)
        state.setdefault("judge_rework_count", 0)
        state.setdefault("scientific_rework_count", state.get("judge_rework_count", 0))
        state.setdefault("json_parse_repair_count", 0)
        state.setdefault("json_normalization_count", 0)
        state.setdefault("contract_repair_count", 0)
        state.setdefault("prompt_metrics", [])
        state.setdefault("investigation_attempts", {})
        status = str(state.get("status") or "active").lower()
        state["runtime_state"] = {
            "active": "running", "over": "over", "completed": "over", "paused": "paused",
            "needs_attention": "paused", "failed": "failed", "interrupted": "cancelled", "cancelled": "cancelled",
        }.get(status, "idle")
        # `completed` above is read-only legacy Cycle spelling. Goal terminal
        # state lives on the independent goal_status / goal_completion axis.
        # research_state is workflow runtime, not a third scientific picture.
        state.pop("remaining_goal_gap", None)
        frontier_context = state.get("frontier_context")
        if isinstance(frontier_context, dict):
            for key in (
                "scientific_synthesis", "established", "unresolved", "boundary",
                "remaining_goal_gap", "remaining_gap", "cycle_scientific_state",
                "judge_rationale", "judge_verdict", "scientific_model_version",
            ):
                frontier_context.pop(key, None)
        state.setdefault("protocol_health", "valid")
        state.setdefault("scientific_phase", "qualification")
        if state["protocol_health"] not in {"valid", "degraded", "violation", "migration_required"}:
            state["protocol_health"] = "violation"
        if state["scientific_phase"] not in {"qualification", "locked", "investigating", "adjudicated", "committed"}:
            state["scientific_phase"] = "investigating"
        state.setdefault("scientific_verdict", None)
        state.setdefault("assessment_state", "pending")
        state.setdefault("closure_reason", None)
        state.setdefault("resource_disposition", None)
        state.setdefault("next_disposition", "investigate")
        state.setdefault("human_attention", None)
        state.setdefault("attention", {})

    def _state_path(self) -> Path:
        if self.cycle_dir is None:
            raise ValueError("cycle directory is not bound")
        return self.cycle_dir / "research_state.json"

    def _run_state_path(self) -> Path:
        if self.cycle_dir is None:
            raise ValueError("cycle directory is not bound")
        return self.cycle_dir / "run_state.json"

    def _recovery_projection(self, state: dict[str, Any]) -> None:
        """Mirror recovery fields into the dashboard state without replacing it."""
        path = self._run_state_path()
        current: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    current = loaded
            except (OSError, ValueError):
                current = {}
        current.update({
            "master_id": self._master_id_for_cycle(),
            "cycle_id": self.cycle_dir.name,
            "cycle_number": cycle_number(self.cycle_dir.name),
            "run_id": self.cycle_dir.name,
            "research_root": str(self.spec.research_root),
            "plan_path": str(self.spec.document),
            "config_path": str(self.spec.config_path),
            "branch_canvas": str(self.spec.branch_canvas),
            "max_cycles": self.current_max_cycles(),
            "status": (
                "COMPLETED"
                if str((state.get("goal_completion") or {}).get("verdict") or "") == "passed"
                else str(state.get("status") or "").upper()
            ),
            "phase": state.get("phase"),
            "current_agent": state.get("active_role") or state.get("current_agent"),
            "current_turn_id": state.get("current_turn_id"),
            "last_seq": state.get("last_seq"),
            "execution_active": bool(state.get("execution_active", False)),
            "owner_pid": state.get("owner_pid"),
            "human_attention": state.get("human_attention"),
            "attention": dict(state.get("attention") or {}),
            "attention_guidance": dict(state.get("attention_guidance") or {}),
            "resumable": bool(state.get("resumable", False)),
            "attention_guidance": dict(state.get("attention_guidance") or {}),
            "last_safe_checkpoint": state.get("last_safe_checkpoint"),
            "recovery": state.get("recovery") or {},
            "recovery_status": state.get("status"),
            "recovery_phase": state.get("phase"),
            "scientific_phase": state.get("scientific_phase"),
            "scientific_verdict": state.get("scientific_verdict"),
            "runtime_state": state.get("runtime_state"),
            "protocol_health": state.get("protocol_health"),
        })
        write_json(path, current)

    def _checkpoint(self, state: dict[str, Any], label: str) -> None:
        """Persist a named safe boundary and expose it as the recovery anchor."""
        if self.cycle_dir is None:
            return
        path = self.cycle_dir / "checkpoints" / f"{state['last_seq']:06d}-{label}.json"
        write_json(path, state)
        state["last_safe_checkpoint"] = str(path)
        state["resumable"] = True
        self._persist(state)

    def _control_action(self) -> dict[str, Any] | None:
        if self.cycle_dir is None:
            return None
        path = self.cycle_dir / "recovery_control.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"action": "invalid", "reason": "invalid recovery_control.json"}
        return value if isinstance(value, dict) else {"action": "invalid"}

    def _check_recovery_control(self, state: dict[str, Any]) -> None:
        action = self._control_action()
        if not action:
            return
        command = str(action.get("action") or "").lower()
        if command not in {"pause", "interrupt", "kill", "abandon_cycle"}:
            raise ValueError("invalid recovery control action")
        state["recovery"] = {**action, "requested_at": action.get("requested_at") or now()}
        state["resumable"] = command != "abandon_cycle"
        if command == "abandon_cycle":
            # Explicit human scientific withdrawal is not a Judge verdict.
            # Leave Workspace, role outputs and registered Evidence readable;
            # do not promote provisional Investigator material to Canvas.
            state.update({"status": "cancelled", "phase": "abandoned",
                          "execution_active": False,
                          "invocation_stop_reason": "human_abandoned_cycle",
                          "abandoned_cycle": {
                              "at": now(), "reason": action.get("reason", "web_operator"),
                              "scientific_verdict": None,
                              "requires_new_cycle": True,
                          }})
            self._emit(state, "run.cycle_abandoned", {
                "reason": action.get("reason", "web_operator"),
                "resumable": False, "new_cycle_required": True,
            })
        elif command == "pause":
            state.update({"status": "paused", "phase": "paused"})
            self._emit(state, "run.paused", {"reason": action.get("reason", "operator request")})
        elif command == "interrupt":
            state.update({"status": "interrupted", "phase": "interrupted"})
            self._emit(state, "run.interrupted", {"reason": action.get("reason", "operator request")})
        else:
            state.update({"status": "interrupted", "phase": "computation_killed"})
            self._emit(state, "computation.killed", {"role": action.get("role"), "thread_id": action.get("thread_id"), "reason": action.get("reason", "operator request")})
        # Abandonment is operational, not a Canvas/Decision update.
        self._persist(state, project=command != "abandon_cycle")
        try:
            (self.cycle_dir / "recovery_control.json").unlink()
        except FileNotFoundError:
            pass
        raise RecoveryStop(command)

    def _event_last_seq(self) -> int:
        """Recover the append-only sequence after an external process interruption.

        A cancellation may be requested after the worker process died between an
        event append and its state checkpoint.  Only the terminal cancellation
        record is repaired here; ordinary historical events are never rewritten.
        """
        if not self.events_path.exists(): return 0
        events = [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not events: return 0
        previous_max = max(int(event.get("seq", 0)) for event in events[:-1]) if len(events) > 1 else 0
        final = events[-1]
        if final.get("type") == "run.cancelled" and int(final.get("seq", 0)) <= previous_max:
            final["seq"] = previous_max + 1
            atomic(self.events_path, "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n")
        return max(int(event.get("seq", 0)) for event in events)

    def _sync_investigator_budget_usage(self, state: dict[str, Any]) -> None:
        """Synchronize the reporting snapshot after live Investigator usage.

        Pre-invocation budget_status remains the spend authorization guard.
        Collector TOKEN_USAGE arrives after that check. Preserve cumulative
        usage, approved grants and cached input accounting across recovery.
        """
        budget = state.get("investigator_budget")
        collector = getattr(self, "collector", None)
        read_tokens = getattr(collector, "agent_total_tokens", None)
        if not isinstance(budget, dict) or self.cycle_dir is None or not callable(read_tokens):
            return
        live_tokens = read_tokens("investigator")
        if live_tokens <= int(budget.get("observed_tokens") or 0):
            return
        from .investigator_budget import budget_status
        current = budget_status(self.cycle_dir, self.spec.research_root, {
            "agents": {"investigator": {"tokens_total": live_tokens}},
            "investigator_budget_policy": state.get("investigator_budget_policy"),
        })
        for key in ("tranche_tokens", "allowance_tokens", "observed_tokens",
                    "remaining_tokens", "exhausted", "rejected",
                    "review_index", "reviews", "policy_version", "granted_tranches",
                    "supervisor_authorized", "supervisor_declined", "hard_cap_reached"):
            budget[key] = current[key]

    def _persist(self, state: dict[str, Any], *, project: bool = False) -> None:
        """Checkpoint research_state.json. Control-line Canvas is cycle-boundary only."""
        self._normalize_state_axes(state)
        self._sync_investigator_budget_usage(state)
        if not isinstance(state.get("runtime_source_provenance"), dict):
            # Keep execution provenance separate from frozen checkpoint artifacts.
            from .runtime_manager import runtime_source_provenance
            state["runtime_source_provenance"] = runtime_source_provenance()
        if project:
            self._render_canvas(state)
        prior: dict[str, Any] = {}
        if self._state_path().is_file():
            try:
                loaded = json.loads(self._state_path().read_text(encoding='utf-8'))
                if isinstance(loaded, dict):
                    prior = loaded
            except (OSError, ValueError):
                pass
        previous_status = str(prior.get('status') or '').lower()
        status = str(state.get('status') or '').lower()
        from .observability.incident_registry import (
            register_cycle_incident, record_incident_lifecycle,
        )
        if previous_status in {'needs_attention', 'failed'} and status not in {'needs_attention', 'failed'}:
            old_attention = prior.get('attention') if isinstance(prior.get('attention'), dict) else {}
            old_code = (str(old_attention.get('code') or prior.get('closure_reason') or 'needs_attention')
                        if previous_status == 'needs_attention' else 'run_failed')
            old_phase = str(prior.get('phase') or '')
            old_severity = 'attention' if previous_status == 'needs_attention' else 'error'
            old_kind = 'needs_attention' if previous_status == 'needs_attention' else 'system_failure'
            old_recovery = prior.get('recovery') if isinstance(prior.get('recovery'), dict) else {}
            old_incident, _ = register_cycle_incident(
                self.cycle_dir, severity=old_severity, kind=old_kind,
                source_event='workflow.recovery',
                summary=str(old_attention.get('reason') or old_recovery.get('message') or 'Previous stop'),
                code=old_code, discriminator=f'{old_phase}:{old_code}',
                fields={'phase': old_phase},
            )
            record_incident_lifecycle(
                self.cycle_dir, incident=old_incident, lifecycle='resume_attempt',
                source_event='workflow.persist', state_from=previous_status, state_to=status,
            )
        write_json(self._state_path(), state)
        # The authoritative state checkpoint, not an optional Collector/TUI,
        # guarantees persistent NEEDS_ATTENTION and FAILED incident records.
        # This also captures intermediate stops before an in-SDK repair succeeds.
        status = str(state.get('status') or '').lower()
        if status in {'needs_attention', 'failed'}:
            attention = state.get('attention') if isinstance(state.get('attention'), dict) else {}
            recovery = state.get('recovery') if isinstance(state.get('recovery'), dict) else {}
            phase = str(state.get('phase') or '')
            if status == 'needs_attention':
                code = str(attention.get('code') or state.get('closure_reason') or 'needs_attention')
                reason = str(attention.get('reason') or state.get('human_attention') or 'NEEDS_ATTENTION')
                severity, kind = 'attention', 'needs_attention'
            else:
                code = 'run_failed'
                reason = str(recovery.get('message') or state.get('human_attention') or state.get('last_event') or 'FAILED')
                severity, kind = 'error', 'system_failure'
            incident, first_occurrence = register_cycle_incident(
                self.cycle_dir, severity=severity, kind=kind,
                source_event='workflow.persist', summary=reason, code=code,
                discriminator=f'{phase}:{code}',
                fields={
                    'phase': phase, 'resumable': state.get('resumable'),
                    'next_executor': state.get('next_executor'),
                    'active_role': state.get('active_role') or state.get('current_agent'),
                },
            )
            if not first_occurrence and previous_status != status:
                record_incident_lifecycle(
                    self.cycle_dir, incident=incident, lifecycle='recurred',
                    source_event='workflow.persist', state_from=previous_status, state_to=status,
                )
        if self.collector is not None:
            self.collector.on_persist(state)
        else:
            # Exactly one writer owns run_state.json.  Mixing the collector and
            # this fallback caused stale INTERRUPTED snapshots to overwrite a
            # successfully resumed scientific state.
            self._recovery_projection(state)

    def _emit(self, state: dict[str, Any], kind: str, payload: dict[str, Any]) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.events_path.with_name(self.events_path.name + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            file_last_seq = self._event_last_seq()
            seq = max(int(state.get("last_seq") or 0), int(file_last_seq or 0)) + 1
            event = {"seq": seq, "at": now(), "type": kind, "payload": payload}
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            state["last_seq"] = seq
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        if self.collector is not None:
            self.collector.on_scientific(kind, payload, state)

    def initialize(self, seed: dict[str, Any] | None = None) -> dict[str, Any]:
        from .scientific_model import load as load_scientific_model
        model = load_scientific_model(self.spec.research_root, goal_id=self.spec.goal_id)
        if self.cycle_dir is None:
            latest = latest_cycle_dir(self.spec.research_root, task_mode=self.spec.task_mode)
            if latest is not None:
                self._bind_cycle(latest, create=False)
            else:
                self._ensure_cycle(create=True)
        if self._state_path().exists():
            state = json.loads(self._state_path().read_text(encoding="utf-8"))
            event_last_seq = self._event_last_seq()
            state.setdefault("threads", {})
            state.setdefault("last_seq", 0)
            state.setdefault("scientific_model_version_in", model.get("version", 0))
            state.setdefault("scientific_model_version_out", model.get("version", 0))
            state.setdefault("scientific_model_patch_status", "not_started")
            state.setdefault("scientific_model_update_status", state.get("scientific_model_patch_status", "not_started"))
            state.setdefault("research_lead_thread_id", state.get("threads", {}).get("research_lead"))
            state.setdefault("evolution_candidates", [])
            state.setdefault("frontier_review", {"status": "pending", "generated_candidates": [], "selected_candidate_id": None, "reviews": []})
            if not isinstance(state.get("frontier_review"), dict):
                state["frontier_review"] = {"status": "pending", "generated_candidates": [], "selected_candidate_id": None, "reviews": []}
            state.setdefault("literature_need", {"status": "not_evaluated", "reason": ""})
            state.setdefault("hypothesis_lock", {"status": "unlocked"})
            state.setdefault("hypothesis_locked", state.get("hypothesis_lock", {}).get("status") == "locked")
            state.setdefault("qualification_status", "pending")
            state.setdefault("qualification_rationale", "")
            state.setdefault("scientific_model_current_state", "")
            self._normalize_state_axes(state)
            state["cycle_dir"] = str(self.cycle_dir)
            if event_last_seq > state.get("last_seq", 0):
                state["last_seq"] = event_last_seq
                write_json(self._state_path(), state)
            return state
        state = self._new_state(seed)
        state["scientific_model_version_in"] = model.get("version", 0)
        state["scientific_model_version_out"] = model.get("version", 0)
        write_json(self._state_path(), state)
        return state

    def _path_anchors(self) -> dict[str, str]:
        """Stable navigation anchors under the canonical Task Path Model."""
        canvas = self.spec.branch_canvas.resolve()
        task_home = (self.spec.task_home or self.spec.document.parent).resolve()
        anchors = {
            "task_home": str(task_home),
            "note_area": str(self.spec.document.parent.resolve()),
            "research_root": str(self.spec.research_root.resolve()),
            "branch_canvas": str(canvas),
        }
        if self.cycle_dir is not None:
            anchors["cycle_dir"] = str(self.cycle_dir.resolve())
        if self.cycle_workspace is not None:
            anchors["cycle_workspace"] = str(self.cycle_workspace.resolve())
        if self.notes_dir is not None:
            anchors["notes_dir"] = str(self.notes_dir.resolve())
        benchmark_root = self.spec.benchmark_root
        if benchmark_root is not None:
            anchors["benchmark_root"] = str(benchmark_root.resolve())
        if self.spec.legacy_research_root is not None:
            anchors["legacy_research_root"] = str(self.spec.legacy_research_root.resolve())
        for parent in (task_home, *task_home.parents):
            if parent.name == "笔记区" or (parent / ".obsidian").is_dir():
                anchors["notes_vault_root"] = str(parent)
                break
        for start in (task_home, canvas, self.spec.research_root.resolve()):
            for parent in (start, *start.parents):
                if (parent / ".git").exists():
                    anchors["repo_root"] = str(parent)
                    break
            if "repo_root" in anchors:
                break
        return anchors

    def _evidence_registry(self):
        if self.cycle_dir is None or self.cycle_workspace is None:
            raise ValueError("cycle workspace is not bound")
        from .evidence import EvidenceRegistry
        anchors = self._path_anchors()
        return EvidenceRegistry(
            self.cycle_dir,
            self.cycle_workspace,
            task_local_root=Path(anchors["task_home"]) if anchors.get("task_home") else None,
            repo_root=Path(anchors["repo_root"]) if anchors.get("repo_root") else None,
            research_root=self.spec.research_root,
            legacy_research_root=self.spec.legacy_research_root,
        )

    def _register_evidence(self, state: dict[str, Any], role: str, output: dict[str, Any], *,
                           stage: str | None = None) -> list[str]:
        self._last_evidence_registration_audit: list[dict[str, Any]] = []
        lock = state.get("hypothesis_lock") or {}
        epoch = "qualification_generated" if lock.get("status") != "locked" else (
            "rework" if state.get("judge_rework_count", 0) else "post_lock_test"
        )
        invocation = {
            "invocation_id": str(state.get("last_invocation_id") or
                                 f"agent-{self.cycle_dir.name}-{role}-a{state.get('investigation_attempt', 0)}-r{state.get('investigation_round_count', 0)}"),
            "kind": "agent", "role": role,
            "stage": str(stage or ""),
            "thread_id": state.get("threads", {}).get(self._thread_key(state, role)),
            "investigation_attempt": state.get("investigation_attempt", 0),
            "investigation_round": (
                int(state.get("investigation_round_count") or 0) + 1
                if role in {"investigator", "literature"}
                and state.get("investigation_attempt")
                else int(state.get("investigation_round_count") or 0)
            ),
        }
        local = lock.get("cycle_criteria") or lock.get("cycle_progress_criteria") or []
        frozen_rows = criterion_rows(local)
        known_criterion_ids = [str(item.get("id") or item.get("criterion_id")) for item in frozen_rows
                               if isinstance(item, dict) and (item.get("id") or item.get("criterion_id"))]
        registration_output = output
        if role == "literature":
            # Literature free-text claims can include retrieval/tool diagnostics.
            # Only explicit LiteratureEvidence records are scientific Evidence.
            records = output.get("evidence_records")
            if not isinstance(records, list) or not records:
                self._evidence_registry().rebuild()
                return []
            registration_output = {"evidence_records": records}
        registry = self._evidence_registry()
        refs = registry.register_role_output(
            role, registration_output, epoch=epoch, producer_invocation=invocation,
            hypothesis_ref=str(lock.get("id") or lock.get("hypothesis_id") or ""),
            default_criterion_ids=None, known_criterion_ids=known_criterion_ids,
            default_scope=str(lock.get("scope") or ""),
            source_transport=(state.get("last_sdk_scholarly_retrieval") if role == "literature"
                              and isinstance(state.get("last_sdk_scholarly_retrieval"), dict) else None),
        )
        state["evidence_refs"] = list(dict.fromkeys([*(state.get("evidence_refs") or []), *refs]))
        self._last_evidence_registration_audit = list(registry.last_registration_audit)
        return refs

    def _evidence_context(self) -> dict[str, Any]:
        """Return the two SDK-owned current-Cycle Evidence projections."""
        return self._evidence_registry().context()

    def _research_lead_state_path(self) -> Path:
        return self.spec.research_root / "research_lead_state.json"

    def _load_research_lead_state(self) -> dict[str, Any]:
        path = self._research_lead_state_path()
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def reset_research_lead(self, reason: str = "human_reset") -> dict[str, Any]:
        """Explicit operator reset for a contaminated or obsolete long-lived RL thread."""
        previous = self._load_research_lead_state()
        history = list(previous.get("rollover_history") or [])
        event = {"cycle_id": self.cycle_dir.name if self.cycle_dir else "", "reason": reason, "at": now()}
        history.append(event)
        route = self.routes["research_lead"]
        self._save_research_lead_state(thread_id=None, route=route, cycle_id=event["cycle_id"],
                                       rollover_reason=reason, rollover_history=history[-20:], context_chars=0)
        return {"thread_id": None, "rollover_reason": reason, "rollover_history": history[-20:]}

    def _save_research_lead_state(self, *, thread_id: str | None, route: RoleRoute, cycle_id: str,
                                  rollover_reason: str | None = None, rollover_history: list[Any] | None = None,
                                  context_chars: int | None = None) -> None:
        previous = self._load_research_lead_state()
        history = list(rollover_history if rollover_history is not None else previous.get("rollover_history") or [])
        reason = rollover_reason if rollover_reason is not None else previous.get("rollover_reason")
        write_json(self._research_lead_state_path(), {
            "schema": "theory-master/research-lead-state/v1",
            "thread_id": thread_id,
            "model": route.model,
            "provider": route.provider,
            "last_cycle_id": cycle_id,
            "updated_at": now(),
            "last_start_node_id": self.branch_parent_id or self.spec.branch_anchor_id,
            "context_chars": int(context_chars if context_chars is not None else previous.get("context_chars") or 0),
            "rollover_policy": {"max_context_chars": 200000, "major_branch_shift": True,
                                "human_reset": "recovery_control.json action=reset_research_lead"},
            "rollover_history": history[-20:],
            "rollover_reason": reason,
        })

    def _research_lead_rollover_reason(self, state: dict[str, Any], persisted: dict[str, Any], route: RoleRoute) -> str | None:
        if state.get("research_lead_reset_reason"):
            return str(state.pop("research_lead_reset_reason"))
        if persisted.get("model") and (persisted.get("model") != route.model or persisted.get("provider") != route.provider):
            return "provider_changed"
        context_chars = int(persisted.get("context_chars") or 0) + len(
            json.dumps(state.get("role_outputs") or {}, ensure_ascii=False, default=str)
        )
        state["research_lead_context_chars"] = context_chars
        if context_chars > 200000:
            return "context_budget"
        prior_start = str(persisted.get("last_start_node_id") or "")
        current_start = str(self.branch_parent_id or self.spec.branch_anchor_id or "")
        if prior_start and current_start and prior_start != current_start:
            return "major_branch_shift"
        return None
