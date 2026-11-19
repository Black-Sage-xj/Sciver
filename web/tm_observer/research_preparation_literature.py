"""Bounded host scholarly retrieval for a pre-Task workspace; never invokes LLMs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterator
import uuid


class PreparationLiteratureError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: str, label: str, limit: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value) > limit or (required and not value.strip()):
        raise PreparationLiteratureError(f"{label}无效或超出长度限制")
    return value.strip()


def _workspace(value: Path) -> Path:
    value = Path(value).expanduser()
    if (not value.is_absolute() or value != value.resolve() or not value.is_dir()
            or value.parent.name != "research-preparations" or not re.fullmatch(r"[a-f0-9]{32}", value.name)):
        raise PreparationLiteratureError("检索只允许使用已登记的绝对路径筹备 workspace")
    marker = value / "metadata.json"
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 256_000:
        raise PreparationLiteratureError("筹备 metadata 不存在或不安全")
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PreparationLiteratureError("无法读取筹备 metadata") from exc
    if (not isinstance(metadata, dict) or metadata.get("schema") != "sciver/research-preparation/v1"
            or metadata.get("id") != value.name or metadata.get("session_id") != value.name
            or metadata.get("entry") not in {"clear", "exploratory"}):
        raise PreparationLiteratureError("筹备身份与 workspace 不一致")
    if metadata.get("created_task") is not None or metadata.get("creation") is not None:
        raise PreparationLiteratureError("筹备已进入立项确认；不能追加未纳入审核的检索资料")
    if (value / "theory-master.config.json").exists() or (value / "science_graph.sqlite3").exists():
        raise PreparationLiteratureError("正式科研任务目录不能作为筹备检索 workspace")
    return value


@contextmanager
def _locked_workspace(value: Path) -> Iterator[Path]:
    from .research_preparation import PreparationError, _locked, _material_locked
    root = _workspace(value)
    try:
        # Match project creation's lock order. Retrieval itself uses hidden
        # staging outside this short publication transaction.
        with _locked(root, blocking=False), _material_locked(root, blocking=False):
            yield _workspace(root)
    except PreparationError as exc:
        raise PreparationLiteratureError(str(exc)) from exc


def _child(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink() or path.resolve() != path or not path.is_relative_to(root):
        raise PreparationLiteratureError("检索产物目录不安全")
    return path


def _artifact(job: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if (not path.is_absolute() or path != path.resolve() or not path.is_relative_to(job)
            or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024):
        return None
    return path


def _json_file(job: Path, value: object, default: Any) -> Any:
    path = _artifact(job, value)
    if path is None:
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _relocate(value: Any, old: Path, new: Path) -> Any:
    if isinstance(value, str):
        return str(new) + value[len(str(old)):] if value == str(old) or value.startswith(str(old) + "/") else value
    if isinstance(value, list):
        return [_relocate(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _relocate(item, old, new) for key, item in value.items()}
    return value


def _reading(job: Path, transport: dict) -> dict[str, dict]:
    details = transport.get("reading") if isinstance(transport.get("reading"), dict) else {}
    manifest = _artifact(job, details.get("pdf_manifest"))
    result: dict[str, dict] = {}
    if manifest is None:
        return result
    with manifest.open(encoding="utf-8-sig", newline="") as stream:
        for index, row in enumerate(csv.DictReader(stream), 1):
            text = _artifact(job, str(manifest.parent / f"paper-{index:02d}.md"))
            available = row.get("status") == "read" and text is not None and bool(text.read_text(encoding="utf-8").strip())
            reading = {
                "materialization_status": "text_materialized" if available else "unverified",
                "text_path": str(text) if available else "", "materialization_reason": str(row.get("reason") or "")[:700],
            }
            if row.get("status") == "conversion_disabled":
                reading["materialization_status"] = "conversion_disabled"
            for key in (str(row.get("doi") or "").lower(), str(row.get("arxiv_id") or "").lower(), str(row.get("paper") or "")):
                if key:
                    result[key] = reading
    return result


def _sources(job: Path, transport: dict) -> list[dict]:
    papers = _json_file(job, transport.get("papers_json"), [])
    readings = _reading(job, transport)
    result = []
    for paper in papers[:100] if isinstance(papers, list) else []:
        if not isinstance(paper, dict):
            continue
        title = str(paper.get("title") or "")[:1000]
        reading = (readings.get(str(paper.get("doi") or "").lower())
                   or readings.get(str(paper.get("arxiv_id") or "").lower())
                   or readings.get(title) or {"materialization_status": "not_requested", "text_path": ""})
        level = ("text_materialized_not_reviewed" if reading["materialization_status"] == "text_materialized"
                 else "abstract_available" if str(paper.get("abstract") or "").strip() else "metadata_available")
        result.append({"title": title, "doi": str(paper.get("doi") or ""), "url": str(paper.get("url") or ""),
                       "year": paper.get("year"), "providers": paper.get("sources") or [paper.get("source") or "unknown"],
                       "source_origin": "provided_target" if paper.get("source") == "source-lock" else "retrieved_metadata",
                       "reading_level": level, "claim_verification": "not_performed", **reading})
    return result


def run_search(workspace: Path, query: str, *, scope: str = "", mode: str = "landscape",
               doi: str = "", paper_title: str = "", retrieve: Callable | None = None) -> dict:
    """One retrieval job, with no Agent, model, graph, bootstrap or Worker calls."""
    query = _text(query, "检索问题", 1200, required=True)
    scope = _text(scope, "研究范围", 4000)
    doi = _text(doi, "DOI", 300)
    paper_title = _text(paper_title, "论文题名", 1000)
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    if mode not in {"landscape", "targeted_source"} or (doi and not re.fullmatch(r"10\.\d{1,9}/\S+", doi)):
        raise PreparationLiteratureError("检索类型或 DOI 无效")
    if mode == "targeted_source" and not (doi or paper_title):
        raise PreparationLiteratureError("定向正文获取须指定一篇核心论文的 --doi 或 --paper-title")
    if mode == "landscape" and (doi or paper_title):
        raise PreparationLiteratureError("指定核心论文请使用 --mode targeted_source")
    ident = uuid.uuid4().hex
    with _locked_workspace(workspace) as root:
        staging_home = _child(root, ".literature-running")
        staging_home.mkdir(exist_ok=True)
        staging = _child(staging_home, ident)
        staging.mkdir()
        destination_home = _child(root, "literature")
        destination_home.mkdir(exist_ok=True)
        destination = _child(destination_home, ident)
    task = {"source_gap_id": "preparation-" + ident, "task_type": mode,
            "search_target": query, "domain_context": scope}
    if mode == "targeted_source":
        task["source_lock"] = {"doi": doi, "title": paper_title, "status": "locked"}
    if retrieve is None:
        from theory_master.scholarly_transport import retrieve_scholarly
        retrieve = retrieve_scholarly
    error = ""
    try:
        transport = retrieve(task, staging, timeout_s=120)
        if not isinstance(transport, dict):
            raise PreparationLiteratureError("SDK 检索没有返回有效状态")
        sources = _sources(staging, transport)
    except Exception as exc:
        transport = {"status": "blocked", "transport_ok": False}
        sources = []
        error = f"{type(exc).__name__}: {exc}"[:1200]
    status = ("blocked" if not transport.get("transport_ok") else "empty"
              if not int(transport.get("metadata_hit_count", len(sources)) or 0) else "completed")
    evidence_index = _artifact(staging, transport.get("evidence_index_md"))
    papers_top = _artifact(staging, transport.get("papers_top_json"))
    transport = _relocate(transport, staging, destination)
    sources = _relocate(sources, staging, destination)
    manifest = {
        "schema": "sciver/preparation-literature/v1", "preparation_id": root.name, "job_id": ident,
        "created_at": _now(), "query": query, "scope": scope, "mode": mode, "status": status,
        "literature_agent_invoked": False, "model_invoked": False, "scientific_evidence_created": False,
        "claim_verification_performed": False, "transport": transport, "sources": sources,
        "error": error, "notice": "SDK 检索与资料物化；尚未由 Literature Agent 或 Chat 核验具体主张。空结果不证明新颖性。",
    }
    with _locked_workspace(root):
        artifacts = []
        total = 0
        for path in sorted(staging.rglob("*")):
            if path.is_symlink():
                raise PreparationLiteratureError("SDK 检索产物含不安全链接；未发布")
            if not path.is_file():
                continue
            size = path.stat().st_size
            total += size
            if size > 32 * 1024 * 1024 or total > 96 * 1024 * 1024 or len(artifacts) >= 300:
                raise PreparationLiteratureError("检索产物超过筹备资料限额；未发布")
            if path.name in {"transport.json", "search_log.md"}:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    path.write_text(json.dumps(_relocate(data, staging, destination), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                except (ValueError, UnicodeError):
                    pass
            artifacts.append({"path": str((destination / path.relative_to(staging)).relative_to(root)),
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
        manifest["artifacts"] = artifacts
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, destination)
    return {"job_id": ident, "status": status, "manifest": str(destination / "manifest.json"),
            "evidence_index": str(destination / evidence_index.relative_to(staging)) if evidence_index else "",
            "papers_top": str(destination / papers_top.relative_to(staging)) if papers_top else "",
            "source_count": len(sources), "model_invoked": False, "literature_agent_invoked": False,
            "notice": manifest["notice"], "error": error}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--query", required=True)
    parser.add_argument("--scope", default="")
    parser.add_argument("--mode", choices=("landscape", "targeted_source"), default="landscape")
    parser.add_argument("--doi", default="")
    parser.add_argument("--paper-title", default="")
    args = parser.parse_args(argv)
    try:
        result = run_search(args.workspace, args.query, scope=args.scope, mode=args.mode,
                            doi=args.doi, paper_title=args.paper_title)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "model_invoked": False}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] != "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
