# Sciver — Development Agent Instructions

This Git repository contains the public Sciver SDK, Web UI, tests,
design and original Test0 teaching example. It has its **own** Git history
and GitHub remote; private research workspaces have separate histories and remotes.

## Development completion / scoped Commit policy

When a user authorizes an implementation in this repository, each
independently verifiable task should finish with **one scoped local Git
Commit by default**, not merely an edited worktree:

1. Before editing, inspect the current branch/HEAD, `git status --short
   --untracked-files=all`, staged diff and concurrent work. Read the
   applicable `tm-change` Skill at `../.codex/skills/tm-change/SKILL.md`.
   Register the appropriate TMC/TMA **before** modifying TM code, tests,
   prompts, or design documents; preserve its source, evidence and required
   review boundaries.
2. Implement one logical change, read its exact diff, and run relevant
   deterministic tests, compilation/format checks and sensitive-file checks.
   Do not interpret a failed test as passing or skip necessary tests silently.
   Tests with paid model calls or new scientific Cycles require **separate
   explicit user authorization** before each run.
3. After verifying, stage **only the exact files/hunks belonging to the
   task** using `git add -- <exact paths>` (or `git add -p` as appropriate).
   Inspect `git diff --cached --name-status`, the patch, and
   `git diff --cached --check`. Never use `git add -A`,
   `git commit -a`, indiscriminate resets or mass stashes in a shared
   Agent worktree.
4. Commit the reviewed task atomically with a meaningful message and the
   actual TMC/TMA IDs using the `tm-change` Commit body convention. If
   unrelated changes are already staged, isolate the intended Commit
   (e.g., a dedicated worktree/index or carefully scoped `git commit
   --only -- <paths>`) **without altering another Agent's index**. If
   that is not safe, leave the change uncommitted and report the blocker.
5. Verify the resulting Commit's SHA, included paths, remaining worktree
   state and passing/failing tests. Never claim the entire working tree
   is clean or validated from a subset test result.

**Important exceptions:** a user request to not Commit wins; do not
Commit unreviewed partial development, another Agent's in-progress
files, credentials, private experiment datasets or unlicensed
third-party material. A failed test or uncertain ownership is a
reason to report incomplete work, not to fake a pass. Local Commit
is **not** a Push: never Push to GitHub/Gitee, rewrite history,
force-Push, or change a remote without explicit authorization.

### Runtime Supervisor exception

The automated `incident_supervisor` role runs under `execution_policy.REPO_GUARD`,
which declines Git commits and history operations. In that role, register TMC
before engine edits and return exact changed paths, source hashes and validation
evidence. Git finalization remains `pending_review` in the SDK receipt; do not
bypass the guard or claim committed work. An SDK validation receipt permits only
its guarded recovery action, not publication or a scientific conclusion. Normal
development agents still follow the scoped local Commit policy above.

## Open-source boundary

Only the original Brownian motion `regression_cases/test_0/` and its
educational reference solution should be distributed as bundled research
fixtures. Public release history must contain only reviewed source and original fixtures.
Do not merge private development ancestry into the public release branch.
Verify the complete tree, commit metadata, and new history before publication.

The upstream `scholarly-deep-research` integration is separately being
refactored. Do not modify its files or dependent implementation without
a specific task authorization.
