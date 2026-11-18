# Engineering ownership and recovery boundaries

Public API compatibility lives in `workflow.py`, `worker_runtime.py`, and Web
`web.py`. These modules delegate to explicit owners. Implementations do not
import their public facade or redirect module globals to support test patches.
Tests inject dependencies or patch the module that owns the operation.

| Responsibility | Owner |
| --- | --- |
| Cycle lifecycle | `CycleRunner`, `CycleStart`, `CycleExit` |
| Invocation working state and phase coordination | `InvocationSession` |
| Thread, skill and invocation setup | `InvocationPreparation` |
| Frozen stage context and execution preflight | `InvocationContextPreparation` |
| Model delivery and typed delivery exits | `InvocationTransport` |
| Output normalization and contract repair | `InvocationOutputValidation` |
| Semantic closure, Evidence registration and commit | `InvocationCommit` |
| Failure cleanup across all invocation phases | `InvocationLifecycle` |
| Qualification coordination | `QualificationSession` |
| Proposal, checkpoint recovery, review and Hypothesis Lock | `qualification_proposal`, `qualification_recovery`, `qualification_review`, `qualification_lock` |
| Ordinary monitored Turns and Native Goal orchestration | `codex_turn_transport`, `codex_goal_transport` |
| Private Codex SDK access and compatibility checks | `codex_sdk_compat` |
| Worker persistence, routes, execution and scheduling | `worker_store`, `worker_routes`, `worker_execution`, `worker_scheduler` |
| Worker quota, preservation and operator actions | `worker_quota`, `worker_preservation`, `worker_actions` |
| Supervisor repair execution | `supervisor_continuity`; runtime review returns a route |
| HTTP transport, authentication and dispatch | `http_handler` with explicit `RequestContext` and `RouteServices` |
| HTTP domains and page composition | `http_*_routes`, `web_pages` |
| Goal material content identity and frozen navigation | `goal_materials`, `goal_snapshot`, `goal_gate` |
| Task collection freshness and bounded transcript reads | `state_cache`, `bounded_log` |
| Console client and escaped configuration | `run_console.js`, `run_console_client` |

## Runtime contracts

- Single-owner runners and both generations of Worker claims acquire
  `owner_admission.lock` before their registry locks. A held `runner.lock` is
  execution ownership even when runtime JSON is missing or corrupt. Unknown
  ownership cannot admit another runner.
- Account status reads do not migrate authentication. Refreshed credentials are
  attributed by stable account identity, including pinned runtimes; a changed
  selector cannot redirect another account's refresh.
- A model Turn delivers successfully only after an explicit successful terminal
  event with no delivery error. Setup uses the same watchdog, cancellation,
  telemetry and quota boundary. Optional UI callbacks cannot prevent cleanup.
- Invocation repair, semantic closure, Evidence registration and persistence
  share one cleanup boundary. Typed quota/control exits remain typed. Completed
  semantic closures remain reusable after a checkpoint failure.
- Native deliveries receive an immutable SDK return journal before semantic
  registration. A stopped invocation with a validated repair receipt can finish
  its original contract and Evidence registration without another model call;
  recovery preserves the authored files and failed invocation audit.
- The `benchmark/` namespace binds only to the task's configured benchmark root.
  Goal snapshots include individually named Required Context files, hash them
  into the delivery revision and expose their frozen aliases to Judge. Missing
  bindings, traversal, symlinks and unregistered benchmark files cannot widen
  that input boundary.
- Goal review captures plan, graph, Scientific Model and delivery authorities.
  The Judge reads independent frozen copies without holding the science writer
  lock. A short exclusive commit compares current authority and source hashes;
  stale results receive immutable receipts and cannot seal or replace a winner.
- Observer collection failures retain a visibly stale last-good snapshot.
  JSON, HTML, HTTP metadata and health checks expose the failure. Event tails
  read at most 256 KiB instead of loading complete historical transcripts.

## Validation and change review

The supported private SDK capability surface is pinned to `openai-codex==0.155.1`
in packaging and checked before research starts. New implementation owners are
included in runtime source provenance, so a process using foreign or mixed
source files is detectable.

Run the core suite with `python scripts/test_profiles.py full`; run the Web
suite with `PYTHONPATH=.:web pytest web/tests`. New invariant tests exercise
failed delivery, callback cleanup, account identity, owner admission, full
invocation cleanup, Goal snapshot drift and concurrent seal contention.
Architecture tests keep the SDK adapter and public facade dependency boundaries
and apply size budgets to the coordination entrypoints.

A real Test0 run must instantiate the official fixture in a new task directory,
with an empty scientific graph/model, new Agent sessions and Cycle 1. Historical
tasks stay intact. Offline fixture tests and wheel smoke checks do not launch a
research Cycle. Internal Goal adjudication and the external numeric grader are
separate results; the grader never changes scientific history.
