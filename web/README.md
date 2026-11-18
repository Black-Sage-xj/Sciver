# Sciver Web Observatory

The public website now uses the **shadcn/ui Dashboard-inspired dark shell** with local-first research and operator pages:

- `/` opens Workspace → Research Chat (`/research-chat/`); Task List (`/run/`) opens prepared and existing tasks.
- **Run** (`/run/`) is the task index and operator console entry: task search/status filters and stable `observer_id` details. When a task has a valid notebook Canvas, its Canvas / MD panel links to Research Explorer; unsupported tasks show a disabled placeholder.
- **Research Explorer** (`/research-explorer/?observer_id=<stable-id>`) displays the original Canvas with pan/zoom and scientific edges alongside Markdown, KaTeX, Obsidian-style links, backlinks and full-text search. It uses the documented `canvas-md` API; editing is disabled.

The visual reference is the MIT-licensed [shadcn-ui/ui](https://github.com/shadcn-ui/ui) project. The shell is implemented with Python HTML/CSS; it does not import the React Dashboard template. **Settings → 外观 → 颜色主题** offers **白色 · 简洁**, **黑色 · 经典**, and **蓝色 · 深海**. White follows the upstream [neutral light palette](https://ui.shadcn.com/docs/theming). The choice applies immediately, persists in this browser, and synchronizes across tabs and the embedded Research Chat.

  The right reader separates **猜想** (claim, conditions, full dossier) from **研究结论** (per-Cycle verdict, conclusion, basis, scope and unresolved questions). Each round has one **阅读研究报告** entry: the unified report reuses that overview and contains the full narrative, folded original adjudication, versioned supplementary documents and reproduction index. Its directory supports section reads and pagination; old Outcome/narrative links resolve to the same report view without rewriting historical files. Only the latest recorded Cycle starts expanded; branch arrangements, references and technical metadata remain in disclosures. Whole-H migration placeholders do not acquire Judge authority.
  Markdown uses scoped GitHub-style typography with explicit white/black/blue palettes, colored headings, quotes, tables, inline code and KaTeX. Prism 1.30.0 highlights Python, Bash, JSON and its default web languages; if unavailable, code remains readable as plain text. Matching repeated document titles are removed only from the view, preserving source comments and heading navigation.

- **Workflow** (`/workflow-stage-preview/`) opens in a compact node-and-routing overview; use **字段详情** to inspect the complete source-backed Agent inputs, outputs, and SDK controls, with historical version switching, editable field annotations, pan, fit-to-width, and fullscreen preserved.
- **Architecture** (`/architecture/`) presents the current six-Agent scientific workflow in a responsive, interactive map. Agent objectives, permissions, exits, and contract rules are read from the live Stage Registry / Contract source; the public page no longer embeds the low-legibility Archify Master/Worker diagram.
- **Services** (`/services/`) shows both Codex accounts' live 5-hour/weekly quotas, reset times, reset-credit balances, original safe account-switch/reset actions, without unrelated Observer debug telemetry.

Overview, legacy `/runs/`, standalone Codex Usage, Change Requests, Current Flow and the standalone workflow guide are no longer public UI routes. **The former Codex Usage controls are embedded into Services, not removed.** The authenticated login, health, JSON, field-note, static artifact, and quota-account APIs remain available for their existing integrations. Reset-card confirmation remains two steps, or three when either quota remaining exceeds 30% or is unknown.

The retired routes above are not part of the current dashboard.

## Design and canvas-md interface

Run incremental plan: [`design/Run控制台设计.md`](design/Run控制台设计.md). The once-reserved `canvas-md` interface is implemented as a separate read-only module: [`design/canvas-md接口预留.md`](design/canvas-md接口预留.md). Only `GET /api/canvas-md/tasks/{observer_id}/index`, `/document?path=...` and `/search?q=...` are supported. No data writes, browser editing or second knowledge store are introduced.

## Operation boundaries

Research Chat is an independent workspace agent with SDK skills for preparation, discussion, progress, evidence, next steps, and authorized intervention. Acceptance of a concrete proposal or an explicit command authorizes its stated changes. Plan edits, next-round conjectures, frontier adjustments and runtime controls use the SDK; formal qualification, frozen Hypotheses, Judge verdicts and Goal records retain scientific authority. Read the [collaboration guide](../docs/research-chat-collaboration.zh-CN.md).

Before a Task exists, `/research-chat/?new=1` supports exploring an interest, importing selected research files, discussing literature, and reviewing a draft Plan. Explicit confirmation creates a new Task with an empty graph and Scientific Model; it does not start execution. Creation is idempotent and preserves the source directory. When a draft is ready for review, its project-creation action appears in the conversation rather than a permanent banner. See the [preparation design](../design/项目筹备与前置文献调研.md).

Literature jobs use the shared Literature identity, skills, Stage and contracts in an independent model session. A planner creates complementary queries; the SDK retrieves sources, materializes selected originals, and preserves source provenance for the Agent's comparison and synthesis. The default bounds are two rounds and six source-acquisition attempts. Existing work is reused on resume; cancellation and access failures remain explicit. The progress card opens stage details, selected papers and a report, keeping raw hits separate from reviewed sources. See the [Literature workflow](../docs/literature-workflow.zh-CN.md) and [live acceptance results](../docs/literature-acceptance-20261118.zh-CN.md).

The Observatory combines artifact viewing with operator controls. It delegates runtime actions to the SDK in a subprocess; avoiding an in-process runtime import does not make it read-only.

| Operation | Effect |
|---|---|
| View task status, logs, Canvas/Markdown, architecture, and workflow contracts | Reads persisted artifacts and process metadata; Canvas/Markdown editing is disabled. |
| `run`, `resume`, `pause`, `kill`, worker and scheduler controls | Changes execution state through SDK operator commands. Starting or resuming work can invoke models and consume provider quota. |
| Save Goal drafts, research plans, field annotations, or model/worker settings | Writes the corresponding local files or configuration. |
| Service/account switching and quota controls | Changes local account/service state and may restart managed services. |

The run-action POST endpoint requires a matching `Origin`/`Host`, `X-TM-Operator-Intent: 1`, JSON input, and a server-discovered task ID. SDK checks reject stale cycle/status/launch expectations and invalid state transitions. These request checks do not grant a read-only role or replace authentication. Scientific verdicts remain governed by the SDK's Agent contracts.

Authentication is enforced only when an auth configuration is supplied. Without it, `_authorized()` accepts requests. Keep the default loopback binding for local use; for remote use, configure authentication and an authenticated HTTPS reverse proxy or SSH tunnel. Each configured Web account has its own workspace and can operate its own tasks.

The retired TMCR engineering-request UI is not the current write boundary.

## Optional PDF conversion

**Settings (`/settings`) → PDF conversion** configures the host component. Fresh installations default to disabled. Local **Marker** uses an explicitly configured executable or `marker_single` from the service `PATH`. Online **MinerU** uses a privately saved API token (0600) or service `MINERU_API_KEY`; enabling and selecting it permits PDF uploads when conversion is requested. Settings APIs never return the key. Configuration defaults to `$XDG_CONFIG_HOME/sciver/document-conversion.json` (otherwise `~/.config/sciver/document-conversion.json`), overridable by `SCIVER_DOCUMENT_CONVERSION_CONFIG`.

Conversion retains Markdown, images, layout metadata, source SHA and page scope. The default limit is 20 pages; 0 means all available pages. A partial page range is not full-paper verification. Metadata, abstracts, materialized text and source-grounded claims stay distinct. Disabled or unavailable PDF conversion still permits discovery and accessible HTML reading. Acquisition deadlines cover the configured conversion timeout; timeout and cancellation clean up the reader's process group. The [acceptance record](../docs/literature-acceptance-20261118.zh-CN.md) separates deterministic checks, real model behavior and remaining limits.

## Install and run

For first-time setup, use `sciver quickstart` from the main [quick start](../README.md#quick-start-install-the-release-recommended). To reopen the local workspace, use `sciver web`. Both open the browser after the server responds; pass `--no-open` on headless machines.

The landing page is Workspace → Research Chat. Its first-visit guide can create a project from scratch or copy selected files from an existing research directory. It asks for the goal, research boundaries and evidence standards, completion criteria, and constraints before showing an editable Research Plan preview. Only the final confirmation creates a task; the Task List then shows it without starting a run. Local Web login is disabled by default and can be enabled in Settings; `sciver web --reset-login` recovers access after stopping the server.

From the repository root, after configuring the SDK dependencies and provider:

```bash
python -m pip install -e ./web
tm-observer serve --search-root /path/to/your/research/workspace --host 127.0.0.1 --port 8787 --open
```

Open `http://127.0.0.1:8787/`. The search root identifies user-owned research workspaces; it does not need to be this source checkout.

For an artifact snapshot without starting the server:

```bash
python -m tm_observer snapshot --search-root /path/to/your/research/workspace --output /tmp/observer_state.json
```

## Authentication and service deployment

Initialize a local credential file, then pass it to the server or service installer:

```bash
python -m tm_observer auth-init
python -m tm_observer serve --search-root /path/to/your/research/workspace \
  --host 127.0.0.1 --port 8787 \
  --basic-auth-file ~/.secrets.d/tm_observer_public.json
```

Browser login uses a seven-day `HttpOnly; Secure; SameSite=Strict` cookie, so remote browser access should use HTTPS. HTTP Basic Auth remains available for API clients. Store credentials outside the checkout; the generated credential file is private to its owner.

When the credential file contains additional accounts in the legacy `viewers`
array, `serve` starts an authentication gateway and one backend process per
account. The top-level account retains the configured search root and state
directory. Each additional account receives a new empty workspace, chat store,
project preparation area, SDK data root and application home under
`<state-dir>/accounts/<account-name>-<stable-hash>/`. Adding an account and
restarting the service creates its workspace automatically; subsequent restarts
reuse it. Choose a state directory outside the primary account's search root so
its scanner cannot discover another account's tasks.

Only the currently selected model-provider authentication is seeded once into a
new account's private home. Research, model conversations, plans and scientific
records are never copied. Directory import and notebook moves stay within the
account's workspace; task IDs, chat IDs and artifacts from another account are
unavailable. Browser chat selections and drafts are also scoped by workspace.
Provider service switching and host quota administration remain available to
the primary account. Workspace settings and PDF conversion use each account's
own configuration. Existing login cookies continue to work.

These workspaces separate application data and normal workflows. Deploy
separate OS users or containers when users also need a filesystem security
boundary for model-executed code.

An optional systemd user service keeps the server on loopback:

```bash
python -m tm_observer service-install --search-root /path/to/your/research/workspace \
  --port 8787 --basic-auth-file ~/.secrets.d/tm_observer_public.json
systemctl --user status tm-observer.service
```

The installer records the Python and Node executables and restarts on failure. Uninstall with `python -m tm_observer service-uninstall`. Set up your own authenticated HTTPS reverse proxy or SSH tunnel for remote access.


## Delivery checks and first use

Both packages require Python 3.12 or newer. Ordinary wheels include the prebuilt
frontend, fonts, vendor license, workflow history and guide; Node.js is required
only when rebuilding frontend source. See [standalone installation](../docs/distribution.md)
and [first use](../docs/first-use.md) for offline setup and account configuration.

The first-visit guide covers setup, model usage and task navigation. Research
Explorer includes an outcome/edge guide. Chat drafts persist per conversation;
settings Save/Revert and Worker close actions have explicit local browser
regressions. Run `python -m pytest -q web/tests` from the repository root. Enable
`TM_BROWSER_E2E=1` after installing Playwright and its browser engines to execute
the real browser tests; those tests use mocked model responses.

Research Chat resolves task-local Markdown citations, including absolute and `file:` references, through `/research-chat/open`. The server validates the registered task home and existing document allowlist, then redirects report aliases to the unified Cycle report or other documents to the graph reader. External links remain ordinary links, citations open a new tab, and missing/out-of-task documents produce a readable error without exposing arbitrary files. Historical stored messages are unchanged.
