"""Read-only report sections and versioned Markdown supplements.

These helpers organize existing scientific material; they never adjudicate it.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .report_materials import _CODE

DOCUMENT_EMBED = re.compile(r"!\[\[(?P<target>[^\]\n]+?\.md(?:#[^\]|\n]+)?)(?:\|(?P<label>[^\]\n]+))?\]\]", re.I)
MAX_SUPPLEMENT_BYTES = 256 * 1024
MAX_SUPPLEMENTS = 12


def outside_code(text: str, transform) -> str:
    parts, end = [], 0
    for code in _CODE.finditer(text):
        parts.extend((transform(text[end:code.start()]), code.group()))
        end = code.end()
    parts.append(transform(text[end:]))
    return "".join(parts)


def sections(text: str) -> list[dict[str, Any]]:
    """Stable section offsets; headings inside literal code are not navigation."""
    masked = _CODE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)
    headings = list(re.finditer(r"(?m)^(#{1,6})[ \t]+([^\n]+)\s*$", masked))
    used: dict[str, int] = {}
    result = []
    for index, match in enumerate(headings):
        title = match[2].strip()
        slug = re.sub(r"[^\w\s-]", "", title.casefold()).strip().replace(" ", "-") or "section"
        count = used.get(slug, 0)
        used[slug] = count + 1
        ident = slug + (f"-{count}" if count else "")
        level = len(match[1])
        end = next((other.start() for other in headings[index + 1:]
                    if len(other[1]) <= level), len(text))
        result.append({"id": ident, "title": title, "level": level,
                       "start": match.start(), "end": end})
    return result


def section_text(text: str, selector: str = "") -> str:
    if not selector:
        return text
    selector = unquote(selector)
    matches = [row for row in sections(text) if row["id"] == selector or row["title"] == selector]
    if len(matches) != 1:
        raise ValueError("报告章节不存在或名称有歧义")
    row = matches[0]
    return text[row["start"]:row["end"]]


def snapshot_documents(body: str, *, task_root: Path, note_path: Path,
                       materials: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Freeze explicitly embedded task documents / admitted document assets."""
    root = task_root.resolve()
    registered = {Path(a["path"]).resolve() for row in materials for a in row["assets"]
                  if a.get("path") and a.get("kind") == "document" and a.get("status") == "available"}
    found = []
    outside_code(body, lambda text: found.extend(DOCUMENT_EMBED.finditer(text)) or text)
    result = []
    for match in found:
        target = match["target"]
        if any(row["reference"] == target for row in result):
            continue
        if len(result) >= MAX_SUPPLEMENTS:
            raise ValueError("too many embedded report documents")
        ref, _, anchor = target.partition("#")
        path = Path(unquote(ref))
        candidates = [path] if path.is_absolute() else [root / path, note_path.parent / path]
        hits = set()
        for candidate in candidates:
            resolved = candidate.resolve()
            in_vault = resolved.is_relative_to(root) and resolved.relative_to(root).parts[:1] in {
                ("notes",), ("nodes",), ("literature",)}
            if (in_vault or resolved in registered) and resolved.suffix.lower() == ".md" and resolved.is_file():
                hits.add(resolved)
        if len(hits) != 1:
            raise ValueError(f"embedded report document is missing, ambiguous or outside task: {ref}")
        source = hits.pop()
        with source.open("rb") as stream:
            raw = stream.read(MAX_SUPPLEMENT_BYTES + 1)
        if len(raw) > MAX_SUPPLEMENT_BYTES:
            raise ValueError(f"embedded report document exceeds snapshot limit: {ref}; use a normal link")
        content = section_text(raw.decode("utf-8"), anchor)
        # Saved reports live beside other Outcomes; preserve relative links in
        # the frozen source excerpt when it is presented in that report.
        def rebase(text: str) -> str:
            def link(m):
                value = m[2]
                if urlsplit(value).scheme or value.startswith(("/", "#")):
                    return m.group()
                relative = os.path.relpath(source.parent / unquote(value), note_path.parent)
                return m[1] + "(" + relative.replace(" ", "%20") + ")"
            return re.sub(r"(!?\[[^\]\n]*\])\(([^\s)]+)\)", link, text)
        content = outside_code(content, rebase)
        source_ref = source.relative_to(root).as_posix() if source.is_relative_to(root) else str(source)
        result.append({"reference": target, "source_ref": source_ref, "section": anchor,
                       "title": match["label"] or anchor or source.stem,
                       "sha256": hashlib.sha256(raw).hexdigest(), "content": content})
    return result


def expand_documents(body: str, documents: list[dict[str, str]]) -> str:
    by_ref = {row["reference"]: row for row in documents}
    def expand(match):
        row = by_ref.get(match["target"])
        if row is None:
            # Historical material without a captured version stays a source
            # link; rendering must not silently read today's file contents.
            return "[[" + match["target"] + "|" + (match["label"] or match["target"]) + "]]"
        content = outside_code(row["content"], lambda text: DOCUMENT_EMBED.sub(
            lambda m: "[[" + m["target"] + "|继续阅读来源文档]]", text))
        content = outside_code(content, lambda text: re.sub(r"(?m)^(#{1,6})(?=[ \t])",
            lambda m: "#" * min(6, len(m[1]) + 2), text))
        source = html.escape(row["source_ref"] + ("#" + row["section"] if row["section"] else ""))
        return ("\n<details class=\"research-supplement\">\n<summary>补充材料 · " +
                html.escape(row["title"]) + "</summary>\n\n来源：<code>" + source +
                "</code> · 固定版本 <code title=\"SHA-256: " + row["sha256"] + "\">" + row["sha256"][:12] +
                "</code>\n\n" + content + "\n\n</details>\n")
    return outside_code(body, lambda text: DOCUMENT_EMBED.sub(expand, text))


def fold_legacy_summary(body: str, *, has_guide: bool) -> str:
    """Preserve an older report's independent abstract without leading twice."""
    rows = sections(body)
    first = next((row for row in rows if row["level"] == 2), None)
    if not has_guide or not first or not re.fullmatch(
            r"(?:结论摘要|conclusion summary)(?:\s*/.*)?", first["title"].casefold()):
        return body
    return (body[:first["start"]] + "<details class=\"research-legacy-summary\">\n"
            "<summary>原报告摘要</summary>\n\n" + body[first["start"]:first["end"]] +
            "\n</details>\n\n" + body[first["end"]:])


def load_report(database: Path, cycle_id: str, *, vault_root: Path | None = None) -> dict[str, Any]:
    """Read the sole Markdown report; scientific status stays in the graph store."""
    from .science_graph_store import ScienceGraphStore
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cycle_id):
        raise ValueError("invalid Cycle ID")
    with ScienceGraphStore(database, readonly=True) as store:
        tables = {row["name"] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        row = (store.db.execute("SELECT source_json FROM cycle_evaluations WHERE cycle_id=?", (cycle_id,)).fetchone()
               if "cycle_evaluations" in tables else None)
        adjudicated = row is not None
        if row is None and "cycle_preservations" in tables:
            row = store.db.execute("SELECT source_json FROM cycle_preservations WHERE cycle_id=?", (cycle_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown Cycle report: {cycle_id}")
        outcome = json.loads(row["source_json"])
    if vault_root is None:
        from .notebook import configured_vault_root, notebook_config
        registered = notebook_config(database.resolve().parent)
        vault_root = configured_vault_root(registered[1]) if registered else database.resolve().parent
    root = vault_root.resolve()
    ref = str(outcome.get("report_ref") or f"notes/outcomes/{cycle_id}.md")
    path = Path(ref)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".md":
        raise ValueError(f"Cycle report must reference a Markdown file inside its vault: {ref}")
    source = (root / path).resolve()
    if not source.is_relative_to(root):
        raise ValueError(f"Cycle report is outside its vault: {ref}")
    if not source.is_file():
        raise FileNotFoundError(f"Cycle report Markdown is missing: {ref}")
    raw = source.read_bytes()
    content = raw.decode("utf-8")
    return {"id": path.as_posix(), "cycle_id": cycle_id,
            "title": str(outcome.get("title") or f"Cycle {cycle_id} · 研究报告"),
            "content": content, "source_sha256": hashlib.sha256(raw).hexdigest(),
            "adjudication_status": "adjudicated" if adjudicated else "unadjudicated",
            "judge_verdict": outcome.get("verdict") if adjudicated else None,
            "sections": [{k: r[k] for k in ("id", "title", "level")} for r in sections(content)]}


def read_report(database: Path, cycle_id: str, *, section: str = "", offset: int = 0,
                max_chars: int = 12_000, vault_root: Path | None = None) -> dict[str, Any]:
    result = load_report(database, cycle_id, vault_root=vault_root)
    text = section_text(result["content"], section)
    if offset < 0 or offset > len(text) or max_chars < 1:
        raise ValueError("invalid report offset or length")
    end = min(len(text), offset + max_chars)
    result.update(content=text[offset:end], section=section, next_offset=end if end < len(text) else None)
    return result


def report_cycle_for_path(database: Path, path: str) -> str | None:
    """Resolve old Outcome/narrative document links without guessing a Cycle."""
    from .science_graph_store import ScienceGraphStore
    if not database.is_file():
        return None
    with ScienceGraphStore(database, readonly=True) as store:
        canonical = re.fullmatch(r"notes/outcomes/([A-Za-z0-9_-]{1,64})\.md", path)
        hits = set()
        for table in ("cycle_evaluations", "cycle_preservations"):
            if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            if canonical and store.db.execute(f"SELECT 1 FROM {table} WHERE cycle_id=?", (canonical[1],)).fetchone():
                hits.add(canonical[1])
            for row in store.db.execute(
                f"SELECT cycle_id FROM {table} WHERE json_extract(source_json,'$.report_ref') IN (?,?)",
                (path, path.removesuffix(".md")),
            ):
                hits.add(str(row["cycle_id"]))
        return next(iter(hits)) if len(hits) == 1 else None
