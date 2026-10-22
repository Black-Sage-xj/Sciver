"""Task specification parsing and Cycle discovery."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

SCHEMA = "theory-master/v2"  # Cycle marker schema; not a Research Plan compatibility contract.

MARKER = "theory-master-task-document"
CYCLE_DIR_RE = re.compile(r"^(?P<cycle_number>[1-9]\d*)$")
LEGACY_PREFIXED_CYCLE_DIR_RE = re.compile(r"^cycle-(?P<cycle_number>\d+)$")
LEGACY_TIMESTAMP_CYCLE_DIR_RE = re.compile(r"^\d{2}-\d{2}-\d{2}:\d{2}-\S+$")
LEGACY_CYCLE_DIR_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{8}$")
MASTER_RUN_DIR_RE = re.compile(r"^tm-(?P<master_run_id>\d+)-\S+$")
CYCLE_MARKER = ".theory-master-cycle"

def cycle_number(name: str) -> int | None:
    """Return the numeric Cycle ordinal for current and historical task-local names."""
    text = str(name or "").strip()
    match = CYCLE_DIR_RE.fullmatch(text) or LEGACY_PREFIXED_CYCLE_DIR_RE.fullmatch(text)
    return int(match.group("cycle_number")) if match else None


def make_run_id(
    abbr: str = "cycle",
    research_root: Path | None = None,
    *,
    task_mode: bool = False,
    legacy_research_root: Path | None = None,
) -> str:
    """Allocate the next task-local Cycle ordinal.

    The task Master ID is not encoded in a Cycle directory.  Legacy ``tm-*``
    directories remain readable for old roots, but are never considered when
    allocating a new Cycle in the new task mode.
    """
    roots = [
        Path(root)
        for root in (research_root, legacy_research_root)
        if root is not None
    ]
    if not task_mode:
        slug = re.sub(r"[^\w.-]+", "-", abbr).strip("-") or "cycle"
        if not roots:
            return f"tm-0000-{slug}"
        existing_ids = [
            master_run_id(path.name)
            for root in roots if root.is_dir()
            for path in root.iterdir()
        ]
        next_id = max((value for value in existing_ids if value is not None), default=-1) + 1
        candidate = f"tm-{next_id:04d}-{slug}"
        while any((root / candidate).exists() for root in roots):
            next_id += 1
            candidate = f"tm-{next_id:04d}-{slug}"
        return candidate
    existing = [
        cycle_number(path.name)
        for root in roots if root.is_dir()
        for path in root.iterdir()
    ]
    next_number = max((value for value in existing if value is not None), default=0) + 1
    candidate = str(next_number)
    while any((root / candidate).exists() for root in roots):
        next_number += 1
        candidate = str(next_number)
    return candidate


def master_run_id(name: str) -> int | None:
    """Return a legacy ``tm-*`` ID; new task Cycles have no Master ID in names."""
    match = MASTER_RUN_DIR_RE.fullmatch(name)
    return int(match.group("master_run_id")) if match else None


def is_cycle_dir(path: Path) -> bool:
    """A cycle directory, not a neighbouring gs_study task root that shares the name pattern."""
    if not path.is_dir():
        return False
    if LEGACY_CYCLE_DIR_RE.fullmatch(path.name):
        return True
    return bool(CYCLE_DIR_RE.fullmatch(path.name) or LEGACY_PREFIXED_CYCLE_DIR_RE.fullmatch(path.name) or LEGACY_TIMESTAMP_CYCLE_DIR_RE.fullmatch(path.name) or MASTER_RUN_DIR_RE.fullmatch(path.name)) and (path / CYCLE_MARKER).is_file()


def list_cycle_dirs(research_root: Path, *, task_mode: bool = False) -> list[Path]:
    root = Path(research_root)
    if not root.is_dir():
        return []
    dirs = [path for path in root.iterdir() if is_cycle_dir(path)]
    if task_mode:
        dirs = [path for path in dirs if master_run_id(path.name) is None]
    dirs.sort(key=lambda path: (cycle_number(path.name) if cycle_number(path.name) is not None else -1,
                                (path / "research_state.json").stat().st_mtime if (path / "research_state.json").exists() else path.stat().st_mtime),
              reverse=True)
    return dirs


def latest_cycle_dir(research_root: Path, *, task_mode: bool = False) -> Path | None:
    dirs = list_cycle_dirs(research_root, task_mode=task_mode)
    return dirs[0] if dirs else None


def list_compatible_cycle_dirs(
    research_root: Path, legacy_research_root: Path | None = None, *, task_mode: bool = False
) -> list[Path]:
    """Read current + legacy Cycle archives; canonical runtime wins duplicate IDs."""
    by_name: dict[str, Path] = {}
    if legacy_research_root is not None:
        for path in list_cycle_dirs(legacy_research_root, task_mode=task_mode):
            by_name[path.name] = path
    for path in list_cycle_dirs(research_root, task_mode=task_mode):
        by_name[path.name] = path
    result = list(by_name.values())
    result.sort(
        key=lambda path: (
            cycle_number(path.name) if cycle_number(path.name) is not None else -1,
            (path / "research_state.json").stat().st_mtime
            if (path / "research_state.json").exists()
            else path.stat().st_mtime,
        ),
        reverse=True,
    )
    return result


def latest_compatible_cycle_dir(
    research_root: Path, legacy_research_root: Path | None = None, *, task_mode: bool = False
) -> Path | None:
    dirs = list_compatible_cycle_dirs(
        research_root, legacy_research_root, task_mode=task_mode
    )
    return dirs[0] if dirs else None


def find_cycle(research_root: Path, identifier: str | int, *, task_mode: bool = False) -> Path:
    """Resolve a stable Master ID or cycle directory name without allocating anything."""
    text = str(identifier).strip()
    cycles = list_cycle_dirs(Path(research_root), task_mode=task_mode)
    numeric = int(text) if text.isdigit() else None
    matches = [
        path for path in cycles
        if path.name == text
        or str(master_run_id(path.name)) == text
        or (numeric is not None and cycle_number(path.name) == numeric)
    ]
    if len(matches) != 1:
        if not matches:
            raise ValueError(f"no TheoryMaster Cycle found for {identifier!r}")
        raise ValueError(f"ambiguous TheoryMaster Cycle identifier: {identifier!r}")
    return matches[0]


RESEARCH_PLAN_H1_FIELDS = (
    "Goal", "Research Contract", "Final Gate", "Required Context", "Scientific Model",
)


def _top_level_headings(markdown: str) -> list[str]:
    return [
        hit.group(1).strip()
        for hit in re.finditer(r"(?m)^# (?!#)(.+?)\s*$", markdown)
    ]


def _validate_research_plan_layout(markdown: str, *, allow_missing_scientific_model: bool) -> None:
    headings = _top_level_headings(markdown)
    expected = list(RESEARCH_PLAN_H1_FIELDS)
    if allow_missing_scientific_model and headings == expected[:-1]:
        return
    if headings != expected:
        raise ValueError(
            "research_plan.md top-level headings must be exactly: "
            + " -> ".join(f"# {name}" for name in expected)
        )


def _section(markdown: str, title: str) -> str:
    lines = markdown.splitlines()
    found = [index for index, line in enumerate(lines) if line.strip() == f"# {title}"]
    if len(found) != 1:
        raise ValueError(f"required unique H1 section missing: {title}")
    index = found[0]
    body: list[str] = []
    for line in lines[index + 1:]:
        if re.match(r"^# (?!#)", line):
            break
        body.append(line)
    answer = "\n".join(body).strip()
    if not answer:
        raise ValueError(f"empty section: {title}")
    return answer


def _optional_abs(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("TheoryMaster paths must be absolute")
    return path


def scientific_sections(
    text: str, *, allow_missing_scientific_model: bool = False,
) -> tuple[str, str, str, str]:
    """Validate the canonical H1 Research Plan layout and return runtime contract text."""
    if text.startswith("---\n"):
        raise ValueError("research_plan.md frontmatter is unsupported")
    _validate_research_plan_layout(
        text, allow_missing_scientific_model=allow_missing_scientific_model,
    )
    goal = _section(text, "Goal")
    contract = _section(text, "Research Contract")
    final_gate = _section(text, "Final Gate")
    required_context = _section(text, "Required Context")
    return goal, contract, final_gate, required_context



@dataclass(frozen=True)
class TaskSpec:
    document: Path; goal_id: str; revision: int; max_cycles: int; max_rounds: int
    research_root: Path; branch_canvas: Path | None
    branch_anchor_id: str | None; next_step_start_id: str | None; notes_mirror_dir: Path | None
    goal: str; contract: str; final_gate: str; required_context: str
    master_id: int = 0
    task_mode: bool = False
    max_judge_reworks_per_cycle: int = 3
    max_total_investigation_rounds_per_cycle: int | None = None
    config_path: Path | None = None
    state_seed: Path | None = None
    task_home: Path | None = None
    legacy_research_root: Path | None = None
    benchmark_dir: Path | None = None
    # Explicit logical Cycle ID used only by isolated checkpoint replay.
    replay_cycle_id: str | None = None

    def __post_init__(self) -> None:
        # Judge rework is the only active round-like permission. Historical
        # round aliases must not block a modern Token-budgeted task.
        if self.max_judge_reworks_per_cycle < 0:
            raise ValueError("invalid Judge rework limit")
        if self.max_rounds < 1:
            object.__setattr__(self, "max_rounds", 1)
        if self.max_total_investigation_rounds_per_cycle is None or self.max_total_investigation_rounds_per_cycle < 1:
            object.__setattr__(
                self, "max_total_investigation_rounds_per_cycle",
                self.max_rounds + self.max_judge_reworks_per_cycle,
            )

    @property
    def max_rounds_per_investigation_attempt(self) -> int:
        return self.max_rounds

    @property
    def max_total_capability_calls_per_cycle(self) -> int:
        """Historical read-only alias for pre-Investigator callers."""
        return int(self.max_total_investigation_rounds_per_cycle or 0)

    @property
    def benchmark_root(self) -> Path | None:
        """Optional task-local frozen/scientific input convention."""
        home = self.task_home or self.document.parent
        candidate = self.benchmark_dir or home / "benchmark"
        return candidate if candidate.is_dir() else None

    @classmethod
    def load(cls, document: Path) -> "TaskSpec":
        document = document.expanduser().resolve()
        text = document.read_text(encoding="utf-8")
        if text.startswith("---\n"):
            raise ValueError("legacy frontmatter research_plan format is unsupported; backward compatibility is disabled")
        from .task import TASK_CONFIG_NAME, TASK_DIR_NAME, TaskBinding
        pointer = document.parent / TASK_DIR_NAME / TASK_CONFIG_NAME
        if pointer.is_file():
            config_path = TaskBinding.load(document.parent).config_path
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if Path(config["plan_path"]).expanduser().resolve() != document:
                raise ValueError("task pointer config does not match requested research_plan.md")
        else:
            from .task import bootstrap
            config_path = bootstrap(document).config_path
        return cls.from_config(config_path)

    @classmethod
    def from_config(cls, config_path: Path) -> "TaskSpec":
        from .task import SYSTEM_SCHEMA, absolute, effective_master_id, effective_path_model
        from .embedded_state import validate_current
        from .scientific_model import load_from_paths as load_scientific_model
        config_path = config_path.expanduser().resolve()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("schema") != SYSTEM_SCHEMA:
            raise ValueError("invalid TheoryMaster system config schema")
        document = absolute(config["plan_path"], "plan_path")
        task_home, root, legacy_research_root = effective_path_model(config)
        root.mkdir(parents=True, exist_ok=True)
        validate_current(document, config)
        load_scientific_model(root, document, goal_id=str(config["goal_id"]))
        goal, contract, final_gate, context = scientific_sections(document.read_text(encoding="utf-8"))
        branch = absolute(config["branch_canvas"], "branch_canvas")
        notes = absolute(config["notes_dir"], "notes_dir")
        def legacy_round_alias(key: str, default: int) -> int:
            # Read-only migration/telemetry; obsolete budget keys are not guards.
            try:
                return max(1, int(config.get(key, default)))
            except (ValueError, TypeError):
                return default

        spec = cls(document, str(config["goal_id"]), 1, int(config["max_cycles"]),
                   legacy_round_alias("max_rounds_per_investigation_attempt", 1), root, branch,
                   config.get("branch_anchor_id"), None, notes,
                   goal, contract, final_gate, context, master_id=effective_master_id(config), task_mode=True,
                   max_judge_reworks_per_cycle=int(config.get("max_judge_reworks_per_cycle", 3)),
                   max_total_investigation_rounds_per_cycle=legacy_round_alias(
                       "max_total_investigation_rounds_per_cycle",
                       legacy_round_alias("max_total_capability_calls_per_cycle", 1 + int(config.get("max_judge_reworks_per_cycle", 3))),
                   ),
                   config_path=config_path,
                   state_seed=absolute(config["state_seed"], "state_seed") if config.get("state_seed") else None,
                   task_home=task_home,
                   legacy_research_root=legacy_research_root,
                   benchmark_dir=absolute(config["benchmark_root"], "benchmark_root") if config.get("benchmark_root") else None,
                   )
        from .role_registry import ROLES
        from .skills import role_skills
        for role in ROLES:
            role_skills(role, task_config_path=config_path)
        spec._validate_external_branch()
        return spec

    @classmethod
    def from_task_root(cls, discovery_root: Path) -> "TaskSpec":
        """Historical method name retained; argument may be Task Home or runtime root."""
        from .task import TaskBinding
        return cls.from_config(TaskBinding.load(discovery_root).config_path)

    def _validate_external_branch(self, next_step_start_id: str | None = None) -> None:
        if self.branch_canvas is None:
            raise ValueError("task configuration must define a Canvas projection path")
        if self.notes_mirror_dir is None or not self.notes_mirror_dir.is_dir():
            raise ValueError("notes directory is missing")
        graph_path = self.branch_canvas.resolve().parent / "science_graph.sqlite3"
        if not graph_path.is_file():
            raise FileNotFoundError(
                f"canonical science_graph.sqlite3 is required: {graph_path}; "
                "Canvas-only runtime tasks are not supported"
            )
        import sqlite3
        with sqlite3.connect(f"file:{graph_path}?mode=ro", uri=True) as db:
            known_h = {str(row[0]) for row in db.execute("SELECT id FROM hypotheses")}
        for node_id in (self.branch_anchor_id, next_step_start_id or self.next_step_start_id):
            if node_id and node_id not in known_h:
                raise ValueError(f"configured research anchor is not an H in SQLite: {node_id}")

    @classmethod
    def from_canvas(cls, canvas: Path) -> "TaskSpec":
        document = bound_document(canvas)
        return cls.load(document)

    @property
    def goal_sha256(self) -> str:
        return hashlib.sha256("\n".join((self.goal, self.contract, self.required_context)).encode()).hexdigest()

def bound_document(canvas: Path) -> Path:
    data = json.loads(canvas.read_text(encoding="utf-8")); hits = []
    pattern = re.compile(rf"<!--\s*{MARKER}:\s*(.*?)\s*-->")
    for node in data.get("nodes", []):
        if node.get("type") == "text": hits += pattern.findall(str(node.get("text", "")))
    if len(hits) != 1: raise ValueError("control Canvas must contain exactly one task-document binding")
    path = Path(hits[0]).expanduser()
    if not path.is_absolute(): raise ValueError("task document binding must be absolute")
    return path.resolve()


def migration_preview(old_state: Path) -> dict[str, Any]:
    old = json.loads(old_state.read_text(encoding="utf-8"))
    return {
        "source": str(old_state.resolve()),
        "writes": [],
        "goal_id": old.get("goal_id"),
        "recommended": "Create a plain Markdown research_plan.md and a theory-master/system/v1 config; import this state as an explicit seed after human review.",
    }
