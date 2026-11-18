"""Project preparation independent of a scientific Task, with explicit creation.

The workspace contains research material and a mutable Plan draft. Only a
reviewed, complete four-section Plan can initialize a fresh Task. No model,
Literature Agent, Worker, or scientific Cycle is invoked by this service.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "sciver/research-preparation/v1"
_IDENT = re.compile(r"[0-9a-f]{32}\Z")
_LIMIT = 1_048_576
_MATERIAL_LIMIT = 32 * _LIMIT


class PreparationError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _directory(path: Path, *, create: bool = False) -> Path:
    path = Path(path).expanduser().absolute()
    if path != path.resolve() or path.is_symlink():
        raise PreparationError("筹备目录路径不安全", 403)
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise PreparationError("筹备工作区不存在", 404)
    return path


def workspace(state_dir: Path, ident: str) -> Path:
    if not isinstance(ident, str) or not _IDENT.fullmatch(ident):
        raise PreparationError("筹备会话标识无效", 404)
    root = _directory(Path(state_dir))
    return _directory(root / "research-preparations" / ident)


def _read(path: Path, limit: int = _LIMIT) -> bytes:
    if path.is_symlink() or not path.is_file() or path != path.resolve():
        raise PreparationError("筹备文件不存在或路径不安全", 409)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise PreparationError("筹备文件超过大小限制", 413)
    data = path.read_bytes()
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise PreparationError("筹备材料正在变化，请刷新后重试", 409)
    return data


def _atomic(path: Path, data: bytes) -> None:
    if path.is_symlink() or path.parent != path.parent.resolve():
        raise PreparationError("筹备保存路径不安全", 403)
    temporary = path.parent / (".preparation-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_meta(root: Path, value: dict) -> None:
    _atomic(root / "metadata.json", (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


@contextmanager
def _locked(root: Path):
    try:
        fd = os.open(root / ".preparation.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise PreparationError("筹备锁路径不可用或不安全", 409) from exc
    with os.fdopen(fd, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def _metadata(root: Path, ident: str) -> dict:
    try:
        value = json.loads(_read(root / "metadata.json"))
    except (ValueError, UnicodeError) as exc:
        raise PreparationError("筹备记录损坏，请先修复记录", 409) from exc
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or value.get("id") != ident or value.get("session_id") != ident
            or not isinstance(value.get("entry"), str) or value["entry"] not in {"clear", "exploratory"}):
        raise PreparationError("筹备记录绑定无效", 409)
    return value


def _snapshot(root: Path, value: dict) -> dict:
    try:
        draft = _read(root / "research_plan.md").decode("utf-8")
    except UnicodeError as exc:
        raise PreparationError("计划草稿须使用 UTF-8", 409) from exc
    reviewed = {key: value[key] for key in ("entry", "title", "intent")}
    reviewed["draft"] = draft
    revision = _review_revision(reviewed)
    plan_ready = False
    if draft.strip():
        try:
            _valid_plan(draft)
            plan_ready = True
        except PreparationError:
            pass
    return {**value, "draft": draft, "revision": revision, "draft_revision": _sha(draft.encode()),
            "plan_ready": plan_ready,
            "creation_status": "created" if value.get("created_task") else "creating" if value.get("creation") else "preparing",
            "execution_started": False}


def _review_revision(reviewed: dict) -> str:
    return _sha(json.dumps(reviewed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value.encode("utf-8")) > limit:
        raise PreparationError(field + "格式或长度无效")
    return value


def initialize(state_dir: Path, ident: str, entry: str, title: str = "") -> dict:
    if (not isinstance(ident, str) or not _IDENT.fullmatch(ident)
            or not isinstance(entry, str) or entry not in {"clear", "exploratory"}):
        raise PreparationError("筹备入口或会话标识无效")
    title = _text(title, "项目标题", 800)
    state = _directory(Path(state_dir), create=True)
    root = _directory(state / "research-preparations" / ident, create=True)
    with _locked(root):
        if (root / "metadata.json").exists():
            value = _metadata(root, ident)
            if value["entry"] != entry:
                raise PreparationError("该筹备会话已选择其他入口", 409)
        else:
            value = {"schema": SCHEMA, "id": ident, "session_id": ident,
                     "entry": entry, "title": title, "intent": "", "created_at": _now(),
                     "updated_at": _now(), "creation": None, "created_task": None}
            if (root / "research_plan.md").exists():
                _read(root / "research_plan.md")
            else:
                _atomic(root / "research_plan.md", b"")
            _write_meta(root, value)
        return _snapshot(root, value)


def snapshot(state_dir: Path, ident: str) -> dict:
    root = workspace(state_dir, ident)
    # Literature holds the material lock throughout acquisition so project
    # creation cannot freeze a partial corpus. Reading Chat must remain usable
    # during that work. Each file is atomically published; retry an observed
    # change instead of joining the long-running material transaction. Mutations
    # and creation still recheck the review revision under their exclusive lock.
    for _ in range(3):
        metadata = _metadata(root, ident)
        result = _snapshot(root, metadata)
        if metadata == _metadata(root, ident) and result["draft_revision"] == _sha(_read(root / "research_plan.md")):
            return result
    raise PreparationError("筹备草稿正在更新，请稍后重新读取", 409)


def update(state_dir: Path, ident: str, payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) - {"title", "intent", "draft", "expected_revision"} or "expected_revision" not in payload:
        raise PreparationError("筹备保存参数无效")
    root = workspace(state_dir, ident)
    with _locked(root):
        value = _metadata(root, ident)
        current = _snapshot(root, value)
        if value.get("creation") or value.get("created_task"):
            raise PreparationError("该筹备已确认立项，请完成创建或在正式项目中编辑", 409)
        if payload["expected_revision"] != current["revision"]:
            raise PreparationError("计划草稿已变化，请刷新并重新审阅", 409)
        for field, limit in (("title", 800), ("intent", 20_000), ("draft", _LIMIT)):
            if field in payload:
                text = _text(payload[field], field, limit)
                if field == "draft":
                    _atomic(root / "research_plan.md", text.encode())
                else:
                    value[field] = text
        value["updated_at"] = _now()
        _write_meta(root, value)
        return _snapshot(root, value)


def _valid_plan(draft: str) -> None:
    from theory_master.specification import scientific_sections

    headings = re.findall(r"(?m)^# (?!#)(.+?)\s*$", draft)
    if headings != ["Goal", "Research Contract", "Final Gate", "Required Context"]:
        raise PreparationError("立项计划必须恰好包含 Goal、Research Contract、Final Gate、Required Context 四个 H1；Scientific Model 由新任务空白初始化", 422)
    try:
        scientific_sections(draft, allow_missing_scientific_model=True)
    except ValueError as exc:
        raise PreparationError("计划尚未完成：" + str(exc), 422) from exc


def _freeze(root: Path, value: dict, draft: str, conversation: Any) -> dict[str, bytes]:
    try:
        dialogue = json.dumps(conversation if conversation is not None else [], ensure_ascii=False, indent=2).encode()
    except (ValueError, TypeError) as exc:
        raise PreparationError("筹备对话快照格式无效") from exc
    if len(dialogue) > _LIMIT:
        raise PreparationError("筹备对话快照超过 1 MiB", 413)
    files = {"research_plan.md": draft.encode(), "conversation.json": dialogue,
             "preparation.json": json.dumps({key: value[key] for key in ("schema", "id", "session_id", "entry", "title", "intent", "created_at")}, ensure_ascii=False, indent=2).encode()}
    total = sum(map(len, files.values()))
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part in {"__pycache__", "tmp"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise PreparationError("筹备材料包含符号链接，不能自动迁移", 403)
        if path.is_dir() or str(relative) in {"metadata.json", "research_plan.md"}:
            continue
        data = _read(path, 16 * _LIMIT)
        total += len(data)
        if total > _MATERIAL_LIMIT or len(files) >= 200:
            raise PreparationError("筹备材料过多，请缩小迁移范围", 413)
        files["materials/" + relative.as_posix()] = data
    return files


def _publish_snapshot(target: Path, files: dict[str, bytes]) -> str:
    home = target / "preparation_snapshot"
    home.mkdir(mode=0o700, exist_ok=False)
    manifest = {"schema": "sciver/preparation-snapshot/v1",
                "files": [{"path": name, "sha256": _sha(data), "bytes": len(data)} for name, data in sorted(files.items())]}
    for name, data in files.items():
        path = home / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _atomic(path, data)
        path.chmod(0o400)
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    _atomic(home / "manifest.json", manifest_bytes)
    (home / "manifest.json").chmod(0o400)
    return _sha(manifest_bytes)


def _verify_snapshot(target: Path, draft_revision: str, manifest_sha256: str,
                     reviewed_revision: str) -> str:
    home = _directory(target / "preparation_snapshot")
    try:
        raw_manifest = _read(home / "manifest.json")
        if _sha(raw_manifest) != manifest_sha256:
            raise ValueError("snapshot manifest changed")
        manifest = json.loads(raw_manifest)
        for row in manifest["files"]:
            relative = Path(row["path"])
            if relative.is_absolute() or ".." in relative.parts or _sha(_read(home / relative, 16 * _LIMIT)) != row["sha256"]:
                raise ValueError("snapshot checksum changed")
        draft = _read(home / "research_plan.md").decode()
        if _sha(draft.encode()) != draft_revision:
            raise ValueError("reviewed Plan changed")
        preparation = json.loads(_read(home / "preparation.json"))
        reviewed = {key: preparation[key] for key in ("entry", "title", "intent")}
        reviewed["draft"] = draft
        if _review_revision(reviewed) != reviewed_revision:
            raise ValueError("reviewed preparation changed")
        _valid_plan(draft)
        return draft
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise PreparationError("筹备快照不完整或已变化，请先修复创建记录", 409) from exc


def _publish_staging(staging: Path, target: Path) -> None:
    """Publish one completed directory atomically, never replacing an occupant.

    A plain POSIX rename can replace an empty directory created by another
    process between our existence check and publication. Keep that protection
    in the filesystem operation itself; unsupported hosts retain the staging
    directory and the durable creation record for a later retry.
    """
    rename = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if rename is None:
        raise PreparationError("当前平台不支持安全的原子项目发布；已保留筹备快照", 409)
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(staging), -100, os.fsencode(target), 1) != 0:
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise PreparationError("新项目目录已被占用；未覆盖已有项目", 409)
        raise OSError(error, os.strerror(error), str(target))


def _creation_directory(target: Path, creation: dict) -> Path:
    """Use only the exact random staging directory named by this creation."""
    token = creation.get("token")
    if not isinstance(token, str) or not _IDENT.fullmatch(token):
        raise PreparationError("新项目创建来源校验失败", 409)
    staging = target.parent / (".prep-stage-" + target.name.removeprefix("project-") + "-" + token)
    if creation.get("staging_root") != str(staging):
        raise PreparationError("新项目临时目录绑定无效", 409)
    return staging


def _verify_origin(directory: Path, creation: dict) -> None:
    _directory(directory)
    try:
        origin = json.loads(_read(directory / "preparation_origin.json"))
    except (ValueError, UnicodeError) as exc:
        raise PreparationError("新项目创建来源校验失败", 409) from exc
    if origin != {key: entry for key, entry in creation.items() if key != "snapshot_sha256"}:
        raise PreparationError("新项目创建来源校验失败", 409)


def _recover_initialization(target: Path, draft: str) -> None:
    """Complete only the empty task initialization owned by this preparation."""
    from theory_master.embedded_state import initialize_model, parse
    from theory_master.scientific_model import empty_model, initialize_state, load_from_paths
    from theory_master.science_graph_store import ScienceGraphStore
    from theory_master.specification import scientific_sections, list_cycle_dirs
    from theory_master.task import non_sync_data_area

    config_path = target / "theory-master.config.json"
    if not config_path.exists():
        return
    config = json.loads(_read(config_path))
    root = Path(config.get("research_root", ""))
    expected = non_sync_data_area(target / "research_plan.md", int(config["task_id"]), str(config["goal_id"])) / "research"
    if (config.get("schema") != "theory-master/system/v1" or config.get("path_model") != "theory-master/task-path/v2"
            or config.get("goal_id") != target.name or config.get("state_seed") is not None
            or config.get("branch_anchor_id") is not None
            or root != expected or config.get("task_home") != str(target)
            or config.get("plan_path") != str(target / "research_plan.md")
            or config.get("graph_database") != str(root / "science_graph.sqlite3")
            or config.get("branch_canvas") != str(target / "exploration.canvas")
            or config.get("notes_dir") != str(target / "notes")):
        raise PreparationError("部分创建的任务绑定已变化，不能自动恢复", 409)
    if root.is_symlink() or root != root.resolve() or list_cycle_dirs(root, task_mode=True):
        raise PreparationError("目标任务已有科研运行或路径变化，不能重复初始化", 409)
    graph = root / "science_graph.sqlite3"
    if graph.is_symlink():
        raise PreparationError("新项目图谱路径不安全", 403)
    if graph.exists():
        with ScienceGraphStore(graph, readonly=True) as store:
            if any(store.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                   for table in ("hypotheses", "outcomes", "external_records", "graph_events")):
                raise PreparationError("目标任务已有科研记录，不能作为空白项目重复初始化", 409)
    else:
        with ScienceGraphStore(graph):
            pass
    current = _read(target / "research_plan.md").decode()
    if scientific_sections(current, allow_missing_scientific_model=True) != scientific_sections(draft, allow_missing_scientific_model=True):
        raise PreparationError("部分创建的研究计划已变化，未覆盖目标文件", 409)
    if "# Scientific Model" in current and parse(current).summary:
        raise PreparationError("目标项目已有 Scientific Model，不能重新初始化", 409)
    initialize_model(target / "research_plan.md", empty_model(str(config["goal_id"])))
    initialize_state(root, goal_id=str(config["goal_id"]))
    model = load_from_paths(root, target / "research_plan.md", goal_id=str(config["goal_id"]))
    if model["version"] != 0 or model["summary"]:
        raise PreparationError("目标项目已有 Scientific Model 版本，不能重新初始化", 409)


def create_project(state_dir: Path, search_root: Path, ident: str, payload: dict,
                   conversation: Any = None) -> dict:
    """Confirm the reviewed draft, initialize one fresh Task and retain snapshots."""
    from theory_master.task import bootstrap

    if (not isinstance(payload, dict) or set(payload) != {"confirm", "expected_revision"}
            or payload.get("confirm") is not True):
        raise PreparationError("请明确确认已审阅的计划后创建新项目")
    root = workspace(state_dir, ident)
    search = _directory(Path(search_root))
    with _locked(root):
        value = _metadata(root, ident)
        target = search / "projects" / ("project-" + ident)
        if value.get("created_task"):
            if value["created_task"].get("task_root") != str(target):
                raise PreparationError("已创建项目不属于当前工作区，不能更换创建目标", 409)
            return {**value["created_task"], "idempotent": True}
        current = _snapshot(root, value)
        creation = value.get("creation")
        if creation is None:
            if payload["expected_revision"] != current["revision"]:
                raise PreparationError("计划草稿已变化，请刷新并重新审阅", 409)
            _valid_plan(current["draft"])
            chat_path = Path(state_dir) / "research-chat" / (ident + ".json")
            if chat_path.exists() or chat_path.is_symlink():
                try:
                    chat = json.loads(_read(chat_path))
                except (ValueError, UnicodeError) as exc:
                    raise PreparationError("筹备对话记录无效，尚未创建项目", 409) from exc
                if (not isinstance(chat, dict) or chat.get("id") != ident
                        or chat.get("mode") != "preparation" or not isinstance(chat.get("messages"), list)):
                    raise PreparationError("筹备对话绑定无效，尚未创建项目", 409)
                conversation = chat["messages"]
            files = _freeze(root, value, current["draft"], conversation)
            _directory(search / "projects", create=True)
            if target.exists() or target.is_symlink():
                raise PreparationError("新项目目录已被占用；未覆盖已有项目", 409)
            if _snapshot(root, value)["revision"] != current["revision"]:
                raise PreparationError("筹备计划在迁移材料时发生变化，请重新审阅", 409)
            creation = {"target_root": str(target), "reviewed_revision": current["revision"],
                        "draft_revision": current["draft_revision"],
                        "token": uuid.uuid4().hex, "started_at": _now()}
            staging = target.parent / (".prep-stage-" + ident + "-" + creation["token"])
            creation["staging_root"] = str(staging)
            staging.mkdir(mode=0o700)
            try:
                _atomic(staging / "preparation_origin.json", (json.dumps(creation) + "\n").encode())
                creation["snapshot_sha256"] = _publish_snapshot(staging, files)
                _atomic(staging / "research_plan.md", current["draft"].encode())
                _verify_snapshot(staging, creation["draft_revision"], creation["snapshot_sha256"], creation["reviewed_revision"])
            except (PreparationError, ValueError, RuntimeError, OSError, KeyError) as exc:
                # No durable creation refers to this new private directory yet.
                # Never clean a target directory or any previously saved stage.
                try:
                    shutil.rmtree(staging)
                except OSError:
                    pass  # An unpublished hidden stage cannot become a Task.
                raise PreparationError("筹备快照尚未完成，未创建项目；请重试同一审阅版本：" + str(exc)[:300], 409) from exc
            value["creation"] = creation
            try:
                _write_meta(root, value)
            except (PreparationError, OSError) as exc:
                # A failed write may still have replaced metadata.json. Retain
                # the complete stage: the next call reads durable state first.
                raise PreparationError("项目确认记录尚未完成；完整筹备快照已保留，请重试同一创建请求：" + str(exc)[:300], 409) from exc
        else:
            if (not isinstance(creation, dict) or creation.get("target_root") != str(target)
                    or payload["expected_revision"] != creation.get("reviewed_revision")):
                raise PreparationError("已有已确认的部分创建，请使用原审阅版本恢复，不能创建第二个项目", 409)
        try:
            staging = _creation_directory(target, creation)
            if not target.exists() and not target.is_symlink():
                _verify_origin(staging, creation)
                draft = _verify_snapshot(staging, creation["draft_revision"], creation["snapshot_sha256"], creation["reviewed_revision"])
                if _read(staging / "research_plan.md").decode() != draft:
                    raise PreparationError("新项目临时计划与审阅快照不一致", 409)
                _publish_staging(staging, target)
            _verify_origin(target, creation)
            draft = _verify_snapshot(target, creation["draft_revision"], creation["snapshot_sha256"], creation["reviewed_revision"])
            _recover_initialization(target, draft)
            binding = bootstrap(target / "research_plan.md", allocation_root=search)
            from theory_master.scientific_model import load
            from theory_master.science_graph_store import ScienceGraphStore
            model = load(binding.research_root)
            with ScienceGraphStore(Path(binding.config["graph_database"]), readonly=True) as store:
                if store.db.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] or model["version"] != 0 or model["summary"]:
                    raise PreparationError("新项目空白状态校验失败", 409)
            from theory_master.specification import scientific_sections
            goal = scientific_sections(draft, allow_missing_scientific_model=True)[0]
            frozen_preparation = json.loads(_read(target / "preparation_snapshot" / "preparation.json"))
            title = " ".join(str(frozen_preparation.get("title") or "").split())
            if not title:
                title = next((line.strip(" #\t") for line in goal.splitlines() if line.strip()), "")
            title = title[:120] or "项目 Task " + str(binding.config["task_id"])
            config = json.loads(_read(binding.config_path))
            config["task_name"] = title
            _atomic(binding.config_path, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode())
        except PreparationError:
            raise
        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            raise PreparationError("项目创建尚未完成；原目标与审阅快照已保留，请重试同一创建请求：" + str(exc)[:300], 409) from exc
        observer_id = hashlib.sha256(str(binding.research_root).encode()).hexdigest()[:16]
        receipt = {"status": "created", "preparation_id": ident, "observer_id": observer_id,
                   "task_id": binding.config["task_id"], "task_root": str(target),
                   "task_name": title,
                   "research_root": str(binding.research_root), "plan_path": str(target / "research_plan.md"),
                   "task_href": "/run/" + observer_id + "/", "plan_href": "/research-plan/?observer_id=" + observer_id,
                   "snapshot_path": str(target / "preparation_snapshot"), "execution_started": False,
                   "scientific_model_empty": True, "graph_empty": True, "idempotent": False}
        value.update(created_task=receipt, updated_at=_now())
        try:
            _write_meta(root, value)
        except (PreparationError, OSError) as exc:
            raise PreparationError("项目已初始化，创建回执尚未保存；请重试同一创建请求以恢复回执：" + str(exc)[:300], 409) from exc
        return receipt
