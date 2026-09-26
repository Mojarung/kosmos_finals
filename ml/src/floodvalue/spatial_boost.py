"""Development-only multiscale optical context experiment; no final-test entry point."""
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
from scipy.special import logit

from floodvalue import paths
from floodvalue.calibration import best_prob_threshold
from floodvalue.chips import Chip, read_label, read_s1, s1_valid
from floodvalue.cv import score_arrays
from floodvalue.cv_calibration import fit_calibrator
from floodvalue.next_boost import check_development, permitted, radar_arrays
from floodvalue.optical import read_optical, spectral_features

ROOT = paths.RUNS_DIR / "spatial_boost"
VERSION = "optical47_plus18_context27_81_v1"
PARAMS = dict(n_estimators=2000, learning_rate=.035, num_leaves=31,
              min_child_samples=200, reg_lambda=5., colsample_bytree=.9,
              subsample=.8, subsample_freq=1, n_jobs=4, random_state=74, verbosity=-1)


def features(s1, s2):
    base, available = spectral_features(s1, s2)
    x = np.clip(np.nan_to_num(s2), 0, 2).astype(np.float32)
    green, red, nir, swir = x[[2, 3, 7, 11]]
    values = [(green-nir)/(green+nir+1e-6), (green-swir)/(green+swir+1e-6),
              (nir-red)/(nir+red+1e-6)]
    count = available.astype(np.float32)
    extra = []
    for size in (27, 81):
        n = np.maximum(uniform_filter(count, size, mode="reflect"), 1e-6)
        for value in values:
            mean = uniform_filter(value*count, size, mode="reflect")/n
            variance = np.maximum(uniform_filter(value**2*count, size, mode="reflect")/n-mean**2, 0)
            extra.extend([mean, np.sqrt(variance), value-mean])
    context = np.stack(extra, axis=-1)
    context[~available] = np.nan
    return np.concatenate([base, context], axis=-1), available


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _signature():
    return {"version": VERSION, "source_sha256": _digest(__file__),
            "optical_source_sha256": _digest(Path(__file__).with_name("optical.py")),
            "texture_source_sha256": _digest(Path(__file__).with_name("tabular.py")),
            "params": PARAMS, "train_pixels": 10000, "valid_pixels": 3000}


def samples(name):
    folder = ROOT / "samples"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + ".npz")
    if not path.exists():
        chip = Chip.from_name(name)
        s1, label = read_s1(paths.SEN1FLOODS11_DIR, chip), read_label(paths.SEN1FLOODS11_DIR, chip)
        f, available = features(s1, read_optical(paths.SEN1FLOODS11_DIR, chip))
        eligible = np.flatnonzero(s1_valid(s1) & (label != -1) & available)
        seed = int.from_bytes(hashlib.sha256(("next73:"+name).encode()).digest()[:4], "little")
        idx = np.random.default_rng(seed).choice(eligible, min(12000, len(eligible)), replace=False)
        np.savez_compressed(path, x=f.reshape(-1, f.shape[-1])[idx], y=label.ravel()[idx] == 1,
                            indices=idx, name=name, version=VERSION)
    with np.load(path) as a:
        if str(a["name"]) != name or str(a["version"]) != VERSION:
            raise ValueError("sample provenance differs")
        return a["x"], a["y"]


def sample_set(names, count):
    rows = [samples(n) for n in permitted(names)]
    return np.concatenate([x[:count] for x, _ in rows]), np.concatenate([y[:count] for _, y in rows])


def train(parts, directory):
    from lightgbm import LGBMClassifier, early_stopping, log_evaluation

    check_development(parts)
    signature = {**_signature(), "parts": parts}
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "provenance.json"
    if manifest.exists() and json.loads(manifest.read_text()) != signature:
        raise ValueError("experiment signature changed; use a new experiment directory")
    manifest.write_text(json.dumps(signature, indent=2))
    checkpoint = directory / "model.joblib"
    if checkpoint.exists():
        return joblib.load(checkpoint)
    start = time.monotonic()
    x, y = sample_set(parts["train"], 10000)
    xv, yv = sample_set(parts["valid"], 3000)
    model = LGBMClassifier(**PARAMS)
    model.fit(x, y, eval_set=[(xv, yv)], eval_metric="binary_logloss",
              callbacks=[early_stopping(80, verbose=False), log_evaluation(200)])
    joblib.dump(model, checkpoint.with_suffix(".tmp"), compress=3)
    checkpoint.with_suffix(".tmp").replace(checkpoint)
    summary = {"rows": len(y), "features": x.shape[1], "iterations": int(model.best_iteration_),
               "seconds": time.monotonic()-start, "sha256": _digest(checkpoint)}
    (directory / "training.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"trained": str(directory), **summary}), flush=True)
    return model


def predict(model, directory, names, part):
    path = directory / (part + "_optical.npz")
    metadata = {"names": names, "weights_sha256": _digest(directory/"model.joblib"), "version": VERSION}
    if path.exists():
        if json.loads(path.with_suffix(".json").read_text()) != metadata:
            raise ValueError("prediction signature changed")
        return
    allowed = set(permitted(names))
    ps, masks = [], []
    for i, name in enumerate(names):
        chip = Chip.from_name(name)
        s1 = read_s1(paths.SEN1FLOODS11_DIR, chip)
        p, mask = np.zeros(s1.shape[1:], np.float32), np.zeros(s1.shape[1:], bool)
        if name in allowed:
            f, mask = features(s1, read_optical(paths.SEN1FLOODS11_DIR, chip))
            flat = f.reshape(-1, f.shape[-1])
            p = np.concatenate([model.predict_proba(flat[j:j+65536])[:, 1]
                                for j in range(0, len(flat), 65536)]).reshape(p.shape).astype(np.float32)
        ps.append(p); masks.append(mask)
        if i % 25 == 0:
            print("predict", directory.name, part, i, flush=True)
    np.savez_compressed(path, names=names, probability=np.stack(ps), available=np.stack(masks))
    path.with_suffix(".json").write_text(json.dumps(metadata, indent=2))


def evaluate(fold, directory):
    old = paths.RUNS_DIR / "next"
    learned = json.loads((old/f"fusion/boost_learned/fold{fold}/result.json").read_text())["learned"]
    arrays = {}
    for part in ("valid", "score"):
        a = radar_arrays(fold, part)
        with np.load(directory/(part+"_optical.npz")) as z:
            if not np.array_equal(z["names"], a["names"]):
                raise ValueError("prediction order differs")
            a["new"], a["available"] = z["probability"], z["available"]
        a["old"] = np.zeros_like(a["new"])
        for name, weight in zip(learned["members"], learned["weights"]):
            base = paths.RUNS_DIR/f"goal072/optical_cv/fold{fold}" if name == "old" else old/f"boost/{name}/fold{fold}"
            with np.load(base/(part+"_optical.npz")) as z:
                if not np.array_equal(z["names"], a["names"]) or not np.array_equal(z["available"], a["available"]):
                    raise ValueError("control inputs differ")
                a["old"] += weight*z["probability"]
        arrays[part] = a
    for weight in (0., .25, .5, 1.):
        destination = directory / f"result_w{weight:.2f}.json"
        if destination.exists():
            continue
        logits = {}
        for part, a in arrays.items():
            optical = weight*a["new"]+(1-weight)*a["old"]
            p = np.where(a["available"], .75*optical+.25*a["probability"], a["probability"])
            logits[part] = logit(np.clip(p, 1e-6, 1-1e-6)).astype(np.float32)
        a = arrays["valid"]; m = a["valid"]
        cal = fit_calibrator(logits["valid"][m], a["target"][m], "isotonic")
        threshold, _ = best_prob_threshold(cal.apply(logits["valid"][m]), a["target"][m])
        a = arrays["score"]
        score = score_arrays(a["names"].tolist(), logits["score"], a["target"], a["valid"], cal, threshold)
        result = {"fold": fold, "weight": weight, "calibration": cal.as_dict(), "threshold": threshold,
                  "score": score, "development_only": True, "version": VERSION}
        destination.write_text(json.dumps(result, indent=2, allow_nan=False))
        print(json.dumps({"fold": fold, "weight": weight, "event_iou": score["event_mean_iou"],
                          "pooled_iou": score["overall"]["micro"]["iou"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folds", nargs="+", type=int, choices=[0, 1, 2], default=[0, 1, 2])
    args = parser.parse_args()
    plan = json.loads((paths.PIPELINE_ROOT/"research/cv_plan.json").read_text())
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = ROOT/"signature.json"
    signature = {**_signature(), "plan_sha256": _digest(paths.PIPELINE_ROOT/"research/cv_plan.json")}
    if manifest.exists() and json.loads(manifest.read_text()) != signature:
        raise ValueError("source or plan changed; preserve previous run and choose a new directory")
    manifest.write_text(json.dumps(signature, indent=2))
    for k in args.folds:
        parts = {p: plan["folds"][k][p] for p in ("train", "valid", "score")}
        directory = ROOT/f"fold{k}"
        model = train(parts, directory)
        for part in ("valid", "score"):
            predict(model, directory, parts[part], part)
        del model
        gc.collect()
        evaluate(k, directory)
        gc.collect()


if __name__ == "__main__":
    main()
