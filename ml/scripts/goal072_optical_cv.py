"""Event-disjoint S1+S2 validation using the existing radar CV controls."""
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from scipy.special import expit, logit
from sklearn.ensemble import HistGradientBoostingClassifier

from floodvalue import paths
from floodvalue.calibration import best_prob_threshold
from floodvalue.chips import Chip, read_s1, read_label, s1_valid
from floodvalue.cv import score_arrays
from floodvalue.cv_blend import blend_arrays
from floodvalue.cv_calibration import fit_calibrator
from floodvalue.optical import (DATE_POLICY, event_dates, optical_date_allowed, read_optical,
                                spectral_features, predict_optical)
from floodvalue.split import load_split

ROOT = Path("runs/goal072/optical_cv")


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    plan_path = Path("research/cv_plan.json")
    plan = json.loads(plan_path.read_text())
    plan_sha = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    split = load_split(paths.ARTIFACTS_DIR/"split.json")
    prohibited = set(split["test_core"]+split["test_adjacent"]+split["bolivia"])
    data_root = paths.SEN1FLOODS11_DIR
    dates = event_dates(data_root)
    eligible = {event: optical_date_allowed(**d) for event, d in dates.items()}
    samples = {}
    rows = []
    for fold in plan["folds"]:
        index = fold["fold"]
        for part in ["train", "valid", "score"]:
            if set(fold[part]) & prohibited:
                raise ValueError("final test chip present in development CV")
        train = [n for n in fold["train"] if eligible[Chip.from_name(n).event]]
        directory = ROOT/f"fold{index}"
        directory.mkdir(exist_ok=True)
        checkpoint = directory/"model.joblib"
        provenance = {"plan_sha256": plan_sha, "train_chips": train,
                      "valid_chips": fold["valid"], "score_chips": fold["score"],
                      "date_policy": DATE_POLICY, "seed": 72, "pixels_per_chip": 3500,
                      "max_iter": 500, "learning_rate": .06, "max_leaf_nodes": 31}
        if checkpoint.exists():
            if json.loads((directory/"provenance.json").read_text()) != provenance:
                raise ValueError("CV optical resume configuration differs")
            model = joblib.load(checkpoint)
        else:
            for name in train:
                if name in samples:
                    continue
                chip = Chip.from_name(name)
                s1, label = read_s1(data_root, chip), read_label(data_root, chip)
                f, available = spectral_features(s1, read_optical(data_root, chip))
                idx = np.flatnonzero(s1_valid(s1) & (label != -1) & available)
                # Per-chip seed is stable across folds; it uses no labels or event statistics.
                seed = int.from_bytes(hashlib.sha256(name.encode()).digest()[:4], "little")
                rng = np.random.default_rng(seed)
                idx = rng.choice(idx, size=min(3500, len(idx)), replace=False)
                samples[name] = (f.reshape(-1, f.shape[-1])[idx], label.ravel()[idx] == 1)
            x = np.concatenate([samples[n][0] for n in train])
            y = np.concatenate([samples[n][1] for n in train])
            print("fit fold", index, x.shape, flush=True)
            model = HistGradientBoostingClassifier(max_iter=500, learning_rate=.06, max_leaf_nodes=31,
                                                   min_samples_leaf=40, l2_regularization=5., random_state=72,
                                                   early_stopping=True, validation_fraction=.1, n_iter_no_change=30)
            model.fit(x, y)
            joblib.dump(model, checkpoint, compress=3)
            (directory/"provenance.json").write_text(json.dumps(provenance, indent=2))
            del x, y
        parts = {}
        for part in ["valid", "score"]:
            controls = []
            for seed in [0, 1]:
                run = Path(f"runs/cv/cv_r34_control/seed{seed}/fold{index}")
                with np.load(run/(part+"_predictions.npz")) as data:
                    controls.append({k: data[k] for k in data.files})
            base = controls[0]
            if base["names"].tolist() != fold[part]:
                raise ValueError("radar CV cache does not match the event plan")
            base_p = expit(blend_arrays(controls, [1., 1.], "probabilities"))
            del controls
            archive = directory/(part+"_optical.npz")
            if not archive.exists():
                optical, available = [], []
                for i, name in enumerate(fold[part]):
                    chip = Chip.from_name(name)
                    s1 = read_s1(data_root, chip)
                    if eligible[chip.event]:
                        p, mask = predict_optical(model, s1, read_optical(data_root, chip))
                    else:
                        p, mask = np.zeros(s1.shape[1:], np.float32), np.zeros(s1.shape[1:], bool)
                    optical.append(p); available.append(mask)
                    if i % 30 == 0:
                        print("predict", index, part, i, flush=True)
                np.savez_compressed(archive, names=fold[part], probability=np.stack(optical),
                                    available=np.stack(available))
            with np.load(archive) as data:
                if data["names"].tolist() != fold[part]:
                    raise ValueError("optical CV cache names differ")
                parts[part] = {**base, "base_p": base_p, "optical_p": data["probability"],
                               "optical_available": data["available"]}
        for alpha in [0., .25, .5, .75, 1.]:
            destination = directory/f"result_alpha{alpha:.2f}.json"
            if destination.exists():
                result = json.loads(destination.read_text())
            else:
                arrays = {}
                for part, a in parts.items():
                    p = np.where(a["optical_available"], (1-alpha)*a["base_p"]+alpha*a["optical_p"], a["base_p"])
                    arrays[part] = logit(np.clip(p, 1e-6, 1-1e-6)).astype(np.float32)
                inner, outer = parts["valid"], parts["score"]
                mask = inner["valid"]
                cal = fit_calibrator(arrays["valid"][mask], inner["target"][mask], "isotonic")
                threshold, _ = best_prob_threshold(cal.apply(arrays["valid"][mask]), inner["target"][mask])
                score = score_arrays(outer["names"].tolist(), arrays["score"], outer["target"], outer["valid"], cal, threshold)
                result = {"fold": index, "optical_weight": alpha, "score": score,
                          "calibration": cal.as_dict(), "threshold": threshold, "provenance": provenance}
                destination.write_text(json.dumps(result, indent=2, allow_nan=False))
            row = {"fold": index, "alpha": alpha, "iou": result["score"]["overall"]["micro"]["iou"],
                   "event_mean_iou": result["score"]["event_mean_iou"],
                   "brier": result["score"]["probability"]["brier"], "result": str(destination)}
            rows.append(row)
            (ROOT/"results.json").write_text(json.dumps(rows, indent=2))
            print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
