# Standalone wheel installation

The SDK and observer require Python 3.12 or newer. The SDK uses the standard
library TOML reader, and the observer uses Python 3.12 f-string syntax.
The observer wheel depends on the matching `sciver` SDK version.

## Install a reviewed release

Put the reviewed `sciver-0.1.0-*.whl` and `tm_observer-0.1.0-*.whl` files in
one directory. Replace `/absolute/path/to/reviewed-wheels` below with that
directory, then create and activate a new environment:

```bash
python3.12 -m venv "$HOME/sciver-venv"
source "$HOME/sciver-venv/bin/activate"
python -m pip install --no-index --find-links /absolute/path/to/reviewed-wheels \
  'sciver==0.1.0' 'tm-observer==0.1.0'
sciver --help
tm-observer --help
sciver smoke
```

The help commands and `smoke` work without model credentials. `smoke` runs a
fake backend. Continue with [First use](first-use.md) or
[首次使用](first-use.zh-CN.md) to prepare a fresh Test0 task, open its plan and
empty graph, then configure an account before starting real model calls.
Running these wheels does not require a source checkout or Node.js. The two
base wheels support offline preparation and viewing. Before real research,
install the SDK extra (`python -m pip install 'sciver[sdk]==0.1.0'`) and a
supported Codex CLI. The extra downloads model-client dependencies, including
`openai-codex` and `mcp`; CLI login alone does not install them. For a fully
offline deployment, supply those dependency wheels in the reviewed wheel
directory and install with `--no-index --find-links` and `sciver[sdk]==0.1.0`.

## Verify release artifacts

Build with local `pip`, `setuptools>=77` and `wheel`, then verify ordinary
installation with the minimum supported interpreter:

```bash
python3.12 scripts/check_distribution.py --work-dir /tmp/sciver-release-check
```

The check disables package indexes, builds both wheels, installs them into a
fresh virtual environment, and runs from a directory outside the checkout with
a blank HOME. It checks CLI help, bundled Test0 hashes, two independently
prepared Test0 tasks with empty scientific state, isolated post-run grading,
observer discovery, prepared-task Goal content, consoles, empty graph APIs,
Web startup, main navigation, JavaScript, CSS and font endpoints. It also runs
the installed `sciver smoke` command. It uses no model calls. `result.json`
records commands, installed module paths and HTTP
results; `server.log` contains the temporary server output. The server is stopped
before the check returns. Use a new `--work-dir` for each run.

The independent **Sciver standalone distribution** GitHub Actions workflow runs
this check on Python 3.12 and 3.13 for pushes, pull requests and manual dispatch.
It installs only local build tools, then installs the generated wheels without
package-index access. Each job retains `result.json` and `server.log`; it needs
no model credentials and does not publish packages.

Repeated builds recreate only each backend's owned package output under
`build_lib`, so deleted source modules and resources cannot remain in a later
wheel. Source-overlapping and symlink output destinations are refused; sibling
build products are preserved. `tests/test_distribution.py` builds each wheel,
deletes a module and resource from that same source fixture, confirms their old
build outputs still exist, then rebuilds and checks that both are absent. The
installed-wheel check also compares every packaged file with its current source
or explicit generated-resource contract.

Only the canonical original `regression_cases/test_0` files named in `setup.py`
are copied into SDK package resources. Other local fixtures are excluded from
this allowlist. The reference evaluator remains outside each instantiated task;
only the original public plan and input data enter its scientific workspace.
Editable installations read the canonical checkout fixture directly. The
observer includes its prebuilt frontend, vendor license, history snapshot and
workflow guide, so users do not need Node.js to run an installed wheel.

## Optional automatic audit integration

Automatic Cycle audit is off by default. It requires a separately installed
audit Skill and compatible relay program; these external services are not
included in the public wheels. Before enabling it, configure absolute paths in
the Runner's environment:

```bash
export THEORY_MASTER_AUDIT_SKILL=/absolute/path/to/audit/SKILL.md
export THEORY_MASTER_AUDIT_RELAY=/absolute/path/to/start_audit_relay.py
export THEORY_MASTER_AUDIT_WORKDIR=/absolute/path/to/workspace
```

The Skill and relay must be files and the work directory must exist. A missing
adapter produces a configuration error before dispatch. Existing integrated
checkouts can still use their installed legacy adapter. Enabling automatic audit
starts additional model calls and consumes quota; the installation checks use
temporary adapter fixtures and mocked dispatch only.

## Public release boundary

Prepare releases on a branch derived from GitHub `main`, apply reviewed public
changes there, and inspect the complete tree and new commit history. Only the
original Test0 teaching fixture is bundled. Run `scripts/public_release_guard.py`
on the exact release Git ref and every wheel and source archive. Use `--base`
with the reviewed public base to check all new commit trees and metadata, including
content deleted by a later commit. Keep the guard's
private-provider checks intact. A successful cold start does not publish anything;
pushing a branch and publishing packages remain separate release operations.
