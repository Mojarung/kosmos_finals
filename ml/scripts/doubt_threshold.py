"""Doubt threshold of the service, fitted on validation and checked on the independent parts.

The service calls a pixel doubtful when its map error chance is high: 1 - p where the mask
says water (p >= threshold), p where it says land. The edge is the top decile of that chance
on validation pixels, so no test part takes part in choosing it. test_core and Bolivia only
report how often the mask is wrong inside and outside the doubtful set.

    uv run python scripts/doubt_threshold.py
writes artifacts/evaluation/doubt_threshold.json.
"""

from __future__ import annotations

import json

import numpy as np

from floodvalue import paths
from floodvalue.baseline_run import load_baseline
from floodvalue.cli import BASELINE_PATH, CALIBRATION_PATH, MODEL_DIR, SPLIT_PATH
from floodvalue.ensemble import load_members
from floodvalue.evaluate import predict_part
from floodvalue.infer import load_calibration
from floodvalue.package import member_dirs
from floodvalue.split import load_split
from floodvalue.uncertainty import binary_entropy

QUANTILE = 0.9
CHECK_PARTS = ("test_core", "bolivia")
OUT_PATH = paths.ARTIFACTS_DIR / "evaluation" / "doubt_threshold.json"


def error_chance(prob: np.ndarray, threshold: float) -> np.ndarray:
    return np.where(prob >= threshold, 1 - prob, prob)


def check(chance: np.ndarray, error: np.ndarray, edge: float) -> dict:
    doubtful = chance >= edge
    return {
        "pixels": int(chance.size),
        "doubtful_share": round(float(doubtful.mean()), 4),
        "error_rate": round(float(error.mean()), 4),
        "error_rate_doubtful": round(float(error[doubtful].mean()), 4),
        "error_rate_other": round(float(error[~doubtful].mean()), 4),
        "errors_in_doubtful": round(float(error[doubtful].sum() / max(error.sum(), 1)), 4),
    }


def main() -> None:
    platt, threshold = load_calibration(CALIBRATION_PATH)
    members = load_members(member_dirs(MODEL_DIR))
    baseline = load_baseline(BASELINE_PATH)
    split = load_split(SPLIT_PATH)

    def part(name: str) -> tuple[np.ndarray, np.ndarray]:
        pred = predict_part(paths.SEN1FLOODS11_DIR, split[name], members, platt, baseline)
        prob = pred.prob[pred.valid]
        return error_chance(prob, threshold), (prob >= threshold) != pred.target[pred.valid]

    chance, error = part("valid")
    edge = round(float(np.quantile(chance, QUANTILE)), 4)
    report = {
        "rule": ("map error chance = 1 - p where p >= threshold, p elsewhere (calibrated p); doubtful pixel: "
                 f"chance >= the {QUANTILE:.0%} quantile of that chance on validation pixels"),
        "fitted_on": "valid",
        "model_threshold": threshold,
        "quantile": QUANTILE,
        "doubt_threshold": edge,
        "entropy_bits_at_threshold": round(float(binary_entropy(np.array([edge]))[0]), 4),
        "valid": check(chance, error, edge),
    }
    for name in CHECK_PARTS:
        report[name] = check(*part(name), edge)
    OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
