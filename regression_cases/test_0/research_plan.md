# Goal

Infer the true diffusion coefficient D of an isotropic two-dimensional Brownian particle from noisy observed trajectories, using reproducible scientific reasoning and numerical evidence.

# Research Contract

- The dynamics are isotropic zero-drift Brownian motion with constant D > 0.
- Every coordinate at every sample has independent, zero-mean Gaussian localization error of unknown common standard deviation.
- Sampling interval dt = 0.10 time units; x and y are arbitrary length units, so D is length^2/time.
- Use 150 trajectories labeled train for method selection and estimating D. Use the 50 trajectories labeled validation only for a separate consistency check after selecting the method. Both splits have the same model.
- Use only the materialized inputs. Do not access source-repository evaluator code, hidden truth, reference outputs, rubrics, or gold solutions.
- Submit a single D estimate plus reproducible method, independent validation summary, and uncertainty/bias limitations.

# Final Gate

This task is internally complete when the finished research trajectory establishes all of the following from the available scientific evidence:

- A single finite, positive numerical estimate of the diffusion coefficient D has been obtained from the 150 train trajectories.
- The estimation method is scientifically justified, reproducible, and accounts for the unknown independent Gaussian localization noise rather than treating observed displacements as noise-free.
- The method was selected on train data; the 50 held-out validation trajectories were used only for a separate, reported consistency check, with any differences and limitations discussed.
- The estimate, method, validation result, and uncertainty or bias limitations are documented sufficiently for independent scientific review.

The Goal Completion Judge assesses these requirements from the Research Plan, Canvas, and linked scientific evidence. A supported local Hypothesis is not by itself sufficient, but additional refinements, stronger estimators, or optional robustness analyses must not block completion once these requirements are met.

The benchmark's evaluator-only hidden-truth accuracy check (relative error at most 10%) is an **external post-run numerical score**, not an internal Goal Completion condition. Its hidden answer and scoring implementation must not be accessed by any research Agent or by the Goal Completion Judge. An external score cannot retroactively overwrite the historical internal Judge verdict or authorize another research Cycle.

# Required Context

- benchmark/instruction.md: schema and submission instructions.
- benchmark/source_manifest.json: original synthetic input provenance and SHA-256.
- benchmark/datasets/brownian_2d/trajectories.csv: positions and train/validation labels.
