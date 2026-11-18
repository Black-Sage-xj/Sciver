"""One explicit local-material boundary for Goal revisions and frozen access."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlsplit

MATERIAL_PATH_KEYS = frozenset(
    {
        "path",
        "reference",
        "image_ref",
        "manifest_path",
        "receipt_path",
        "detail_ref",
        "dossier_ref",
        "report_ref",
        "document_ref",
        "research_root",
        "index_path",
        "revision_path",
    }
)
_HISTORY_KEYS = frozenset({"goal_gate", "goal_gate_history"})


def material_roots(spec: Any) -> tuple[Path, ...]:
    return tuple(
        sorted(
            {
                Path(spec.research_root).resolve(),
                Path(spec.document).parent.resolve(),
                Path(spec.branch_canvas).parent.resolve(),
            },
            key=str,
        )
    )


def material_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_material(spec: Any, reference: str) -> dict[str, str]:
    """Describe access without granting access to inherited or unsafe paths."""
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc:
        return {"reference": reference, "status": "external"}
    path = Path(unquote(parsed.path))
    candidates = (
        [path]
        if path.is_absolute()
        else [
            Path(spec.branch_canvas).parent / path,
            Path(spec.research_root) / path,
        ]
    )
    roots = material_roots(spec)
    safe: dict[str, Path] = {}
    rejected = "missing"
    for candidate in candidates:
        # Registered Hypothesis dossiers use Obsidian's extensionless stems.
        if (
            not candidate.suffix
            and not candidate.exists()
            and candidate.with_suffix(".md").is_file()
        ):
            candidate = candidate.with_suffix(".md")
        resolved = candidate.resolve()
        if not any(resolved.is_relative_to(base) for base in roots):
            rejected = "outside_registered_roots"
        elif any(part.is_symlink() for part in (candidate, *candidate.parents)):
            rejected = "symlink"
        elif resolved.is_file() or resolved.is_dir():
            safe[str(resolved)] = resolved
    if len(safe) != 1:
        return {"reference": reference, "status": "ambiguous" if safe else rejected}
    resolved = next(iter(safe.values()))
    row = {"reference": reference, "path": str(resolved)}
    if resolved.is_file():
        return {**row, "status": "available", "sha256": material_sha256(resolved)}
    return {**row, "status": "directory"}


def goal_material_sources(spec: Any, values: Iterable[Any]) -> list[dict[str, str]]:
    """Hash explicit Judge materials, excluding self-referential Goal receipts.

    A prior Goal assessment is already represented by its SQLite verdict and
    rationale. Its receipt is provenance, not a new current delivery input.
    No workspace or linked-directory scan is performed here.
    """
    references: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for name, item in value.items():
                if name not in _HISTORY_KEYS:
                    visit(item, name)
        elif isinstance(value, list):
            for item in value:
                visit(item, key)
        elif key in MATERIAL_PATH_KEYS and isinstance(value, str) and value:
            references.add(value)

    for value in values:
        visit(value)
    return [resolve_material(spec, ref) for ref in sorted(references)]


def rewrite_markdown_navigation(text: str, destination: Callable[[str], str]) -> str:
    """Change Markdown destinations only, preserving prose and literal code.

    Relative and remote targets are retained by the caller. This intentionally
    does not interpret code, tabular results, or arbitrary document strings.
    """

    def target_span(line: str, start: int) -> tuple[int, int] | None:
        while start < len(line) and line[start] in " \t":
            start += 1
        if start >= len(line):
            return None
        if line[start] == "<":
            end = line.find(">", start + 1)
            return (start + 1, end) if end >= 0 else None
        end, depth = start, 0
        while end < len(line):
            char = line[end]
            if char == "\\" and end + 1 < len(line):
                end += 2
                continue
            if char.isspace() or (char == ")" and depth == 0):
                break
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            end += 1
        return (start, end) if end > start else None

    def active_text(line: str) -> str:
        spans = [match.end() for match in re.finditer(r"(?<!\\)\]\(", line)]
        definition = re.match(r"^ {0,3}\[[^]\n]+\]:[ \t]*", line)
        if definition:
            spans.append(definition.end())
        changes = []
        for start in spans:
            span = target_span(line, start)
            if span is None:
                continue
            begin, end = span
            original = line[begin:end]
            replacement = destination(original)
            if replacement != original:
                changes.append((begin, end, replacement))
        for begin, end, replacement in sorted(changes, reverse=True):
            line = line[:begin] + replacement + line[end:]
        return line

    lines = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            lines.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1]:
                if not line[marker.end() :].strip():
                    fence = None
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
            lines.append(line)
            continue
        if line.startswith(("    ", "\t")):
            lines.append(line)
            continue
        # Inline code can contain convincing Markdown examples. Preserve its
        # bytes as well as fenced and indented code blocks.
        pieces, position = [], 0
        for literal in re.finditer(r"(`+).*?\1", line):
            pieces.append(active_text(line[position : literal.start()]))
            pieces.append(literal[0])
            position = literal.end()
        pieces.append(active_text(line[position:]))
        lines.append("".join(pieces))
    return "".join(lines)
