#!/usr/bin/env python3
"""Dependency-free public teaching solution for TheoryMaster's Brownian Test0."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

DEFAULT_CSV = Path(__file__).resolve().parents[2] / "regression_cases/test_0/benchmark/datasets/brownian_2d/trajectories.csv"
DT = 0.10
LAGS = range(1, 9)


def read_splits(path: Path) -> dict[str, list[list[tuple[float, float]]]]:
    groups: dict[str, dict[int, list[tuple[int, float, float]]]] = {
        "train": defaultdict(list), "validation": defaultdict(list)
    }
    with path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            split = row["split"]
            if split not in groups:
                raise ValueError(f"unknown split: {split}")
            groups[split][int(row["trajectory_id"])].append(
                (int(row["time_index"]), float(row["x"]), float(row["y"]))
            )
    result = {}
    for split, trajectories in groups.items():
        ordered = []
        for _, samples in sorted(trajectories.items()):
            samples.sort(key=lambda item: item[0])
            if len(samples) < max(LAGS) + 1:
                raise ValueError("trajectory is too short for selected lags")
            if [item[0] for item in samples] != list(range(len(samples))):
                raise ValueError("missing or repeated time indices")
            ordered.append([(x, y) for _, x, y in samples])
        if not ordered:
            raise ValueError(f"no trajectories in {split}")
        result[split] = ordered
    return result


def estimate_diffusion(trajectories: list[list[tuple[float, float]]]) -> tuple[float, float]:
    """Noisy 2D Brownian MSD = 4*D*t + 4*sigma^2; D is slope/4."""
    msd = []
    for lag in LAGS:
        distance_sum = 0.0
        count = 0
        for points in trajectories:
            for index in range(len(points) - lag):
                x0, y0 = points[index]
                x1, y1 = points[index + lag]
                distance_sum += (x1 - x0) ** 2 + (y1 - y0) ** 2
                count += 1
        msd.append(distance_sum / count)

    times = [lag * DT for lag in LAGS]
    mt = sum(times) / len(times)
    my = sum(msd) / len(msd)
    slope = sum((t - mt) * (y - my) for t, y in zip(times, msd)) / sum((t - mt) ** 2 for t in times)
    return slope / 4.0, my - slope * mt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--output", type=Path, help="optional JSON submission file")
    args = parser.parse_args()
    splits = read_splits(args.csv)
    train_d, offset = estimate_diffusion(splits["train"])
    validation_d, _ = estimate_diffusion(splits["validation"])
    if train_d <= 0:
        raise ValueError("nonpositive fitted diffusion estimate")
    result = {
        "D_estimate": train_d,
        "method": "Fit 2D MSD versus lags 1-8 with intercept for localization noise; D=slope/4.",
        "validation": (
            f"Independent holdout D={validation_d:.6f}; "
            f"relative train/holdout difference={abs(validation_d - train_d) / train_d:.2%}."
        ),
        "limitations": "Overlapping increments are correlated; naive regression errors may be optimistic.",
        "estimated_msd_intercept": offset,
    }
    payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"Wrote submission to {args.output}")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
