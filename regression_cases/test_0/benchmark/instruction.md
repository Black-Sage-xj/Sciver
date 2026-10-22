# Test0 — Original Brownian Motion Scientific Challenge

Estimate diffusion coefficient D from 200 independent 2D noisy trajectories, each sampled at 200 time points with dt = 0.10. Ground-truth parameters are not provided to a materialized research task, although the public educational repository includes the reference answer separately.

CSV: datasets/brownian_2d/trajectories.csv

Columns: trajectory_id (0..199), time_index (0..199), split (train for trajectory_id 0..149, validation for 150..199), x and y (measured coordinates, arbitrary length unit). Physical time is 0.10 * time_index.

Physical assumptions: isotropic zero-drift Brownian motion with unknown D, plus independent, zero-mean Gaussian measurement error in each coordinate with an unknown common standard deviation.

Use the train split to estimate D and select your scientific method; reserve validation for a separate consistency check. Account for measurement noise in observed displacements.

Submission: UTF-8 JSON artifact of the form:
{"D_estimate": 0.25, "method": "explain estimation and reproducibility", "validation": "describe holdout check"}

0.25 is a purely illustrative schema value, NOT a truth hint. D_estimate is a finite positive JSON number in length^2/time. Submit reproducible code and scientific explanation. The offline evaluator checks only D_estimate against its fixed 10% relative error gate, while Judge assesses scientific evidence. For an Agent-run demonstration, do not feed the public reference answer, solved baseline, or grader into the research context.
