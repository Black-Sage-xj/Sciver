"""Bounded, read-only replay checks for independent Literature deliveries.

Seals attest local delivery bytes, not scientific correctness. Historical jobs
without a seal can be checked against their surviving contracts and materials,
but are explicitly reported as unsealed rather than assigned invented hashes.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import stat
from typing import Any

PROTOCOL = "sciver/literature-delivery-integrity/v1"
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FILES = 1024


class LiteratureDeliveryIntegrityError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _read(root: Path, value: str | Path) -> tuple[Path, bytes]:
    try:
        path = Path(value)
        path = path if path.is_absolute() else root / path
        if (
            root != root.resolve()
            or path != path.resolve()
            or not path.is_relative_to(root)
            or any(part.is_symlink() for part in (path, *path.parents))
        ):
            raise ValueError("unsafe local path")
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise ValueError("not a bounded regular file")
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("file exceeds its byte bound")
        return path, data
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        raise LiteratureDeliveryIntegrityError(
            f"Literature delivery material is absent or unsafe: {value}"
        ) from exc


def _json(root: Path, value: str | Path) -> Any:
    try:
        return json.loads(_read(root, value)[1])
    except (ValueError, UnicodeError) as exc:
        raise LiteratureDeliveryIntegrityError(
            f"Literature delivery JSON is unreadable: {value}"
        ) from exc


def _inventory(root: Path, paths: list[str | Path]) -> list[dict]:
    if len(paths) > MAX_FILES * 4:
        raise LiteratureDeliveryIntegrityError(
            "Literature delivery inventory exceeds its reference bound"
        )
    result, total = {}, 0
    seen = set()
    for value in paths:
        candidate = Path(value)
        candidate = candidate if candidate.is_absolute() else root / candidate
        if candidate in seen:
            continue
        seen.add(candidate)
        path, data = _read(root, value)
        key = path.relative_to(root).as_posix()
        if key in result:
            continue
        total += len(data)
        if len(result) >= MAX_FILES or total > MAX_TOTAL_BYTES:
            raise LiteratureDeliveryIntegrityError(
                "Literature delivery inventory exceeds its bound"
            )
        result[key] = {
            "path": key,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    return [result[key] for key in sorted(result)]


def _seal(root: Path, paths: list[str | Path], binding: Any) -> dict:
    return {
        "protocol": PROTOCOL,
        "binding_sha256": hashlib.sha256(_canonical(binding)).hexdigest(),
        "files": _inventory(root, paths),
    }


def _verify(root: Path, seal: dict, paths: list[str | Path], binding: Any) -> None:
    if not isinstance(seal, dict) or seal != _seal(root, paths, binding):
        raise LiteratureDeliveryIntegrityError(
            "Literature delivery integrity mismatch; retained bytes or identity changed"
        )


def _invocation_paths(invocation: dict) -> list[str]:
    output = invocation.get("output_path")
    if not isinstance(output, str) or not output:
        raise LiteratureDeliveryIntegrityError(
            "Literature invocation has no output reference"
        )
    paths = [
        invocation.get(key)
        for key in ("raw_output_path", "prompt_path", "developer_path")
    ]
    paths.append(
        invocation.get("context_path") or str(Path(output).parent / "context.json")
    )
    paths.extend([str(Path(output).parent / "agent_output.json"), output])
    if any(not isinstance(value, str) or not value for value in paths):
        raise LiteratureDeliveryIntegrityError(
            "Literature invocation has incomplete delivery references"
        )
    return paths


def _invocation_binding(invocation: dict) -> dict:
    return {
        key: invocation.get(key)
        for key in (
            "key",
            "role",
            "stage",
            "thread_id",
            "contract",
            "raw_output_path",
            "prompt_path",
            "developer_path",
            "context_path",
            "output_path",
        )
    }


def capture_invocation_delivery(root: Path, invocation: dict) -> dict:
    return _seal(root, _invocation_paths(invocation), _invocation_binding(invocation))


def verify_invocation_delivery(root: Path, invocation: dict) -> dict:
    paths = _invocation_paths(invocation)
    seal = invocation.get("delivery_integrity")
    if seal is not None:
        _verify(root, seal, paths, _invocation_binding(invocation))
    else:
        _inventory(root, paths)
    return {"status": "sealed" if seal is not None else "legacy_unsealed"}


def _transport_paths(root: Path, transport: dict) -> list[str | Path]:
    papers = transport.get("papers_json")
    if not isinstance(papers, str) or not papers:
        raise LiteratureDeliveryIntegrityError(
            "Literature retrieval has no papers index"
        )
    paths: list[str | Path] = [papers]
    # Optional navigation/log files are sealed when actually materialized;
    # required metadata, reading receipts and read bodies never become optional.
    for key in (
        "evidence_index_md",
        "papers_top_json",
        "paper_focus_json",
        "report_md",
        "topic_brief_md",
        "search_log_md",
        "errors_log",
    ):
        if transport.get(key):
            path = Path(transport[key])
            path = path if path.is_absolute() else root / path
            if path.exists() or path.is_symlink():
                paths.append(path)
    reading = transport.get("reading") or {}
    if not isinstance(reading, dict):
        raise LiteratureDeliveryIntegrityError(
            "Literature reading receipt is malformed"
        )
    if reading.get("reading_index"):
        paths.append(reading["reading_index"])
    if reading.get("pdf_manifest"):
        manifest, data = _read(root, reading["pdf_manifest"])
        paths.append(manifest)
        try:
            for number, row in enumerate(
                csv.DictReader(io.StringIO(data.decode("utf-8-sig"))), 1
            ):
                if number > 2000:
                    raise ValueError("too many reading records")
                if row.get("status") != "read":
                    continue
                target = (
                    row.get("text_path")
                    or row.get("markdown_path")
                    or manifest.parent / f"paper-{number:02d}.md"
                )
                target = Path(target)
                paths.append(
                    target if target.is_absolute() else manifest.parent / target
                )
                paths.extend(
                    row[key]
                    for key in ("conversion_manifest", "converted_markdown_path")
                    if row.get(key)
                )
        except (ValueError, UnicodeError, csv.Error) as exc:
            raise LiteratureDeliveryIntegrityError(
                "Literature reading receipt cannot be replayed"
            ) from exc
    return paths


def capture_transport_delivery(root: Path, transport: dict) -> dict:
    return _seal(root, _transport_paths(root, transport), transport)


def verify_transport_delivery(root: Path, entry: dict) -> dict:
    transport = entry.get("transport")
    if not isinstance(transport, dict):
        raise LiteratureDeliveryIntegrityError(
            "Literature retrieval receipt is malformed"
        )
    if transport.get("status") == "blocked" or transport.get("transport_ok") is False:
        raise LiteratureDeliveryIntegrityError(
            "Literature retrieval is blocked; connector failure is not source material"
        )
    paths = _transport_paths(root, transport)
    seal = entry.get("delivery_integrity")
    if seal is not None:
        _verify(root, seal, paths, transport)
    else:
        _inventory(root, paths)
    return {"status": "sealed" if seal is not None else "legacy_unsealed"}


def validate_saved_invocation(
    root: Path,
    invocation: dict,
    papers: list[dict],
    sources: list[dict],
    *,
    exact: bool = True,
) -> dict:
    """Recheck surviving raw/contract/quote bindings without changing a file."""
    from .contract_closure import close_agent_output
    from .contracts import role_contract
    from .literature_service import _access, _paper_ref
    from .output_normalization import normalize_agent_output
    from .scholarly_selection import _identity_helpers

    verify_invocation_delivery(root, invocation)
    try:
        context = _json(
            root,
            invocation.get("context_path")
            or Path(invocation["output_path"]).parent / "context.json",
        )
        raw = normalize_agent_output(
            _read(root, invocation["raw_output_path"])[1].decode("utf-8")
        ).value
        agent = _json(
            root, Path(invocation["output_path"]).parent / "agent_output.json"
        )
        output = _json(root, invocation["output_path"])
        if not all(isinstance(value, dict) for value in (context, raw, agent, output)):
            raise ValueError(
                "context, raw, agent and closed delivery must be JSON objects"
            )
        contract = role_contract("literature", phase=invocation["stage"])
        contract.validate(raw, context)
        metadata_fields = {
            "paper_title",
            "authors",
            "year",
            "doi_or_url",
            "local_original_path",
        }

        def check_metadata(row, authored):
            paper = _paper_ref(authored.get("source_ref"), papers)
            added = {
                key: val
                for key, val in row.items()
                if key in metadata_fields and key not in authored
            }
            if not added:
                return
            identity = _identity_helpers()
            ref = added.get("doi_or_url") or ""
            declared = {
                "title": added.get("paper_title"),
                "authors": added.get("authors") or [],
                "year": added.get("year"),
                "doi": identity.doi_key(ref),
                "arxiv_id": identity.arxiv_key(ref),
            }
            if not paper or not identity.same_paper(paper, declared):
                raise ValueError(
                    "SDK-added citation metadata no longer identifies the retained source"
                )
            if added.get("paper_title") and identity.title_key(
                added["paper_title"]
            ) != identity.title_key(paper.get("title")):
                raise ValueError(
                    "SDK-added citation title does not match the retained source"
                )

        # Closure historically enriched nested route navigation in-place.
        # Preserve every authored field; only SDK-added citation metadata may
        # differ, and its bibliographic identity must match the retained source.
        authored_agent = json.loads(json.dumps(agent))
        for raw_route, agent_route in zip(
            (raw.get("landscape") or {}).get("major_routes") or [],
            (authored_agent.get("landscape") or {}).get("major_routes") or [],
        ):
            for raw_row, agent_row in zip(
                raw_route.get("representative_papers") or [],
                agent_route.get("representative_papers") or [],
            ):
                check_metadata(agent_row, raw_row)
                for key in metadata_fields - raw_row.keys():
                    agent_row.pop(key, None)
        if _canonical(raw) != _canonical(authored_agent):
            raise ValueError("raw and parsed agent delivery disagree")
        # Derive current navigation on a copy, never on the authored raw.
        closed = close_agent_output(
            "literature", invocation["stage"], json.loads(json.dumps(raw)), context, {}
        )
        expected = _access(closed, raw, papers, sources)

        def without_historical_metadata(value):
            value = json.loads(json.dumps(value))
            pairs = [
                (
                    value.get("evidence_records") or [],
                    raw.get("evidence_records") or [],
                ),
                (
                    (value.get("landscape") or {}).get("representative_papers") or [],
                    (raw.get("landscape") or {}).get("representative_papers") or [],
                ),
            ]
            for closed_route, raw_route in zip(
                (value.get("landscape") or {}).get("major_routes") or [],
                (raw.get("landscape") or {}).get("major_routes") or [],
            ):
                pairs.append(
                    (
                        closed_route.get("representative_papers") or [],
                        raw_route.get("representative_papers") or [],
                    )
                )
            for rows, declarations in pairs:
                for row, declaration in zip(rows, declarations):
                    check_metadata(row, declaration)
                    for key in metadata_fields - declaration.keys():
                        row.pop(key, None)
                    # These SDK diagnostics describe acquisition availability
                    # at that earlier stage, not an authored scientific field.
                    row.pop("materialized_source_access", None)
                    row.pop("access_assessment", None)
            return value

        compare = (lambda value: value) if exact else without_historical_metadata
        if _canonical(compare(expected)) != _canonical(compare(output)):
            raise ValueError(
                "saved closure or source access no longer matches its raw delivery"
            )
        return output
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        UnicodeError,
        RecursionError,
    ) as exc:
        raise LiteratureDeliveryIntegrityError(
            f"Saved Literature delivery cannot be replayed: {exc}"
        ) from exc


def _job_paths(root: Path, state: dict) -> list[str | Path]:
    paths: list[str | Path] = [root / "report.md"]
    for invocation in state.get("invocations") or []:
        if invocation.get("status") != "completed":
            raise LiteratureDeliveryIntegrityError(
                "Completed Literature job contains an unfinished invocation"
            )
        paths.extend(_invocation_paths(invocation))
    for entry in state.get("transports") or []:
        paths.extend(_transport_paths(root, entry["transport"]))
    for source in state.get("sources") or []:
        if source.get("reading_level") == "full_text":
            paths.append(source.get("text_path") or "")
    paths.extend(
        root / "corpus" / relative
        for relative in (
            "retrieval/papers.json",
            "retrieval/papers_top.json",
            "retrieval/paper_focus.json",
            "retrieval/evidence_index.md",
            "reading/reading_index.md",
            "access_levels.json",
            "rejected_metadata.json",
            "document_conversion.json",
        )
    )
    return paths


def _job_binding(state: dict) -> dict:
    return {
        key: state.get(key)
        for key in (
            "schema",
            "job_id",
            "request",
            "search_plan",
            "route",
            "thread_id",
            "output",
            "sources",
            "source_reviews",
            "source_aliases",
            "selected_sources",
            "source_attempts",
            "transports",
            "invocations",
        )
    }


def _validate_job(root: Path, state: dict) -> None:
    if not _read(root, root / "report.md")[1].strip():
        raise LiteratureDeliveryIntegrityError("Completed Literature report is empty")
    papers = _json(root, root / "corpus/retrieval/papers.json")
    if not isinstance(papers, list):
        raise LiteratureDeliveryIntegrityError(
            "Literature corpus metadata is malformed"
        )
    sources = state.get("sources") or []
    completed = state.get("invocations") or []
    if not completed or not str(completed[-1].get("key") or "").endswith("-synthesis"):
        raise LiteratureDeliveryIntegrityError(
            "Completed Literature job has no final synthesis"
        )
    # Earlier stages may have used less metadata than the final merged corpus.
    # Their byte seals remain authoritative; the final delivery must still bind
    # the current retained sources and exact quotations.
    for entry in state.get("transports") or []:
        verify_transport_delivery(root, entry)
    for invocation in completed:
        verify_invocation_delivery(root, invocation)
        if invocation.get("delivery_integrity") is None:
            validate_saved_invocation(root, invocation, papers, sources, exact=False)
    final = validate_saved_invocation(root, completed[-1], papers, sources)
    if _canonical(final) != _canonical(state.get("output")):
        raise LiteratureDeliveryIntegrityError(
            "Literature state.output differs from its final synthesis"
        )
    if final.get("remaining_scientific_question") or any(
        row.get("declared_source_access") != "unavailable"
        and not (row.get("access_assessment") or {}).get("quote_located")
        for row in final.get("evidence_records") or []
    ):
        raise LiteratureDeliveryIntegrityError(
            "Completed Literature delivery has unresolved source access"
        )


def capture_completed_delivery(root: Path, state: dict) -> dict:
    _validate_job(root, state)
    return _seal(root, _job_paths(root, state), _job_binding(state))


def verify_completed_delivery(root: Path, state: dict) -> dict:
    """Check a sealed or legacy delivery; this function never writes or invokes."""
    try:
        seal = state.get("delivery_integrity")
        if seal is not None:
            _verify(root, seal, _job_paths(root, state), _job_binding(state))
        else:
            _inventory(root, _job_paths(root, state))
        _validate_job(root, state)
        return {"status": "sealed" if seal is not None else "legacy_unsealed"}
    except LiteratureDeliveryIntegrityError:
        raise
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        UnicodeError,
        RecursionError,
    ) as exc:
        raise LiteratureDeliveryIntegrityError(
            "Literature delivery structure is unreadable or invalid"
        ) from exc
