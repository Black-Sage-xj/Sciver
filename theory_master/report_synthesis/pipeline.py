"""Final synthesis is a derived publication, separate from scientific authority."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable


def _save(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def generate_report(snapshot: dict[str, Any], output_dir: Path,
                    backend: Callable[[str, str, dict, dict], dict]) -> dict[str, Any]:
    """Make one draft and one independent review; never silently retry a model.

    Feedback is retained as structured issues for the maintainer to improve the
    shared synthesis prompt. Models cannot edit prompts or scientific records.
    A changed prompt requires a fresh output directory, preserving comparisons.
    """
    from .prompts import REPORT_SCHEMA, writer_prompt, prompt_metadata
    from .review import canonicalize_report_references, run_review_agent, validate_report
    from .snapshot import materialize_snapshot
    from .render import render_report
    from .retrieval import build_report_bundle

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    prompts = prompt_metadata()
    fingerprint = snapshot["fingerprint"]
    result_path = output_dir / "result.json"
    if result_path.is_file():
        existing = json.loads(result_path.read_text(encoding="utf-8"))
        if existing.get("snapshot_fingerprint") == fingerprint and existing.get("prompt_metadata") == prompts:
            return existing
        raise ValueError("Output already contains another report version; select a new directory")
    lock = output_dir / ".generating"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(fingerprint)
    result: dict[str, Any] = {"state": "collecting", "snapshot_fingerprint": fingerprint,
        "prompt_metadata": prompts, "report_path": None, "html_path": None, "review_path": None}
    _save(output_dir / "status.json", result)
    try:
        publication = materialize_snapshot(snapshot, output_dir / "sources")
        frozen = publication["snapshot"]
        for figure in frozen.get("figures", []):
            if figure.get("frozen_path"):
                figure["path"] = "sources/" + figure["frozen_path"]
        _save(output_dir / "source-snapshot.json", frozen)
        # Reserve the remaining backend input budget for the draft and review
        # envelope. Scientific failure records keep their text when present.
        bundle = build_report_bundle(frozen, max_chars=750_000)
        _save(output_dir / "retrieval-bundle.json", bundle)
        result["state"] = "composing"
        _save(output_dir / "status.json", result)
        report = backend("writer", writer_prompt(), {"snapshot": bundle,
            "publication_root": str(output_dir)}, REPORT_SCHEMA)
        report = canonicalize_report_references(report, bundle)
        _save(output_dir / "report.json", report)
        errors = validate_report(report, bundle)
        active_version = REPORT_SCHEMA["properties"]["schema_version"]["enum"][0]
        if report.get("schema_version") != active_version:
            errors.append(f"report.schema_version: new reports must use active schema {active_version}")
        if not isinstance(report.get("markdown"), str):
            raise ValueError("Report contains no Markdown")
        report_path = output_dir / "report.md"
        report_path.write_text(report["markdown"], encoding="utf-8")
        result.update(state="reviewing", report_path=str(report_path), validation_errors=errors)
        _save(output_dir / "status.json", result)
        review, review_errors = run_review_agent(report, bundle, frozen=frozen,
            publication_root=output_dir, report_errors=errors, backend=backend)
        _save(output_dir / "review.json", review)
        _save(output_dir / "validation.json", {"report_errors": errors, "review_errors": review_errors})
        ready = not errors and not review_errors and review.get("verdict") == "pass"
        result.update(state="ready" if ready else "needs_revision", review_path=str(output_dir / "review.json"),
                      review_validation_errors=review_errors,
                      report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest())
        result["html_path"] = str(render_report(report, frozen, output_dir, result))
    except Exception as exc:
        result.update(state="failed", error_type=type(exc).__name__, error=str(exc))
    finally:
        _save(output_dir / "status.json", result)
        _save(result_path, result)
        lock.unlink(missing_ok=True)
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Write and independently review a final research-graph report")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--allowed-root", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", help="Optional explicit override; defaults to the existing Codex configuration")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--snapshot-only", action="store_true", help="Freeze sources without making any model calls")
    args = parser.parse_args(argv)
    from .snapshot import build_snapshot, materialize_snapshot
    snapshot = build_snapshot(args.database, research_root=args.research_root,
        plan_path=args.plan, allowed_roots=args.allowed_root)
    if args.snapshot_only:
        publication = materialize_snapshot(snapshot, args.output)
        result = {key: value for key, value in publication.items() if key != "snapshot"}
    else:
        from .codex import CodexFinalReportBackend
        backend = CodexFinalReportBackend(work_dir=args.output / "agent-runs",
            model=args.model, timeout_seconds=args.timeout_seconds)
        result = generate_report(snapshot, args.output, backend)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.snapshot_only or result.get("state") == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
