# Contributing to Sciver

Create a branch for each change and open a pull request with a short description,
reproduction steps where relevant, and the offline checks you ran. Install the
SDK and Web package as described in the README.

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
