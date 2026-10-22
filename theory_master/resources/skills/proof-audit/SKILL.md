---
name: proof-audit
description: >-
  Judge 审查注册 theoretical Evidence 中 proof 的 obligations、假设、量词、极限交换、边界情形和 scope 时使用。
  只形成只读审计证据；不修证明、不改命题、不调用外部 reviewer、不自行循环或裁决 workflow。
---

# Proof Audit

This is the read-only audit capability extracted from the upstream ARIS `proof-checker`. Its fixing, cross-model review, persistence, retry loop, and workflow verdict machinery are intentionally excluded.

## Scope

Audit only proof material already present in the current Judge packet and registered Evidence. The frozen Hypothesis, theorem statement, definitions, criterion, evidence scope, and Stage contract remain authoritative. Do not search for a friendlier theorem or complete missing research on the author's behalf.

## Audit method

1. Canonicalize each theorem, lemma, proposition, and corollary without changing it: explicit quantifiers, domains, parameter regimes, limit order, conclusion, and declared assumptions.
2. Build a proof-obligation ledger and dependency DAG. For each nontrivial implication, record the claimed justification, required side conditions, and where the supplied proof claims to discharge them. A location pointer is not proof that the step is valid.
3. Track a typed symbol/definition table and an assumption ledger. Flag undefined symbols, inconsistent normalization, hidden assumptions, unused stated assumptions, circular dependencies, and silent strengthening.
4. Check high-risk steps explicitly:
   - implication direction, cases, boundary and degenerate regimes;
   - limit/expectation/derivative/integral interchange and its actual theorem conditions;
   - pointwise versus uniform convergence and modes of stochastic convergence;
   - dimensions, constants, asymptotic parameter dependence, and uniformity scope;
   - theorem applications whose hypotheses are not discharged;
   - statement–conclusion match and downstream use of lemmas.
5. Attempt lightweight adversarial checks from the supplied material: smallest cases, zero/singular limits, symmetry-breaking cases, dimensional analysis, sign checks, and candidate counterexamples. Do not run new calculations or create new Evidence.

## Finding taxonomy

Classify a gap by both status and impact where the packet supports it:

- status: `invalid`, `unjustified`, `under-assumed`, `overstated`, `unclear`, or `verified`;
- impact: `local`, `dependency`, or `global`;
- severity: `fatal`, `critical`, `major`, `minor`, or `note`.

Severity must follow scientific impact, not writing style. A missing explanation is not automatically a mathematical failure; a plausible step is not verified merely because it is standard-looking.

## Handoff

Use the current Judge JSON contract; do not introduce another verdict schema. The rationale/objections should identify, as applicable:

- proof obligation and exact supplied location;
- verified steps and their scope;
- unsupported step or counterexample candidate;
- hidden/missing assumption;
- dependency impact and severity;
- the minimal kind of evidence that would resolve the gap.

State uncertainty when the packet lacks enough material to audit a step. Do not silently treat absent proof text as a false theorem.

## Capability boundary

This skill must not edit or rewrite the proof, add derivations, strengthen assumptions, weaken the claim, repair LaTeX, compile artifacts, call another model/reviewer, spawn agents, or repeat review rounds. It must not decide executor, budget, routing, retry, the next Stage, or Cycle lifecycle. The Judge's formal adjudication remains governed only by the current Stage Card and contract; this skill supplies one audit lens inside that adjudication.
