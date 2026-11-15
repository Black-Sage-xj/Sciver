# Research graph user guide

[Back to README](../README.md) · [简体中文](research-graph-guide.zh-CN.md)

The graph records how research moves from hypotheses to evidence and conclusions, and how follow-up questions emerge. Open a task's graph link, or visit `/research-explorer/` and select a task. The **图谱使用说明 (Graph guide)** button beside the graph heading opens help. Close it with the Close button, Esc, or a click outside the dialog. Help is also available in graph fullscreen.

## 1. Hypothesis, Cycle, and Outcome

- **Hypothesis (H)**: a testable scientific proposition with conditions, scope, and decision criteria.
- **Cycle**: one investigation of a frozen proposition. Each new scientific Cycle freezes a new H; recovery, retries, and Judge rework within that Cycle retain the same H.
- **Outcome**: the Judge's terminal scientific verdict for that Cycle, with rationale, findings, open questions, boundaries, evidence, and report. Each terminally adjudicated Cycle has exactly one Outcome.

Outcomes are embedded in H cards. Broad historical hypotheses may contain several Cycle Outcomes. Read each frozen proposition and scope: a local result applies to its own scope, while assessment of the whole H is recorded separately.

The graph's primary node types are **H and External**. Outcomes are part of H's reading content. Historical Canvas views can retain Decision, Finding, and StageSummary nodes; check their provenance and scope.

## 2. Outcome states

There are three scientific verdicts:

| State | Meaning and what to read |
|---|---|
| `supported` | Evidence supports the frozen proposition within this Cycle's conditions and test scope. Read the boundaries to establish what was supported. |
| `rejected` | Evidence contradicts the frozen proposition. Counterexamples and failure mechanisms can guide further research. |
| `open` | The Judge has completed adjudication and finds that the available evidence cannot determine whether the proposition holds. Read the rationale, open questions, and missing discriminating conditions. |

Other legend labels distinguish progress, preserved unfinished research, and historical compatibility:

| Display label | Value | Meaning and what to read |
|---|---|---|
| Pending / 待裁决 | `none` | No scientific verdict is available. Check the Cycle's execution status for progress or failure; this differs from an adjudicated Open. |
| Stopped, unadjudicated / 研究中止·未裁决 | `unadjudicated` | Preserved partial work from unfinished execution, without a terminal scientific verdict. Details can include the termination reason, findings, and outstanding rework. |
| Mixed / 多轮结论不一 | `mixed` | A historical H's Cycle Outcomes have different display classifications. Inspect each Cycle's frozen scope and original verdict before interpreting the whole hypothesis. |
| Legacy / 历史归一 | `legacy` | A normalized historical, nonstandard verdict. Read the original verdict in the details; compatibility mapping does not establish a formal Judge Open. |

Running, failed, interrupted, cancelled, or operationally closed Cycles without a terminal scientific verdict have no scientific Outcome and do not automatically receive `open`. The `unadjudicated` preservation label is separate from the three scientific verdicts.

## 3. Frontier states describe research opportunities

| State | UI label | Meaning |
|---|---|---|
| `active` | 活跃 | Available as a source for further research, subject to the current goal and scientific increment. |
| `parked` | 搁置 | Deferred; inspect the reason in the details. |
| `closed` | 已终止 | Outside the default research frontier. |
| `unknown` (display value) | 未标记 | Current frontier state is missing or awaiting verification. |

Frontier and Outcome are recorded independently. Frontier describes future research opportunities; Outcome describes what the evidence establishes in this Cycle. A supported H may stay active to inform new questions; a rejected branch may support research into its failure mechanism. Active does not mean a Worker is currently running, closed does not mean rejected, and unknown does not mean closed. An H can remain active after descendants appear.

Research Lead maintains the frontier, Judge adjudicates scientific claims, the SDK validates and persists state, and Reporter writes explanatory summaries and reports.

## 4. Cards, colors, and counts

1. **Zoom in to read cards**: above 60%, the upper half shows the hypothesis and frontier, and the lower half shows its Outcome. Click either half for its details.
2. **Zoom out to inspect branches**: at ≤60%, numbered overviews use frontier colors by default. The **远景颜色** button switches to Outcome colors. These controls affect presentation.
3. **Read the top legends**: frontier keys correspond to the upper card half; Outcome keys correspond to the lower half. Less common labels appear under **更多 (More)**.
4. **Interpret counts by H**: in the SQLite graph, frontier and Outcome counts each group by H. Several Outcomes under a historical H contribute one summary classification. External sources are counted separately. Worker and adjudicated Cycle counts belong to their respective views.

## 5. Trace scientific evolution and sources

**H→H scientific edges** record typed evolution with provenance. Read edge and node details to understand how a successor inherits, revises, or changes an earlier question. Adjacent Cycle numbers and nearby positions alone do not establish scientific lineage.

**External (E)** nodes hold literature, supplied materials, historical findings, decisions, and summaries. H→E and E→E reference edges trace sources. External material has no Judge authority by itself; inspect the corresponding records to establish how it was admitted as evidence.

## 6. A reading sequence

1. Click a frontier state at the top to open the branch list and locate an H.
2. Click its upper half to read the proposition, conditions, and test criteria.
3. Click its lower half to inspect each Cycle's frozen test, Judge rationale, findings, questions, and boundaries; then open the full Markdown report.
4. Follow scientific edges to subsequent hypotheses and reference edges to external sources. The index on a node's left side helps navigate predecessors.
5. Search nodes and Markdown, drag the divider to resize the graph and reader, hover a branch edge to collapse or expand it, and use Back/Forward to revisit previous positions.

For example, a Cycle tests a mechanism under specified conditions. Supported means the evidence supports it within that scope; rejected means counterevidence was found; Open means the Judge still cannot decide. If execution stops before adjudication, preserved work may appear as unadjudicated. Its frontier state is determined separately.

A Cycle Outcome answers what this investigation concluded. **Goal Completion** separately assesses the overall task goal. Read task progress alongside its goal, Scientific Model, and Cycle conclusions.

Developer references: [ResearchGraph design](../design/ResearchGraph设计文档.md) and [Canvas design](../design/Canvas设计文档.md).
