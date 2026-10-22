---
name: report-writing
description: Narrative craft for the Reporter report stage - structure, title, formulas, figures, and clear presentation of Evidence, counter-explanations, and limitations.
---

# TheoryMaster Report Writing

Writing craft for the Reporter `report` narrative. What may be written and
returned is defined by the Stage Card and contract.

## Narrative

Write for a researcher before a machine. Normally organize the account as:

1. A one-sentence result and its status.
2. The scientific question and what this Cycle changed or tested.
3. The decisive evidence, necessary quantitative details, and only the formulas
   needed to understand the result.
4. Interpretation and the strongest plausible counter-explanation, with what
   the Evidence does or does not do to rule it out.
5. Scope, assumptions, limitations, and what this Cycle leaves unestablished.

Adapt the headings to the science. Distinguish observation, interpretation, and
implication; do not turn a correlation into causation or a bounded result into
a general law. When cutting length, cut detail before qualifiers.

## Presentation rules

- Use a scientific, content-bearing title; never use placeholders such as
  `result`, `report`, `final`, `analysis`, `summary`, `正文`, or `结论`.
- When the rendered destination is an Obsidian Markdown note, use `$...$` for
  inline mathematics and `$$...$$` for display mathematics. Do not use
  `\\(...\\)` or `\\[...\\]` there.
- Define a non-standard metric, normalization, or null model on first use,
  including its input, output, baseline, and interpretive limit.
- Explain the comparison represented by each cited figure. State SD, SEM,
  confidence interval, quantiles, or another uncertainty convention whenever
  uncertainty marks materially affect interpretation. Refer to multi-panel
  figures with their English `(a)`, `(b)`, … labels.
- Summarize raw tables, trajectories, simulation logs, and command transcripts
  as the few quantities the argument needs; the SDK appends audit and
  reproduction pointers.

## Scholarly prose discipline

- Start with the specific result, comparison, or unresolved scientific fact, not
  generic importance, novelty, or progress claims. Remove promotional adjectives,
  stock openers, empty emphasis, repeated transitions, and synonym cycling that
  obscures stable technical referents.
- Prefer one substantial idea per sentence. Split stacked clauses and remove
  filler, but retain necessary definitions, derivation dependencies, scholarly
  passive voice, conventional `we`, and scientifically meaningful detail.
- Phrase empirical comparisons with the supplied metric, baseline, range,
  uncertainty, and source when material. Use `significant` only when the supplied
  record actually supports a statistical claim; reserve `prove` for an established
  proof, not finite numerical examples. Do not manufacture missing comparisons,
  tests, numbers, citations, or causal/novelty claims to make prose more concrete.
- Preserve calibrated qualifiers such as `consistent with`, conditional model
  assumptions, the tested domain, and the difference between current results and
  re-analysis of historical data. Never silently strengthen or weaken an already
  decided scientific finding while polishing its prose.
- Match an author or venue style only if the relevant sample or preference is
  already supplied; otherwise use neutral, precise scientific language.
- Apply this discipline within the requested title/summary, not as a separate
  audit, rewrite pass, edit log, or additional output.
