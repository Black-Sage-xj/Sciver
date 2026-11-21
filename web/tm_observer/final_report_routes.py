"""Report-reader HTTP adapter; caller has already validated the task binding."""
from __future__ import annotations

import json
import re
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from .canvas_md import ExplorerError


def post_report_generation(handler, path: str, ctx) -> bool:
    """Launch only a registered, sealed task's independent report consumer."""
    if not handler._authorized():
        handler._send_unauthorized()
        return True
    origin, host = handler.headers.get("Origin"), handler.headers.get("Host")
    if (not host or origin not in {f"http://{host}", f"https://{host}"}
            or handler.headers.get("X-TM-Operator-Intent") != "1"):
        handler._send(b'{"error":"origin or operator intent rejected"}',
                      "application/json", HTTPStatus.FORBIDDEN)
        return True
    match = re.fullmatch(r"/api/run/([a-f0-9]{16})/final-report/generate", path)
    run = next((row for row in ctx.cache.get().get("runs", [])
                if match and row.get("observer_id") == match[1]), None)
    try:
        from .final_report_ui import task_is_complete
        from theory_master.cli_discovery import spec_from_task
        from theory_master.final_report import launch_final_report_job
        if run is None:
            raise ExplorerError("任务不存在", 404)
        if not task_is_complete(run):
            raise ExplorerError("任务完成后才能生成总报告", 409)
        raw_root = run.get("research_root")
        if not raw_root or not Path(raw_root).is_absolute():
            raise ExplorerError("任务目录绑定不可用", 409)
        root = Path(raw_root).resolve(strict=True)
        spec = spec_from_task(root)
        if Path(spec.research_root).resolve() != root:
            raise ExplorerError("任务目录绑定不一致", 409)
        job = launch_final_report_job(spec, retry_failed=True)
        if job is None:
            raise ExplorerError("任务缺少完成凭据，暂时无法生成总报告", 409)
        if job.get("launch_status") == "task_binding_unavailable":
            raise ExplorerError("任务目录绑定不可用", 409)
        result = {"ok": True, "state": job["state"]}
        status = HTTPStatus.ACCEPTED
    except ExplorerError as error:
        result, status = {"error": str(error)}, HTTPStatus(error.status)
    except (OSError, ValueError, TypeError, RuntimeError):
        result, status = {"error": "总报告生成未能启动，请稍后重试"}, HTTPStatus.SERVICE_UNAVAILABLE
    handler._send(json.dumps(result, ensure_ascii=False).encode("utf-8"),
                  "application/json; charset=utf-8", status)
    return True


def report_metadata(research_root: Path) -> dict[str, Any]:
    from theory_master.final_report_publication import final_report_descriptor
    try:
        return final_report_descriptor(research_root)
    except (OSError, ValueError, TypeError, KeyError):
        return {"available": False, "state": "unavailable", "version": None,
                "title": "科研总报告", "versions": [], "message": "总报告暂时无法读取"}


def report_get(research_root: Path, action: str, raw_query: str) -> dict[str, Any]:
    from theory_master.final_report_publication import read_final_report, read_final_report_source
    query = parse_qs(raw_query, keep_blank_values=True)
    version = (query.get("version") or [""])[0]
    section = (query.get("section") or [""])[0]
    try:
        offset = int((query.get("offset") or ["0"])[0])
        if action == "final-report-source":
            return read_final_report_source(research_root, (query.get("ref") or [""])[0],
                                            version=version, section=section, offset=offset)
        return read_final_report(research_root, version=version, section=section, offset=offset)
    except FileNotFoundError as error:
        raise ExplorerError("总报告或冻结来源不存在", 404) from error
    except (ValueError, UnicodeError) as error:
        raise ExplorerError(str(error), 400) from error


def report_image(research_root: Path, source: str, target: str, *, vault: bool = False) -> tuple[bytes, str]:
    from theory_master.final_report_publication import final_report_image
    try:
        return final_report_image(research_root, source, target, vault=vault)
    except FileNotFoundError as error:
        raise ExplorerError("总报告图片不存在或未登记", 404) from error
    except ValueError as error:
        raise ExplorerError(str(error), 400) from error
