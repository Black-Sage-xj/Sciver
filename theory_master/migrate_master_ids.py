"""One-shot, auditable migration from legacy TheoryMaster cycle names to Master IDs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from theory_master.runtime import atomic
from theory_master.specification import CYCLE_MARKER, MASTER_RUN_DIR_RE, TaskSpec, is_cycle_dir

TEXT_SUFFIXES = {".canvas", ".json", ".jsonl", ".md", ".log", ".txt", ".py"}


def _legacy_cycles(root: Path, goal_id: str) -> list[Path]:
    return sorted(path for path in root.iterdir() if is_cycle_dir(path) and not MASTER_RUN_DIR_RE.fullmatch(path.name)
                  and path.name.endswith(f"-{goal_id}"))


def _replace_text(path: Path, replacements: dict[str, str]) -> None:
    if path.suffix not in TEXT_SUFFIXES:
        return
    try:
        before = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return
    after = before
    for old, new in replacements.items():
        after = after.replace(old, new)
    if after != before:
        atomic(path, after)


def migrate(spec: TaskSpec, *, dry_run: bool = False) -> dict[str, str]:
    root = spec.research_root.resolve()
    legacy = _legacy_cycles(root, spec.goal_id)
    if not legacy:
        return {}
    existing = [path for path in root.iterdir() if MASTER_RUN_DIR_RE.fullmatch(path.name)]
    if existing:
        raise ValueError("refusing mixed migration: tm-#### directories already exist")
    mapping = {path.name: f"tm-{index:04d}-{spec.goal_id}" for index, path in enumerate(legacy)}
    if any((root / new).exists() for new in mapping.values()):
        raise ValueError("a Master ID destination already exists")
    if dry_run:
        return mapping
    for cycle in legacy:
        for path in cycle.rglob("*"):
            if path.is_file():
                _replace_text(path, mapping)
    if spec.branch_canvas is not None:
        _replace_text(spec.branch_canvas, mapping)
    # Master IDs are encoded by the historical cycle directory names.
    for old, new in mapping.items():
        (root / old).rename(root / new)
    return mapping


def repair_references(root: Path, replacements: dict[str, str]) -> None:
    """Repair textual absolute-path references after an interrupted older migration."""
    for path in root.rglob("*"):
        if path.is_file():
            _replace_text(path, replacements)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repair-mapping", help="JSON object of old directory name to new directory name")
    args = parser.parse_args()
    spec = TaskSpec.load(args.plan)
    if args.repair_mapping:
        mapping = json.loads(args.repair_mapping)
        if not isinstance(mapping, dict) or not all(isinstance(old, str) and isinstance(new, str) for old, new in mapping.items()):
            parser.error("--repair-mapping must be a JSON object of string pairs")
        repair_references(spec.research_root, mapping)
    else:
        mapping = migrate(spec, dry_run=args.dry_run)
    print(json.dumps(mapping, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
