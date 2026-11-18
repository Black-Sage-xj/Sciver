# Sciver — Development Agent Instructions

This Git repository contains the public Sciver SDK, Web UI, tests,
design and original Test0 teaching example. It has its **own** Git history
and GitHub remote; private research workspaces have separate histories and remotes.

## Shared public development baseline

`main` tracks `origin/main`; they are the shared baseline for everyday SDK,
Web, tests and documentation work. Start each `codex/*` feature branch from
an up-to-date `main`. Develop and review changes in this public history directly;
do not maintain a separate development tree that must be copied into a public
release tree for every change.

Integrate reviewed features with `git merge --ff-only` or a reviewed squash
commit onto current `main`. `scripts/public_release_guard.py` rejects merge
commits in the new release history, so do not use ordinary merge commits.
Keep its tree, history and artifact checks intact. Pushes still require the
explicit authorization described below.

Retired `master` and other private-history archives are local recovery material,
never a development base or a branch to push or merge into public history.
Keep any recovery `git bundle` outside the checkout in private storage. Apply
any needed legacy fix as a reviewed, scoped change on the current public base.

Keep credentials, private configuration, research workspaces and local audit
or change-register data outside the checkout in private directories with mode
`0700`. Existing local integrations may use ignored compatibility symlinks at
repository-root `config/local`, `audit` and `changes`. Preserve their external
targets and never force-add these links, their contents, bundles or private data.

## Development completion / scoped Commit policy

When a user authorizes an implementation in this repository, each
independently verifiable task should finish with **one scoped local Git
Commit by default**, not merely an edited worktree:

1. Before editing, inspect the current branch/HEAD, `git status --short
   --untracked-files=all`, staged diff and concurrent work. Apply
   `../.codex/skills/tm-change/SKILL.md` only when a concrete Agent/workflow
   behavior or quality effect needs post-change real execution traces to
   judge and cannot be accepted through immediate deterministic checks.
   Register TMA before those behavior changes and preserve the observation
   scope. Ordinary UI, deterministic fixes, documentation and directly
   verifiable prompt edits need no TMC/TMA or runtime-review document;
   mixed changes register only the behavior requiring later observation.
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
4. Commit the reviewed task atomically with a meaningful message. Changes
   meeting the `tm-change` trigger use its Commit body convention and actual
   TMA IDs; ordinary changes need no registration ID. If
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
