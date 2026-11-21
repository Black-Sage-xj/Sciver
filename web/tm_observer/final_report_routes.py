"""Report-reader HTTP adapter; caller has already validated the task binding."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from .canvas_md import ExplorerError


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
