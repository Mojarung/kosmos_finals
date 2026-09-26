"""Evaluate separate optical/fallback calibration using the unchanged event folds."""
import gc
import json
from pathlib import Path
import time

import numpy as np

from floodvalue.calibration import best_prob_threshold
from floodvalue.cv import score_arrays
from floodvalue.fusion_calibration import fit_branch_calibration, BranchCalibration
from next_fusion import load_arrays, apply

ROOT = Path("runs/next")
PARENTS = {"old_branchcal": "control", "boost_branchcal": "boost_learned", "all_branchcal": "all_learned"}


def parent_path(parent, fold):
    if parent == "control":
        return Path("runs/goal072/optical/recipe_alpha0.75.json") if fold is None else Path(f"runs/goal072/optical_cv/fold{fold}/result_alpha0.75.json")
    return ROOT/"fusion"/parent/("full" if fold is None else f"fold{fold}")/"result.json"


def main():
    for name, parent in PARENTS.items():
        for fold in [0, 1, 2, None]:
            destination = ROOT/"fusion"/name/("full" if fold is None else f"fold{fold}")/"result.json"
            if destination.exists():
                continue
            source = parent_path(parent, fold)
            while not source.exists():
                time.sleep(10)
            original = json.loads(source.read_text())
            learned = original.get("learned", {"members": ["old"], "weights": [1.], "gate": None, "alpha": .75})
            inner = load_arrays(learned["members"], fold, "valid")
            z = apply(inner, learned)
            parameters = fit_branch_calibration(z, inner["target"], inner["valid"], inner["available"])
            cal = BranchCalibration(parameters, inner["available"])
            p = cal.apply(z)
            m = inner["valid"]
            threshold, _ = best_prob_threshold(p[m], inner["target"][m])
            if fold is None:
                a = inner
            else:
                del inner, z, p, cal, m
                gc.collect()
                a = load_arrays(learned["members"], fold, "score")
                z = apply(a, learned)
            cal = BranchCalibration(parameters, a["available"])
            score = score_arrays(a["names"].tolist(), z, a["target"], a["valid"], cal, threshold)
            result = {"name": name, "fold": fold, "learned": learned, "parent": str(source),
                      "calibration_method": "branch_isotonic", "calibration": parameters,
                      "threshold": threshold, "score": score,
                      "fitting": "separate isotonic calibration by optical availability on inner validation only"}
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(result, indent=2, allow_nan=False))
            print(json.dumps({"name": name, "fold": fold, "iou": score["overall"]["micro"]["iou"],
                              "event_mean_iou": score["event_mean_iou"]}), flush=True)
            del a, z, cal
            gc.collect()


if __name__ == "__main__":
    main()
