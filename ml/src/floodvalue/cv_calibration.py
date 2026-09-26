"""Compare calibration families on cached CV predictions, fitting inner valid only."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar
from sklearn.isotonic import IsotonicRegression

from floodvalue.calibration import Platt, best_prob_threshold, fit_platt, subsample
from floodvalue.cv import score_arrays


@dataclass
class Isotonic:
    x: np.ndarray
    y: np.ndarray

    def apply(self, logits):
        return np.interp(logits, self.x, self.y).astype(np.float32)

    def as_dict(self):
        return {"type": "isotonic", "x": self.x.tolist(), "y": self.y.tolist()}


def fit_calibrator(z, target, method):
    idx = subsample(z.size, 2_000_000, 0)
    x, y = z[idx].astype(np.float64), target[idx].astype(np.float64)
    if method == "identity":
        return Platt(1., 0.)
    if method == "platt":
        return fit_platt(z, target)
    if method == "temperature":
        def objective(a):
            t = a*x
            return float(np.mean(np.logaddexp(0, t) - y*t))
        result = minimize_scalar(objective, bounds=(.05, 5.), method="bounded")
        if not result.success:
            raise RuntimeError("temperature fit failed")
        return Platt(float(result.x), 0.)
    if method == "isotonic":
        model = IsotonicRegression(y_min=0., y_max=1., out_of_bounds="clip").fit(x, y)
        return Isotonic(model.X_thresholds_, model.y_thresholds_)
    raise ValueError(method)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--methods", nargs="+", default=["identity", "temperature", "isotonic"])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads((args.run/"cv_result.json").read_text())
    with np.load(args.run/"valid_predictions.npz") as inner:
        valid = inner["valid"]
        z, target = inner["logits"][valid], inner["target"][valid]
    with np.load(args.run/"score_predictions.npz") as outer:
        arrays = {k: outer[k] for k in outer.files}
    results = {}
    for method in args.methods:
        calibrator = fit_calibrator(z, target, method)
        threshold, _ = best_prob_threshold(calibrator.apply(z), target)
        scored = score_arrays(arrays["names"].tolist(), arrays["logits"], arrays["target"], arrays["valid"],
                              calibrator, threshold)
        results[method] = {"calibrator": calibrator.as_dict(), "threshold": threshold, "score": scored}
        print(json.dumps({"method": method, "event_mean_iou": scored["event_mean_iou"],
                          "brier": scored["probability"]["brier"], "ece": scored["probability"]["ece"]}), flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"source_run": str(args.run), "fold": source["fold"],
                                   "plan_sha256": source["plan_sha256"], "results": results},
                                  indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
