"""Immutable service dependencies and per-server request context."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class RouteServices:
    _console_cookie: Callable[..., Any]
    _console_landing: Callable[..., Any]
    _csv_or_lines: Callable[..., Any]
    _page: Callable[..., Any]
    _remembered_console: Callable[..., Any]
    _synced_field_notes: Callable[..., Any]
    _workflow_unavailable_html: Callable[..., Any]
    architecture_html: Callable[..., Any]
    canvas_md_get: Callable[..., Any]
    canvas_md_image_get: Callable[..., Any]
    change_requests_html: Callable[..., Any]
    collect_codex_usage: Callable[..., Any]
    consume_reset_credit: Callable[..., Any]
    create_request: Callable[..., Any]
    execute_run_action: Callable[..., Any]
    explorer_html: Callable[..., Any]
    field_note_catalog: Callable[..., Any]
    field_note_keys: Callable[..., Any]
    flow_node_ids: Callable[..., Any]
    history_plan: Callable[..., Any]
    is_local_host: Callable[..., Any]
    issue_session_token: Callable[..., Any]
    login_html: Callable[..., Any]
    match_account: Callable[..., Any]
    read_plan: Callable[..., Any]
    research_chat_snapshot: Callable[..., Any]
    research_plan_html: Callable[..., Any]
    run_detail_body: Callable[..., Any]
    run_index_body: Callable[..., Any]
    save_goal_draft: Callable[..., Any]
    save_override: Callable[..., Any]
    save_section: Callable[..., Any]
    services_html: Callable[..., Any]
    services_workers_snapshot: Callable[..., Any]
    stage_workflow_html: Callable[..., Any]
    switch_active_account: Callable[..., Any]
    vault_get: Callable[..., Any]
    vault_post: Callable[..., Any]
    vault_root: Callable[..., Any]
    vault_route_action: Callable[..., Any]
    view_goal: Callable[..., Any]
    workflow_snapshot: Callable[..., Any]
    workspace_path: Callable[..., Any]
    workspace_settings_body: Callable[..., Any]


@dataclass
class RequestContext:
    cache: Any
    static_manager: Any
    auth: Any
    access: Any
    notes_state_dir: Path
    isolated_workspace: bool
    quota_history: Any
    workspace_id: str

    def quota_snapshot(self, *, request_update: bool = False) -> dict | None:
        return None if self.isolated_workspace else self.quota_history.snapshot(request_update=request_update)
