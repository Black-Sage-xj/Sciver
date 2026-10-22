---
name: novelty-check
description: >-
  Literature 在 SDK 已授权 novelty_check task 时使用：把候选贡献拆成可检索主张，比较最近邻文献、重叠、剩余差异与检索不确定性。
  只产出 scholarly evidence assessment，不给继续/放弃 verdict，也不调度 reviewer 或下一阶段。
---

# Novelty Check

Adapted from the upstream ARIS `novelty-check` method as a TheoryMaster Literature capability. The useful unit is an auditable prior-art comparison, not a research-lifecycle decision.

## Scope

Use only for the current SDK-provided `literature_task` with `task_type: novelty_check`. Preserve its frozen search target and claim scope. Do not broaden the active Hypothesis or turn nearby literature into a different research question.

## Method

1. Decompose the proposed contribution into 3–5 independently checkable claims: object/problem, mechanism, claimed result, setting, and the delta from obvious baselines. Do not assume every component must itself be novel; a combination or resulting finding can be the relevant delta.
2. Use only the scholarly retrieval artifacts materialized by the TheoryMaster SDK/runner. Vary terminology, older/newer names, mechanism terms, target setting, and citation-neighbor cues when assessing search coverage. Do not perform network transport or spawn reviewers/agents from this skill.
3. For each plausible neighbor, separate:
   - bibliographic identity and verification level;
   - exact overlap with the candidate claim;
   - what remains different or unestablished;
   - whether the comparison is metadata-, abstract-, secondary-, or full-text-grounded;
   - locator and short source evidence when full text was actually verified.
4. Compare the whole contribution as well as individual claims. Distinguish direct precedent, close conceptual precedent, shared method in a different setting, and merely adjacent work.
5. Report negative-search evidence cautiously. “Not found” means only “not found within the recorded queries, sources, dates, and accessible material”; it never proves absence.

## Evidence assessment

Express the result through the current Literature stage contract. The assessment should make these ideas explicit without inventing new top-level fields:

- closest prior work and verification status;
- claim-by-claim overlap;
- remaining delta and whether it is method-, mechanism-, setting-, combination-, or finding-level;
- direct-precedent status (`found`, `not_found_in_coverage`, or `unresolved`);
- novelty risks or conflicting sources;
- search coverage, inaccessible material, uncertainty, and remaining gap.

Every named paper must be traceable to SDK retrieval metadata. Do not fabricate titles, authors, DOI/arXiv IDs, venues, dates, quotations, or locators. An abstract is not full-text verification.

## Capability boundary

This skill must not:

- assign a novelty score or emit `PROCEED`, `PROCEED WITH CAUTION`, `ABANDON`, accept, or reject;
- decide whether TheoryMaster continues, replaces, narrows, or closes the active Hypothesis/Cycle;
- invoke a second reviewer, spawn another Agent, schedule work, or start another search round;
- mutate Scientific Model, Canvas, upstream state, or files outside `cycle_workspace/literature/`.

Possible positioning language or discriminating follow-up evidence may be reported as scientific information only. Routing and lifecycle decisions remain with the TheoryMaster SDK and the authorized role contracts.
