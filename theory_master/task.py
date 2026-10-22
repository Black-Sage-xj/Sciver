"""Visible system configuration and task-local discovery."""
from __future__ import annotations
import json
import hashlib
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import fcntl

TASK_DIR_NAME = ".theory-master"
TASK_CONFIG_NAME = "task.json"
SYSTEM_CONFIG_NAME = "theory-master.config.json"
TASK_SCHEMA = "theory-master/task/v2"
SYSTEM_SCHEMA = "theory-master/system/v1"
PATH_MODEL = "theory-master/task-path/v2"
LEGACY_PATH_MODEL = "theory-master/task-path/v1"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

def ensure_runtime_outside_package(path: Path, name: str = "research_root") -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(PACKAGE_ROOT)
    except ValueError:
        return resolved
    raise ValueError(f"{name} must be outside the TheoryMaster source tree: {PACKAGE_ROOT}")

def absolute(value: object, name: str) -> Path:
    path = Path(str(value or "")).expanduser()
    if not value or not path.is_absolute():
        raise ValueError(f"system config {name} must be absolute")
    return path.resolve()


def note_vault_root(note_area: Path) -> Path | None:
    """Recognize the repository's synchronized Chinese 笔记区 boundary."""
    return next((p for p in (note_area, *note_area.parents) if p.name == "笔记区"), None)


def non_sync_data_area(plan: Path, master_id: int, goal_id: str) -> Path:
    """Stable non-Vault workspace; never infer a data root inside a note area."""
    plan = plan.expanduser().resolve()
    note_area = plan.parent
    configured = os.environ.get("THEORY_MASTER_DATA_ROOT", "").strip()
    if configured:
        base = Path(configured).expanduser().resolve()
    else:
        repo = next((p for p in (note_area, *note_area.parents)
                     if (p / ".git").exists() and (p / "gs_study").is_dir()), None)
        base = (repo / "gs_study" / ".theory-master-tasks" if repo
                else note_area.parent / ".theory-master-tasks")
    goal_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", goal_id).strip("-")[:72] or "research"
    digest = hashlib.sha256(str(note_area).encode("utf-8")).hexdigest()[:12]
    data_area = (base / f"{master_id}-{goal_slug}-{digest}").resolve()
    vault = note_vault_root(note_area)
    if (data_area == note_area or note_area in data_area.parents or
            (vault is not None and (data_area == vault or vault in data_area.parents))):
        raise ValueError("TheoryMaster data area must be outside the human note area/vault")
    return data_area


def effective_path_model(config: dict) -> tuple[Path, Path, Path | None]:
    """Resolve canonical Task Home/runtime paths without rewriting legacy assets."""
    plan_home = absolute(config.get("plan_path"), "plan_path").parent
    task_home = absolute(config["task_home"], "task_home") if config.get("task_home") else plan_home
    configured_root = ensure_runtime_outside_package(
        absolute(config.get("research_root"), "research_root")
    )
    path_model = config.get("path_model", LEGACY_PATH_MODEL)
    if path_model == PATH_MODEL:
        research_root = configured_root
        vault = note_vault_root(task_home)
        if (research_root == task_home or task_home in research_root.parents or
                (vault is not None and (research_root == vault or vault in research_root.parents))):
            raise ValueError("v2 research_root must be outside task_home/vault (non-sync)")
        benchmark_value = config.get("benchmark_root")
        if not benchmark_value:
            raise ValueError("v2 config requires explicit benchmark_root")
        benchmark = absolute(benchmark_value, "benchmark_root")
        if (benchmark == task_home or task_home in benchmark.parents or
                (vault is not None and (benchmark == vault or vault in benchmark.parents))):
            raise ValueError("v2 benchmark_root must be outside task_home/vault (non-sync)")
        if benchmark == research_root or research_root in benchmark.parents:
            raise ValueError("benchmark_root must be separate from research_root")
    elif path_model == LEGACY_PATH_MODEL:
        research_root = ensure_runtime_outside_package(task_home / TASK_DIR_NAME / "research")
    else:
        raise ValueError(f"unsupported path_model: {path_model}")
    legacy = (
        ensure_runtime_outside_package(
            absolute(config["legacy_research_root"], "legacy_research_root"),
            "legacy_research_root",
        )
        if config.get("legacy_research_root")
        else (configured_root if configured_root != research_root else None)
    )
    return task_home, research_root, legacy


def master_id_for_root(research_root: Path) -> int:
    """Return the legacy root-hash ID used briefly for compatibility lookup only."""
    canonical = str(research_root.expanduser().resolve()).encode("utf-8")
    value = int.from_bytes(hashlib.sha256(canonical).digest()[:8], "big") & ((1 << 63) - 1)
    return value or 1


def effective_master_id(config: dict) -> int:
    """Return the persisted compact task ID exactly as stored."""
    try:
        raw = int(config.get("task_id", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid TheoryMaster task_id") from exc
    if raw < 0:
        raise ValueError("invalid TheoryMaster task_id")
    return raw


def task_id_scope_root(plan: Path) -> Path:
    """Choose the repository/search scope within which human-facing task IDs are unique."""
    configured = (os.environ.get("THEORY_MASTER_SEARCH_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    current = plan.expanduser().resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def canonical_system_configs(search_root: Path) -> list[Path]:
    """List live system configs, excluding immutable load snapshots."""
    root = search_root.expanduser().resolve()
    found: list[Path] = []
    for path in root.rglob(SYSTEM_CONFIG_NAME):
        if "load-snapshots" in path.parts:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("schema") == SYSTEM_SCHEMA:
            found.append(path)
    return found


def next_task_id(search_root: Path) -> int:
    """Return the smallest unused non-negative compact task ID in the search scope."""
    used: set[int] = set()
    for path in canonical_system_configs(search_root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            task_id = int(payload.get("task_id", 0))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if task_id >= 0:
            used.add(task_id)
    candidate = 0
    while candidate in used:
        candidate += 1
    return candidate


@contextmanager
def task_id_allocation_lock(search_root: Path):
    """Serialize compact ID allocation without storing runtime data in the source tree."""
    digest = hashlib.sha1(str(search_root.resolve()).encode("utf-8")).hexdigest()[:12]
    lock_path = Path(tempfile.gettempdir()) / f"theory-master-task-id-{digest}.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

@dataclass(frozen=True)
class TaskBinding:
    task_home: Path
    research_root: Path
    config_path: Path
    config: dict

    @property
    def task_root(self) -> Path:
        """Historical compatibility alias; current runtime code uses research_root."""
        return self.research_root

    @property
    def master_id(self) -> int: return effective_master_id(self.config)
    @property
    def goal_id(self) -> str: return str(self.config["goal_id"])
    @property
    def branch_canvas(self) -> Path: return absolute(self.config["branch_canvas"], "branch_canvas")
    @property
    def note_area(self) -> Path:
        """Human-visible documentation area; currently the Task Home."""
        return self.task_home
    @property
    def legacy_research_root(self) -> Path | None:
        value = self.config.get("legacy_research_root")
        return absolute(value, "legacy_research_root") if value else None
    @property
    def benchmark_root(self) -> Path | None:
        explicit = self.config.get("benchmark_root")
        candidate = absolute(explicit, "benchmark_root") if explicit else self.task_home / "benchmark"
        return candidate if candidate.is_dir() else None

    @classmethod
    def load(cls, discovery_root: Path) -> "TaskBinding":
        root = discovery_root.expanduser().resolve()
        pointer = root / TASK_DIR_NAME / TASK_CONFIG_NAME
        if not pointer.is_file():
            raise ValueError(f"task discovery pointer is missing: {pointer}")
        data = json.loads(pointer.read_text(encoding="utf-8"))
        if data.get("schema") != TASK_SCHEMA:
            raise ValueError("unsupported task discovery schema")
        config_path = absolute(data.get("config_path"), "config_path")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("schema") != SYSTEM_SCHEMA:
            raise ValueError("invalid TheoryMaster system config")
        effective_master_id(config)
        task_home, research_root, legacy_research_root = effective_path_model(config)
        allowed_roots = {task_home, research_root}
        if legacy_research_root is not None:
            allowed_roots.add(legacy_research_root)
        if root not in allowed_roots:
            raise ValueError("system config does not bind this discovery root")
        for field in ("plan_path", "branch_canvas", "notes_dir"):
            absolute(config.get(field), field)
        from .embedded_state import validate_current
        from .scientific_model import load_from_paths as load_scientific_model
        task_home, research_root, legacy_research_root = effective_path_model(config)
        research_root.mkdir(parents=True, exist_ok=True)
        plan = absolute(config["plan_path"], "plan_path")
        validate_current(plan, config)
        load_scientific_model(research_root, plan, goal_id=str(config["goal_id"]))
        runtime_pointer = research_root / TASK_DIR_NAME / TASK_CONFIG_NAME
        if not runtime_pointer.is_file():
            write_binding(research_root, config_path)
        effective = dict(config)
        effective["path_model"] = config.get("path_model", LEGACY_PATH_MODEL)
        effective["task_home"] = str(task_home)
        effective["research_root"] = str(research_root)
        if legacy_research_root is not None:
            effective["legacy_research_root"] = str(legacy_research_root)
        return cls(task_home, research_root, config_path, effective)

def write_binding(root: Path, config_path: Path) -> None:
    path = root / TASK_DIR_NAME / TASK_CONFIG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": TASK_SCHEMA, "config_path": str(config_path.resolve())},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def bootstrap(plan: Path) -> TaskBinding:
    """One-input startup for the current Research Plan format."""
    plan = plan.expanduser().resolve()
    if plan.name != "research_plan.md" or not plan.is_file():
        raise ValueError("new task requires a research_plan.md path")
    from .specification import scientific_sections
    plan_text = plan.read_text(encoding="utf-8")
    if plan_text.startswith("---\n"):
        raise ValueError("new research_plan.md must be plain scientific Markdown")
    scientific_sections(plan_text, allow_missing_scientific_model=True)
    note_area = plan.parent
    ensure_runtime_outside_package(note_area / TASK_DIR_NAME / "research")
    config_path = note_area / SYSTEM_CONFIG_NAME
    is_new_task = not config_path.exists()
    if is_new_task:
        existing_canvas = note_area / "exploration.canvas"
        existing_graph = note_area / "science_graph.sqlite3"
        if existing_canvas.exists() and not existing_graph.is_file():
            raise FileNotFoundError(
                f"Existing Canvas found without canonical SQLite graph at {existing_graph}; "
                "run the explicit Canvas→SQLite migration before bootstrapping this workspace"
            )
    if is_new_task:
        search_root = task_id_scope_root(plan)
        with task_id_allocation_lock(search_root):
            # Another bootstrap may have created this same task while waiting.
            if config_path.exists():
                is_new_task = False
            else:
                goal_id = re.sub(r"[^a-z0-9._-]+", "-", note_area.name.lower()).strip("-") or "research"
                allocated_id = next_task_id(search_root)
                data_area = non_sync_data_area(plan, allocated_id, goal_id)
                research_root = data_area / "research"
                config = {
                    "schema": SYSTEM_SCHEMA, "path_model": PATH_MODEL,
                    "task_id": allocated_id, "goal_id": goal_id,
                    "task_home": str(note_area), "plan_path": str(plan),
                    "research_root": str(research_root),
                    "benchmark_root": str(data_area / "benchmark"),
                    "branch_canvas": str(note_area / "exploration.canvas"),
                    "notes_dir": str(note_area / "notes"), "max_cycles": 5,
                    "max_judge_reworks_per_cycle": 3,
                    # Investigator resource budgeting is controlled only by the
                    # cumulative renewable token-tranche policy, not rounds.
                    "branch_anchor_id": None, "branch_provenance": "new task workspace",
                    "state_seed": None,
                    "embedded_state_format": 4,
                }
                config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if is_new_task:
        # New tasks have one human-visible note area. Historical tasks are not
        # migrated or rewritten and may keep external Canvas/notes paths.
        visible = {
            "plan": absolute(config["plan_path"], "plan_path"),
            "canvas": absolute(config["branch_canvas"], "branch_canvas"),
            "notes": absolute(config["notes_dir"], "notes_dir"),
        }
        if visible["plan"].parent != note_area or visible["canvas"].parent != note_area or visible["notes"].parent != note_area:
            raise ValueError("new task human-visible files must share one note area")
    branch = absolute(config["branch_canvas"], "branch_canvas")
    branch.parent.mkdir(parents=True, exist_ok=True)
    graph_path = branch.with_name("science_graph.sqlite3")
    if is_new_task and not graph_path.exists():
        # Initialize the canonical data layer first; the Canvas is exported from it.
        from .science_graph_store import ScienceGraphStore
        with ScienceGraphStore(graph_path):
            pass
    if not graph_path.is_file():
        raise FileNotFoundError(
            f"canonical science_graph.sqlite3 is required: {graph_path}; "
            "runtime will not create an empty database to mask a legacy Canvas"
        )
    if not branch.exists():
        from .science_graph_store import ScienceGraphStore
        from .science_canvas_export import canvas_projection
        with ScienceGraphStore(graph_path, readonly=True) as store:
            projection = canvas_projection(store)
        branch.write_text(json.dumps(projection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    absolute(config["notes_dir"], "notes_dir").mkdir(parents=True, exist_ok=True)
    root = ensure_runtime_outside_package(absolute(config["research_root"], "research_root"))
    if is_new_task:
        from .embedded_state import initialize_model
        from .scientific_model import empty_model, initialize_state
        initialize_model(plan, empty_model(str(config["goal_id"])))
        initialize_state(root, goal_id=str(config["goal_id"]))
    if absolute(config["plan_path"], "plan_path") != plan:
        raise ValueError("system config plan_path differs from requested plan")
    write_binding(root, config_path)
    if root != note_area:
        write_binding(note_area, config_path)
    return TaskBinding.load(root)

def discover_task_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file(): current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / TASK_DIR_NAME / TASK_CONFIG_NAME).is_file(): return candidate
    raise ValueError(f"no TheoryMaster task found from {current}")

def load_discovered(start: Path | None = None) -> TaskBinding:
    return TaskBinding.load(discover_task_root(start))

def summary(binding: TaskBinding) -> str:
    lines = [
        "TheoryMaster task discovered",
        f"Task ID        : {binding.master_id}",
        f"Goal           : {binding.goal_id}",
        f"Task home      : {binding.task_home}",
        f"Note area      : {binding.note_area}",
        f"Research root  : {binding.research_root}",
        f"System config  : {binding.config_path}",
        f"Exploration    : {binding.branch_canvas}",
    ]
    if binding.legacy_research_root is not None:
        lines.append(f"Legacy archive : {binding.legacy_research_root}")
    return "\n".join(lines)
