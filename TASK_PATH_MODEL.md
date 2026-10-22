# Sciver Task Path Model

## v2: vault sync is for scientific notes, never bulk data

```text
笔记区/探索台账/<task>/              # HUMAN / sync
  research_plan.md
  exploration.canvas
  notes/
  theory-master.config.json
  .theory-master/task.json             # tiny discovery pointer only

gs_study/.theory-master-tasks/<id>-<goal>-<hash>/  # SCIENTIFIC DATA / not synced
  benchmark/                           # optional raw network/CSV/NPZ/etc
  research/                            # Cycle state, workspace, checkpoints, audit, Evidence
    1/
    2/
    .theory-master/
```

A non-repository plan uses a sibling `.theory-master-tasks/` directory outside its Task Home.
`THEORY_MASTER_DATA_ROOT` may explicitly choose another external non-sync base;
a base inside Task Home is rejected. The absolute resolved research_root and
benchmark_root are persisted and are authoritative in `task-path/v2`.

**Agent authority**: Agents can explore the referenced raw data but may only
write Canvas/notes as compact scientific results, stable links and provenance.
The SDK persists runtime/large artifact data in research_root or benchmark_root,
not in the note area.

**Discovery / legacy**: `task-path/v1` remains readable *without automatic
reassignment or migration*. v1 still binds its historical canonical root to
`task_home/.theory-master/research` and benchmark convention to
`task_home/benchmark`; no active task's root is redirected on config load.
A v2 system explicitly binds external research_root and benchmark_root.
Both Task Home and external runtime directory have a task.json pointer to the
same visible config. Existing absolute scientific refs must remain resolvable
after any offline relocation (e.g., compatibility symlinks where supported),
and file ownership/sha256 must be checked before deleting any existing copy.
**Sync caveat:** a compatibility symlink can be followed by a sync provider;
Obsidian itself discourages symlink-based vault workflows. A migration using
links is not certified sync-clean until its *actual* sync provider is verified
not to traverse those links. Exclude the legacy aliases on each configured
sync client where supported; do not delete the only intact source to hide a
sync warning.

**Offline migration**: stop the runtime owner, verify a clean runtime lease,
and migrate full directory trees (not individual .data/.pickle files).
Preserve scientific source/lineage history, Task ID, read links, checkpoint
paths and runtime recovery state. Do not migrate or touch an active/paused
runtime; never silently rewrite old Evidence rows or commit a half-moved state.

## v1 historical layout (read-only compatibility)

The earlier model colocated benchmark, runtime and human notes below Task Home,
which caused large Obsidian sync. v1 is kept solely for backward-compatible
loading until a safe offline migration is explicitly performed.

## Evidence lineage

Evidence producer identity and scientific asset lineage are separate facts.

A current Investigator may analyze a historical asset. In that case the Registry keeps the current `producer_invocation`, while also exposing:

```json
{
  "origin_cycle": 95,
  "analysis_cycle": 96,
  "freshness": "historical_reanalysis"
}
```

For a scientific turn, historical lineage is conservatively propagated across sibling Findings when any Finding explicitly attributes a reused historical Cycle asset. This prevents a current producer/Cycle ID from being misread as independent current-Cycle data generation.

Legacy append-only Registry rows are never rewritten solely to add lineage. The manifest projection can infer lineage from the historical batch/invocation and its source references.
