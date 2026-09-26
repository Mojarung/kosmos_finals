"""Five spatial-fold OOF comparison of service L2A pixel boosting; no test command."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import time

import joblib
import numpy as np
from scipy.ndimage import uniform_filter
from scipy.special import expit, logit

from floodvalue import paths
from floodvalue.chips import Chip, read_s1, read_label, s1_valid
from floodvalue.l2a_data import ROOT as DATA, development
from floodvalue.tabular import texture_features

ROOT = paths.RUNS_DIR/"l2a_boost"
VERSION = "s1texture23_l2a12_indices5_local6_usable1_v1"
CONFIG = {
    "hgb": {"max_iter": 900, "learning_rate": .04, "max_leaf_nodes": 31,
            "min_samples_leaf": 100, "l2_regularization": 5., "random_state": 2026},
    "lightgbm": {"n_estimators": 1600, "learning_rate": .04, "num_leaves": 31,
                 "min_child_samples": 200, "reg_lambda": 5., "colsample_bytree": .9,
                 "subsample": .8, "subsample_freq": 1, "n_jobs": 4, "random_state": 2026, "verbosity": -1},
    "catboost": {"iterations": 1200, "learning_rate": .04, "depth": 6, "l2_leaf_reg": 5.,
                 "random_seed": 2026, "thread_count": 4, "loss_function": "Logloss",
                 "allow_writing_files": False, "nan_mode": "Min", "verbose": 200},
}


def features(s1, reflectance, usable):
    if reflectance.shape != (12, *s1.shape[1:]) or usable.shape != s1.shape[1:]:
        raise ValueError("L2A must contain 12 bands on the S1 grid")
    usable = usable & np.isfinite(reflectance).all(axis=0)
    x = np.clip(np.nan_to_num(reflectance), 0, 2).astype(np.float32)
    blue, green, red, nir, swir1, swir2 = x[[1, 2, 3, 7, 10, 11]]
    indices = [(green-nir)/(green+nir+1e-6), (green-swir1)/(green+swir1+1e-6),
               (nir-red)/(nir+red+1e-6), blue+2.5*green-1.5*(nir+swir1)-.25*swir2,
               (nir-swir1)/(nir+swir1+1e-6)]
    optical = list(x)+indices
    count = usable.astype(np.float32)
    for size in (3, 9):
        n = np.maximum(uniform_filter(count, size, mode="reflect"), 1e-6)
        for value in indices[:3]:
            optical.append(uniform_filter(value*count, size, mode="reflect")/n)
    optical = np.stack(optical, axis=-1)
    optical[~usable] = np.nan
    return np.concatenate([texture_features(s1), optical, count[..., None]], axis=-1)


def split_fold(fold, fold_of):
    outer = sorted(n for n, k in fold_of.items() if k == fold)
    inner = sorted(n for n, k in fold_of.items() if k == (fold+1) % 5)
    pilot = sorted(n for n, k in fold_of.items() if k not in (fold, (fold+1) % 5))
    if not outer or not inner or not pilot or (set(outer) & (set(inner)|set(pilot))):
        raise ValueError("invalid nested fold")
    return pilot, inner, sorted(pilot+inner), outer


def raw(name):
    if name not in development():
        raise ValueError("only development data may be read by this experiment")
    chip = Chip.from_name(name)
    s1, label = read_s1(paths.SEN1FLOODS11_DIR, chip), read_label(paths.SEN1FLOODS11_DIR, chip)
    with np.load(DATA/"chips"/(name+".npz")) as z:
        x = features(s1, z["reflectance"].astype(np.float32), z["usable"])
        usable = z["usable"]
    return x, label == 1, s1_valid(s1) & (label != -1), usable


def samples(name):
    folder = ROOT/"samples"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder/(name+".npz")
    if not p.exists():
        x, y, valid, _ = raw(name)
        idx = np.flatnonzero(valid)
        seed = int.from_bytes(hashlib.sha256(("l2a2026:"+name).encode()).digest()[:4], "little")
        idx = np.random.default_rng(seed).choice(idx, size=min(6000, len(idx)), replace=False)
        np.savez_compressed(p, x=x.reshape(-1, x.shape[-1])[idx], y=y.ravel()[idx], indices=idx)
    with np.load(p) as z:
        return z["x"], z["y"]


def sample_set(names):
    rows = [samples(n) for n in names]
    return np.concatenate([x for x, _ in rows]), np.concatenate([y for _, y in rows])


def fit(kind, x, y, validation=None, iterations=None):
    config = CONFIG[kind].copy()
    if kind == "hgb":
        from sklearn.ensemble import HistGradientBoostingClassifier
        config.update(early_stopping=validation is not None, n_iter_no_change=60, tol=1e-6)
        if iterations:
            config["max_iter"] = iterations
        model = HistGradientBoostingClassifier(**config)
        model.fit(x, y, **({"X_val": validation[0], "y_val": validation[1]} if validation else {}))
        n = model.n_iter_
    elif kind == "lightgbm":
        from lightgbm import LGBMClassifier, early_stopping, log_evaluation
        if iterations:
            config["n_estimators"] = iterations
        model = LGBMClassifier(**config)
        model.fit(x, y, **({"eval_set": [validation], "eval_metric": "binary_logloss",
                           "callbacks": [early_stopping(80, verbose=False), log_evaluation(200)]} if validation else {}))
        n = model.best_iteration_ if validation else iterations
    else:
        from catboost import CatBoostClassifier
        if iterations:
            config["iterations"] = iterations
        model = CatBoostClassifier(**config)
        model.fit(x, y, **({"eval_set": validation, "early_stopping_rounds": 80, "use_best_model": True} if validation else {}))
        n = model.tree_count_
    return model, int(n)


def train(kind, fold, fold_of):
    pilot, inner, all_train, outer = split_fold(fold, fold_of)
    directory = ROOT/kind/f"fold{fold}"
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint = directory/"model.joblib"
    if checkpoint.exists():
        return joblib.load(checkpoint), outer
    started = time.monotonic()
    selection = directory/"iterations.json"
    if selection.exists():
        iterations = json.loads(selection.read_text())["iterations"]
    else:
        x, y = sample_set(pilot)
        xv, yv = sample_set(inner)
        model, iterations = fit(kind, x, y, (xv, yv))
        selection.write_text(json.dumps({"iterations": iterations, "pilot_train": pilot, "inner_valid": inner,
                                         "outer": outer}, indent=2))
        del x, y, xv, yv, model
        gc.collect()
    x, y = sample_set(all_train)
    model, _ = fit(kind, x, y, iterations=iterations)
    joblib.dump(model, checkpoint.with_suffix(".tmp"), compress=3)
    checkpoint.with_suffix(".tmp").replace(checkpoint)
    summary = {"kind": kind, "fold": fold, "iterations": iterations, "train": all_train,
               "outer": outer, "rows": len(y), "seconds": time.monotonic()-started,
               "weights_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
    (directory/"training.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: summary[k] for k in ("kind", "fold", "iterations", "rows", "seconds")}), flush=True)
    return model, outer


def predict(kind, fold, model, names):
    out = ROOT/kind/"oof"
    out.mkdir(exist_ok=True)
    for i, name in enumerate(names):
        path = out/(name+".npz")
        if path.exists():
            continue
        x, _, _, _ = raw(name)
        flat = x.reshape(-1, x.shape[-1])
        prob = np.concatenate([model.predict_proba(flat[j:j+65536])[:, 1] for j in range(0, len(flat), 65536)])
        with path.with_suffix(".tmp").open("wb") as f:
            np.savez_compressed(f, probability=prob.reshape(x.shape[:2]).astype(np.float32), name=name, fold=fold)
        path.with_suffix(".tmp").replace(path)
        if i % 20 == 0:
            print("oof", kind, fold, i, flush=True)


def evaluate(kinds):
    from floodvalue.context_final import fit_platt, iou_curve, THRESHOLDS
    from floodvalue.metrics import Confusion, confusion

    fold_of = development()
    recipes = {k: [k] for k in kinds}
    if len(kinds) > 1:
        recipes["mean"] = kinds
    results = {}
    for name, members in recipes.items():
        predictions, truths = [], []
        for chip_name in sorted(fold_of):
            chip = Chip.from_name(chip_name)
            label = read_label(paths.SEN1FLOODS11_DIR, chip)
            valid = s1_valid(read_s1(paths.SEN1FLOODS11_DIR, chip)) & (label != -1)
            probabilities = []
            for member in members:
                with np.load(ROOT/member/"oof"/(chip_name+".npz")) as z:
                    assert str(z["name"]) == chip_name and int(z["fold"]) == fold_of[chip_name]
                    probabilities.append(z["probability"])
            p = np.mean(probabilities, axis=0)
            predictions.append(p[valid]); truths.append(label[valid] == 1)
        p, y = np.concatenate(predictions), np.concatenate(truths)
        logits = logit(np.clip(p, 1e-6, 1-1e-6))
        a, b = fit_platt(logits, y)
        calibrated = expit(a*logits+b)
        curve = iou_curve(calibrated, y)
        threshold = float(THRESHOLDS[curve.argmax()])
        per_event, slices = {}, {"usable_optics": Confusion(), "missing_optics": Confusion()}
        offset = 0
        for chip_name, chunk_y in zip(sorted(fold_of), truths):
            chunk_p = calibrated[offset:offset+len(chunk_y)]
            chip = Chip.from_name(chip_name)
            c = confusion(chunk_p >= threshold, chunk_y, np.ones(chunk_y.shape, bool))
            per_event[chip.event] = per_event.get(chip.event, Confusion())+c
            label = read_label(paths.SEN1FLOODS11_DIR, chip)
            valid = s1_valid(read_s1(paths.SEN1FLOODS11_DIR, chip)) & (label != -1)
            with np.load(DATA/"chips"/(chip_name+".npz")) as z:
                usable = z["usable"][valid]
            slices["usable_optics"] += confusion(chunk_p >= threshold, chunk_y, usable)
            slices["missing_optics"] += confusion(chunk_p >= threshold, chunk_y, ~usable)
            offset += len(chunk_y)
        global_counts = sum(per_event.values(), Confusion())
        result = {"members": members, "platt": {"a": a, "b": b}, "threshold": threshold,
                  "pooled_oof": global_counts.as_dict(), "raw_iou_p05": confusion(p >= .5, y, np.ones(y.shape, bool)).iou,
                  "raw_brier": float(np.mean((p-y)**2)), "oof_fitted_brier": float(np.mean((calibrated-y)**2)),
                  "event_mean_iou": float(np.mean([c.iou for c in per_event.values() if c.iou is not None])),
                  "events": {e: c.as_dict() for e, c in per_event.items()}, "slices": {s: c.as_dict() for s, c in slices.items()},
                  "calibration_evaluation": "resubstitution on OOF labels, for development selection only"}
        results[name] = result
        print("RESULT", name, result["pooled_oof"]["iou"], result["event_mean_iou"], flush=True)
    out = paths.PIPELINE_ROOT/"research/L2A_BOOST_RESULTS.json"
    report = {"protocol": "research/L2A_BOOST_PROTOCOL.md", "results": results, "new_test_evaluation": False,
              "context_reference_oof": .7602354244756738, "signature": json.loads((ROOT/"signature.json").read_text())}
    out.write_text(json.dumps(report, indent=2, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", choices=list(CONFIG), default=list(CONFIG))
    parser.add_argument("--folds", nargs="+", type=int, choices=list(range(5)), default=list(range(5)))
    parser.add_argument("--evaluate-only", action="store_true")
    args = parser.parse_args()
    ready = json.loads((DATA/"ready.json").read_text())
    source_files = [Path(__file__), Path(__file__).with_name("l2a_data.py"), Path(__file__).with_name("tabular.py")]
    signature = {"version": VERSION, "config": CONFIG, "pixels_per_chip": 6000,
                 "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
                 "acquisition_sha256": ready["plan_sha256"],
                 "folds_sha256": hashlib.sha256((paths.PIPELINE_ROOT/"research/fresh/folds.json").read_bytes()).hexdigest()}
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT/"signature.json"
    if path.exists() and json.loads(path.read_text()) != signature:
        raise ValueError("changed experiment inputs/code; preserve old run before starting a new one")
    path.write_text(json.dumps(signature, indent=2))
    if not args.evaluate_only:
        for kind in args.models:
            for fold in args.folds:
                model, names = train(kind, fold, development())
                predict(kind, fold, model, names)
                del model
                gc.collect()
    if set(args.folds) == set(range(5)):
        evaluate(args.models)


if __name__ == "__main__":
    main()
