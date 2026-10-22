# Sciver Public Regression Example: Test0

The **only benchmark fixture distributed in this public repository** is
`test_0`: an original, fully inspectable two-dimensional Brownian motion
diffusion-coefficient estimation example. It uses 200 synthetic trajectories,
150 for training and 50 for validation, with unknown Gaussian localization
noise inside the scientific task.

See the [guided onboarding tutorial](../examples/test0/README.md).

Both the reference answer (`test_0/expected.json`) and the offline
grader (`test_0/evaluator/`) are public. This is a **learning example**,
not a hidden-answer leaderboard. A materialized research task receives only
`research_plan.md` and `benchmark/`, so that an Agent is not
automatically handed the reference solution.

From the Sciver repository root, with Python dependencies installed:

```bash
python -m theory_master.regression_cases list
python -m theory_master.regression_cases show Test0
python -m theory_master.regression_cases verify Test0
python -m theory_master.regression_cases materialize Test0 /tmp/tm-test0-task
python examples/test0/solve_baseline.py --output /tmp/tm-test0-answer.json
python -m theory_master.regression_cases grade Test0 /tmp/tm-test0-answer.json
python regression_cases/test_0/evaluator/generate_data.py --check
```

These commands do not initiate LLM calls. A full Sciver research
Workflow requires separately configured provider credentials.

**Other historical scientific challenges and third-party fixtures are not
distributed with Sciver.** Researchers who have separate permission
to use them should keep those datasets in their own research workspace,
not under this public repository's `regression_cases/` directory.
