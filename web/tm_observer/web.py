from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import sys
from datetime import datetime, timezone
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode

from .archify_views import StaticViewManager, default_archify_root, default_state_dir
from .access_settings import AccessSettings, AccessSettingsError, is_local_host
from .auth import (
    SESSION_COOKIE,
    SESSION_TTL_S,
    AuthRealm,
    authorize_header,
    issue_session_token,
    load_auth_file,
    match_account,
    session_from_cookie,
)
from .collector import SCHEMA, collect_git_info, collect_state
from .codex_usage import AccountSwitchConflict, collect_codex_usage, consume_reset_credit, switch_active_account
from .quota_snapshot_store import QuotaHistory
from .change_requests import create_request, load_change_requests
from .current_flow import current_flow_body, flow_node_ids
from .field_notes import save_override, sync_machine_defaults
from .sequence_views import SequenceBuildResult, build_run_sequence
from .stage_flow import field_note_catalog, field_note_keys, stage_workflow_body, stage_workflow_guide_body
from .workflow_versions import workflow_snapshot
from .agent_workflow_architecture import agent_workflow_architecture_body
from .run_views import run_index_body, run_detail_body
from .task_settings_ui import workspace_settings_body
from .onboarding_ui import first_use_panel
from .goal_drafts import GoalDraftError, view_goal, save_goal_draft
from .research_plan_editor import ResearchPlanError, read_plan, save_section, history_plan
from .research_plan_ui import page_html as research_plan_html
from .run_control import execute as execute_run_action, OperatorError
from .services_worker_accounts import section_html as worker_account_section_html, snapshot as services_workers_snapshot
from .canvas_md import ExplorerError, vault_root
from .canvas_md_routes import canvas_md_get, canvas_md_image_get
from .vault_files import FILE_ACTIONS, local_file_actions, route_action as vault_route_action, vault_get, vault_post
from .research_explorer_ui import explorer_html
from .research_chat import ResearchChatError, snapshot as research_chat_snapshot
from .navigation_model import task_console_url
from .workspace_scope import WorkspaceScopeError, host_route, scoped_state, task_in_workspace, workspace_path
from .login_ui import login_html


from .dashboard_ui import STYLE, THEME_BOOTSTRAP_SCRIPT, dashboard_shell


from .web_pages import (
    _page,
    _fmt,
    _run_link,
    overview_html,
    placeholder_html,
    _static_warning,
    architecture_html,
    _workflow_unavailable_html,
    workflow_html,
    _tm_system_requests,
    current_flow_html,
    _synced_field_notes,
    stage_workflow_html,
    stage_workflow_guide_html,
    _csv_or_lines,
    change_requests_html,
    _cached_services_accounts,
    services_html,
    codex_usage_body,
    codex_usage_html,
    _find_run,
    run_detail_html,
    _remembered_console,
    _console_landing,
    _console_cookie,
    _LAST_CONSOLE_COOKIE,
)


from .state_cache import StateCache as _StateCache


class StateCache(_StateCache):
    """Compatibility constructor; data-source dependencies remain explicit."""

    def __init__(self, search_root: Path, ttl_s: float = 5.0, *, git_ttl_s: float = 60.0,
                 cold_wait_s: float = 8.0, isolated_workspace: bool = False):
        super().__init__(search_root, ttl_s, git_ttl_s=git_ttl_s, cold_wait_s=cold_wait_s,
                         isolated_workspace=isolated_workspace,
                         state_collector=lambda *a, **kw: collect_state(*a, **kw),
                         git_collector=lambda *a, **kw: collect_git_info(*a, **kw))


def make_handler(cache: StateCache, static_manager: StaticViewManager | None = None,
                 auth: AuthRealm | None = None, *, field_notes_state_dir: Path | None = None,
                 isolated_workspace: bool = False):
    """Build the HTTP transport with explicit per-server services and context."""
    from .http_context import RequestContext, RouteServices
    from .http_handler import build_handler
    cache.isolated_workspace = isolated_workspace
    notes = (field_notes_state_dir or (static_manager.state_dir if static_manager is not None
             else cache.search_root / ".tm-observer-state")).expanduser()
    access = AccessSettings(notes, cache.search_root, auth)
    context = RequestContext(cache, static_manager, access.current, access, notes,
                             isolated_workspace, QuotaHistory(notes / "codex-quota-last-good.json"),
                             hashlib.sha256(str(cache.search_root.resolve()).encode()).hexdigest()[:16])

    def services() -> RouteServices:
        return RouteServices(
            _console_cookie=_console_cookie,
            _console_landing=_console_landing,
            _csv_or_lines=_csv_or_lines,
            _page=_page,
            _remembered_console=_remembered_console,
            _synced_field_notes=_synced_field_notes,
            _workflow_unavailable_html=_workflow_unavailable_html,
            architecture_html=architecture_html,
            canvas_md_get=canvas_md_get,
            canvas_md_image_get=canvas_md_image_get,
            change_requests_html=change_requests_html,
            collect_codex_usage=collect_codex_usage,
            consume_reset_credit=consume_reset_credit,
            create_request=create_request,
            execute_run_action=execute_run_action,
            explorer_html=explorer_html,
            field_note_catalog=field_note_catalog,
            field_note_keys=field_note_keys,
            flow_node_ids=flow_node_ids,
            history_plan=history_plan,
            is_local_host=is_local_host,
            issue_session_token=issue_session_token,
            login_html=login_html,
            match_account=match_account,
            read_plan=read_plan,
            research_chat_snapshot=research_chat_snapshot,
            research_plan_html=research_plan_html,
            run_detail_body=run_detail_body,
            run_index_body=run_index_body,
            save_goal_draft=save_goal_draft,
            save_override=save_override,
            save_section=save_section,
            services_html=services_html,
            services_workers_snapshot=services_workers_snapshot,
            stage_workflow_html=stage_workflow_html,
            switch_active_account=switch_active_account,
            vault_get=vault_get,
            vault_post=vault_post,
            vault_root=vault_root,
            vault_route_action=vault_route_action,
            view_goal=view_goal,
            workflow_snapshot=workflow_snapshot,
            workspace_path=workspace_path,
            workspace_settings_body=workspace_settings_body,
        )

    return build_handler(context, services)


def _open_ready_browser(url: str) -> None:
    """Wait for an HTTP response before asking the desktop to open the UI."""
    import webbrowser
    from urllib.error import HTTPError, URLError
    from urllib.request import urlopen
    for _ in range(50):
        try:
            with urlopen(url + "healthz", timeout=0.2):
                pass
        except HTTPError:
            break  # An authenticated response also proves that the server is ready.
        except (URLError, TimeoutError, OSError):
            time.sleep(0.1)
        else:
            break
    else:
        print(f"Browser launch timed out. Open {url} manually.", flush=True)
        return
    try:
        opened = webbrowser.open(url)
    except webbrowser.Error:
        opened = False
    if not opened:
        print(f"No desktop browser found. Open {url} manually (SSH: forward this port).", flush=True)


def serve(
    search_root: Path,
    host: str = "127.0.0.1",
    port: int = 8787,
    cache_ttl: float = 5.0,
    *,
    state_dir: Path | None = None,
    archify_root: Path | None = None,
    static_refresh: float = 30.0,
    auth_file: Path | None = None,
    open_browser: bool = False,
) -> None:
    auth = load_auth_file(auth_file) if auth_file is not None else None
    from .auth import auth_accounts
    if auth is not None and len(auth_accounts(auth)) > 1:
        from .account_workspaces import serve_accounts
        serve_accounts(auth, search_root, host, port, cache_ttl,
                       state_dir=state_dir or default_state_dir(),
                       archify_root=archify_root or default_archify_root(),
                       static_refresh=static_refresh, open_browser=open_browser)
        return
    _serve_single(search_root, host, port, cache_ttl, state_dir=state_dir,
                  archify_root=archify_root, static_refresh=static_refresh,
                  auth=auth, open_browser=open_browser)


def _serve_single(
    search_root: Path,
    host: str = "127.0.0.1",
    port: int = 8787,
    cache_ttl: float = 5.0,
    *,
    state_dir: Path | None = None,
    archify_root: Path | None = None,
    static_refresh: float = 30.0,
    auth: AuthRealm | None = None,
    open_browser: bool = False,
    isolated_workspace: bool = False,
    ready=None,
) -> None:
    search_root = search_root.resolve()
    state_dir = (state_dir or default_state_dir()).expanduser().resolve()
    cache = StateCache(search_root, cache_ttl)
    manager = None if isolated_workspace else StaticViewManager(
        search_root,
        state_dir,
        archify_root=archify_root or default_archify_root(),
        interval_s=static_refresh,
    )
    try:
        server = ThreadingHTTPServer((host, int(port)), make_handler(
            cache, manager, auth, field_notes_state_dir=state_dir,
            isolated_workspace=isolated_workspace))
    except OSError as exc:
        raise OSError(f"Cannot listen on {host}:{port}: {exc}. Choose another port with --port 8788.") from exc
    if ready is not None:
        ready(server.server_port)
    if manager is not None:
        manager.start()
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "127.0.0.1"} else host
    url = f"http://{browser_host}:{server.server_port}/"
    print(f"Sciver Web UI: {url}\nPress Ctrl+C to stop the server; task files are preserved.", flush=True)
    if open_browser:
        threading.Thread(target=_open_ready_browser, args=(url,), daemon=True).start()
    from theory_master.provider_extensions import optional_account_quota_plugin
    monitor = optional_account_quota_plugin()
    monitor_stop = threading.Event()
    if monitor is not None:
        # Optional local account monitor; no network polling in public wheels.
        threading.Thread(target=monitor.watch, kwargs={"stop": monitor_stop},
                         name="tm-optional-provider-quota", daemon=True).start()
    try:
        server.serve_forever()
    finally:
        monitor_stop.set()
        server.server_close()
        if manager is not None:
            manager.stop()
