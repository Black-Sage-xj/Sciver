"""Domain routes; request dependencies are provided explicitly by the server."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlencode
from .goal_drafts import GoalDraftError
from .research_plan_editor import ResearchPlanError
from .run_control import OperatorError
from .canvas_md import ExplorerError
from .vault_files import FILE_ACTIONS
from .workspace_scope import WorkspaceScopeError
from .http_context import RequestContext, RouteServices


def get_explorer(self, path: str, state: dict, ctx: RequestContext, services: RouteServices) -> bool:
    if path.startswith("/api/canvas-md/tasks/"):
        try:
            matched = services.vault_route_action(path)
            if matched and matched[1] in FILE_ACTIONS:
                if ctx.isolated_workspace and matched[1] == "vault-directories":
                    params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                    directory = (params.get("path") or [""])[0] or str(ctx.cache.search_root)
                    services.workspace_path(directory, ctx.cache.search_root)
                    # Clamp the default/parent navigation at the account root.
                    query = urlencode({"path": directory})
                else:
                    query = self.path.partition("?")[2]
                data, content_type, filename = services.vault_get(path, query,
                    ctx.cache.search_root, state, local=self._local_file_actions())
                if ctx.isolated_workspace and matched[1] == "vault-directories":
                    listing = json.loads(data)
                    if listing.get("path") == str(ctx.cache.search_root.resolve()):
                        listing["parent"] = None
                    data = json.dumps(listing, ensure_ascii=False).encode()
                self._send(data, content_type, download_name=filename)
                return True
            if path.endswith("/image"):
                data, content_type = services.canvas_md_image_get(
                    path, self.path.partition("?")[2], ctx.cache.search_root, state)
                self._send(data, content_type)
                return True
            data, status = services.canvas_md_get(path, self.path.partition("?")[2], ctx.cache.search_root, state)
        except (ExplorerError, WorkspaceScopeError) as exc:
            data = (json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n").encode("utf-8")
            status = exc.status
        except (OSError, RuntimeError, ValueError):
            data = b'{"error":"notebook files are temporarily unavailable"}\n'
            status = HTTPStatus.SERVICE_UNAVAILABLE
        if status == HTTPStatus.OK and path.endswith("/index"):
            # The index is task-scoped and validated by its file signature.
            # Revalidate in-browser to avoid repeatedly transferring multi-MB JSON.
            etag = '"' + hashlib.sha256(data).hexdigest() + '"'
            self._send(data, "application/json; charset=utf-8", HTTPStatus(status),
                       cache_control="private, no-cache", etag=etag)
        else:
            self._send(data, "application/json; charset=utf-8", HTTPStatus(status))
        return True
    return False


def get_task(self, path: str, state: dict, ctx: RequestContext, services: RouteServices) -> bool:
    if path.startswith("/api/run/") and path.endswith("/research-plan/history"):
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 5 and parts[:2] == ["api", "run"] and parts[3:] == ["research-plan", "history"] else ""
        run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(c in "0123456789abcdef" for c in ident) else None
        try:
            if run is None:
                raise ResearchPlanError("task not found", 404)
            query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
            requested = (query.get("version") or [""])[0]
            if requested and (not requested.isdigit() or len(requested) > 12):
                raise ResearchPlanError("invalid history version")
            result, code = services.history_plan(ctx.cache.search_root, run, int(requested) if requested else None,
                                             scope=(query.get("scope") or ["plan"])[0]), HTTPStatus.OK
        except ResearchPlanError as exc:
            result, code = {"error": str(exc)}, HTTPStatus(exc.status)
        except (OSError, ValueError):
            result, code = {"error": "Research Plan 历史暂不可用"}, HTTPStatus.SERVICE_UNAVAILABLE
        self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8", code)
        return True
    if path.startswith("/api/run/") and path.endswith("/research-plan"):
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "research-plan" else ""
        run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(c in "0123456789abcdef" for c in ident) else None
        try:
            if run is None:
                raise ResearchPlanError("task not found", 404)
            result, code = services.read_plan(ctx.cache.search_root, run), HTTPStatus.OK
        except ResearchPlanError as exc:
            result, code = {"error": str(exc)}, HTTPStatus(exc.status)
        except (OSError, ValueError):
            result, code = {"error": "Research Plan 暂不可用"}, HTTPStatus.SERVICE_UNAVAILABLE
        self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8", code)
        return True
    if path.startswith("/api/run/") and path.endswith("/goal"):
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "goal" else ""
        run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(c in "0123456789abcdef" for c in ident) else None
        if run is None:
            result, code = {"error": "task not found"}, HTTPStatus.NOT_FOUND
        else:
            try:
                result, code = services.view_goal(run), HTTPStatus.OK
            except GoalDraftError as exc:
                result, code = {"error": str(exc), "code": exc.code}, HTTPStatus(exc.status)
            except (OSError, ValueError):
                result, code = {"error": "Native Goal 记录暂不可用"}, HTTPStatus.SERVICE_UNAVAILABLE
        self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"), "application/json; charset=utf-8", code)
        return True
    if path.startswith("/api/run/") and path.endswith("/cycle-page"):
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "cycle-page" else ""
        run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
        if run is None or not run.get("research_root"):
            self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        from .cycle_history import cycle_summary_page
        params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        try:
            page = cycle_summary_page(Path(str(run["research_root"])),
                                      (params.get("before") or [""])[0])
        except ValueError:
            self._send(b"invalid Cycle cursor\n", "text/plain; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        self._send((json.dumps(page, ensure_ascii=False) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8", cache_control="private, no-store")
        return True
    if path.startswith("/api/run/") and path.endswith("/view"):
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "view" else ""
        run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
        if run is None:
            self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        tab = (params.get("tab") or ["activity"])[0]
        # Keep Explore map availability consistent if a Run cockpit
        # is rebuilt during polling (e.g. a Cycle/layout transition).
        try:
            services.vault_root(ctx.cache.search_root, ident, state)
            canvas_available = True
        except ExplorerError:
            canvas_available = False
        self._send(services.run_detail_body(run, tab=tab, fragment=True,
                  history_cycle=(params.get("history_cycle") or [""])[0],
                  canvas_available=canvas_available,
                  worker_id=(params.get("worker") or [None])[0],
                  quota_snapshot=ctx.quota_snapshot(request_update=True)).encode("utf-8"),
                  "text/html; charset=utf-8")
        return True
    return False


def post_task(self, path: str, ctx: RequestContext, services: RouteServices) -> bool:
    if path.startswith("/api/run/") and path.endswith("/final-report/generate"):
        from .final_report_routes import post_report_generation
        return post_report_generation(self, path, ctx)
    if path.startswith("/api/run/") and path.endswith("/rename"):
        if not self._authorized():
            self._send_unauthorized()
            return True
        origin, host = self.headers.get("Origin"), self.headers.get("Host")
        if not host or origin not in {f"http://{host}", f"https://{host}"} or self.headers.get("X-TM-Operator-Intent") != "1":
            self._send(b'{"error":"forbidden"}', "application/json", HTTPStatus.FORBIDDEN)
            return True
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] else ""
        run = next ( (r for r in ctx.cache.get().get("runs", []) if r.get("observer_id") == ident), None)
        if not run or len(ident) != 16 or any(c not in "0123456789abcdef" for c in ident):
            self._send(b'{"error":"task not found"}', "application/json", HTTPStatus.NOT_FOUND)
            return True
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 4096 or not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            name = data.get("name")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120 or any(ord(c) < 32 for c in name):
                raise ValueError()
            root = Path(run["task_root"]).resolve()
            directory = root / ".theory-master"
            if not directory.is_dir() or directory.resolve().parent != root:
                raise OSError("invalid task directory")
            fd, temp = tempfile.mkstemp(prefix=".display-name-", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"name": name.strip()}, f, ensure_ascii=False)
                os.replace(temp, directory / "display-name.json")
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            ctx.cache.include_created_task(root)
            result, status = {"name": name.strip()}, HTTPStatus.OK
        except (ValueError, TypeError, UnicodeError, AttributeError):
            result, status = {"error": "名称须为 1–120 个字符，且不能包含换行"}, HTTPStatus.BAD_REQUEST
        except OSError:
            result, status = {"error": "保存失败，请检查任务目录权限"}, HTTPStatus.SERVICE_UNAVAILABLE
        self._send(json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)
        return True
    if path.startswith("/api/run/") and path.endswith("/research-plan"):
        if not self._authorized():
            self._send_unauthorized()
            return True
        origin, host = self.headers.get("Origin"), self.headers.get("Host")
        if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                or self.headers.get("X-TM-Operator-Intent") != "1"):
            self._send(b'{"error":"origin or operator intent rejected"}\n',
                       "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return True
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._send(b'{"error":"expected JSON"}\n',
                       "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "research-plan" else ""
        run = next((r for r in ctx.cache.get().get("runs", []) if r.get("observer_id") == ident), None) if len(ident) == 16 and all(c in "0123456789abcdef" for c in ident) else None
        if run is None:
            self._send(b'{"error":"task not found"}\n',
                       "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if size < 2 or size > 350_000:
            self._send(b'{"error":"invalid size"}\n',
                       "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        try:
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            result, code = services.save_section(ctx.cache.search_root, run, data), HTTPStatus.OK
        except ResearchPlanError as exc:
            result, code = {"error": str(exc)}, HTTPStatus(exc.status)
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            result, code = {"error": "invalid JSON payload"}, HTTPStatus.BAD_REQUEST
        except (OSError, RuntimeError):
            result, code = {"error": "Research Plan 保存失败"}, HTTPStatus.SERVICE_UNAVAILABLE
        self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8", code)
        return True
    if path.startswith("/api/run/") and path.endswith("/goal"):
        if not self._authorized():
            self._send_unauthorized()
            return True
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                or self.headers.get("X-TM-Operator-Intent") != "1"):
            self._send(b'{"error":"origin or operator intent rejected"}\n', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return True
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._send(b'{"error":"expected JSON"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "goal" else ""
        run = next((row for row in ctx.cache.get().get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(c in "0123456789abcdef" for c in ident) else None
        if run is None:
            self._send(b'{"error":"task not found"}\n', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if size < 2 or size > 14000:
            self._send(b'{"error":"invalid size"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        try:
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
            result, code = services.save_goal_draft(run, payload), HTTPStatus.OK
        except GoalDraftError as exc:
            result, code = {"error": str(exc), "code": exc.code}, HTTPStatus(exc.status)
        except (UnicodeError, ValueError, json.JSONDecodeError, TypeError):
            result, code = {"error": "invalid JSON payload"}, HTTPStatus.BAD_REQUEST
        except (OSError, RuntimeError):
            result, code = {"error": "Goal 草稿保存失败，请检查记录再重试"}, HTTPStatus.SERVICE_UNAVAILABLE
        self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"), "application/json; charset=utf-8", code)
        return True
    if path.startswith("/api/run/") and path.endswith("/action"):
        if not self._authorized():
            self._send_unauthorized()
            return True
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                or self.headers.get("X-TM-Operator-Intent") != "1"):
            self._send(b'{"error":"origin or operator intent rejected"}\n', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return True
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._send(b'{"error":"expected JSON"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "action" else ""
        if len(ident) != 16 or any(c not in "0123456789abcdef" for c in ident):
            self._send(b'{"error":"task not found"}\n', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        # Resolve only server-discovered task IDs; never accept file paths.
        run = next((item for item in ctx.cache.get().get("runs", []) if item.get("observer_id") == ident), None)
        if run is None:
            self._send(b'{"error":"task not found"}\n', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if size < 2 or size > 6000:
            self._send(b'{"error":"invalid size"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        try:
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(data, dict) or not set(data).issubset({"action", "value", "request_id", "expected_cycle", "expected_status", "expected_launch_id"}):
                raise ValueError("invalid fields")
            result = services.execute_run_action(run, data)
            code = HTTPStatus.OK
        except OperatorError as exc:
            result, code = {"ok": False, "error": str(exc)}, HTTPStatus(exc.status)
        except (UnicodeError, ValueError, json.JSONDecodeError):
            result, code = {"ok": False, "error": "invalid JSON payload"}, HTTPStatus.BAD_REQUEST
        except (OSError, RuntimeError, TimeoutError):
            result, code = {"ok": False, "error": "operator backend unavailable; inspect task before retry"}, HTTPStatus.SERVICE_UNAVAILABLE
        self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"), "application/json; charset=utf-8", code)
        return True
    return False
