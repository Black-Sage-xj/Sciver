"""Read-only review reports archived separately in each workspace's state directory."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path


_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,95}\Z")


def read_report(root: Path, slug: str) -> dict:
    if not _SLUG.fullmatch(slug):
        raise ValueError("invalid report id")
    folder = root / "reviews" / slug
    if folder.is_symlink():
        raise ValueError("invalid report directory")
    return json.loads((folder / "report.json").read_text(encoding="utf-8"))


def attachment(root: Path, slug: str, filename: str) -> bytes:
    report = read_report(root, slug)
    allowed = {item["file"] for item in report.get("details", [])}
    if filename not in allowed or Path(filename).name != filename:
        raise ValueError("unknown attachment")
    path = root / "reviews" / slug / filename
    if path.is_symlink():
        raise ValueError("invalid attachment")
    return path.read_bytes()


def page_body(root: Path, slug: str = "") -> str:
    escape = html.escape
    style = """<style>
    .review-report{max-width:1040px;margin:auto}
    .review-report .review-meta{color:var(--tm-muted);font-size:13px}
    .review-report .review-result{font-size:17px;line-height:1.7;margin:22px 0}
    .review-report .review-table{overflow-x:auto;margin:20px 0}
    .review-report th,.review-report td{min-width:135px;font-variant-numeric:tabular-nums}
    .review-report th:first-child{min-width:170px}
    .review-report details{border-top:1px solid var(--tm-border);padding:14px 0}
    .review-report summary{cursor:pointer;font-weight:600}
    .review-report pre{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:14px;line-height:1.8;max-width:100%}
    .review-report .review-download{display:inline-block;margin:12px 0}
    </style>"""
    if not slug:
        entries = []
        for path in sorted((root / "reviews").glob("*/report.json"), reverse=True):
            try:
                report = read_report(root, path.parent.name)
            except (OSError, ValueError):
                continue
            entries.append('<p><a href="/reviews/' + path.parent.name + '/">'
                           + escape(report["title"]) + '</a><br><span class="review-meta">'
                           + escape(report["reviewed_at"]) + '</span></p>')
        return style + '<article class="review-report"><h1>审查报告</h1>' + (
            ''.join(entries) or '<p>暂无已归档的审查报告。</p>') + '</article>'
    report = read_report(root, slug)
    columns = ''.join('<th scope="col">' + escape(v) + '</th>' for v in report["columns"])
    rows = ''.join('<tr><th scope="row">' + escape(row[0]) + '</th>'
                   + ''.join('<td>' + escape(v) + '</td>' for v in row[1:]) + '</tr>'
                   for row in report["metrics"])
    notes = ''.join('<p class="review-meta">' + escape(v) + '</p>' for v in report["notes"])
    details = []
    for item in report.get("details", []):
        name = item["file"]
        href = '/reviews/' + slug + '/files/' + name
        download = '<a class="review-download" href="' + escape(href, quote=True) + '">下载原始记录</a>'
        text = attachment(root, slug, name).decode("utf-8")
        details.append('<details><summary>' + escape(item["title"]) + '</summary>'
                       + download + '<pre>' + escape(text) + '</pre></details>')
    return (style + '<article class="review-report"><p><a href="/reviews/">全部审查报告</a></p><h1>'
            + escape(report["title"]) + '</h1><p class="review-meta">'
            + escape(report["reviewed_at"]) + ' · ' + escape(report["scope"]) + '</p>'
            + '<p class="review-result">' + escape(report["result"]) + '</p>'
            + '<div class="review-table"><table><thead><tr>' + columns
            + '</tr></thead><tbody>' + rows + '</tbody></table></div>' + notes
            + ''.join(details) + '</article>')
