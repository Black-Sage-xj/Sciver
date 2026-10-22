<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/Sciver_Lockup_Horizontal_Blue_White.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/brand/Sciver_Lockup_Horizontal_Primary.svg">
    <img src="docs/assets/brand/Sciver_Lockup_Horizontal_Primary.svg" alt="Sciver official logo" width="420">
  </picture>
</p>

<h1 align="center">Sciver</h1>
<p align="center"><strong>An Open-Source Autonomous Scientific Discovery System</strong></p>
<p align="center"><em>From Hypotheses to Discoveries.</em></p>
<p align="center"><a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a></p>

Sciver is a persistent multi-agent scientific-research workflow engine with a companion **Web Observatory** for inspecting runs and reviewing research progress. The Observatory primarily reads persisted artifacts and process metadata; it can create validated engineering change requests, but it does not directly start or resume research runs or mutate scientific state. Sciver is designed to help research teams move from hypotheses through structured investigation, evidence review, and recorded outcomes.

This repository contains the maintained source for the engine, agent workflows, and Web UI. It is separate from experiment data, researcher accounts, local deployment settings, and personal research workspaces.

## What is included

- **Research workflow engine** — orchestrates multi-stage, multi-agent scientific investigation.
- **Agent roles and skills** — reusable identities, instructions, and research procedures.
- **Web Observatory** — inspect persisted runs and review progress; it can submit validated engineering change requests but does not directly start/resume research runs or change scientific state.
- **Tests and design references** — regression tests and technical design documentation.

## Repository layout

- `theory_master/` — core SDK, workflow orchestration, agent contracts, and runtime.
- `web/tm_observer/` — Web Observatory; install the Web package from `web/`.
- `tests/`, `web/tests/` — regression and integration tests.
- `design/`, `web/design/` — technical design notes.
- `skills/`, `identities/`, `scripts/` — project-supported skills, agent identities, and development tools.
- `config/.env.example` — credential-free configuration template.
- `docs/assets/brand/` — Sciver logo and brand-preview SVGs used by this README.

Some package and CLI names retain historical `TheoryMaster`/`tm` naming. Those paths are implementation details and do not change the product name, Sciver.

## Quick start

Use Python 3.10 or newer. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[sdk]'
python -m pip install -e ./web
```

Configure a supported SDK provider and the external tools required for the features you plan to use. Numerical-compute Python and scholarly-retrieval dependencies may need to be configured separately.

The repository includes the `research-lit` skill, but it does not bundle the external scholarly-retrieval adapter. To enable external retrieval, install an adapter you are licensed to use. The SDK looks for `lit_retrieve.py` in `~/.local/share/theory-master/scholarly-scripts` by default; override that directory with `THEORYMASTER_SCHOLARLY_SCRIPTS` and, if needed, set `THEORYMASTER_SCHOLARLY_PYTHON` to the Python interpreter that has the adapter's dependencies. Literature can still inspect research artifacts supplied directly to it without an external retrieval adapter.

## Run tests

List the available test categories with:

```bash
python scripts/run_tests.py --list
```

Run the registered test suite with `python scripts/run_tests.py`, or run the invariant gate with `python scripts/run_tests.py --category invariant`. Some integration paths may require locally configured services or optional dependencies.

## Run the Web Observatory

```bash
tm-observer serve --search-root /path/to/your/research/workspace --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787/` locally. For remote access, configure your own authenticated reverse proxy or SSH tunnel. **Do not expose the server directly without authentication.**

The `tm` CLI remains available for programmatic workflows and diagnostics; it is not required for using the Web interface.

The legacy interactive TUI is retained for compatibility but is not the preferred interface. Its retirement, if needed, will be handled separately.

## Sciver brand assets

The repository includes lightweight SVG assets so the README and project materials can use the same identity without external image dependencies.

| Asset | Path | Recommended use |
|---|---|---|
| Primary horizontal logo | `docs/assets/brand/Sciver_Lockup_Horizontal_Primary.svg` | Light backgrounds, websites, and documents |
| Blue symbol + white wordmark | `docs/assets/brand/Sciver_Lockup_Horizontal_Blue_White.svg` | Dark backgrounds; used by the README in dark mode |
| All-white horizontal logo | `docs/assets/brand/Sciver_Lockup_Horizontal_White.svg` | Monochrome white treatment on dark backgrounds |
| Standalone symbol | `docs/assets/brand/Sciver_Symbol_primary.svg` | App navigation, avatars, and compact brand placements |
| Brand overview | `docs/assets/brand/Sciver_Brand_Overview.svg` | Brand and color reference |
| Icon-size preview | `docs/assets/brand/Sciver_Icon_Size_Test.svg` | Check symbol legibility at different sizes |

The A2 symbol uses seven editable geometric facets and a deliberate negative-space gap. Prefer SVG for scalable use; do not stretch, rotate, redraw individual facets, or close the gap. For dark backgrounds, prefer the blue-symbol/white-wordmark lockup; use the all-white lockup only when a monochrome treatment is needed.

Core color references:

- Primary blue: `#1762F5`
- Highlight blue: `#408EF7`
- Deep facet blue: `#1A45BB`
- Wordmark / dark background: `#08162C` / `#07162F`

## Research data and security

This repository does **not** include personal Canvas graphs, experiment runs, model credentials, local provider accounts, operator logs, historical change registries, or private benchmark data. Those belong in separate user-owned locations.

Review `config/.env.example` and set secrets locally. The repository's `.gitignore` is not a substitute for security review. Never commit credentials, private scientific data, or third-party material without redistribution permission.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and pull requests and [SECURITY.md](SECURITY.md) for reporting security issues. Avoid editing production research tasks during development.

The software in this repository is licensed under the MIT License; see [LICENSE](LICENSE). Sciver brand artwork, trademarks, and third-party materials may be subject to separate rights or notices. Do not assume that the MIT software license grants trademark rights; review applicable terms before reusing or redistributing those materials.

