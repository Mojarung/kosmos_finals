"""Larger optical boosting experiments on fixed chip/event development splits."""
import argparse
import gc
import hashlib
import json
from pathlib import Path
import time

import joblib
import numpy as np
from scipy.special import expit, logit
from sklearn.ensemble import HistGradientBoostingClassifier

from floodvalue import paths
from floodvalue.chips import Chip, read_label, read_s1, s1_valid
from floodvalue.optical import event_dates, optical_date_allowed, read_optical, spectral_features, predict_optical
from floodvalue.refine import combine
from floodvalue.cv_blend import blend_arrays
from floodvalue.cv_calibration import fit_calibrator
from floodvalue.calibration import best_prob_threshold
from floodvalue.cv import score_arrays
from floodvalue.split import load_split

ROOT = Path("runs/next")
CONFIGS = {
    "hgb_large": {"backend": "hgb", "iterations": 1200, "lr": .03, "seed": 73},
    "hgb_hard": {"backend": "hgb", "iterations": 1200, "lr": .03, "seed": 73, "hard": True},
    "lightgbm": {"backend": "lightgbm", "iterations": 2000, "lr": .035, "seed": 74},
    "catboost": {"backend": "catboost", "iterations": 1500, "lr": .04, "seed": 75},
}


def permitted(names):
    dates = event_dates(paths.SEN1FLOODS11_DIR)
    return [n for n in names if optical_date_allowed(**dates[Chip.from_name(n).event])]


def check_development(parts):
    split = load_split(paths.ARTIFACTS_DIR/"split.json")
    allowed = set(split["train"]+split["valid"])
    if any(not set(names) <= allowed for names in parts.values()):
        raise ValueError("final-test chip in development experiment")
    if set(parts["train"]) & (set(parts["valid"]) | set(parts.get("score", []))):
        raise ValueError("overlapping development parts")
    if set(parts["valid"]) & set(parts.get("score", [])):
        raise ValueError("overlapping development parts")
    if "score" in parts:
        fitting_events = {Chip.from_name(n).event for n in parts["train"]+parts["valid"]}
        scoring_events = {Chip.from_name(n).event for n in parts["score"]}
        if fitting_events & scoring_events:
            raise ValueError("outer scoring events overlap fitting events")


def samples(name):
    folder = ROOT/"samples12000"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/(name+".npz")
    if not path.exists():
        chip = Chip.from_name(name)
        s1, label = read_s1(paths.SEN1FLOODS11_DIR, chip), read_label(paths.SEN1FLOODS11_DIR, chip)
        f, available = spectral_features(s1, read_optical(paths.SEN1FLOODS11_DIR, chip))
        eligible = np.flatnonzero(s1_valid(s1) & (label != -1) & available)
        seed = int.from_bytes(hashlib.sha256(("next73:"+name).encode()).digest()[:4], "little")
        idx = np.random.default_rng(seed).choice(eligible, size=min(12000, len(eligible)), replace=False)
        np.savez_compressed(path, x=f.reshape(-1, f.shape[-1])[idx], y=label.ravel()[idx] == 1,
                            indices=idx, name=name, feature_version="optical47_v1")
    with np.load(path) as a:
        if str(a["name"]) != name or str(a["feature_version"]) != "optical47_v1":
            raise ValueError("feature cache provenance differs")
        return a["x"], a["y"]


def sample_set(names, count):
    rows = [samples(n) for n in permitted(names)]
    return np.concatenate([x[:count] for x,y in rows]), np.concatenate([y[:count] for x,y in rows])


def train_model(name, parts, directory):
    check_development(parts)
    config = {**CONFIGS[name], "name": name, "samples_per_chip": 10000, "feature_version": "optical47_v1",
              "early_stopping": "original inner validation chips, 3000 sampled pixels/chip, log loss"}
    signature = {"config": config, "train_chips": permitted(parts["train"]), "valid_chips": parts["valid"]}
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory/"model.joblib"
    if model_path.exists():
        if json.loads((directory/"provenance.json").read_text()) != signature:
            raise ValueError("boosting resume configuration differs")
        return joblib.load(model_path)
    started = time.monotonic()
    x, y = sample_set(parts["train"], 12000 if config.get("hard") else 10000)
    xv, yv = sample_set(parts["valid"], 3000)
    if config.get("hard"):
        # Pilot sees this fold's training labels only; no global baseline or outer labels.
        px, py = sample_set(parts["train"], 3000)
        pilot = HistGradientBoostingClassifier(max_iter=200, max_leaf_nodes=15, learning_rate=.06,
                                               min_samples_leaf=100, l2_regularization=5,
                                               random_state=73, early_stopping=False).fit(px, py)
        del px, py
        rows = []
        offset = 0
        for chip in permitted(parts["train"]):
            fx, fy = samples(chip)
            n = len(fy)
            regular = np.arange(min(8000, n))
            tail = np.arange(min(8000, n), n)
            negatives = tail[~fy[tail]]
            if len(negatives):
                p = pilot.predict_proba(fx[negatives])[:, 1]
                hard = negatives[np.argsort(p)[-min(2000, len(p)):]]
            else:
                hard = np.empty(0, int)
            rows.append(np.concatenate([regular, hard])+offset)
            offset += n
        idx = np.concatenate(rows)
        x, y = x[idx], y[idx]
        del pilot
    print(json.dumps({"train": str(directory), "rows": len(y), "valid_rows": len(yv),
                      "backend": config["backend"]}), flush=True)
    if config["backend"] == "hgb":
        model = HistGradientBoostingClassifier(max_iter=config["iterations"], learning_rate=config["lr"],
                    max_leaf_nodes=31, min_samples_leaf=100, l2_regularization=5.,
                    random_state=config["seed"], early_stopping=True, n_iter_no_change=60, tol=1e-6)
        model.fit(x, y, X_val=xv, y_val=yv)
        iterations = int(model.n_iter_)
    elif config["backend"] == "lightgbm":
        from lightgbm import LGBMClassifier, early_stopping, log_evaluation
        model = LGBMClassifier(n_estimators=config["iterations"], learning_rate=config["lr"], num_leaves=31,
                    min_child_samples=200, reg_lambda=5., colsample_bytree=.9, subsample=.8,
                    subsample_freq=1, n_jobs=4, random_state=config["seed"], verbosity=-1)
        model.fit(x, y, eval_set=[(xv, yv)], eval_metric="binary_logloss",
                  callbacks=[early_stopping(80, verbose=False), log_evaluation(200)])
        iterations = int(model.best_iteration_)
    else:
        from catboost import CatBoostClassifier
        model = CatBoostClassifier(iterations=config["iterations"], learning_rate=config["lr"], depth=6,
                    l2_leaf_reg=5., random_seed=config["seed"], thread_count=4, loss_function="Logloss",
                    allow_writing_files=False, nan_mode="Min", verbose=200)
        model.fit(x, y, eval_set=(xv, yv), early_stopping_rounds=80, use_best_model=True)
        iterations = int(model.tree_count_)
    temporary = model_path.with_suffix(".tmp")
    joblib.dump(model, temporary, compress=3)
    (directory/"config.json").write_text(json.dumps(config, indent=2))
    (directory/"provenance.json").write_text(json.dumps(signature, indent=2))
    (directory/"train_summary.json").write_text(json.dumps({**signature, "sampled_training_pixels": len(y),
                         "iterations": iterations, "seconds": time.monotonic()-started}, indent=2))
    temporary.replace(model_path)
    print(json.dumps({"trained": str(directory), "iterations": iterations, "seconds": time.monotonic()-started}), flush=True)
    return model


def prediction_cache(model, directory, names, part):
    path = directory/(part+"_optical.npz")
    fingerprint = hashlib.sha256((directory/"model.joblib").read_bytes()).hexdigest()
    metadata = {"names": names, "weights_sha256": fingerprint, "part": part, "feature_version": "optical47_v1"}
    if path.exists():
        if json.loads(path.with_suffix(".json").read_text()) != metadata:
            raise ValueError("prediction cache differs")
        return path
    dates = event_dates(paths.SEN1FLOODS11_DIR)
    probabilities, available = [], []
    for i, name in enumerate(names):
        chip = Chip.from_name(name)
        s1 = read_s1(paths.SEN1FLOODS11_DIR, chip)
        if optical_date_allowed(**dates[chip.event]):
            p, mask = predict_optical(model, s1, read_optical(paths.SEN1FLOODS11_DIR, chip))
        else:
            p, mask = np.zeros(s1.shape[1:], np.float32), np.zeros(s1.shape[1:], bool)
        probabilities.append(p); available.append(mask)
        if i % 20 == 0:
            print("predict", str(directory), part, i, flush=True)
    np.savez_compressed(path, names=names, probability=np.stack(probabilities), available=np.stack(available))
    path.with_suffix(".json").write_text(json.dumps(metadata, indent=2))
    return path


def radar_arrays(fold, part):
    if fold is None:
        recipe = json.loads(Path("runs/goal072/recipes/v1_weighted_probability_mean.json").read_text())
        a = combine([Path(f"runs/refine/predictions/v1_m{i}_rot4_valid.npz") for i in range(5)],
                    recipe["weights"], recipe["space"])
    else:
        arrays = []
        for seed in [0, 1]:
            with np.load(f"runs/cv/cv_r34_control/seed{seed}/fold{fold}/{part}_predictions.npz") as f:
                arrays.append({k: f[k] for k in f.files})
        a = {**arrays[0], "logits": blend_arrays(arrays, [1., 1.], "probabilities")}
    a["probability"] = expit(a["logits"])
    return a


def evaluate(directory, fold=None):
    parts = ["valid"] if fold is None else ["valid", "score"]
    arrays = {part: radar_arrays(fold, part) for part in parts}
    for part, a in arrays.items():
        with np.load(directory/(part+"_optical.npz")) as f:
            if not np.array_equal(f["names"], a["names"]):
                raise ValueError("radar and optical prediction names differ")
            a["optical"], a["available"] = f["probability"], f["available"]
    rows = []
    for alpha in [.5, .75, 1.]:
        path = directory/f"result_alpha{alpha:.2f}.json"
        if path.exists():
            r = json.loads(path.read_text())
        else:
            zs = {part: logit(np.clip(np.where(a["available"], alpha*a["optical"]+(1-alpha)*a["probability"],
                                               a["probability"]), 1e-6, 1-1e-6)).astype(np.float32)
                  for part, a in arrays.items()}
            inner = arrays["valid"]; mask = inner["valid"]
            cal = fit_calibrator(zs["valid"][mask], inner["target"][mask], "isotonic")
            threshold, _ = best_prob_threshold(cal.apply(zs["valid"][mask]), inner["target"][mask])
            part = "valid" if fold is None else "score"
            a = arrays[part]
            score = score_arrays(a["names"].tolist(), zs[part], a["target"], a["valid"], cal, threshold)
            r = {"alpha": alpha, "calibration": cal.as_dict(), "threshold": threshold,
                 "calibration_method": "isotonic", "fold": fold, "score": score}
            path.write_text(json.dumps(r, indent=2, allow_nan=False))
        rows.append({"path": str(path), "alpha": alpha, "iou": r["score"]["overall"]["micro"]["iou"],
                     "event_mean_iou": r["score"]["event_mean_iou"], "brier": r["score"]["probability"]["brier"]})
    (directory/"ranking.json").write_text(json.dumps(rows, indent=2))
    print(json.dumps({"finished": str(directory), "ranking": rows}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", nargs="+", choices=list(CONFIGS), default=list(CONFIGS))
    parser.add_argument("--stage", choices=["full", "cv"], default="full")
    args = parser.parse_args()
    split = load_split(paths.ARTIFACTS_DIR/"split.json")
    plan = json.loads(Path("research/cv_plan.json").read_text())
    specs = [(None, {"train": split["train"], "valid": split["valid"]})] if args.stage == "full" else [
            (f["fold"], {k: f[k] for k in ["train", "valid", "score"]}) for f in plan["folds"]]
    for name in args.names:
        for fold, parts in specs:
            directory = ROOT/"boost"/name/("full" if fold is None else f"fold{fold}")
            if (directory/"ranking.json").exists():
                continue
            model = train_model(name, parts, directory)
            for part in ["valid"] if fold is None else ["valid", "score"]:
                prediction_cache(model, directory, parts[part], part)
            del model
            gc.collect()
            evaluate(directory, fold)


if __name__ == "__main__":
    main()
