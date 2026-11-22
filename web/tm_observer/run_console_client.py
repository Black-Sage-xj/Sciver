"""Render one data boundary for the console's independently checked client asset."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _asset() -> str:
    return Path(__file__).with_name("run_console.js").read_text(encoding="utf-8")


def script(observer_id: str, *, selected_worker: str | None, selected_history_cycle: str,
           secondary: str, pinned_worker_account: str | None,
           quota_snapshot: dict[str, Any] | None, hot_role_route: bool) -> str:
    config = json.dumps({
        "observerId": observer_id, "selectedWorker": selected_worker,
        "selectedHistoryCycle": selected_history_cycle, "secondary": secondary,
        "pinnedAccount": "account-" + pinned_worker_account if pinned_worker_account else None,
        "quotaSnapshot": quota_snapshot if isinstance(quota_snapshot, dict) else None,
        "hotRoleRoute": hot_role_route,
    }, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("&", "\\u0026")
    return ('<script id="tm-run-config" type="application/json">' + config + '</script>'
            '<script>' + _asset() + '</script>')
