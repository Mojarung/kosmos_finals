"""Event-CV follow-up only if a new radar mixture improves full validation."""
from dataclasses import replace
import gc
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
from scipy.special import expit

from floodvalue import paths
from floodvalue.calibration import best_prob_threshold
from floodvalue.cv import run_fold, score_arrays
from floodvalue.cv_calibration import fit_calibrator
from floodvalue.train import TrainConfig
from next_fusion import load_arrays, apply

ROOT = Path("runs/next/radar_cv")
SPECS = {"radar_old": ["old"], "radar_hgb": ["hgb_large"],
         "radar_all": ["old", "hgb_large", "hgb_hard", "lightgbm", "catboost", "cnn"]}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    decision_path = ROOT/"decision.json"
    if decision_path.exists() and json.loads(decision_path.read_text())["status"] in ["rejected", "complete"]:
        return
    while len(list(Path("runs/next/radar_screen").glob("*.json"))) != 24:
        time.sleep(10)
    rows = [json.loads(p.read_text()) for p in Path("runs/next/radar_screen").glob("*_hybrid.json")]
    selected = max(rows, key=lambda r: r["score"]["overall"]["micro"]["iou"])
    winner_iou = selected["score"]["overall"]["micro"]["iou"]
    decision = {"rule": "highest full hybrid validation IoU; event CV only if above frozen control .8547464612",
                "selected": {k: selected[k] for k in ["model", "tta", "new_weight"]},
                "valid_iou": winner_iou, "status": "cv" if winner_iou > .8547464612151694 else "rejected"}
    decision_path.write_text(json.dumps(decision, indent=2))
    print(json.dumps(decision), flush=True)
    if decision["status"] == "rejected":
        return
    # Keep neural training serial on the shared MPS device.
    while not Path("runs/next/cnn/fold2/ranking.json").exists():
        time.sleep(10)
    name, tta, weight = selected["model"], selected["tta"], selected["new_weight"]
    plan = json.loads(Path("research/cv_plan.json").read_text())
    config = replace(TrainConfig.from_json(Path("configs/goal072")/(name+".json")),
                     name="next/radar_cv/"+name, epochs=30, crops_per_chip=2, resume_training=True)
    for fold in range(3):
        directory = ROOT/f"fold{fold}"
        run_fold(plan, config, fold, paths.SEN1FLOODS11_DIR, directory)
        if tta == "rot4" and not (directory/"rot4/cv_result.json").exists():
            subprocess.run([sys.executable, "-m", "floodvalue.cv_tta", "--run", str(directory),
                            "--out", str(directory/"rot4")], check=True)
        gc.collect()
    source_base = Path("runs/goal072/recipes/v1_weighted_probability_mean.json")
    base = json.loads(source_base.read_text())
    metadata = json.loads(Path(f"runs/goal072/predictions/{name}_seed0_{tta}_valid.json").read_text())
    base["members"].append(metadata)
    base["weights"] = ((1-weight)*np.asarray(base["weights"])).tolist()+[weight]
    base["space"] = "probabilities"
    radar_score = json.loads(Path(f"runs/next/radar_screen/{name}_{tta}_{weight:.2f}_radar.json").read_text())
    base.update(calibration=radar_score["calibration"], calibration_method="isotonic",
                threshold=radar_score["threshold"], validation=radar_score["score"],
                rule="S1 mixture selected on original validation; final inclusion requires event CV; no new final-test predictions used")
    base_path = ROOT/"base_recipe.json"
    base_path.write_text(json.dumps(base, indent=2))
    for candidate, members in SPECS.items():
        for fold in [0, 1, 2, None]:
            out = ROOT/candidate/("full" if fold is None else f"fold{fold}")/"result.json"
            if out.exists():
                continue
            if candidate == "radar_all":
                parent = Path("runs/next/fusion/all_learned")/("full" if fold is None else f"fold{fold}")/"result.json"
                while not parent.exists():
                    time.sleep(10)
                learned = json.loads(parent.read_text())["learned"]
            else:
                learned = {"members": members, "weights": [1.], "gate": None, "alpha": .75}
            def arrays(part):
                a = load_arrays(members, fold, part)
                path = Path(f"runs/goal072/predictions/{name}_seed0_{tta}_valid.npz") if fold is None else (
                    ROOT/f"fold{fold}"/("rot4" if tta == "rot4" else "")/(part+"_predictions.npz"))
                with np.load(path) as z:
                    for key in ["names", "target", "valid"]:
                        if not np.array_equal(a[key], z[key]):
                            raise ValueError("radar CV pixels differ")
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
            result = {"name": candidate, "fold": fold, "learned": learned,
                      "base_recipe": str(base_path), "radar_selection": decision,
                      "calibration_method": "isotonic", "calibration": cal.as_dict(),
                      "threshold": threshold, "score": score}
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=2, allow_nan=False))
            print(json.dumps({"candidate": candidate, "fold": fold,
                              "iou": score["overall"]["micro"]["iou"], "event_mean_iou": score["event_mean_iou"]}), flush=True)
            del a, z
            gc.collect()
    decision["status"] = "complete"
    decision_path.write_text(json.dumps(decision, indent=2))


if __name__ == "__main__":
    main()
