---
name: tm-scholar
description: TheoryMaster-native Literature evidence interface for source discovery, citation tracing, open-access reading and provenance-aware verification.
---

# TM Scholar — Literature evidence

This is a **TheoryMaster-owned** skill, not a third-party research skill. The SDK host runner performs retrieval, downloads, PDF conversion and citation tracing before the Literature Agent responds. The Agent only reads the materialized `scholarly_retrieval_root` and current `search_target` to evaluate sources.

## Scientific boundaries

- `landscape`: discover relevant papers and methods; label metadata/abstract evidence separately from verified text
- `targeted_source`: verify the frozen original source, exact locator, and claim; a metadata match alone is not confirmation
- `novelty_check`: identify closest related work and search limitations, not issue a novelty verdict
- Follow references/citations as **bibliographic links**, not evidence of a paper's intellectual dependence or endorsement
- Missing APIs, 429s, unavailable full texts and unavailable PDF converters must remain explicitly `unverified` or `blocked`
- Never declare an open-access PDF verified until actual text was materialized and examined

- A `read` manifest entry means text materialization only; publisher landing pages do not prove complete article access. Verify actual claim and locator.
- Ambiguous identity, unresolved citation edges, and partial provider outages must remain explicit; never silently select an uncertain source.
- **Minimum-information reading**: first inspect `retrieval/evidence_index.md` and `retrieval/papers_top.json` (up to six candidates), then the small `reading/reading_index.md` and `traces/*/paper_trace_report.md`. For a claim, programmatically extract only matching DOI/title records and a short relevant abstract from `retrieval/papers.json`; do not concatenate the full corpus or full graph JSON into the model context. Expanding beyond the index is allowed when scientifically necessary, but keep each retrieval bounded.
- Provider HTTP 429, incomplete citation pagination, and missing legal OA URLs are coverage limitations, not evidence for the scientific claim or permission to bypass paywalls.

Literature does not own Research Lead or Judge authority, Canvas mutations, workflow routing, scientific closure, local project search, new model calls, or additional Literature tasks. Outputs stay under `cycle_workspace/literature/`. Use `references/standalone.md` only for host-CLI examples, not as Agent instructions.
