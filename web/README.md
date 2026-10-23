# Sciver Web Observatory

The public website now uses the **shadcn/ui Dashboard-inspired dark shell** with five visible pages (local-first):

- `/` opens the remembered task console when available, otherwise the task list (`/run/`).
- **Run** (`/run/`) is the task index and operator console entry: task search/status filters and stable `observer_id` details. When a task has a valid notebook Canvas, its Canvas / MD panel links to Research Explorer; unsupported tasks show a disabled placeholder.
- **Research Explorer** (`/research-explorer/?observer_id=<stable-id>`) displays the original Canvas with pan/zoom and scientific edges alongside Markdown, KaTeX, Obsidian-style links, backlinks and full-text search. It uses the documented `canvas-md` API; editing is disabled.

- **Workflow** (`/workflow-stage-preview/`) opens in a compact node-and-routing overview; use **字段详情** to inspect the complete source-backed Agent inputs, outputs, and SDK controls, with historical version switching, editable field annotations, pan, fit-to-width, and fullscreen preserved.
- **Architecture** (`/architecture/`) presents the current six-Agent scientific workflow in a responsive, interactive map. Agent objectives, permissions, exits, and contract rules are read from the live Stage Registry / Contract source; the public page no longer embeds the low-legibility Archify Master/Worker diagram.
- **Services** (`/services/`) shows both Codex accounts' live 5-hour/weekly quotas, reset times, reset-credit balances, original safe account-switch/reset actions, without unrelated Observer debug telemetry.

Overview, legacy `/runs/`, standalone Codex Usage, Change Requests, Current Flow and the standalone workflow guide are no longer public UI routes. **The former Codex Usage controls are embedded into Services, not removed.** The authenticated login, health, JSON, field-note, static artifact, and quota-account APIs remain available for their existing integrations. Reset-card confirmation remains two steps, or three when either quota remaining exceeds 30% or is unknown.

The retired routes above are not part of the current dashboard.

## Design and canvas-md interface

Run incremental plan: [`design/Run控制台设计.md`](design/Run控制台设计.md). The once-reserved `canvas-md` interface is implemented as a separate read-only module: [`design/canvas-md接口预留.md`](design/canvas-md接口预留.md). Only `GET /api/canvas-md/tasks/{observer_id}/index`, `/document?path=...` and `/search?q=...` are supported. No data writes, browser editing or second knowledge store are introduced.

## Operation boundaries

The Observatory combines artifact viewing with operator controls. It delegates runtime actions to the SDK in a subprocess; avoiding an in-process runtime import does not make it read-only.

| Operation | Effect |
|---|---|
| View task status, logs, Canvas/Markdown, architecture, and workflow contracts | Reads persisted artifacts and process metadata; Canvas/Markdown editing is disabled. |
| `run`, `resume`, `pause`, `kill`, worker and scheduler controls | Changes execution state through SDK operator commands. Starting or resuming work can invoke models and consume provider quota. |
| Save Goal drafts, research plans, field annotations, or model/worker settings | Writes the corresponding local files or configuration. |
| Service/account switching and quota controls | Changes local account/service state and may restart managed services. |

The run-action POST endpoint requires a matching `Origin`/`Host`, `X-TM-Operator-Intent: 1`, JSON input, and a server-discovered task ID. SDK checks reject stale cycle/status/launch expectations and invalid state transitions. These request checks do not grant a read-only role or replace authentication. Scientific verdicts remain governed by the SDK's Agent contracts.

Authentication is enforced only when an auth configuration is supplied. Without it, `_authorized()` accepts requests. Keep the default loopback binding for local use; for remote use, configure authentication and an authenticated HTTPS reverse proxy or SSH tunnel. All configured Observatory logins access the same interface, including its operator controls; do not distribute one as a read-only viewer account.

The retired TMCR engineering-request UI is not the current write boundary.

## Install and run

For first-time setup, use `sciver quickstart` from the main [quick start](../README.md#quick-start-test0--web-ui). To reopen the local workspace, use `sciver web`. Both open the browser after the server responds; pass `--no-open` on headless machines.

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

An optional systemd user service keeps the server on loopback:

```bash
python -m tm_observer service-install --search-root /path/to/your/research/workspace \
  --port 8787 --basic-auth-file ~/.secrets.d/tm_observer_public.json
systemctl --user status tm-observer.service
```

The installer records the Python and Node executables and restarts on failure. Uninstall with `python -m tm_observer service-uninstall`. Set up your own authenticated HTTPS reverse proxy or SSH tunnel for remote access.
