# Sciver Task ④ — compact-frontier-v3 offline audit (2026-10-22)

**Scope:** read-only `ScienceGraphStore.agent_view` projection and isolated regression tests. SQLite schema, Canvas, Stage Cards, Identity, Contract, Native Goal, and scientific results are unchanged. `science_graph_tools.py` and `workflow_invocation.py` were inspected but needed no edit: their existing `hypothesis-detail` / `cycle-detail` / `external-detail` commands and version-qualified continuation stamp remain valid.

**Basis:** existing H8 `research/<Cycle>/replay_checkpoints/*/science_graph.sqlite3` snapshots, C186–197, evaluated offline with the old HEAD (`098e890`, v2) versus this code (v3). For each Cycle, both versions used the same snapshot and attempted `H-<Cycle>` as focus; metrics are UTF-8-independent Python string character lengths (`ensure_ascii=False`, compact JSON). Snapshots were opened read-only; no Canvas or live Worker change.

| Cycle | H | Agent graph v2 | v3 | Saved |
| --- | ---: | ---: | ---: | ---: |
| 186 | 148 | 19,887 | 16,812 | 3,075 |
| 187 | 149 | 20,386 | 17,247 | 3,139 |
| 188 | 150 | 20,215 | 17,321 | 2,894 |
| 189 | 151 | 23,092 | 19,817 | 3,275 |
| 190 | 152 | 20,892 | 17,950 | 2,942 |
| 191 | 153 | 20,372 | 17,150 | 3,222 |
| 192 | 154 | 20,493 | 17,242 | 3,251 |
| 193 | 155 | 20,690 | 17,413 | 3,277 |
| 194 | 156 | 20,742 | 17,413 | 3,329 |
| 195 | 157 | 20,947 | 17,664 | 3,283 |
| 196 | 158 | 20,914 | 17,618 | 3,296 |
| 197 | 159 | 21,603 | 18,522 | 3,081 |

C197 field breakdown (v2 → v3): `hypotheses` 10,334→9,765; `current_frontiers` 4,319→3,933; `lineage` 3,606→1,830; `semantics` 839→503; `external_sources` unchanged at 1,291. C197 snapshot's 268 canonical typed edges remain in SQLite; v3 places 53 in the focused prompt and reports 215 omitted, all accessible on demand.

**Fixed-context complete stage prompt lengths** (including the actual Stage Card/Procedure Card and JSON contract text, but **not** the historical runtime-specific goal/other invocation fields): C197 Research Lead/frontier_proposal 29,739→26,250; Challenger/candidate_review 9,000→9,000. These measure the same frozen fixture before/after, **not** live initial model payloads; the reported historical ~38.8k complete Lead prompt cannot be reconstructed exactly from a graph snapshot, and no production-provider tokens were measured.

## Review table

| Required property | Evidence / result |
| --- | --- |
| Full H ID index, discoverability | **PASS**: all 159 C197 IDs present, each returned by global ID search (159/159). |
| Active/parked/closed/unmarked | **PASS**: existing H index retains full frontier state strings; unmarked is still distinct from closed; synthetic regression covers status transitions. |
| Whole-H vs Cycle-local Outcome | **PASS**: unchanged H verdict and cycle pattern fields; formal Judge outcomes remain separate from preserved records. |
| Historic C5/93/123/170/183/184/189 | **PASS**: C5 open, C93 open, C123 supported, C170 open, C183 rejected, C184 preserved/unadjudicated, C189 open; all returned provenance/read-only paths. |
| C186–197 and failure results | **PASS**: cycle-detail accessible in C197 snapshot (C197 itself unadjudicated at investigator checkpoint, not mislabeled Judge). |
| Typed lineage across branches | **PASS**: incoming active and focus-adjacent edges are injected; omitted outgoing/non-focus edges remain in SQLite and `hypothesis-detail`, including explicit `evolution_operator` and originating Outcome. |
| External access/references | **PASS**: direct External ID search and `external-detail` return Oct13 negative-edge-balance and frozen B-compression records; canonical External edges unchanged. |
| Projection version / continuation | **PASS**: `compact-frontier-v3` participates in the existing Lead same-stage versioned graph stamp; fake continuation and checkpoint regression preserve old data without migration. |
| Parallel Worker / checkpoint compatibility | **PASS**: offline same-checkpoint repeat reads are deterministic and non-mutating; multi-worker, recovery and checkpoint regression suite passes. Does not prove live production Worker behavior. |
| Scope and scientific quality | **PASS for static scope**: only Agent graph projection and regression/audit files changed. **NOT RUN**: LLM behavior regression, live Cycle, token accounting, rollout. |
| Performance target | **PARTIAL**: 10/12 focused Cycle snapshots are in the 15k–18k target band; C189 (19,817) and C197 (18,522) retained more necessary focus details. |

The primary goal is **context reduction, not an assertion of scientific judgment improvement**. Future scientific-behavior evaluation requires separately approved tests, not a passing size benchmark.