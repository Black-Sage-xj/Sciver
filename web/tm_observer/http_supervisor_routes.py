"""Domain routes; request dependencies are provided explicitly by the server."""
from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, quote
from .http_context import RequestContext, RouteServices


def get_streams(self, path: str, state: dict, ctx: RequestContext, services: RouteServices) -> bool:
    if path.startswith("/api/supervisor-session/") and path.endswith(("/state", "/progress")):
        parts = [item for item in path.strip("/").split("/") if item]
        if len(parts) == 4 and parts[:2] == ["api", "supervisor-session"] and parts[3] in {"state", "progress"}:
            from theory_master.supervisor_incident_session import (
                IncidentSessionError, follow_up_pending, locate_incident,
            )
            from .supervisor_incident_ui import research_roots
            session_id = parts[2]
            try:
                incident_root, record, rows = locate_incident(research_roots(state.get("runs", [])), session_id)
                streaming = bool(record.get("status") == "active" and follow_up_pending(incident_root, session_id))
                if parts[3] == "progress":
                    payload = json.dumps({"status": "streaming" if streaming else "complete",
                                          "streaming": streaming, "text": "", "error": None},
                                         ensure_ascii=False).encode("utf-8")
                else:
                    role_map = {"supervisor": "assistant", "human": "user", "tool": "system", "system": "system"}
                    messages = []
                    for row in rows:
                        role = str(row.get("role") or "system")
                        content = str(row.get("content") or "")
                        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                        for image in metadata.get("images") or []:
                            if not isinstance(image, dict):
                                continue
                            image_id = str(image.get("id") or "")
                            if len(image_id) != 16 or any(ch not in "0123456789abcdef" for ch in image_id):
                                continue
                            name = str(image.get("name") or "image").replace("]", r"\]")
                            content += f"\n\n![{name}](/api/supervisor-session/{quote(session_id, safe='')}/image/{image_id})"
                        messages.append({"id": str(row.get("id") or ""),
                                         "role": role_map.get(role, "system"),
                                         "content": content, "created_at": row.get("at")})
                    payload = json.dumps({"id": session_id, "mode": "supervisor",
                                          "status": str(record.get("status") or "active"),
                                          "cycle_id": str(record.get("cycle_id") or ""),
                                          "trigger_event": str(record.get("trigger_event") or ""),
                                          "messages": messages, "streaming": streaming,
                                          "stream_status": "streaming" if streaming else "complete",
                                          "stream_text": "", "active_run": None, "pending_action": None,
                                          "available_models": [], "available_reasoning_efforts": []},
                                         ensure_ascii=False).encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
            except (IncidentSessionError, OSError, ValueError):
                self._send(b'{"error":"supervisor incident session not found"}\n',
                           "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
    if path.startswith("/api/supervisor-session/") and "/image/" in path:
        parts = [item for item in path.strip("/").split("/") if item]
        if len(parts) == 5 and parts[:2] == ["api", "supervisor-session"] and parts[3] == "image":
            from theory_master.supervisor_incident_session import IncidentSessionError
            from .supervisor_incident_ui import read_image
            try:
                payload, media = read_image(state.get("runs", []), parts[2], parts[4])
            except IncidentSessionError:
                self._send(b"image not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return True
            self._send(payload, media)
            return True
    if path == "/api/supervisor-notifications":
        from .supervisor_notifications import notification_feed
        params = parse_qs(self.path.partition("?")[2])
        try:
            offset = int((params.get("offset") or ["0"])[0])
            limit = int((params.get("limit") or ["40"])[0])
            result = notification_feed(state.get("runs", []), offset=offset, limit=limit)
        except (ValueError, OverflowError):
            self._send(b'{"error":"invalid notification page"}',
                       "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        payload = (json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8")
        self._send(payload, "application/json; charset=utf-8")
        return True
    return False


def get_reviews(self, path: str, state: dict, ctx: RequestContext, services: RouteServices) -> bool:
    if path.startswith("/api/run/") and path.endswith("/attention-review"):
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "attention-review" else ""
        run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
        if run is None:
            self._send(b'{"error":"task not found"}', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        from .attention_review_trace import attention_review_trace
        params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        try:
            result = attention_review_trace(
                run, cycle=(params.get("cycle") or [""])[0],
                incident=(params.get("incident") or [""])[0],
                review_ref=(params.get("ref") or [""])[0],
                cursor=int((params.get("cursor") or ["0"])[0]),
                full_item=(int(params["item"][0]) if "item" in params else None),
            )
        except (ValueError, OSError, OverflowError, KeyError):
            self._send(b'{"error":"invalid or unavailable Attention Reviewer trace"}',
                       "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        self._send((json.dumps(result, ensure_ascii=False, default=str) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8")
        return True
    if path.startswith("/supervisor-notification/"):
        parts = [item for item in path.strip("/").split("/") if item]
        ident = parts[1] if len(parts) == 2 and parts[0] == "supervisor-notification" else ""
        run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
        if run is None:
            self._send(b"task not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        cycle = (params.get("cycle") or [""])[0]
        notification_id = (params.get("notification_id") or [""])[0]
        from .supervisor_notifications import notification_feed
        feed = notification_feed(state.get("runs", []), offset=0, limit=100)
        item = next((row for row in feed.get("items", [])
                     if row.get("cycle") == cycle and
                     (row.get("session_id") or row.get("id") or row.get("code")) == notification_id and
                     row.get("url", "").find("/run/" + ident + "/") >= 0), None)
        if item is None:
            self._send(b"notification not found or no longer actionable\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        code = str(item.get("code") or "notification")
        summary = str(item.get("summary") or "需要检查当前任务状态。")
        from .run_monitor import CYCLE_NAME, data as monitor_data
        from .supervisor_incident_ui import research_roots
        from theory_master.supervisor_incident_session import IncidentSessionError, open_notification_incident, locate_incident
        root = Path(str(run.get("research_root") or "")).resolve()
        cycle_dir = Path(str(run.get("cycle_dir") or "")).resolve()
        if (not CYCLE_NAME.fullmatch(cycle) or cycle != str(run.get("cycle") or "")
                or cycle_dir.parent != root or cycle_dir.name != cycle
                or not root.is_dir() or not cycle_dir.is_dir()):
            self._send(b"invalid or stale notification cycle\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        state_now = monitor_data(run)
        if not isinstance(state_now, dict): state_now = {}
        task_id_value = run.get("task_id")
        if task_id_value is None or not str(task_id_value).strip():
            task_id_value = run.get("master_id")
        if task_id_value is None or not str(task_id_value).strip():
            task_id_value = run.get("task_name")
        if task_id_value is None or not str(task_id_value).strip():
            task_id_value = root.name
        try:
            record = open_notification_incident(
                research_root=root, cycle_dir=cycle_dir,
                task_id=str(task_id_value),
                notification_id=notification_id, code=code, summary=summary,
                source={"observer_id": ident, "run_status": str(state_now.get("status") or "")},
            )
            _root, record, messages = locate_incident(research_roots([run]), record["id"])
        except (IncidentSessionError, OSError, ValueError):
            self._send(b"notification conversation unavailable\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        from .navigation_model import incident_owner_id
        owner_id = incident_owner_id(state.get("runs", []), root) or ident
        target = f"/run/{quote(owner_id, safe='')}/supervisor/?session={quote(str(record['id']), safe='')}"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True
    if path.startswith("/supervisor-session/"):
        parts = [item for item in path.strip("/").split("/") if item]
        session_id = parts[1] if len(parts) == 2 and parts[0] == "supervisor-session" else ""
        from theory_master.supervisor_incident_session import IncidentSessionError, locate_incident
        from .supervisor_incident_ui import research_roots
        try:
            incident_root, record, _messages = locate_incident(research_roots(state.get("runs", [])), session_id)
        except (IncidentSessionError, OSError):
            self._send(b"supervisor incident session not found\n",
                       "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        from .navigation_model import incident_owner_id
        owner_id = incident_owner_id(state.get("runs", []), incident_root)
        if owner_id:
            target = f"/run/{quote(owner_id, safe='')}/supervisor/?session={quote(str(record['id']), safe='')}"
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            from .supervisor_incident_ui import page_body as incident_page_body
            payload = services._page("Supervisor Incident", incident_page_body(record, _messages),
                            active="run", breadcrumb_leaf="Supervisor Incident").encode("utf-8")
            self._send(payload, "text/html; charset=utf-8")
        return True
    if path.startswith("/api/run/") and path.endswith("/supervisor"):
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "supervisor" else ""
        run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
        if run is None:
            self._send(b'{"error":"task not found"}', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        from .run_monitor import data as monitor_data
        from .supervisor_view import home_status, sessions as supervisor_sessions
        params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        try:
            result = supervisor_sessions(run, (params.get("cycle") or [""])[0])
            result["home"] = home_status(run, monitor_data(run))
        except (ValueError, OSError):
            self._send(b'{"error":"invalid or unavailable Supervisor session"}',
                       "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        self._send((json.dumps(result, ensure_ascii=False, default=str) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8")
        return True
    if path.startswith("/api/run/") and path.endswith("/budget-review-trace"):
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "budget-review-trace" else ""
        run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
        if run is None:
            self._send(b'{"error":"task not found"}', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        from .budget_review_trace import review_trace
        params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        try:
            result = review_trace(
                run, cycle=(params.get("cycle") or [""])[0],
                review_index=int((params.get("review") or ["0"])[0]),
                cursor=int((params.get("cursor") or ["0"])[0]),
                full_item=(int(params["item"][0]) if "item" in params else None),
            )
        except (ValueError, OSError, OverflowError):
            self._send(b'{"error":"invalid or unavailable Reviewer trace"}',
                       "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        self._send((json.dumps(result, ensure_ascii=False, default=str) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8")
        return True
    if path.startswith("/api/run/") and path.endswith("/conversation"):
        parts = path.strip("/").split("/")
        ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "conversation" else ""
        run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
        if run is None:
            self._send(b'{"error":"task not found"}', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        from .stage_conversation import overview, transcript, sdk_detail, recent_event_detail
        parameters = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        mode = (parameters.get("mode") or ["index"])[0]
        cycle = (parameters.get("cycle") or [""])[0]
        try:
            if mode == "index":
                result = overview(run, cycle=cycle)
            elif mode == "stage":
                cursor = int((parameters.get("cursor") or ["0"])[0])
                if cursor < 0:
                    raise ValueError("negative cursor")
                result = transcript(run, cycle=cycle, stage_id=(parameters.get("stage") or [""])[0], offset=cursor)
            elif mode == "sdk":
                result = sdk_detail(run, cycle=cycle,
                                    sequence=int((parameters.get("cursor") or ["-1"])[0]))
            elif mode == "recent":
                result = recent_event_detail(
                    run,
                    timestamp=(parameters.get("at") or [""])[0],
                    kind=(parameters.get("kind") or [""])[0],
                    role=(parameters.get("role") or [""])[0],
                    hint=(parameters.get("hint") or [""])[0])
            else:
                raise ValueError("invalid view mode")
        except (OSError, ValueError, OverflowError):
            self._send(b'{"error":"invalid or unavailable Stage Conversation"}',
                       "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        self._send((json.dumps(result, ensure_ascii=False, default=str) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8")
        return True
    return False


def post_supervisor(self, path: str, ctx: RequestContext, services: RouteServices) -> bool:
    if path.startswith("/api/supervisor-session/"):
        if not self._authorized():
            self._send_unauthorized()
            return True
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                or self.headers.get("X-TM-Operator-Intent") != "1"):
            self._send(b'{"error":"origin or operator intent rejected"}\n',
                       "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return True
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._send(b'{"error":"expected JSON"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        parts = [item for item in path.strip("/").split("/") if item]
        action = parts[3] if len(parts) == 4 and parts[:2] == ["api", "supervisor-session"] else ""
        session_id = parts[2] if action in {"message", "resolve", "close"} else ""
        if not session_id:
            self._send(b'{"error":"unknown supervisor incident session"}\n',
                       "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        limit = 9_000_000 if action == "message" else 8000
        if size < 2 or size > limit:
            self._send(b'{"error":"invalid size"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        from theory_master.supervisor_incident_session import IncidentSessionError
        from .supervisor_incident_ui import finish as finish_incident, submit_human
        try:
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("invalid payload")
            runs = ctx.cache.get().get("runs", [])
            if action == "message":
                content = data.get("content", "")
                images = data.get("images") or []
                if not isinstance(content, str) or not isinstance(images, list):
                    raise ValueError("content is required")
                if not content.strip() and not images:
                    raise ValueError("content is required")
                result = submit_human(runs, session_id, content, images)
            else:
                note = data.get("note") or ""
                if not isinstance(note, str):
                    raise ValueError("invalid note")
                result = finish_incident(runs, session_id, "resolve" if action == "resolve" else "close", note)
            code = HTTPStatus.OK
        except IncidentSessionError as exc:
            result, code = {"ok": False, "error": str(exc)}, HTTPStatus(exc.status)
        except (UnicodeError, ValueError, json.JSONDecodeError, TypeError):
            result, code = {"ok": False, "error": "invalid JSON payload"}, HTTPStatus.BAD_REQUEST
        except (OSError, RuntimeError):
            result, code = {"ok": False, "error": "supervisor incident session unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE
        self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8", code)
        return True
    return False
