"""Evaluate fusion algorithms on event CV and fit full models from OOF training rows."""
import argparse
import gc
import json
from pathlib import Path
import time

import numpy as np
from scipy.special import logit

from floodvalue import paths
from floodvalue.chips import Chip
from floodvalue.optical import event_dates, optical_date_allowed, read_optical
from floodvalue.fusion import image_quality, fit_weights, fit_gate, fuse
from floodvalue.next_boost import ROOT, radar_arrays
from floodvalue.calibration import best_prob_threshold
from floodvalue.cv_calibration import fit_calibrator
from floodvalue.cv import score_arrays
from floodvalue.split import load_split

SPECS = {
    "old_gate": {"members": ["old"], "learn_weights": False, "gate": True},
    "boost_mean": {"members": ["old", "hgb_large", "lightgbm", "catboost"], "learn_weights": False, "gate": False},
    "boost_learned": {"members": ["old", "hgb_large", "lightgbm", "catboost"], "learn_weights": True, "gate": False},
    "boost_gate": {"members": ["old", "hgb_large", "lightgbm", "catboost"], "learn_weights": True, "gate": True},
    "cnn_blend": {"members": ["old", "cnn"], "learn_weights": False, "gate": False},
    "all_learned": {"members": ["old", "hgb_large", "hgb_hard", "lightgbm", "catboost", "cnn"], "learn_weights": True, "gate": False},
    "all_gate": {"members": ["old", "hgb_large", "hgb_hard", "lightgbm", "catboost", "cnn"], "learn_weights": True, "gate": True},
}


def optical_path(member, fold, part):
    if member == "old":
        return Path("runs/goal072/optical/valid_predictions.npz") if fold is None else Path(f"runs/goal072/optical_cv/fold{fold}/{part}_optical.npz")
    base = ROOT/"cnn" if member == "cnn" else ROOT/"boost"/member
    return base/("full" if fold is None else f"fold{fold}")/(part+"_optical.npz")


def quality_cache(name):
    directory = ROOT/"quality"
    directory.mkdir(exist_ok=True)
    path = directory/(name+".npz")
    if path.exists():
        with np.load(path) as a:
            return a["quality"]
    chip = Chip.from_name(name)
    dates = event_dates(paths.SEN1FLOODS11_DIR)
    q = image_quality(read_optical(paths.SEN1FLOODS11_DIR, chip)) if optical_date_allowed(**dates[chip.event]) else np.zeros((512, 512, 3), np.float32)
    np.savez_compressed(path, quality=q)
    return q


def load_arrays(members, fold, part):
    a = radar_arrays(fold, part)
    values = []
    for member in members:
        path = optical_path(member, fold, part)
        with np.load(path) as z:
            if not np.array_equal(z["names"], a["names"]):
                raise ValueError("optical fusion names differ")
            available = z["available"] if "available" in z else z["optical_available"]
            if "available" in a and not np.array_equal(a["available"], available):
                raise ValueError("optical availability differs")
            a["available"] = available
            values.append(z["probability"])
    a["components"] = values
    return a


def sample_rows(a, allowed_names=None, maximum=200000):
    allowed = set(allowed_names) if allowed_names is not None else set(a["names"].tolist())
    x, s, q, y = [], [], [], []
    count = max(1, maximum//max(1, sum(n in allowed for n in a["names"])))
    rng = np.random.default_rng(77)
    for i, name in enumerate(a["names"]):
        if name not in allowed:
            continue
        idx = np.flatnonzero(a["valid"][i] & a["available"][i])
        idx = rng.choice(idx, min(count, len(idx)), replace=False)
        if not len(idx):
            continue
        x.append(np.column_stack([p[i].ravel()[idx] for p in a["components"]]))
        s.append(a["probability"][i].ravel()[idx])
        q.append(quality_cache(str(name)).reshape(-1, 3)[idx])
        y.append(a["target"][i].ravel()[idx])
    return {"optical": np.concatenate(x), "radar": np.concatenate(s),
            "quality": np.concatenate(q), "target": np.concatenate(y)}


def learn(spec, rows):
    weights = fit_weights(rows["optical"], rows["target"]) if spec["learn_weights"] else [1/len(spec["members"])]*len(spec["members"])
    optical = rows["optical"]@weights
    gate = fit_gate(rows["radar"], optical, rows["quality"], rows["target"]) if spec["gate"] else None
    return {"members": spec["members"], "weights": weights, "gate": gate, "alpha": .75}


def apply(a, learned):
    optical = sum(w*p for w,p in zip(learned["weights"], a["components"]))
    result = []
    for i, name in enumerate(a["names"]):
        q = quality_cache(str(name)) if learned["gate"] else None
        result.append(fuse(a["probability"][i], optical[i], a["available"][i], q, learned["gate"], learned["alpha"]))
    return logit(np.stack(result)).astype(np.float32)


def evaluate(name, spec, fold, learned=None):
    directory = ROOT/"fusion"/name/("full" if fold is None else f"fold{fold}")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory/"result.json"
    if destination.exists():
        return
    inner = load_arrays(spec["members"], fold, "valid")
    if learned is None:
        learned = learn(spec, sample_rows(inner))
    zv = apply(inner, learned)
    m = inner["valid"]
    cal = fit_calibrator(zv[m], inner["target"][m], "isotonic")
    threshold, _ = best_prob_threshold(cal.apply(zv[m]), inner["target"][m])
    if fold is None:
        a, z = inner, zv
    else:
        del inner, zv
        gc.collect()
        a = load_arrays(spec["members"], fold, "score")
        z = apply(a, learned)
    score = score_arrays(a["names"].tolist(), z, a["target"], a["valid"], cal, threshold)
    result = {"name": name, "fold": fold, "learned": learned, "calibration_method": "isotonic",
              "calibration": cal.as_dict(), "threshold": threshold, "score": score,
              "fitting": "OOF original-training chips for full model; inner-validation predictions for CV meta-model; outer events never fitted"}
    destination.write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps({"fusion": name, "fold": fold, "iou": score["overall"]["micro"]["iou"],
                      "event_mean_iou": score["event_mean_iou"], "weights": learned["weights"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", nargs="+", choices=list(SPECS), default=list(SPECS))
    args = parser.parse_args()
    split = load_split(paths.ARTIFACTS_DIR/"split.json")
    for name in args.names:
        spec = SPECS[name]
        for fold in range(3):
            while not all(optical_path(member, fold, part).with_suffix(".json").exists() if member != "old" else optical_path(member, fold, part).exists()
                          for member in spec["members"] for part in ["valid", "score"]):
                time.sleep(10)
            evaluate(name, spec, fold)
        full_result = ROOT/"fusion"/name/"full/result.json"
        if full_result.exists():
            continue
        rows = []
        for fold in range(3):
            a = load_arrays(spec["members"], fold, "score")
            rows.append(sample_rows(a, split["train"], maximum=100000))
            del a
            gc.collect()
        joined = {k: np.concatenate([r[k] for r in rows]) for k in rows[0]}
        learned = learn(spec, joined)
        while not all(optical_path(member, None, "valid").exists() for member in spec["members"]):
            time.sleep(10)
        evaluate(name, spec, None, learned)


if __name__ == "__main__":
    main()
