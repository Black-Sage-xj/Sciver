---
name: report-writing
description: Write a scientific Cycle report and a graph reading guide from adjudicated findings and available materials.
---

# TheoryMaster Report Writing

## Graph reading guide

Write the single overview in `graph_summary`. In `conclusion`, lead with what changed this Cycle, including informative negative or partial results. In `basis`, give the decisive comparison, uncertainty or proof reason. In `scope`, name material conditions and asset origin. In `open_question`, state the concrete local gap or return an empty string. Distinguish new results from inherited findings and re-analysis. The SDK uses this guide both in the graph and at the top of the report.

## Scientific body

Give the report a content-bearing title. Write three sections in `summary`, with headings in the report’s language.

1. **问题与必要条件 / Question and necessary conditions**. State the question and the definitions, assumptions or comparison needed to understand it.
2. **主要结果 / Main results**. Explain the decisive calculation, argument or observation, including material contrary results. Define nonstandard quantities when first used. Preserve the actual metric, comparison, units and uncertainty where they affect interpretation.
3. **解释与限制 / Interpretation and limitations**. Give the supported interpretation, relevant alternatives and remaining limitations. Reserve mechanism, causal or priority claims for conclusions with a sufficient basis in the supplied record.

Keep the essential argument in the body. For supplementary derivations or methods in an existing supplied Markdown document, use `![[path.md#Section|Readable label]]` with a short explanation. The SDK freezes the cited revision and appends the formal ruling and reproduction index. Use `$...$` or `$$...$$` for mathematics in an Obsidian note.

## Figures and tables

Embed a relevant existing figure at its argument using `![descriptive alt](image_ref)` with the supplied image_ref. Caption its scientific meaning and explain axes, panels or uncertainty conventions as needed. Transcribe established quantities into a compact comparison table when helpful. Cite the supporting Evidence and preserve missing entries. If a material asset is unavailable, describe the supported result and its limits. A theoretical result can be presented entirely in prose and formulas.

## Scholarly prose discipline

Write for a researcher who has not seen the Cycle. Keep the methods and conditions that matter to the argument. Use precise technical referents and preserve the strength of decided findings. Do not manufacture missing comparisons, statistical tests, numbers or citations. Use significance language only for supported statistical claims. Match an author or venue style only when the user has supplied one.
