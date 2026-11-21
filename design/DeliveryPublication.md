# Explicit delivery publication

`publish-delivery` gives the SDK a narrow file publication operation for a task
whose Research Contract names TaskHome `analysis/` and `result/` deliverables.
An operator supplies the mapping explicitly and binds it to the current Research
Plan hash. The SDK validates file identity and provenance; the operator remains
responsible for matching the mapping to the contract. The SDK does not infer
destination paths from prose or expand any Agent's write permissions.

```sh
sciver publish-delivery TASK_ID --manifest publication.json
sciver publish-delivery TASK_ID --manifest publication.json --apply
```

The first command previews without writing files, creating a lock or starting a
Workflow, backend, scientific Cycle or Goal Judge. `--apply` acquires the task's
exclusive science commit barrier, revalidates the whole mapping and publishes
files. A sealed Goal rejects publication. A paused later Cycle does not make an
earlier committed invocation material unpublished or change its identity.
The CLI loads the existing runtime task pointer named by `research_root` and
checks its registered Task ID and root. Missing pointers fail without repair.

## Manifest

All hashes below are lowercase 64-character SHA-256 digests of existing files.
Absolute paths identify this task, its plan, TaskHome destinations and any
supplemental document. A registered material path is relative to its exact Cycle
workspace. Mappings are individual files; directory copies are unsupported.

```json
{
  "schema": "theory-master/delivery-publication/v1",
  "research_root": "/srv/sciver/task/research",
  "plan_sha256": "<current research_plan.md SHA-256>",
  "destinations": {
    "analysis": "/srv/sciver/task-home/analysis",
    "result": "/srv/sciver/task-home/result"
  },
  "files": [
    {
      "source": {
        "kind": "registered_material",
        "cycle_id": "6",
        "invocation_id": "inv-6-investigator",
        "path": "analysis/main.py",
        "sha256": "<captured source SHA-256>"
      },
      "target": "analysis/c6/main.py"
    },
    {
      "source": {
        "kind": "supplemental_document",
        "path": "/srv/sciver/task-home/field-coverage.md",
        "sha256": "<document SHA-256>",
        "references": [
          {
            "path": "/srv/sciver/task-home/inputs/description.md",
            "sha256": "<source SHA-256>"
          }
        ]
      },
      "target": "result/field-coverage.md"
    }
  ]
}
```

`registered_material` must match a committed Evidence registration's completed
invocation manifest, captured hash and exact local Cycle workspace. The shared
delivery material verifier checks that binding. Availability of a historical
file does not change the scientific verdict or Evidence lifecycle.

`supplemental_document` registers an existing `.md`, `.txt`, `.rst`, `.pdf` or
`.html` document as delivery only. Its content and at least one explicit source
reference must have pinned hashes. Sources and references must belong to this
task's research, note or TaskHome roots. Files under the task's benchmark root
require exact registration in Required Context, including when benchmark is
nested inside TaskHome. The publisher neither writes document content nor
asserts that its prose is a scientific finding. A document cannot cite itself.

Targets must be files below the two explicitly mapped TaskHome directories.
These directories cannot overlap research, notes, inputs or benchmark roots,
including the repository's recognized synchronized `笔记区` vault boundary.
Symlinks, path traversal, foreign task identities, duplicate/overlapping targets,
missing sources and hash drift are rejected. Existing different target bytes
are a conflict, including a concurrent write after preflight.

## Receipt and recovery

The publication ID is SHA-256 of canonical manifest JSON. The immutable receipt
is `research/delivery_publications/<publication_id>.json` and contains the plan
hash, manifest, exact source/target identities, provenance and its own digest.
It carries `authority: "delivery_only"` and `scientific_evidence: false`.
It is not an Evidence registration, scientific adjudication or Goal receipt.

The SDK checks the complete mapping before writing. Each file is staged in its
destination directory, hashed and fsynced, then installed atomically without
overwriting an existing name. Identical existing bytes are an idempotent replay.
The receipt is installed last. A multi-file publication is not a filesystem
transaction: interruption can leave some published files without a receipt.
Repeating the same manifest completes missing files and preserves existing
identical ones. A conflict requires a new explicit mapping or operator action;
the SDK does not delete or replace the conflicting content.

`read_publications(spec) -> list[dict[str, Any]]` performs no writes. It checks
the receipt digest and task mapping, current committed source binding, source
and reference hashes, and every destination. A verified publication has
`status: "available"`, and each `files[]` item includes its absolute target
`path`, `sha256`, `status: "available"`, `kind`, `source` and `references`.
A failed receipt returns `status: "drifted"`, diagnostics and an empty `files`
list, granting no file access. Goal material access accepts only these verified
exact destination files; it does not gain a general TaskHome read root.

Future delivery snapshots include publications. Tasks without receipts retain
the old delivery payload shape. Publication does not create or rewrite an old
delivery revision, Goal assessment, scientific model, Evidence or Cycle state.
The scientific authority digest is unchanged by publication. `repair-delivery`
retains its existing scientific identity checks: publishing files cannot make an
old supported Cycle eligible for rejudgment after scientific authority changes.
A formal Goal assessment remains a separate explicitly requested operation.
