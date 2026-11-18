"""Task-scoped Research Plan Markdown reader and human section editor.

Only a server-discovered observer ID can select the canonical file. Saving one
section re-reads the full file and preserves the other four sections verbatim.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from .research_plan_history import capture_current, compare, content, plan_lock, record, versions, project, merge, SCOPES

SECTIONS = ("Goal", "Research Contract", "Final Gate", "Required Context", "Scientific Model")
OWNERS = ("Human", "Human", "Human", "Human / operator", "Research Lead / SDK; human editable")
LIMIT_BYTES = 1024 * 1024
MAX_SECTION_CHARS = 250_000
HEADER = re.compile(r"^# ([^\r\n]+)\r?$", re.MULTILINE)
IDENT = re.compile(r"^[0-9a-f]{16}$")


class ResearchPlanError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _binding(root: Path) -> dict[str, Any]:
    path = root / ".theory-master" / "task.json"
    if (not path.is_file() or path.is_symlink() or path.parent.is_symlink()
            or path.stat().st_size > 512 * 1024 or not path.resolve().is_relative_to(root)):
        raise ResearchPlanError("任务绑定文件无效", 404)
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(marker, dict):
            raise ValueError("invalid task binding")
        return marker
    except (UnicodeError, OSError, ValueError) as exc:
        raise ResearchPlanError("任务绑定文件无效", 404) from exc


def _scoped_path(raw: Any, repo: Path) -> Path:
    from .workspace_scope import WorkspaceScopeError, workspace_path

    try:
        path = workspace_path(raw, repo)
        if raw != str(path):
            raise WorkspaceScopeError("noncanonical path")
        return path
    except WorkspaceScopeError as exc:
        raise ResearchPlanError("Research Plan 的任务路径不安全或超出当前工作区", 403) from exc


def _location(search_root: Path, run: dict[str, Any]) -> tuple[Path, Path | None]:
    repo = search_root.resolve()
    observer = str(run.get("observer_id") or "")
    if not IDENT.fullmatch(observer):
        raise ResearchPlanError("无效任务标识", 404)
    root_value, research_value = run.get("task_root"), run.get("research_root")
    if not isinstance(root_value, str) or not isinstance(research_value, str):
        raise ResearchPlanError("任务根目录无效", 404)
    root_path = Path(root_value).expanduser()
    root, research = root_path.resolve(), Path(research_value).expanduser().resolve()
    if (not root.is_relative_to(repo) or root_path != root
            or hashlib.sha256(str(research).encode("utf-8")).hexdigest()[:16] != observer):
        raise ResearchPlanError("任务来源校验失败", 403)
    # Plan and config may live in a separate notebook inside this search scope.
    # Only exact reciprocal SDK bindings authorize that layout; aliases and
    # runtime paths never become alternate Plan file access roots.
    requires_canonical_binding = root == repo or not research.is_relative_to(repo)
    marker = _binding(root)
    config = (_scoped_path(marker["config_path"], repo) if "config_path" in marker
              else root / "theory-master.config.json")
    requires_canonical_binding = requires_canonical_binding or config.parent != root
    path = root / "research_plan.md"
    if config.exists() or config.is_symlink():
        if config.is_symlink() or not config.is_file() or config.stat().st_size > 512 * 1024:
            raise ResearchPlanError("任务配置文件不安全", 403)
        _scoped_path(str(config), repo)
        try:
            doc = json.loads(config.read_text(encoding="utf-8"))
            configured = doc.get("research_root") if isinstance(doc, dict) else None
            if configured and (root / str(configured)).resolve() != research:
                raise ResearchPlanError("Research Plan 的任务绑定已发生变化", 409)
            configured_plan = doc.get("plan_path") if isinstance(doc, dict) else None
            if configured_plan and configured_plan != str(path):
                path = _scoped_path(configured_plan, repo)
                requires_canonical_binding = True
            if requires_canonical_binding:
                if (marker.get("schema") != "theory-master/task/v2"
                        or marker.get("config_path") != str(config)
                        or not isinstance(doc, dict) or doc.get("schema") != "theory-master/system/v1"
                        or doc.get("path_model") != "theory-master/task-path/v2"):
                    raise ResearchPlanError("任务来源校验失败：需要当前任务的规范绑定", 403)
                if (doc.get("task_home") != str(root) or doc.get("plan_path") != str(path)
                        or doc.get("research_root") != str(research)):
                    raise ResearchPlanError("Research Plan 的任务绑定已发生变化", 409)
                if path.parent != root:
                    plan_marker = _binding(path.parent)
                    if (plan_marker.get("schema") != "theory-master/task/v2"
                            or plan_marker.get("config_path") != str(config)):
                        raise ResearchPlanError("Research Plan 的任务绑定已发生变化", 409)
        except (UnicodeError, OSError, ValueError, TypeError) as exc:
            if isinstance(exc, ResearchPlanError):
                raise
            raise ResearchPlanError("任务配置文件无效", 409) from exc
    elif requires_canonical_binding or "config_path" in marker:
        raise ResearchPlanError("任务来源校验失败：缺少当前任务配置", 403)
    else:
        config = None
    if path.is_symlink() or not path.is_file() or path != path.resolve():
        raise ResearchPlanError("未找到安全的 research_plan.md", 404)
    return path, config


def _file(search_root: Path, run: dict[str, Any]) -> Path:
    return _location(search_root, run)[0]


def _read(path: Path) -> str:
    if path.is_symlink() or path.resolve().parent != path.parent.resolve():
        raise ResearchPlanError("Research Plan 文件路径已改变", 409)
    if path.stat().st_size > LIMIT_BYTES:
        raise ResearchPlanError("Research Plan 文件过大", 413)
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ResearchPlanError("Research Plan 暂不可读取", 503) from exc


def _parse(raw: str) -> dict[str, str]:
    matches = list(HEADER.finditer(raw))
    if (len(matches) != len(SECTIONS)
            or [match.group(1).strip() for match in matches] != list(SECTIONS)
            or (matches and raw[:matches[0].start()].strip())):
        raise ResearchPlanError("Research Plan 不符合五个 H1 字段的当前规范，请在文件中核对", 422)
    return {
        name: raw[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(raw)]
        for i, (name, match) in enumerate(zip(SECTIONS, matches))
    }


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _response(run: dict[str, Any], raw: str, version: int, model_version: int) -> dict[str, Any]:
    parsed = _parse(raw)
    return {
        "observer_id": run["observer_id"],
        "task_name": run.get("task_name") or "",
        "file": "research_plan.md",
        "revision": _sha(raw),
        "version": version,
        "model_version": model_version,
        "scope_revisions": {scope: _sha(project(raw, scope)) for scope in SCOPES},
        "sections": [
            {"name": name, "text": parsed[name], "revision": _sha(parsed[name]), "owner": OWNERS[i]}
            for i, name in enumerate(SECTIONS)
        ],
    }


def read_plan(search_root: Path, run: dict[str, Any]) -> dict[str, Any]:
    path = _file(search_root, run)
    with plan_lock(path):
        raw = _read(path)
        _parse(raw)
        row = capture_current(path)
        return _response(run, raw, row["version"], row["model_version"])


def history_plan(search_root: Path, run: dict[str, Any], version: int | None = None, scope: str = "plan") -> dict[str, Any]:
    if scope not in SCOPES:
        raise ResearchPlanError("历史类型无效", 400)
    path = _file(search_root, run)
    with plan_lock(path):
        raw = _read(path)
        _parse(raw)
        capture_current(path)
        rows = versions(path, scope)
        result: dict[str, Any] = {"scope": scope, "history": list(reversed(rows)),
                                  "current_revision": _sha(project(raw, scope))}
        if version is not None:
            if version < 1:
                raise ResearchPlanError("历史版本号无效", 400)
            try:
                result["content"] = content(path, version, scope)
                result["diff"] = compare(path, version, raw, scope)
            except ValueError as exc:
                raise ResearchPlanError(str(exc), 404) from exc
        return result


def _write_updated(path: Path, before: os.stat_result, updated: str, *, before_replace=None) -> None:
    if len(updated.encode("utf-8")) > LIMIT_BYTES:
        raise ResearchPlanError("更新后的 Research Plan 超过大小限制", 413)
    _parse(updated)
    def unchanged() -> bool:
        s = path.stat()
        return (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
            s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if not unchanged():
        raise ResearchPlanError("Research Plan 在保存过程中发生变化，请重试", 409)
    fd, temporary = tempfile.mkstemp(prefix=".web-research-plan-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as out:
            os.fchmod(out.fileno(), stat.S_IMODE(before.st_mode))
            out.write(updated)
            out.flush()
            os.fsync(out.fileno())
        if not unchanged():
            raise ResearchPlanError("Research Plan 在保存过程中发生变化，请重试", 409)
        if before_replace is not None:
            before_replace()
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _edit_guard(path: Path, run: dict, *, model_edit: bool, config_path: Path | None):
    """Real task bindings must use runtime CAS metadata; bare Markdown may edit alone."""
    from theory_master.goal_terminal import assert_goal_open, science_commit_barrier
    from theory_master.scientific_model import model_commit_lock, _authoritative_plan

    if config_path is None:
        # The standalone Markdown viewer has no SDK runtime or scientific model.
        marker = json.loads((path.parent / ".theory-master" / "task.json").read_text(encoding="utf-8"))
        if Path(run["research_root"]).resolve() != path.parent or marker.get("schema") == "theory-master/task/v2":
            raise ResearchPlanError("缺少科研任务配置，无法安全保存", 409)
        yield None
        return
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not config.get("goal_id"):
        raise ResearchPlanError("Scientific Model 运行元数据绑定无效，请先修复任务", 409)
    root = Path(run["research_root"]).resolve()
    try:
        bound_plan, goal_id, bound_root = _authoritative_plan(root)
        if bound_plan != path or bound_root != root:
            raise ValueError("Scientific Model task binding changed")
    except (KeyError, ValueError, RuntimeError, OSError) as exc:
        raise ResearchPlanError("Scientific Model 运行元数据绑定无效，请先修复任务", 409) from exc
    graph = Path(config.get("graph_database") or Path(config["branch_canvas"]).with_name("science_graph.sqlite3"))
    with science_commit_barrier(root, "shared"):
        from theory_master.science_graph_store import ScienceGraphStore
        try:
            if not graph.is_absolute() or graph.is_symlink() or not graph.is_file() or graph != graph.resolve():
                raise ValueError("missing or unsafe canonical graph")
            with ScienceGraphStore(graph, readonly=True) as store:
                store.goal_terminal()
        except (ValueError, OSError) as exc:
            raise ResearchPlanError("规范科研图谱不可用，无法确认 Goal 状态；未保存", 409) from exc
        assert_goal_open(root, graph)
        with model_commit_lock(root):
            from theory_master.scientific_model import load as load_scientific_model
            try:
                load_scientific_model(root, goal_id=goal_id)
            except (ValueError, OSError) as exc:
                raise ResearchPlanError("Scientific Model 运行元数据无效，请先修复任务", 409) from exc
            yield (root, goal_id)


def save_section(search_root: Path, run: dict[str, Any], payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ResearchPlanError("保存参数无效")
    restore = payload.get("operation") == "restore"
    allowed = ({"operation", "version", "expected_revision", "change_reason"} if restore
               else {"section", "text", "expected_revision", "change_reason"})
    if restore and "scope" in payload:
        allowed.add("scope")
    if set(payload) != allowed:
        raise ResearchPlanError("保存参数无效，请填写更正问题说明")
    reason = payload["change_reason"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1200:
        raise ResearchPlanError("保存时必须填写 1-1200 字更正问题说明")
    expected = payload["expected_revision"]
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ResearchPlanError("版本号无效")
    if restore:
        scope = payload.get("scope", "plan")
        if scope not in SCOPES:
            raise ResearchPlanError("历史类型无效")
        version = payload["version"]
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ResearchPlanError("恢复版本号无效")
    else:
        name, value = payload["section"], payload["text"]
        if (name not in SECTIONS or not isinstance(value, str) or "\x00" in value
                or len(value) > MAX_SECTION_CHARS):
            raise ResearchPlanError("字段名称或长度无效")
    path, config_path = _location(search_root, run)
    model_edit = (scope == "model") if restore else name == "Scientific Model"
    with _edit_guard(path, run, model_edit=model_edit, config_path=config_path) as model_binding, plan_lock(path):
        if _location(search_root, run) != (path, config_path):
            raise ResearchPlanError("Research Plan 的任务绑定已发生变化", 409)
        before = path.stat()
        raw = _read(path)
        parsed = _parse(raw)
        current = capture_current(path)
        if restore:
            if expected not in {_sha(project(raw, scope)), _sha(raw)}:
                raise ResearchPlanError("当前 Plan 已变化，请先刷新历史", 409)
            try:
                updated = merge(raw, content(path, version, scope), scope)
                _parse(updated)
            except ValueError as exc:
                raise ResearchPlanError(str(exc), 404) from exc
            section = "Scientific Model" if scope == "model" else "plan"
            source = "web-restore"
        else:
            if _sha(parsed[name]) != expected:
                raise ResearchPlanError("此字段已被其他窗口或科研进程修改。请重新读取后再编辑。", 409)
            if parsed[name] == value:
                return _response(run, raw, current["version"], current["model_version"])
            matches = list(HEADER.finditer(raw))
            idx = SECTIONS.index(name)
            start, end = matches[idx].end(), (matches[idx + 1].start() if idx + 1 < len(matches) else len(raw))
            replacement = value if value.endswith("\n") else value + ("\n" if idx == len(SECTIONS) - 1 else "\n\n")
            newline = "\r\n" if "\r\n" in raw else "\n"
            replacement = replacement.replace("\r\n", "\n").replace("\n", newline)
            updated = raw[:start] + replacement + raw[end:]
            section, source = name, "web-edit"
        if updated == raw:
            return _response(run, raw, current["version"], current["model_version"])
        human_model = None
        prepare_revision = None
        if model_edit and model_binding is not None:
            from theory_master.embedded_state import parse as parse_model
            from theory_master.scientific_model import load_from_paths, prepare_human_revision

            root, goal_id = model_binding
            try:
                previous = load_from_paths(root, path, goal_id=goal_id)
                summary = parse_model(updated).summary
                # Validate before reserving a runtime version or changing bytes.
                from theory_master.scientific_model import validate_model
                validate_model({**previous, "summary": summary})
            except (ValueError, OSError) as exc:
                raise ResearchPlanError(str(exc), 409) from exc
            def prepare_revision():
                nonlocal human_model
                human_model = prepare_human_revision(root, previous, summary, reason=reason)
        try:
            _write_updated(path, before, updated, before_replace=prepare_revision)
        except OSError as exc:
            if human_model is not None:
                raise ResearchPlanError(
                    f"内容未保存；Scientific Model v{human_model['version']} 已预留以阻止旧科研进程覆盖，请刷新后重试", 409
                ) from exc
            raise
        if human_model is not None:
            from theory_master.scientific_model import finalize_human_revision
            try:
                finalize_human_revision(model_binding[0], human_model)
            except (OSError, ValueError, RuntimeError) as exc:
                raise ResearchPlanError(
                    f"内容已保存；Scientific Model v{human_model['version']} 来源或历史尚待恢复，请刷新核对后重试", 409
                ) from exc
        row = record(path, updated, reason=reason, source=source, section=section)
        response = _response(run, updated, row["version"], row["model_version"])
        if human_model is not None:
            response["scientific_model_version"] = human_model["version"]
            response["scientific_model_source"] = human_model["provenance"].get("created_by")
            response["scientific_model_status"] = "human_revision_not_evidence"
        return response
