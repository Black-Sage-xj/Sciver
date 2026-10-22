from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

SCHEMA = "theory-master/change-request/v1"
REQUEST_ID_RE = re.compile(r"^TMCR-(\d{8})-(\d{3})$")
CHANGE_TYPES = {"prompt", "scale", "routing", "field", "model_architecture", "ui_observability", "other"}

@dataclass(frozen=True)
class ChangeRequestLoad:
    requests: tuple[dict, ...]
    warnings: tuple[str, ...]

def default_request_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "change_requests"

def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return [item.strip() for item in value]

def validate_request(payload: object, *, allowed_flow_nodes: Iterable[str] | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("request must be a JSON object")
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"schema must be {SCHEMA}")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError("request_id must match TMCR-YYYYMMDD-NNN")
    for field in ("title", "status", "created_at", "summary", "design_motivation", "current_failure_mode", "proposal", "priority"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise ValueError(f"{field} must be a non-empty string")
    problem = payload.get("problem")
    if not isinstance(problem, dict) or any(not isinstance(problem.get(k), str) or not problem[k].strip() for k in ("owner", "stage", "issue")):
        raise ValueError("problem must contain non-empty owner, stage, and issue")
    for field in ("cost_audit", "risk_audit"):
        if not isinstance(payload.get(field), dict) or not payload[field]:
            raise ValueError(f"{field} must be a non-empty object")
    change_types = _string_list(payload.get("change_types"), "change_types")
    unknown_types = sorted(set(change_types) - CHANGE_TYPES)
    if unknown_types:
        raise ValueError(f"unknown change_types: {', '.join(unknown_types)}")
    sdk_refs = _string_list(payload.get("sdk_design_refs"), "sdk_design_refs")
    if not all(ref.startswith("tool/theory_master/") for ref in sdk_refs):
        raise ValueError("sdk_design_refs must point into tool/theory_master/")
    affected_nodes = _string_list(payload.get("affected_flow_nodes"), "affected_flow_nodes")
    allowed = set(allowed_flow_nodes or ())
    if allowed:
        unknown_nodes = sorted(set(affected_nodes) - allowed)
        if unknown_nodes:
            raise ValueError(f"unknown affected_flow_nodes: {', '.join(unknown_nodes)}")
    _string_list(payload.get("affected_files"), "affected_files")
    _string_list(payload.get("validation"), "validation")
    projection = payload.get("after_flow_projection")
    if projection is not None:
        if not isinstance(projection, dict):
            raise ValueError("after_flow_projection must be an object")
        remove_nodes = _string_list(projection.get("remove_nodes", []), "after_flow_projection.remove_nodes")
        replace_nodes = projection.get("replace_nodes", {})
        if not isinstance(replace_nodes, dict) or any(
            not isinstance(node, str) or not node.strip() or not isinstance(definition, str) or not definition.strip()
            for node, definition in replace_nodes.items()
        ):
            raise ValueError("after_flow_projection.replace_nodes must map node IDs to non-empty Mermaid definitions")
        add_edges = projection.get("add_edges", [])
        if not isinstance(add_edges, list):
            raise ValueError("after_flow_projection.add_edges must be a list")
        edge_nodes: set[str] = set()
        for edge in add_edges:
            if not isinstance(edge, dict) or any(not isinstance(edge.get(k), str) or not edge[k].strip() for k in ("from", "to")):
                raise ValueError("after_flow_projection.add_edges entries require non-empty from/to")
            edge_nodes.update((edge["from"].strip(), edge["to"].strip()))
        if allowed:
            referenced = set(remove_nodes) | set(replace_nodes) | edge_nodes
            unknown_projection_nodes = sorted(referenced - allowed)
            if unknown_projection_nodes:
                raise ValueError(f"unknown after_flow_projection nodes: {', '.join(unknown_projection_nodes)}")
        removed = set(remove_nodes)
        invalid_edges = sorted(node for node in edge_nodes if node in removed)
        if invalid_edges:
            raise ValueError(f"after_flow_projection edges reference removed nodes: {', '.join(invalid_edges)}")
        for node, definition in replace_nodes.items():
            if not re.match(rf"^\s*{re.escape(node)}\s*[\[\{{\(]", definition):
                raise ValueError(f"after_flow_projection replacement for {node} must define that same node")
    return payload

def load_change_requests(request_dir: Path | None = None, *, allowed_flow_nodes: Iterable[str] | None = None) -> ChangeRequestLoad:
    root = (request_dir or default_request_dir()).resolve()
    if not root.exists():
        return ChangeRequestLoad((), ())
    requests: list[dict] = []
    warnings: list[str] = []
    ids: set[str] = set()
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            validate_request(payload, allowed_flow_nodes=allowed_flow_nodes)
            request_id = payload["request_id"]
            if request_id in ids:
                raise ValueError(f"duplicate request_id: {request_id}")
            ids.add(request_id)
            payload = dict(payload)
            payload["_path"] = str(path)
            requests.append(payload)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            warnings.append(f"{path.name}: {type(exc).__name__}: {exc}")
    requests.sort(key=lambda item: (item["created_at"], item["request_id"]), reverse=True)
    return ChangeRequestLoad(tuple(requests), tuple(warnings))

def next_request_id(requests: Iterable[dict], *, now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    day = current.strftime("%Y%m%d")
    used = []
    for request in requests:
        match = REQUEST_ID_RE.fullmatch(str(request.get("request_id") or ""))
        if match and match.group(1) == day:
            used.append(int(match.group(2)))
    return f"TMCR-{day}-{(max(used, default=0) + 1):03d}"

def create_request(fields: dict[str, object], request_dir: Path | None = None, *, allowed_flow_nodes: Iterable[str] | None = None, now: datetime | None = None) -> dict:
    root = (request_dir or default_request_dir()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    existing = load_change_requests(root, allowed_flow_nodes=allowed_flow_nodes)
    current = now or datetime.now().astimezone()
    payload = dict(fields)
    payload["schema"] = SCHEMA
    payload["request_id"] = next_request_id(existing.requests, now=current)
    payload["created_at"] = current.isoformat(timespec="seconds")
    validate_request(payload, allowed_flow_nodes=allowed_flow_nodes)
    path = root / f"{payload['request_id']}.json"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
