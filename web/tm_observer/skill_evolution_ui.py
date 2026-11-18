"""Read-only exact-proposal view; never imports the SDK or follows ledger paths."""
from __future__ import annotations

import difflib
import html
import json
import re
from typing import Any

from .run_monitor import _safe_cycle, redact


def proposal_details(run: dict[str, Any], proposal_id: str) -> str:
    if not re.fullmatch(r"SKP-[a-f0-9]{24}", proposal_id):
        return ""
    cycle = _safe_cycle(run)
    if cycle is None:
        return ""
    path = cycle.parent / ".theory-master/skill-evolution/proposals" / (proposal_id + ".json")
    if any(p.is_symlink() for p in (path, *path.parents)):
        return ""
    try:
        if path.stat().st_size > 64_000:
            return ""
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict) or data.get("id") != proposal_id:
        return ""
    before, after = data.get("before_text", ""), data.get("after_text", "")
    if not isinstance(before, str) or not isinstance(after, str):
        return ""
    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                       fromfile="previous/SKILL.md", tofile="current/SKILL.md"))
    e = lambda value: html.escape(redact(str(value)), quote=True)
    changes = data.get("supporting_file_changes", [])
    files = ""
    if isinstance(changes, list):
        files = "".join('<p>辅助文件 ' + e(item.get("path")) + '：'
                        + e(str(item.get("from_sha256") or "new")[:12]) + ' → '
                        + e(str(item.get("to_sha256") or "removed")[:12]) + '</p>'
                        for item in changes[:6] if isinstance(item, dict))
    absorbed = data.get("absorbed_skills", [])
    if isinstance(absorbed, list):
        files += "".join('<p>合并后归档：' + e(item.get("name")) + ' @ '
                         + e(str(item.get("version", ""))[:12]) + '</p>'
                         for item in absorbed[:4] if isinstance(item, dict))
    return ('<details class="tm-skill-change-details"><summary>查看技能变更与提请</summary>'
            '<p>' + e(proposal_id) + ' · ' + e(data.get("state")) + '</p>'
            '<p>' + e(data.get("reason")) + '</p>'
            '<p>来源：正常科研轨迹；未进行专门效果评测。新版本仅在后续安全调用采用。</p>'
            '<pre>' + e(diff[:24_000]) + '</pre>' + files + '</details>')
