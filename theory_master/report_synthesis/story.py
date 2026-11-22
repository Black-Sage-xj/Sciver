"""A linear reading sequence of the report's sourced research milestones.

The arrows connect narrative stages. Recorded scientific derivation remains in
the frozen graph and is used only to retain necessary legacy turning points.
"""
from __future__ import annotations

from collections.abc import Mapping
import html
import re
import unicodedata
from typing import Any

from .reading import cycle_reading

STORY_WIDTH = 420
STORY_CAPTION = "按关键问题、检验和转向整理的研究主线；箭头表示叙述推进，各阶段可回查原始来源。"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _refs(value: Any) -> list[str]:
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item)) if isinstance(value, list) else []


def research_story(report: Mapping[str, Any], snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Keep explicit stage order, or project an old author's important path.

    Legacy selection never creates new prose or orders nodes from their IDs.
    Main ancestors are retained only when the author already put them in the
    research path; other branches remain available in the original report.
    """
    if "research_story" in report:
        values = report["research_story"]
        if not isinstance(values, list):
            return []
        return [{**{key: _text(stage.get(key)) for key in ("title", "action", "finding", "transition")},
                 **{key: _refs(stage.get(key)) for key in ("hypothesis_ids", "outcome_ids", "source_refs")}}
                for stage in values if isinstance(stage, Mapping)]

    values = report.get("research_path", [])
    if not isinstance(values, list):
        return []
    path = [step for step in values if isinstance(step, Mapping) and _text(step.get("hypothesis_id"))]
    seeds = {step["hypothesis_id"] for step in path if step.get("role") in {"main", "turning_point"}}
    keep = set(seeds)
    if seeds:
        parents: dict[str, set[str]] = {}
        for edge in snapshot.get("lineage", []):
            if (isinstance(edge, Mapping) and edge.get("role") == "main"
                    and isinstance(edge.get("source_hypothesis"), str)
                    and isinstance(edge.get("target_hypothesis"), str)):
                parents.setdefault(edge["target_hypothesis"], set()).add(edge["source_hypothesis"])
        pending = list(seeds)
        while pending:
            for parent in parents.get(pending.pop(), ()):
                if parent not in keep:
                    keep.add(parent)
                    pending.append(parent)
        path = [step for step in path if step["hypothesis_id"] in keep]
    hypotheses = {row["id"]: row for row in snapshot.get("hypotheses", [])
                  if isinstance(row, Mapping) and isinstance(row.get("id"), str)}
    return [{"title": "检验：" + (_text(hypotheses.get(step["hypothesis_id"], {}).get("title")) or step["hypothesis_id"]),
             "action": "", "finding": _text(step.get("explanation")), "transition": "",
             "hypothesis_ids": [step["hypothesis_id"]], "outcome_ids": _refs(step.get("outcome_ids")),
             "source_refs": _refs(step.get("source_refs"))}
            for step in path]


def _wrap(text: str, units: int) -> list[str]:
    """Wrap full Unicode text using CJK display widths without ellipses."""
    if not text:
        return []
    result = []
    for paragraph in text.expandtabs(4).split("\n"):
        line, used = "", 0
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*|.", paragraph):
            sizes = [0 if unicodedata.combining(character) else 2
                     if unicodedata.east_asian_width(character) in {"W", "F"} else 1 for character in token]
            if sum(sizes) <= units:
                if line and used + sum(sizes) > units:
                    result.append(line)
                    line, used = "", 0
                line += token
                used += sum(sizes)
            else:
                for character, size in zip(token, sizes):
                    if line and used + size > units:
                        result.append(line)
                        line, used = "", 0
                    line += character
                    used += size
        result.append(line)
    return result


def story_svg(report: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str:
    """Render sourced stages as one readable column, with narrative arrows."""
    stages = research_story(report, snapshot)
    if not stages:
        return ""
    reading = cycle_reading(report, snapshot)
    ref_cycles = reading["ref_cycles"]
    layouts = []
    for number, stage in enumerate(stages, 1):
        title = _wrap(f"{number}. {stage['title']}", 36)
        paragraphs = []
        for key, label in (("action", "开展"), ("finding", "发现"), ("transition", "推进")):
            if stage[key]:
                paragraphs.append(_wrap((label + "：" if stage["action"] or stage["transition"] else "") + stage[key], 40))
        identities = stage["outcome_ids"] + stage["hypothesis_ids"] + stage["source_refs"]
        cycle_ids = {cycle for ref in identities for cycle in ref_cycles.get(ref, [])}
        source_labels = [row["title"] for row in reading["cycles"].values() if row["cycle_id"] in cycle_ids]
        references = _wrap("来源：" + "；".join(source_labels), 57) if source_labels else []
        height = 34 + len(title) * 28 + sum(len(lines) * 26 + 10 for lines in paragraphs) + len(references) * 19 + 12
        layouts.append((stage, title, paragraphs, references, height))
    height = 36 + sum(row[4] for row in layouts) + 44 * (len(layouts) - 1)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{STORY_WIDTH}" viewBox="0 0 {STORY_WIDTH} {height}" role="img" aria-labelledby="story-title story-description">',
             '<title id="story-title">研究主线概览</title>',
             '<desc id="story-description">' + html.escape(STORY_CAPTION) + '</desc>',
             '<defs><marker id="story-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#526877"/></marker></defs>',
             f'<rect width="{STORY_WIDTH}" height="{height}" fill="#f6f8fa"/>']
    y = 18
    for number, (stage, title, paragraphs, references, box_height) in enumerate(layouts, 1):
        provenance = "；".join(stage["source_refs"])
        parts.extend([f'<g data-story-stage="{number}">',
                      '<title>' + html.escape(stage["title"]) + '</title>',
                      '<metadata>来源引用：' + html.escape(provenance) + '</metadata>',
                      f'<rect x="18" y="{y}" width="384" height="{box_height}" rx="5" fill="#fff" stroke="#ccd6df"/>'])
        baseline = y + 30
        for line in title:
            parts.append(f'<text x="34" y="{baseline}" fill="#265c85" font-size="19" font-weight="600" font-family="sans-serif">{html.escape(line)}</text>')
            baseline += 28
        baseline += 6
        for lines in paragraphs:
            for line in lines:
                parts.append(f'<text x="34" y="{baseline}" fill="#1c2e3d" font-size="17" font-family="sans-serif">{html.escape(line)}</text>')
                baseline += 26
            baseline += 10
        baseline += 2
        for line in references:
            parts.append(f'<text x="34" y="{baseline}" fill="#526877" font-size="12" font-family="sans-serif">{html.escape(line)}</text>')
            baseline += 19
        parts.append('</g>')
        y += box_height
        if number < len(layouts):
            parts.append(f'<path data-kind="narrative-step" d="M210,{y+8} L210,{y+34}" fill="none" stroke="#526877" stroke-width="1.5" marker-end="url(#story-arrow)"/>')
            y += 44
    parts.append('</svg>')
    return "\n".join(parts)
