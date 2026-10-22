# Test0 — Public onboarding and assessment boundary

Test0 is an **openly inspectable teaching example**, not a blind benchmark.
The synthetic inputs, `expected.json` reference parameters, and all
`evaluator/` scripts are intentionally included in the public source tree.

The distinction is **workflow scope**, not GitHub confidentiality:

- **Internal Goal Completion:** the regular TM Judge checks whether the
  research has produced a training-set D estimate, reproducible noise-aware
  method, independent holdout validation, and uncertainty/bias limitations
  using the `research_plan.md`, Canvas, and recorded research evidence.
  A locally supported Hypothesis does not alone prove Goal Completion.
- **Offline numerical grader:** `evaluator/grade.py` publicly demonstrates
  the fixed 10% relative-error criterion against the reference parameter
  **after** a finished result exists. This score neither modifies a historical
  Judge verdict nor authorizes a new Cycle.

## Try both offline and inspect the implementation

```bash
python examples/test0/solve_baseline.py --output /tmp/tm-test0-answer.json
python -m theory_master.regression_cases grade Test0 /tmp/tm-test0-answer.json
```

To compare a previously saved Investigator result and Goal Completion receipt,
use the public `evaluator/score_trajectory.py` from outside the research task:

```bash
python regression_cases/test_0/evaluator/score_trajectory.py \
  --scientific-result /path/to/research/N/investigation_attempts/attempt-0001/investigation_result.json \
  --goal-completion /path/to/research/N/goal_completion_out.json
```

The two judgments have distinct outputs; possible statuses include
`both_pass`, `goal_pass_oracle_fail`, `oracle_pass_goal_not_passed`
and `both_fail`. They are diagnostic, not historical verdict rewrites.

To let a TM Agent reason from the question rather than read the solved
example, `materialize Test0` copies only the plan and `benchmark/`.
This is a teaching convention. A GitHub reader can deliberately inspect
the published answer, baseline, and grader.

See `examples/test0/README.md` for the walkthrough.
