"""Isotonic calibration fitted on validation only, scored once on test_core next to the shipped Platt.

    uv run python scripts/isotonic_check.py        # from ml/, CPU, about 5 minutes

Every metric comes from floodvalue.reliability_check, as in artifacts/evaluation/reliability_test_core.json,
so the Platt rows reproduce that file. The service keeps Platt: the mask threshold 0.41 and the doubt threshold
were chosen on validation for Platt, and switching the calibration means choosing both again on validation and
rebuilding the scenes. Mean logits are cached in runs/isotonic_cache (git-ignored).
"""
from __future__ import annotations

import json
import time

import numpy as np
from sklearn.isotonic import IsotonicRegression

from floodvalue import paths
from floodvalue.calibration import subsample
from floodvalue.cli import CALIBRATION_PATH, EVALUATION_DIR, MODEL_DIR, SPLIT_PATH
from floodvalue.ensemble import load_members
from floodvalue.infer import load_calibration
from floodvalue.package import member_dirs
from floodvalue.reliability_check import calibration_metrics, part_logits
from floodvalue.split import load_split

CACHE = paths.PIPELINE_ROOT / "runs" / "isotonic_cache"
OUT = EVALUATION_DIR / "isotonic_check.json"
FIT_PIXELS, SEED = 2_000_000, 0  # the Platt subsample rule of calibration.json
KEEP = ("ece_equal_width", "ece_equal_mass", "ece_mid_range", "brier", "mid_range_share_of_pixels")


def logits_of(part: str, split: dict, members) -> tuple[np.ndarray, np.ndarray]:
    cache = CACHE / f"logits_{part}.npz"
    if cache.exists():
        loaded = np.load(cache)
        return loaded["logits"], loaded["target"]
    start = time.time()
    logits, target = part_logits(paths.SEN1FLOODS11_DIR, split[part], members)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(cache, logits=logits.astype(np.float32), target=target.astype(np.uint8))
    print(part, len(split[part]), "chips", round(time.time() - start), "s", flush=True)
    return logits, target


def summary(metrics: dict) -> dict:
    bins = [b for b in metrics["equal_width_bins"] if b.get("count") and 0.6 <= b["mean_p"] <= 0.87]
    return {**{key: metrics[key] for key in KEEP}, "bins_p_0_6_to_0_87": bins}


def main() -> None:
    platt, _ = load_calibration(CALIBRATION_PATH)
    split = load_split(SPLIT_PATH)
    members = load_members(member_dirs(MODEL_DIR))
    data = {part: logits_of(part, split, members) for part in ("valid", "test_core")}
    logits, target = data["valid"]
    index = subsample(logits.size, FIT_PIXELS, SEED)
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    iso.fit(logits[index].astype(np.float64), target[index].astype(np.float64))

    def isotonic(values: np.ndarray) -> np.ndarray:
        chunks = np.array_split(values, max(1, values.size // FIT_PIXELS))
        return np.concatenate([iso.predict(chunk.astype(np.float64)).astype(np.float32) for chunk in chunks])

    record = {
        "command": "uv run python scripts/isotonic_check.py",
        "rule": ("isotonic regression (sklearn, increasing, clipped to [0,1]) of the label on the ensemble mean logit, "
                 f"fitted on <= {FIT_PIXELS:,} validation pixels (seed {SEED}, the Platt subsample rule); test_core "
                 "scored once after the fit, never used for any choice"),
        "status": ("verified alternative, not shipped: the service keeps Platt with its validation-chosen mask "
                   "threshold 0.41 and doubt threshold"),
        "parameters": {"breakpoints": int(iso.X_thresholds_.size),
                       "mean_logit": [round(float(x), 6) for x in iso.X_thresholds_],
                       "probability": [round(float(y), 6) for y in iso.y_thresholds_]},
    }
    for part, (values, labels) in data.items():
        record[part] = {"pixels": int(labels.size),
                        "platt": summary(calibration_metrics(platt.apply(values), labels)),
                        "isotonic": summary(calibration_metrics(isotonic(values), labels))}
    OUT.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print(json.dumps({part: {name: {key: record[part][name][key] for key in ("ece_equal_width", "ece_mid_range", "brier")}
                             for name in ("platt", "isotonic")} for part in ("valid", "test_core")}, indent=1))


if __name__ == "__main__":
    main()
