# Contributing to Sciver

Create a branch for each change and open a pull request with a short description,
reproduction steps where relevant, and the offline checks you ran. For regular
use, install the released wheels as described in the README. To modify Sciver,
use the editable source installation below.

Use `README.zh-CN.md` as the editorial source for the project overview and
onboarding instructions. Update `README.md` in the same change whenever those
instructions change, and run `python scripts/check_readme_sync.py` to check
that sections, commands, and links stay aligned. This structural check does
not replace reviewing the English translation for meaning.

## Development installation

Use Python 3.12+ on Linux, macOS, or Windows with WSL2. Clone the repository and
create a development environment separate from any wheel installation:

```bash
git clone https://github.com/Black-Sage-xj/Sciver.git
cd Sciver
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[sdk,test]' -e ./web
sciver quickstart
```

This editable installation uses Python code from the checkout. Restart the Web
server after changing Python code. The checked-in Web assets can run without
Node.js; rebuilding the Research Chat frontend requires the tools and commands
in [the frontend README](web/research_chat_frontend/README.md).

`main` receives ongoing changes; a Release tag fixes a version. To investigate
v0.1.0 specifically, check out that tag before installing and create a branch
for your changes. Switching from wheels does not require moving your research
data: activate the development environment and run `sciver web` to reopen the
default `~/Sciver-workspace`, or pass the same `--workspace` you used before.
Preserve your workspace and account configuration. Avoid editing files inside
the wheel environment's `site-packages`; package updates can replace them.

## Verification and pull requests

The repository provides a test runner with registered test categories. From the
repository root, use `python scripts/run_tests.py --list` to inspect the
available categories, `python scripts/run_tests.py --category invariant` to run
the invariant gate, or `python scripts/run_tests.py` to run the registered suite.
For a focused change, also run the relevant individual tests.

Do not include credentials, local configuration, personal research workspaces,
run logs, or third-party datasets in a pull request. If a change affects Agent
prompts or research decisions, describe the expected behavior and the evidence
used to check it. Tests that make real model calls require explicit approval
from the person paying for those calls.
