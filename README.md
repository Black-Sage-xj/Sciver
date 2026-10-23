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

Sciver is a persistent multi-agent scientific-research workflow engine with a companion **Web Observatory** for inspecting runs and reviewing research progress. The Observatory displays persisted artifacts and process metadata and provides explicit operator controls through the SDK, including starting, resuming, pausing, and terminating research runs. Sciver is designed to help research teams move from hypotheses through structured investigation, evidence review, and recorded outcomes.

This repository contains the maintained source for the engine, agent workflows, and Web UI. It is separate from experiment data, researcher accounts, local deployment settings, and personal research workspaces.

## What is included

- **Research workflow engine** — orchestrates multi-stage, multi-agent scientific investigation.
- **Agent roles and skills** — reusable identities, instructions, and research procedures.
- **Web Observatory** — inspect persisted runs, review progress, and explicitly control research runs through the SDK.
- **Tests and design references** — regression tests and technical design documentation.

## Repository layout

- `theory_master/` — core SDK, workflow orchestration, agent contracts, and runtime.
- `web/tm_observer/` — Web Observatory; install the Web package from `web/`.
- `tests/`, `web/tests/` — regression and integration tests.
- `design/`, `web/design/` — technical design notes.
- `theory_master/resources/skills/`, `identities/`, `scripts/` — project-supported skills, agent identities, and development tools.
- `config/.env.example` — credential-free configuration template.

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

Install test dependencies and list the available unittest categories:

```bash
python -m pip install -e '.[test]' -e ./web
python scripts/run_tests.py --list
```

Run the full core pytest gate used by CI with `python scripts/test_profiles.py default`. Run the categorized unittest suite with `python scripts/run_tests.py`, or run the invariant gate with `python scripts/run_tests.py --category invariant`. Some integration paths may require locally configured services or optional dependencies.

## Run the Web Observatory

```bash
tm-observer serve --search-root /path/to/your/research/workspace --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787/` locally. For remote access, configure your own authenticated reverse proxy or SSH tunnel. **Do not expose the server directly without authentication.**

The Web interface includes state-changing operations. Viewing task status, logs, and Canvas/Markdown documents is read-only; `run`, `resume`, `pause`, and `kill` change execution state through the SDK. Goal drafts, research plans, worker settings, and service/account controls can also write local state. Starting or resuming work can send model requests and consume provider quota. The UI is not a read-only access role. See [Web operation boundaries](web/README.md#operation-boundaries) for request protections and deployment details.

The `tm` CLI remains available for programmatic workflows and diagnostics; it is not required for using the Web interface.

The legacy interactive TUI is retained for compatibility but is not the preferred interface. Its retirement, if needed, will be handled separately.

## Research data and security

This repository does **not** include personal Canvas graphs, experiment runs, model credentials, local provider accounts, operator logs, historical change registries, or private benchmark data. Those belong in separate user-owned locations.

Review `config/.env.example` and set secrets locally. The repository's `.gitignore` is not a substitute for security review. Never commit credentials, private scientific data, or third-party material without redistribution permission.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and pull requests and [SECURITY.md](SECURITY.md) for reporting security issues. Avoid editing production research tasks during development.

The software in this repository is licensed under the MIT License; see [LICENSE](LICENSE). Sciver brand artwork, trademarks, and third-party materials may be subject to separate rights or notices. Do not assume that the MIT software license grants trademark rights; review applicable terms before reusing or redistributing those materials.

