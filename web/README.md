# Sciver Web Observatory

The public website now uses the **shadcn/ui Dashboard-inspired dark shell** with five visible pages (local-first; AL HTTPS/SSH reverse proxy remains an optional deployment adapter):

- `/` redirects to **Workflow** (`/workflow-stage-preview/`).
- **Run** (`/run/`) is the read-only task index and Web TUI entry: task search/status filters and stable `observer_id` details. When a task has a valid notebook Canvas, its Canvas / MD panel links to Research Explorer; unsupported tasks show a disabled placeholder.
- **Research Explorer** (`/research-explorer/?observer_id=<stable-id>`) displays the original Canvas with pan/zoom and scientific edges alongside Markdown, KaTeX, Obsidian-style links, backlinks and full-text search. It uses the documented `canvas-md` API; editing is disabled.

- **Workflow** (`/workflow-stage-preview/`) opens in a compact node-and-routing overview; use **字段详情** to inspect the complete source-backed Agent inputs, outputs, and SDK controls, with historical version switching, editable field annotations, pan, fit-to-width, and fullscreen preserved. Legacy TMCR diagnostics are accessible only in a collapsed section below the chart.
- **Architecture** (`/architecture/`) presents the current six-Agent scientific workflow in a responsive, interactive map. Agent objectives, permissions, exits, and contract rules are read from the live Stage Registry / Contract source; the public page no longer embeds the low-legibility Archify Master/Worker diagram.
- **Services** (`/services/`) shows both Codex accounts' live 5-hour/weekly quotas, reset times, reset-credit balances, original safe account-switch/reset actions, without unrelated Observer debug telemetry.

Overview, legacy `/runs/`, standalone Codex Usage, Change Requests, Current Flow and the standalone workflow guide are no longer public UI routes. **The former Codex Usage controls are embedded into Services, not removed.** The authenticated login, health, JSON, field-note, static artifact, and quota-account APIs remain available for their existing integrations. Reset-card confirmation remains two steps, or three when either quota remaining exceeds 30% or is unknown.

The sections below retain historical architecture and deployment information; legacy frontend paths mentioned there are not accessible in the current dashboard.

## Design and canvas-md interface

Run incremental plan: [`design/Run控制台设计.md`](design/Run控制台设计.md). The once-reserved `canvas-md` interface is implemented as a separate read-only module: [`design/canvas-md接口预留.md`](design/canvas-md接口预留.md). Only `GET /api/canvas-md/tasks/{observer_id}/index`, `/document?path=...` and `/search?q=...` are supported. No data writes, browser editing or second knowledge store are introduced.

## Boundary

Sciver Web Observatory reads persisted Sciver artifacts and process metadata without importing the `theory_master` runtime modules. Runtime/scientific controls remain absent. The only write surface is the engineering-only `/change-requests/create` endpoint, which creates a validated formal request manifest under `tool/theory_master/change_requests/`; it never starts/resumes Sciver or mutates scientific state.

## Run

From this directory:

```bash
python -m tm_observer snapshot --search-root ../.. --output /tmp/observer_state.json
python -m tm_observer serve --search-root ../.. --host 127.0.0.1 --port 8787
```

Then open `http://127.0.0.1:8787/`.

## Archify static views

Archify is pinned to v3.0.1 and is installed outside this repository:

```bash
python -m tm_observer archify-bootstrap
python -m tm_observer build-static --search-root ../..
python -m tm_observer build-delta --search-root ../..
```

The server then exposes:

- `/architecture/` — custom source-backed Agent Workflow Architecture (in-process renderer; no Archify dependency for the public page)
- `/workflow/` — legacy bookmark redirect to `/workflow-stage-preview/`
- `/current-flow/` — direct Mermaid rendering of the detailed current scientific flow, starting at Research Lead qualification and including cross-Cycle stable Hypothesis identity\n- `/change-requests/` — formal SDK-first TM system modification request list + creation form; each request gets a unique `TMCR-YYYYMMDD-NNN` and declares stable Current Flow node IDs
- `/runs/<observer-id>/` — persisted run facts + Archify Sequence
- `/changes/` — **已废弃**；兼容重定向到 `/change-requests/`，不再维护独立 Architecture Delta 汇报页

Archify-generated static artifacts remain available for compatibility and are refreshed by the static-view manager when their source projection changes; the public `/architecture/` route is rendered directly from the current workflow source and does not embed `static/architecture.html`. A run Sequence uses the canonical Cycle `events.jsonl` workflow ledger when present, with `runtime_events.jsonl` only as a legacy fallback, and rebuilds when the selected source or Sequence projection version changes. Sequence participants are created only from real Agent-start events; idle role slots never become execution nodes, while repeated invocations of the same role remain distinct calls. If Archify is absent or rendering fails, Overview and run-state collection continue working.

The Workflow page intentionally exposes exactly one **Scientific Workflow**. Stable Agent lanes are first-class: Research Lead, Challenger, Investigator, Literature, Judge, and Reporter all appear, with SDK deterministic gates in a separate lane. The flow covers qualification, Hypothesis Lock, specialist routing, Semantic Closure/Evidence/Readiness, terminal Judge, and post-Judge closeout. Exact per-run invocation timing remains a separate Sequence concern. The builder still parses Sciver's `STAGE_CARDS` source without importing the runtime and fails on stage-catalog drift.

The `/current-flow/` page stores the workflow as Mermaid source and renders that source directly in the browser with Mermaid.js. It begins at `Research Lead / frontier_proposal`, shows that omitting `evolution_operator` continues the unique H parent while a typed operator creates a derived H with an SDK-assigned node ID, and exposes the Mermaid source below the diagram for inspection/copying. Selecting `?request=TMCR-*` adds a sticky right-side request panel, marks declared `affected_flow_nodes` red, and provides click-to-locate controls for those nodes. The formal request projection does not alter the legacy Archify `/workflow/` artifact.

`/workflow-stage-preview/` is the field-level audit view. It intentionally shows only (1) fields actually sent to each Agent, (2) fields each Agent must return, and (3) a compact set of non-Agent SDK controls marked `◆` when they materially change routing, authority, stable identity, Evidence admissibility, budget, or atomic commit. Ordinary SDK bookkeeping is intentionally omitted. The companion maintenance document is `WORKFLOW_STAGE_PREVIEW_GUIDE.md` and is also available from `/workflow-stage-preview-guide/`.

TM 系统修改的人类网页汇报统一使用 `/change-requests/` 与 `/current-flow/?request=TMCR-*`，默认使用中文。旧 `/changes/` Architecture Delta 页面已废弃；底层 snapshot/delta 能力若仍被内部工具使用，也不再形成第二套修改汇报界面。

Run detail URLs use a stable `observer_id` derived from the canonical `research_root`, rather than legacy `master_id` alone, because historical/TMLab runs may reuse IDs such as `0`.

## Long-running service

Install the localhost-only systemd user service:

```bash
python -m tm_observer service-install --search-root ../.. --port 8787
systemctl --user status tm-observer.service
curl http://127.0.0.1:8787/healthz
```

The installer records the current Python and Node executables explicitly, so an NVM-only Node installation does not depend on systemd's reduced `PATH`. The unit restarts on failure, writes Observatory state outside the repository, and never binds a public interface.

Rollback:

```bash
python -m tm_observer service-uninstall
```

## Authenticated public endpoint

Current production path:

```text
Browser
  -> https://your-private-hostname.example/
  -> AL Caddy :443
  -> AL 127.0.0.1:8453
  -> dedicated reverse SSH tunnel
  -> local 127.0.0.1:8787
  -> Sciver Web Observatory Basic Auth
```

One-time local auth setup and service update:

```bash
python -m tm_observer auth-init
python -m tm_observer service-install --search-root ../.. \
  --basic-auth-file ~/.secrets.d/tm_observer_public.json
python -m tm_observer al-tunnel-install --key ~/.ssh/tm_observer_al_ed25519
```

Credentials are intentionally not printed by the deployment workflow. The username/password are stored only in `~/.secrets.d/tm_observer_public.json` (`0600`; parent `0700`). Optional `viewers` entries are additional logins for the same Observatory; they do not get a separate dataset. The dedicated SSH private key is `~/.ssh/tm_observer_al_ed25519` (`0600`).

Browser access uses an HTML login page at `/login` and then a seven-day `HttpOnly; Secure; SameSite=Strict` session cookie. HTTP Basic Auth is retained only as an API/fallback mechanism; this avoids relying on the native iOS/Safari Basic Auth dialog.

The AL side uses a dedicated `tmobserver` account. Its sshd policy permits only remote forwarding to `127.0.0.1:8453` and sets `GatewayPorts no`; Caddy terminates TLS and proxies the fixed hostname to that loopback port. Do not reuse the older WebCodex 8452 root tunnel for Observatory.

Public rollback:

```bash
python -m tm_observer al-tunnel-uninstall
```

This removes the local tunnel service only. The Origin remains localhost-only and authenticated. The AL Caddy site can then be removed independently if the public endpoint is being retired.

## Current scope

Phase 0-7 are implemented: read-only collectors, normalized state, Overview/Runs, validated Archify Architecture/Workflow/Lifecycle views, per-run Sequence views, Architecture Delta, localhost-only systemd service, and the authenticated AL public endpoint.
