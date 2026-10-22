# Test0 — First scientific research example

This **fully public teaching example** illustrates how Sciver turns a
scientific question into a Research Plan, investigation, Judge decision, and
separate numerical evaluation.

**Not a blind benchmark:** the synthetic CSV, its generator, the reference
parameters (`regression_cases/test_0/expected.json`), the grader
(`regression_cases/test_0/evaluator/`), and an inspectable baseline solution
are all intended to be published.

## The question

Estimate diffusion coefficient `D` from 200 independent noisy 2D Brownian
trajectories, each sampled at 200 points with interval `dt=0.10`. The data
include 150 training and 50 holdout validation trajectories. Measurement
noise creates an offset in mean-square displacement (MSD):

```text
E[|observed_position(t0+t) - observed_position(t0)|^2] = 4 D t + 4 sigma^2
```

Fit MSD against lag and use `D = slope/4`; do not estimate D solely from a
single noisy displacement.

## Quick start (no model calls)

Run from the **Sciver Git repository root**:

```bash
# Inspect the example and check its public input hash.
python -m theory_master.regression_cases show Test0
python -m theory_master.regression_cases verify Test0

# Materialize only research inputs into a fresh TM task.
python -m theory_master.regression_cases materialize Test0 /tmp/tm-test0-onboarding

# Run the inspectable educational baseline and public numerical grader.
python examples/test0/solve_baseline.py --output /tmp/tm-test0-answer.json
python -m theory_master.regression_cases grade Test0 /tmp/tm-test0-answer.json
```

The materialized task contains `research_plan.md` and `benchmark/` only;
the reference answer, scorer, generator and solved baseline remain visible
in the repository but are not automatically copied into the Agent task.
This input separation demonstrates autonomous reasoning; it **does not**
promise that the GitHub reference is secret.

To try a **real multi-Agent research Cycle**, configure a supported model
provider and run Sciver on the materialized research task according to the main
README. That can incur API costs and is not required for the offline example.

## Guide to the files

- `regression_cases/test_0/research_plan.md`: scientific Goal, Research
  Contract, evidence requirements, and internal Goal Completion gate
- `regression_cases/test_0/benchmark/`: input CSV and source manifest
- `examples/test0/solve_baseline.py`: independent training/holdout analysis
- `regression_cases/test_0/expected.json`: **public** reference values
- `regression_cases/test_0/evaluator/`: **public** generator and grader
- `regression_cases/test_0/PROTOCOL.md`: why internal Goal Completion
  and external numerical scoring are different

For reproducibility (without modifying the CSV):

```bash
python regression_cases/test_0/evaluator/generate_data.py --check
```

Passing the numerical score is **not** proof that TM itself autonomously
completed the scientific Goal; it checks only the submitted number.
