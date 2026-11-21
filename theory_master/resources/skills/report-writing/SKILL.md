---
name: report-writing
description: Narrative craft for the Reporter report stage - structure, title, formulas, figures, and clear presentation of Evidence, counter-explanations, and limitations.
---

# TheoryMaster Report Writing

Writing craft for the Reporter `report` narrative. What may be written and
returned is defined by the Stage Card and contract.

## Graph reading guide

Return one `graph_summary` and the scientific body in `summary`. The SDK reuses
this exact guide at the top of the single Cycle report and in graph overviews.
Write the conclusion summary once, in this guide; begin the body with the question.
The guide gives the next exploration a clear account of this Cycle:

- `conclusion`: lead with the specific scientific result or change in knowledge.
  Preserve partial results of an open or rejected conjunction. Distinguish
  inherited knowledge from what this Cycle actually established or excluded.
- `basis`: retain the decisive comparison, uncertainty against the frozen
  threshold, or proof reason. A completed numerical check alone is not the
  conclusion when the frozen question remains unresolved.
- `scope`: retain the conditions and asset origin that materially limit use,
  including historical re-analysis when applicable. Detailed eligibility lists
  and reproduction material belong in the linked report.
- `open_question`: state the specific unresolved gap of this H and why the
  supplied evidence leaves it open. Use an empty string if none remains.
  Do not repeat the whole Task's goals or invent an obligation for a resolved H.

Use the existing overview's level of scientific detail; do not reduce it to a
slogan or fill a character budget. Keep essential quantities and uncertainty.
The guide is presentation of authorized records, not a new verdict, evidence,
frontier decision or next-Cycle plan. All four fields are prose strings; SDK
owns IDs, links, state labels and persistence. It is not a prefix of the report.

## Ordered report structure

Write a self-contained scientific body for a researcher. Return these three
sections, in this order, in `summary`. Use the report's language
for headings (the Chinese headings below are the default for Chinese reports).

1. **问题与必要条件 / Question and necessary conditions**: explain the question,
   what this Cycle tested or changed, comparison objects, indispensable sample
   conditions, parameters, definitions and formulas. Include enough explanation
   to understand the results without opening another report.
2. **主要结果 / Main results**: organize subsections by scientific question.
   State a finding, show its relevant existing figure, give its caption and
   explain how it supports the argument. Bring established values together in
   small comparison tables when this helps the reader compare conditions or
   outcomes. Keep important adverse or contrary results visible.
3. **解释与限制 / Interpretation and limitations**: explain what the results mean,
   the strongest relevant counter-explanation and what the Evidence resolves.
   State remaining questions and limitations that affect interpretation or use.

The SDK adds the shared overview, formal Judge record and reproduction index.
Do not duplicate these blocks or append a path inventory; end after section 3.

## Supplementary documents

For an existing relevant supplied Markdown document, use
`![[path.md#Section|Readable label]]` at the point where it helps the reader.
The section and label are optional. Explain the supplement's purpose in nearby
prose. SDK freezes the document hash and selected text, and renders it in a
folded source-labelled block. Put the argument needed to understand the main
result directly in the body; use supplements for long derivations, methods or
secondary detail. Use ordinary links for large documents and raw data. Reference
only existing supplied task notes or registered document assets, never invent
an attachment or write another document as part of the Reporter turn.

Adapt result subsection titles to the science. Distinguish observation,
interpretation and implication; preserve the formally decided strength and
scope. A theoretical report should explain its argument and necessary formulas;
do not fabricate or insert decorative figures or empty tables to fill a slot.

## Figures and comparison tables

- Consult `report_materials` for assets linked to admissible Evidence, their
  file types and availability. Evidence meaning, scope and limitations remain
  in `evidence_scope_digest`; an asset path alone establishes no new finding.
- Embed available figures relevant to the central argument at the point where
  they are discussed; a filename or Evidence ID alone is not a substitute for
  showing the figure. Use standard Markdown `![descriptive alt](image_ref)`
  with the exact supplied `image_ref`. The SDK handles the saved note location.
- Read only relevant listed files to understand an existing figure or transcribe
  established values, units and statistical conventions. Do not rerun research,
  derive new statistics from raw arrays, scan Agent trajectories or invent a
  scientific comparison for presentation. External assets are navigation leads;
  their accessibility is not verified by the material index.
- Place a 1–3 sentence caption directly below each figure: identify the objects
  and necessary axes, colors or panels, explain its principal supported finding,
  and give sampling or uncertainty conventions when they affect interpretation.
  Do not put commands, hashes or a per-figure reproduction inventory in captions.
- Use compact Markdown tables for established quantities or conditions that
  benefit from comparison. Label metrics and units and retain supplied sample
  counts and uncertainty definitions where material. Cite the corresponding
  Evidence in the surrounding paragraph or caption; explain what the table
  shows. Do not invent missing entries or imply a statistical test was performed.
- Keep original wide tables, CSV/NPZ contents, raw arrays, command transcripts
  and logs in their existing files. The SDK lists them in one final index.
- For a missing or ambiguous asset, preserve the supported scientific account
  and briefly explain any absence that affects reading. Do not guess a path or
  claim to have inspected that asset. Use another supplied, relevant available
  asset if one exists; otherwise explain the result in prose.

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
- Keep the report self-contained in its scientific argument. Use existing
  figures and compact result tables for that argument; the SDK appends the
  original data and reproduction pointers once at the end.

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
- Keep the tested prediction distinct from its broader interpretation. Preserve
  its metric, denominator, weighting and contrast when material. Refuting a local
  prediction alone does not establish relative contributions or identify a
  mechanism; report a broader conclusion only with its supplied sufficient basis.
- Preserve calibrated qualifiers such as `consistent with`, conditional model
  assumptions, the tested domain, and the difference between current results and
  re-analysis of historical data. Never silently strengthen or weaken an already
  decided scientific finding while polishing its prose.
- State necessary limitations once in the relevant section; retain a qualifier
  in the summary or result sentence when its omission would mislead. Remove
  repeated caveats and vague self-criticism rather than compressing the argument
  into an abstract. Prefer a precise supported statement followed by evidence.
- Match an author or venue style only if the relevant sample or preference is
  already supplied; otherwise use neutral, precise scientific language.
- Apply this discipline within the requested title, report and graph guide, not as a separate
  audit, rewrite pass, edit log, or additional output.
