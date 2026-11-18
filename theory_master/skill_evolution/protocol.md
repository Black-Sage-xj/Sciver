# Silent procedural memory sidecar

Record useful methods during normal Investigator or Literature work. This is
optional, subordinate to the scientific contract and never a new objective.
Do not run extra experiments, test suites, replays, model calls or Cycles for
learning. Keep the scientific delivery JSON unchanged.

The SDK supplies a unique sidecar path for the current invocation. Write at
most 16 KB to it; omit it when nothing warrants maintenance. The envelope has
optional `skill` and `feedback` keys. Return no maintenance narrative to users.

`skill` fields:

- `operation`: create, update, merge, archive or rollback.
- `name`: lowercase hyphenated name, at most 56 characters (the SDK adds learned-).
- `description`: relevance description, at most 400 characters.
- `when_to_use`: assumptions, applicability and exclusions, at most 800 characters.
- `body`: reusable procedure and known limits, at most 6000 characters.
- `reason`: why retain or change the procedure, at most 800 characters.
- `scope`: task (default) or general. General procedures omit task answers,
  scientific conclusions, data, credentials and host-specific paths.
- `tags`: up to eight relevance strings; required for general scope.
- `outcome`: succeeded, failed, corrected or unknown. This is an authored
  observation, not independent evidence that a skill improves performance.
- `evidence`: one to four objects with `path` (workspace-relative existing
  artifact) and `observation` (what the normal work showed).
- `scripts`: up to three workspace-relative existing .py/.sh/.js/.R helpers.
  Refer to them in the body as scripts/<filename>. Include only portable code
  actually used during normal research, never data or authentication files.
- `base_version`: exact version from the catalogue for updates/merge/archive/rollback.
- `target_version`: historical version to restore for rollback.
- `absorbs`: merge sources as objects containing name and exact version.

`feedback`: up to five objects containing `id`, `version`, `outcome` and `detail`
(at most 800 characters). Refer only to skills in this invocation's SDK catalogue.
Record actual method use separately from loading or merely seeing a catalogue.
Scientific rejection is not a method failure.

Prefer a specific correction to an existing procedure. Do not infer universal
validity from a single run. Mark inferred generalizations explicitly. Shared
skills from other tasks are read-only; create a task-local derivative to adapt
them. The SDK retains conflicting edits for revision and records all applied
changes with source, immutable versions and a proposal.
