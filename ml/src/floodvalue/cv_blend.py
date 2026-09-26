"""Evaluate predeclared mixtures of cached CV predictions without retraining.

Calibration and threshold are fitted only on each fold's inner validation.
The outer folds remain development data for comparing candidates.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.special import expit, logit

from floodvalue.calibration import best_prob_threshold, fit_platt
from floodvalue.cv import score_arrays
from floodvalue.cv_calibration import fit_calibrator


def blend_arrays(archives, weights, space="logits"):
    if len(weights) != len(archives) or min(weights) < 0 or sum(weights) <= 0:
        raise ValueError("invalid mixture weights")
    first = archives[0]
    result = np.zeros_like(first["logits"], dtype=np.float32)
    for archive, weight in zip(archives, weights):
        for key in ("names", "target", "valid"):
            if not np.array_equal(first[key], archive[key]):
                raise ValueError(f"incompatible prediction archives: {key}")
        values = archive["logits"]
        if space == "probabilities":
            values = expit(values)
        elif space != "logits":
            raise ValueError("unknown blend space")
        result += values * (weight / sum(weights))
    if space == "probabilities":
        result = logit(np.clip(result, 1e-6, 1-1e-6)).astype(np.float32)
    return result


def evaluate_blend(runs, weights, space, calibration="platt"):
    metadata = [json.loads((p/"cv_result.json").read_text()) for p in runs]
    if len({(r["fold"], r["plan_sha256"]) for r in metadata}) != 1:
        raise ValueError("folds/plans differ")
    parts = {}
    for part in ("valid", "score"):
        archives = []
        for run in runs:
            with np.load(run/(part+"_predictions.npz")) as src:
                archives.append({k: src[k] for k in src.files})
        z = blend_arrays(archives, weights, space)
        parts[part] = {k: archives[0][k] for k in ("names", "target", "valid")}
        parts[part]["logits"] = z
        del archives
    inner = parts["valid"]
    valid = inner["valid"]
    calibrator = (fit_platt(inner["logits"][valid], inner["target"][valid]) if calibration == "platt"
                  else fit_calibrator(inner["logits"][valid], inner["target"][valid], calibration))
    threshold, _ = best_prob_threshold(calibrator.apply(inner["logits"][valid]), inner["target"][valid])
    outer = parts["score"]
    scored = score_arrays(outer["names"].tolist(), outer["logits"], outer["target"], outer["valid"], calibrator, threshold)
    return {"runs": [str(p) for p in runs], "weights": weights, "space": space,
            "fold": metadata[0]["fold"], "plan_sha256": metadata[0]["plan_sha256"],
            "calibration": {calibration: calibrator.as_dict(), "threshold": threshold}, "score": scored}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--weights", type=float, nargs="+", required=True)
    parser.add_argument("--space", choices=["logits", "probabilities"], default="logits")
    parser.add_argument("--calibration", choices=["platt", "isotonic", "temperature", "identity"], default="platt")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate_blend(args.runs, args.weights, args.space, args.calibration)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps({"fold": result["fold"], "event_mean_iou": result["score"]["event_mean_iou"],
                      "iou": result["score"]["overall"]["micro"]["iou"]}))


if __name__ == "__main__":
    main()
