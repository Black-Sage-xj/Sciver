# TM Scholar host-side CLI

From the packaged `theory_master/resources/skills/tm-scholar/` directory, use `python scripts/lit_retrieve.py --help` to inspect the CLI.

Run from the TheoryMaster root (no LLM calls):

```bash
python theory_master/resources/skills/tm-scholar/scripts/lit_retrieve.py --query "stochastic diffusion processes" --sources openalex,crossref,arxiv --limit 10 --output-dir /tmp/tm-scholar-search
python theory_master/resources/skills/tm-scholar/scripts/read_papers.py --papers-json /tmp/tm-scholar-search/papers.json --output-dir /tmp/tm-scholar-read --reading-limit 2
python theory_master/resources/skills/tm-scholar/scripts/trace_paper.py --paper "10.1103/physreve.102.052503" --output-dir /tmp/tm-scholar-trace --limit 10 --openalex
```

Search writes `papers.json`, CSV, BibTeX, summary and search log. An all-provider failure returns nonzero; partial failures are written to `errors.log`. Citation trace status is `metadata_only`, `partial_metadata`, or `unverified`, never a claim of verified intellectual dependence.

Reading writes `pdf_manifest.csv` and `reading_index.md`; `read` means nonempty text materialization, not scientific claim verification. Explicit OA URL fallbacks and bounded retries handle transient errors. Marker uses isolated conversion output; missing converter or empty PDF text remains unverified. Set `SCHOLARLY_MARKER_BIN` for a custom local converter.

The SDK `theory_master.scholarly_transport` owns network execution and paths; Literature consumes materialized artifacts. Offline tests: `python -m pytest -q tests/test_tm_scholar_native.py`.
