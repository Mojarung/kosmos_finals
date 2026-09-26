"""Check radar blend weights .25 and 1 using the same independently trained CV models."""
import gc
import json
from pathlib import Path
import time

import numpy as np
from scipy.special import expit

from floodvalue.calibration import best_prob_threshold
from floodvalue.cv import score_arrays
from floodvalue.cv_calibration import fit_calibrator
from next_fusion import load_arrays, apply
from next_radar_cv import SPECS

ROOT = Path("runs/next/radar_cv")
WEIGHTS = {"w025": .25, "w100": 1.}


def main():
    decision_path = ROOT/"decision.json"
    while not decision_path.exists() or json.loads(decision_path.read_text())["status"] not in ["complete", "rejected"]:
        time.sleep(10)
    decision = json.loads(decision_path.read_text())
    if decision["status"] == "rejected":
        return
    selected = decision["selected"]
    model, tta = selected["model"], selected["tta"]
    metadata = json.loads(Path(f"runs/goal072/predictions/{model}_seed0_{tta}_valid.json").read_text())
    for tag, weight in WEIGHTS.items():
        base = json.loads(Path("runs/goal072/recipes/v1_weighted_probability_mean.json").read_text())
        base["members"].append(metadata)
        base["weights"] = ((1-weight)*np.asarray(base["weights"])).tolist()+[weight]
        screen = json.loads(Path(f"runs/next/radar_screen/{model}_{tta}_{weight:.2f}_radar.json").read_text())
        base.update(calibration=screen["calibration"], threshold=screen["threshold"], validation=screen["score"],
                    rule="Radar weight grid on original validation and event CV; no new final-test predictions")
        base_path = ROOT/("base_recipe_"+tag+".json")
        base_path.write_text(json.dumps(base, indent=2))
        for parent, members in SPECS.items():
            name = parent+"_"+tag
            for fold in [0, 1, 2, None]:
                out = ROOT/name/("full" if fold is None else f"fold{fold}")/"result.json"
                if out.exists():
                    continue
                source = ROOT/parent/("full" if fold is None else f"fold{fold}")/"result.json"
                learned = json.loads(source.read_text())["learned"]
                def arrays(part):
                    a = load_arrays(members, fold, part)
                    path = Path(f"runs/goal072/predictions/{model}_seed0_{tta}_valid.npz") if fold is None else (
                        ROOT/f"fold{fold}"/("rot4" if tta == "rot4" else "")/(part+"_predictions.npz"))
                    with np.load(path) as z:
                        for key in ["names", "target", "valid"]:
                            if not np.array_equal(a[key], z[key]):
                                raise ValueError("radar weight search pixels differ")
                        a["probability"] = (1-weight)*a["probability"]+weight*expit(z["logits"])
                    return a
                inner = arrays("valid")
                z = apply(inner, learned)
                mask = inner["valid"]
                cal = fit_calibrator(z[mask], inner["target"][mask], "isotonic")
                threshold, _ = best_prob_threshold(cal.apply(z[mask]), inner["target"][mask])
                if fold is None:
                    a = inner
                else:
                    del inner, z, mask
                    gc.collect()
                    a = arrays("score")
                    z = apply(a, learned)
                score = score_arrays(a["names"].tolist(), z, a["target"], a["valid"], cal, threshold)
                result = {"name": name, "fold": fold, "learned": learned, "base_recipe": str(base_path),
                          "radar_weight": weight, "radar_selection": decision, "calibration_method": "isotonic",
                          "calibration": cal.as_dict(), "threshold": threshold, "score": score}
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(result, indent=2, allow_nan=False))
                print(json.dumps({"candidate": name, "fold": fold, "iou": score["overall"]["micro"]["iou"],
                                  "event_mean_iou": score["event_mean_iou"]}), flush=True)
                del a, z
                gc.collect()


if __name__ == "__main__":
    main()
