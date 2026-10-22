---
name: result-to-claim
description: >-
  Judge 把已注册 theoretical、numerical 与 literature Evidence 映射到冻结 claim/criterion 时使用：检查支持范围、反证、替代解释、完整性和剩余缺口。
  不运行补充实验、不 pivot、不触发其它 skill/Agent，也不拥有 Cycle routing。
---

# Result to Claim

Adapted from the scientific core of the upstream ARIS `result-to-claim`. It evaluates what the supplied evidence can support; all auto-routing, external jury calls, wiki mutation, experiment execution, ablation, paper-writing, and self-rerun behavior is removed.

## Scope

Use only the registered Evidence, Evidence Manifest, Coverage Matrix, frozen claim/criteria, integrity information, and Judge packet supplied for the current Stage. Missing or unregistered artifacts are not evidence. Do not collect new results, execute analyses, or reinterpret a different hypothesis.

## Method

1. Freeze the exact claim before reading favorable results: population/domain, intervention or comparison, metric, direction, magnitude, uncertainty, assumptions, and intended generality.
2. Build a claim–evidence map. For each atomic part of the claim, identify evidence for, evidence against, neutral/inapplicable evidence, verification/provenance level, and scope.
3. Check evidence integrity before scientific interpretation: artifact existence, input/config match, baseline comparability, sample independence, uncertainty reporting, missing/failed runs, selection effects, and any registered audit warning. Integrity concern lowers what can be claimed; it is not silently averaged away.
4. Separate observation from interpretation and implication. A metric difference is not automatically a mechanism, causal claim, theorem, or universal result.
5. Test scope transfer explicitly: studied instances to population, finite cases to asymptotic claims, correlation to causation, fitted range to extrapolation, simulation convention to physical statement, and partial criterion to terminal Hypothesis.
6. Consider serious alternative explanations and disconfirming evidence. Explain whether current controls distinguish them; do not invent new experiments.
7. Assess the strongest evidential support warranted by the packet in ordinary scientific language: how strong the support is, what conflicts with it, what scope remains unsupported, and what uncertainty remains. Express the formal outcome only through the current Judge contract. Do not create, name, or emit a parallel verdict taxonomy.

## Handoff

Express the analysis through the current Judge contract. The rationale/objections should preserve:

- exact claim and supported scope;
- evidence for and against, with registered references;
- unsupported scope or claim components;
- integrity qualifications;
- viable alternative explanations;
- remaining scientific gap and the kind of discriminating evidence needed;
- calibrated confidence and why.

Do not “round up” partial evidence. Conversely, do not reject a bounded supported claim merely because a stronger unasked claim is unsupported. Keep milestone support distinct from terminal Hypothesis support.

The Judge contract is the only formal verdict vocabulary. Descriptive phrases such as “strong evidence”, “partial support in a bounded scope”, “conflicting evidence”, or “indeterminate from this packet” belong in rationale/analysis only and must never be emitted as alternative verdict field values.

## Capability boundary

This skill must not emit or act on `pivot`, `next idea`, `run supplementary experiments`, `trigger ablation`, `ready for paper`, or `rerun result-to-claim`. It cannot invoke another Judge/reviewer, spawn agents, edit a research wiki, mutate Scientific Model/Canvas/upstream state, or schedule follow-up work. It does not choose accept/reject/revise lifecycle actions outside the current Judge Stage contract. The TheoryMaster SDK owns executability, routing, retries, state commits, and Cycle transitions.
