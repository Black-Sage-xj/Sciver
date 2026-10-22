# TM Scholar — Sciver-native Literature adapter

Original standard-library scholarly metadata and open-access text adapter. No source from the unlicensed third-party `scholarly-deep-research` project is bundled.

## Capabilities

- **Cross-source search:** OpenAlex, Crossref, Semantic Scholar, PubMed and arXiv; bounded API responses, metadata provenance, partial-failure logs and deterministic relevance ranking.
- **Identity:** canonical DOI and arXiv IDs; conservative title/year/author matching for records lacking IDs; conflicting DOI or author identities are not silently merged. Available ORCID metadata is retained.
- **Citation trace:** exact DOI, OpenAlex ID or unambiguous title; seed DOI/title/author/year checks; deduplicated bibliographic edges with explicit unresolved and partial statuses.
- **Open text:** HTTPS public sources only, transient download retries, alternate explicitly open-access candidates, private-address/redirect rejection, bounded file sizes and isolated Marker conversion.

## Evidence and limitations

`papers.json` is **metadata**, not proof of a claim. `paper_trace.json` is **provider-indexed citation metadata**, not verified intellectual dependence. `pdf_manifest.csv` status `read` means text was materialized, **not** that the Literature Agent has verified the scientific claim or that the source was a complete paper rather than a landing page. The Agent must inspect source content and locators.

API rate limits, provider outages, author ambiguity, incomplete OA metadata, and optional Marker models limit coverage. HTTPS URL checks are not a substitute for network-level egress controls.

See `references/standalone.md` for commands and `SKILL.md` for Literature authority boundaries. Run `python -m pytest -q tests/test_tm_scholar_native.py` for deterministic offline regressions. Set `SCHOLARLY_MARKER_BIN` to override the optional local Marker executable.
