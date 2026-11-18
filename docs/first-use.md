# First use

Use Python 3.12 or newer. Follow the source installation in [README](../README.md) or the [wheel distribution instructions](distribution.md), then activate that virtual environment in each terminal. Keep research tasks and runtime data outside the source checkout.

## Offline preparation

```bash
export SCIVER_WORKSPACE="$HOME/sciver-workspace"
mkdir -p "$SCIVER_WORKSPACE"
export THEORY_MASTER_SEARCH_ROOT="$SCIVER_WORKSPACE"
export THEORY_MASTER_DATA_ROOT="$SCIVER_WORKSPACE/.theory-master-tasks"
sciver smoke
sciver test --list
sciver test Test0 --root "$SCIVER_WORKSPACE" --prepare-only
```

`smoke` uses a fake backend and prints its temporary artifacts directory. Omitting `--prepare-only` starts real model calls and uses account quota. `--prepare-only` verifies and copies the official Test0 inputs and initializes a new task without model requests. Keep the printed Task ID, Task Home, Research Root, and Console path. Every Test0 invocation creates a fresh task, blank graph and Scientific Model, and new Agent sessions from Cycle 1. Historical tasks remain available; use `resume` only when intentionally continuing an existing task.

## View the prepared task

In another terminal, activate the same virtual environment and set the same workspace variables, then run:

```bash
tm-observer serve --search-root "$SCIVER_WORKSPACE" --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787/run/`. Refresh Task List, open the prepared task, and inspect Research Plan. `--search-root` must contain the task homes. Runtime data may live separately: the observer checks each task's canonical SDK binding before opening its plan or graph. The commands above keep both in one workspace. Preparation alone does not produce scientific results.

## Configure a model and run

Install a supported Codex CLI so `codex --version` succeeds. Then use Sciver's managed account login rather than assuming your desktop login is shared:

```bash
python -m pip install 'sciver[sdk]==0.1.1'
export THEORYMASTER_COMPUTE_PYTHON="$(command -v python)"
codex --version
sciver codex-account login-1
sciver codex-account 1
sciver codex-account status
sciver model-profile status
```

The SDK extra installs model-client dependencies and may require package-index access. For an offline installation, provide their wheels and add `--no-index --find-links /absolute/path/to/reviewed-wheels`. The export selects the active virtual environment for computation; install any numerical libraries your research needs there. Complete the device-code login shown in the terminal. `codex-account status` should show the selected account as logged in; this reports local credentials, not a successful online model request. Review the actual model routes printed by `model-profile status`. Services shows account and quota information. Optional providers and tool integrations require their own local configuration; review `config/.env.example`.

**The following operation starts real model calls, uses account quota and may incur charges.** Costs depend on provider, model, token use and run duration; starting a research task can involve multiple Agents and Cycles. Research Chat also uses model quota. Use your actual Task ID in place of `0`, in a terminal with the same workspace variables:

```bash
sciver run 0
```

Task List → Task Console shows runtime state, errors, Cycles and reports; Canvas shows the accumulated scientific graph. Scientific conclusions appear only after a completed adjudication. A stopped or failed task is not a scientific result. The printed Research Root holds persisted artifacts; keep it with the task home when backing up the workspace.

## Create your own topic

In the Web UI, open `/research-chat/?new=1` and choose **创建新项目**. Start with a clear question or an exploratory interest, discuss relevant work, and develop a Plan gradually. **审阅计划并创建项目** lets you save an incomplete draft or review and confirm a new Task. Creation returns a task link without starting scientific execution. Preparation messages use your configured Chat model account. When the question needs literature, Chat can call the existing Literature Agent within the requested scope; that independent model session reuses the production identity, skills and output contracts. Discovery, selected-source reading, comparison and gap-driven follow-up are bounded by a default of two rounds and six source-acquisition attempts. Unresolved source needs pause with retained results; resuming the same job reuses completed work.

For terminal creation:

Create a separate task home and write a plan. Keep the four H1 headings below in this exact order; `sciver new` adds the SDK-maintained Scientific Model. Replace each example paragraph with your concrete goal, scope, allowed resources, completion criteria and available inputs before starting real research.

```bash
mkdir -p "$SCIVER_WORKSPACE/my-topic"
cat > "$SCIVER_WORKSPACE/my-topic/research_plan.md" <<'PLAN'
# Goal

Determine whether a specified scientific proposition holds within a stated domain.

# Research Contract

Define the testable scope, evidence standards, allowed tools, resource limits and deliverables.

# Final Gate

State the measurable criteria for completing the overall research goal.

# Required Context

List the supplied inputs and their absolute paths, background assumptions and constraints.
PLAN
sciver new "$SCIVER_WORKSPACE/my-topic/research_plan.md"
```

`new` validates and initializes the plan without model calls. Record the new Task ID. Review the plan in Research Plan, then use `sciver run <Task ID>` when ready. The example text is a format template; it needs a real research question and inputs to become a useful task.

## Troubleshooting

- Command missing: activate the installation's virtual environment; verify `sciver --help` and `tm-observer --help`.
- Empty Task List: check the printed Task Home is under the Web service's `--search-root`; restart the service with the workspace above and refresh.
- Missing login: install the Codex executable, complete `login-1`, select account `1`, and check `status` again. Do not paste credentials into chat or commit them.
- Invalid plan: use exactly `# Goal`, `# Research Contract`, `# Final Gate`, `# Required Context`; each section must contain text. After initialization, retain the generated `# Scientific Model` as the last section.
- Test0 preparation failure: check the error, disk permissions and `sciver test --list`. Existing destinations must be empty; use `--root` to allocate a fresh instance automatically.


## Optional PDF conversion

Fresh installations leave PDF conversion **off**. Open **Settings (`/settings`) → PDF conversion** to enable a provider on the host running Sciver:

- **Local Marker**: configure the executable explicitly or place `marker_single` on the service process's `PATH`. Sciver does not assume a personal environment path or install Marker automatically. Enabling your own host does not change product defaults.
- **Online MinerU**: enter an API Token or set `MINERU_API_KEY` for the service process. Conversion uploads the PDF to MinerU. The key stays in a private server configuration (0600) or environment; settings responses expose only whether a key is configured.

Settings live at `$XDG_CONFIG_HOME/sciver/document-conversion.json`, or `~/.config/sciver/document-conversion.json` when XDG is unset; `SCIVER_DOCUMENT_CONVERSION_CONFIG` can override this location. Changes apply to subsequent conversions. Without conversion, metadata, abstracts and accessible HTML text remain usable. The default page limit is 20; set 0 to process all available pages. Retained text, images and layout artifacts support source review, but converted pages are not automatically verified scientific claims. Reading the first N pages does not establish full-paper verification. Deterministic and mock tests cover the integration; real model quality and online conversion have not been validated by those tests.
