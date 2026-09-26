"""Fresh frozen-model inference and independently aggregated evaluation, no tuning.

Run from ml/: python scripts/audit_models.py freeze|infer|report
Raw predictions remain in ignored outputs/audit_20260926; small evidence is versioned.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import time
from datetime import datetime, timezone

import numpy as np
from scipy.special import logit

from floodvalue import paths
from floodvalue.chips import Chip, read_label, read_s1, s1_valid
from floodvalue.split import load_split

ROOT = paths.PIPELINE_ROOT
OUT = ROOT / "outputs/audit_20260926"
EVIDENCE = ROOT / "research/audit_20260926"
PARTS = ("test_core", "test_adjacent", "bolivia")
VERSIONS = ("service_s1", "previous_s1s2", "latest_s1s2", "latest_no_s2")
SEED = 260926


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def freeze():
    if (EVIDENCE / "protocol.json").exists():
        raise RuntimeError("Protocol already frozen; use the existing fingerprint")
    split = load_split(paths.ARTIFACTS_DIR / "split.json")
    dev = set(split["train"]) | set(split["valid"])
    test = [name for part in PARTS for name in split[part]]
    assert len(test) == len(set(test)) == 105 and not dev.intersection(test)
    files = [ROOT / "artifacts/split.json", ROOT / "artifacts/evaluation/calibration.json",
             ROOT / "artifacts/baseline/baseline.json", Path(__file__),
             ROOT / "research/INDEPENDENT_AUDIT_PROTOCOL.md"]
    for package in ("model", "next_optical", "goal072_optical"):
        files.extend(p for p in (ROOT / "artifacts" / package).rglob("*") if p.is_file())
    files.extend((ROOT / "src/floodvalue").glob("*.py"))
    protocol = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "candidate": "next_optical", "parts": {p: split[p] for p in PARTS},
        "train_test_id_overlap": [],
        "events_shared_with_development": {p: sorted({n.rsplit('_', 1)[0] for n in split[p]} &
                                                     {n.rsplit('_', 1)[0] for n in dev}) for p in PARTS},
        "independence": "fresh reproducibility audit; previously observed test; CV used for selection",
        "thresholds": {"service_s1": read(ROOT / "artifacts/evaluation/calibration.json")["threshold"],
                       "previous_s1s2": read(ROOT / "artifacts/goal072_optical/recipe.json")["threshold"],
                       "latest_s1s2": read(ROOT / "artifacts/next_optical/recipe.json")["threshold"],
                       "latest_no_s2": read(ROOT / "artifacts/next_optical/recipe.json")["threshold"]},
        "source_sha256": {str(p.relative_to(ROOT)): sha(p) for p in sorted(set(files))},
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "packages": {n: importlib.metadata.version(n) for n in
                                     ("numpy", "torch", "rasterio", "scikit-learn", "lightgbm", "catboost")}},
        "bootstrap_seed": SEED, "bootstrap_repeats": 2000,
    }
    write(EVIDENCE / "protocol.json", protocol)
    print(json.dumps({"frozen": sha(EVIDENCE / "protocol.json"), "chips": len(test)}), flush=True)


def verified_protocol():
    protocol = read(EVIDENCE / "protocol.json")
    for name, digest in protocol["source_sha256"].items():
        if sha(ROOT / name) != digest:
            raise RuntimeError(f"Frozen input changed: {name}")
    return protocol, sha(EVIDENCE / "protocol.json")


def infer():
    import torch
    import joblib
    from floodvalue.baseline import ThresholdModel
    from floodvalue.ensemble import load_members, run_ensemble
    from floodvalue.infer import load_calibration
    from floodvalue.optical import event_dates, optical_date_allowed, read_optical, predict_optical
    from floodvalue.refine import predict_recipe, load_recipe_calibration
    from floodvalue.fusion_infer import optical_ensemble
    from floodvalue.fusion_calibration import fusion_calibration
    from floodvalue.fusion import fuse

    protocol, fingerprint = verified_protocol()
    torch.set_num_threads(4)
    dates = event_dates(paths.SEN1FLOODS11_DIR)
    latest_path = ROOT / "artifacts/next_optical/recipe.json"
    previous_path = ROOT / "artifacts/goal072_optical/recipe.json"
    latest, previous = read(latest_path), read(previous_path)
    radar_path = latest_path.parent / latest["base_recipe"]
    assert sha(radar_path) == latest["base_recipe_sha256"] == previous["base_recipe_sha256"]
    assert sha(previous_path.parent / previous["optical_member"] / "model.joblib") == previous["weights_sha256"]
    old_optical = joblib.load(previous_path.parent / previous["optical_member"] / "model.joblib")
    service = load_members(sorted((ROOT / "artifacts/model/members").iterdir()))
    platt, _ = load_calibration(ROOT / "artifacts/evaluation/calibration.json")
    baseline = ThresholdModel(**read(ROOT / "artifacts/baseline/baseline.json")["model"])
    OUT.mkdir(parents=True, exist_ok=True)
    pending = [(part, name) for part in PARTS for name in protocol["parts"][part]]
    start = time.monotonic()
    for offset in range(0, len(pending), 4):
        batch = []
        for part, name in pending[offset:offset + 4]:
            cache = OUT / f"{name}.npz"
            if cache.exists():
                with np.load(cache) as a:
                    if str(a["fingerprint"]) != fingerprint:
                        raise RuntimeError("Prediction provenance differs")
                continue
            batch.append((part, name))
        if not batch:
            continue
        chips = [Chip.from_name(n) for _, n in batch]
        inputs = [read_s1(paths.SEN1FLOODS11_DIR, c) for c in chips]
        before = time.monotonic()
        current = run_ensemble(service, inputs, platt)
        service_seconds = time.monotonic() - before
        before = time.monotonic()
        _, radar, radar_spread = predict_recipe(radar_path, inputs, calibrated=False)
        radar_seconds = time.monotonic() - before
        for i, ((part, name), chip, s1) in enumerate(zip(batch, chips, inputs)):
            before = time.monotonic()
            acquisition = dates[chip.event]
            labels = read_label(paths.SEN1FLOODS11_DIR, chip)
            assert set(np.unique(labels)) <= {-1, 0, 1}
            valid = s1_valid(s1) & np.isin(labels, [0, 1])
            base = radar[i]
            available = np.zeros(base.shape, bool)
            z = logit(np.clip(base, 1e-6, 1 - 1e-6)).astype(np.float32)
            no_s2 = fusion_calibration(latest, available).apply(z)
            latest_prob, previous_prob = no_s2.copy(), load_recipe_calibration(previous).apply(z)
            latest_spread, previous_spread = radar_spread[i].copy(), radar_spread[i].copy()
            enabled = optical_date_allowed(**acquisition)
            if enabled:
                s2 = read_optical(paths.SEN1FLOODS11_DIR, chip)
                optical, available, variance = optical_ensemble(latest_path, latest, s1, s2)
                hybrid = fuse(base, optical, available, None, latest.get("gate"), latest["optical_weight"])
                latest_prob = fusion_calibration(latest, available).apply(logit(np.clip(hybrid, 1e-6, 1-1e-6)).astype(np.float32))
                latest_spread = np.where(available, np.sqrt(.25*(optical-base)**2 + .5*variance), radar_spread[i])
                old, old_available = predict_optical(old_optical, s1, s2)
                assert np.array_equal(available, old_available)
                hybrid_old = np.where(available, (1-previous["optical_weight"])*base + previous["optical_weight"]*old, base)
                previous_prob = load_recipe_calibration(previous).apply(logit(np.clip(hybrid_old, 1e-6, 1-1e-6)).astype(np.float32))
                previous_spread = np.where(available, .5*np.abs(old-base), radar_spread[i])
            probs = dict(zip(VERSIONS, [current.prob[i], previous_prob, latest_prob, no_s2]))
            spreads = dict(zip(VERSIONS, [current.member_std[i], previous_spread, latest_spread, radar_spread[i]]))
            for key, probability in probs.items():
                assert np.isfinite(probability[s1_valid(s1)]).all(), (name, key)
                assert ((probability[valid] >= 0) & (probability[valid] <= 1)).all()
                assert np.isfinite(spreads[key][valid]).all()
            payload = {**probs, **{k + "_spread": v for k, v in spreads.items()},
                       "valid": valid, "target": labels == 1, "baseline": baseline.predict(s1)[0],
                       "optical_available": available, "part": part, "fingerprint": fingerprint}
            temporary = OUT / f"{name}.part.npz"
            np.savez_compressed(temporary, **payload)
            temporary.replace(OUT / f"{name}.npz")
            write(OUT / f"{name}.json", {"name": name, "part": part, "dates": acquisition,
                  "optical_allowed_by_date": enabled, "optical_valid_pixels": int((available & valid).sum()),
                  "valid_pixels": int(valid.sum()), "device": os.environ.get("FLOODVALUE_DEVICE", "cpu"),
                  "service_seconds_per_chip_batch": service_seconds / len(batch),
                  "radar_rot4_seconds_per_chip_batch": radar_seconds / len(batch),
                  "both_optical_seconds": time.monotonic() - before})
        print(json.dumps({"completed": min(offset + 4, len(pending)), "total": len(pending),
                          "elapsed_seconds": round(time.monotonic() - start, 1)}), flush=True)


def counts(pred, truth):
    return np.array([np.count_nonzero(pred & truth), np.count_nonzero(pred & ~truth),
                     np.count_nonzero(~pred & truth), np.count_nonzero(~pred & ~truth)], dtype=np.int64)


def ratios(c):
    tp, fp, fn, tn = map(int, c)
    def ratio(a, b):
        return a / b if b else None
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, n_pixels=sum(c).item() if hasattr(sum(c), 'item') else sum(c),
                iou=ratio(tp, tp+fp+fn), f1=ratio(2*tp, 2*tp+fp+fn),
                precision=ratio(tp, tp+fp), recall=ratio(tp, tp+fn))


def report():
    from sklearn.metrics import roc_auc_score
    protocol, fingerprint = verified_protocol()
    rows, pools, samples = [], {}, {}
    for part in PARTS:
        for name in protocol["parts"][part]:
            with np.load(OUT / f"{name}.npz") as a:
                assert str(a["fingerprint"]) == fingerprint
                valid, truth = a["valid"], a["target"][a["valid"]]
                event = name.rsplit("_", 1)[0]
                seed = SEED + int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
                sample = np.random.default_rng(seed).choice(len(truth), min(10000, len(truth)), replace=False)
                for version in ("baseline", *VERSIONS):
                    p = None if version == "baseline" else a[version][valid]
                    pred = a["baseline"][valid] if p is None else p >= protocol["thresholds"][version]
                    conf = counts(pred, truth)
                    row = {"part": part, "chip": name, "event": event, "model": version, **ratios(conf)}
                    bins = np.zeros((15, 3), np.float64)
                    if p is not None:
                        row["brier"] = float(np.mean((p.astype(np.float64)-truth)**2))
                        bin_id = np.minimum((p*15).astype(int), 14)
                        bins[:, 0] = np.bincount(bin_id, minlength=15)
                        bins[:, 1] = np.bincount(bin_id, weights=p, minlength=15)
                        bins[:, 2] = np.bincount(bin_id, weights=truth, minlength=15)
                        row["ece"] = float(np.abs(bins[:, 1]-bins[:, 2]).sum()/len(p))
                        samples.setdefault((part, version), []).append((
                            (pred != truth)[sample], a[version + "_spread"][valid][sample],
                            np.where(pred, 1-p, p)[sample]))
                    rows.append(row)
                    for scope in (part, part + ":" + event):
                        key = (scope, version)
                        pool = pools.setdefault(key, {"confusion": np.zeros(4, np.int64), "bins": np.zeros((15, 3)), "brier_sum": 0.0, "chips": 0})
                        pool["confusion"] += conf
                        pool["bins"] += bins
                        pool["brier_sum"] += row.get("brier", 0)*len(truth)
                        pool["chips"] += 1
    aggregate = {}
    for (scope, version), pool in pools.items():
        result = {**ratios(pool["confusion"]), "chips": pool["chips"]}
        if version != "baseline":
            b = pool["bins"]
            result.update(brier=pool["brier_sum"]/result["n_pixels"], ece=float(np.abs(b[:, 1]-b[:, 2]).sum()/result["n_pixels"]),
                          reliability=[{"count": int(n), "mean_p": float(p/n) if n else None,
                                        "water_fraction": float(y/n) if n else None} for n, p, y in b])
        aggregate.setdefault(scope, {})[version] = result
    for (part, version), values in samples.items():
        errors, spread, chance = (np.concatenate([v[i] for v in values]) for i in range(3))
        if len(np.unique(errors)) == 2:
            aggregate[part][version].update(uncertainty_auroc=float(roc_auc_score(errors, spread)),
                                           map_error_chance_auroc=float(roc_auc_score(errors, chance)),
                                           uncertainty_sample_pixels=len(errors))
    deltas = []
    for part in PARTS:
        for control in ("service_s1", "previous_s1s2"):
            groups = sorted({r["event"] if part != "bolivia" else r["chip"] for r in rows if r["part"] == part})
            tables = {}
            for model in (control, "latest_s1s2"):
                tables[model] = np.array([np.sum([[r[k] for k in ("tp", "fp", "fn", "tn")]
                    for r in rows if r["part"] == part and r["model"] == model and
                    (r["event"] if part != "bolivia" else r["chip"]) == group], axis=0) for group in groups])
            indices = np.random.default_rng(SEED).integers(0, len(groups), (2000, len(groups)))
            values = []
            for model in (control, "latest_s1s2"):
                c = tables[model][indices].sum(axis=1)
                values.append(c[:, 0]/np.maximum(c[:, :3].sum(axis=1), 1))
            delta = values[1]-values[0]
            deltas.append({"part": part, "control": control,
                           "iou_delta": aggregate[part]["latest_s1s2"]["iou"]-aggregate[part][control]["iou"],
                           "descriptive_paired_bootstrap_95": np.quantile(delta, [.025, .975]).tolist(),
                           "unit": "chip (one event; spatial correlation remains)" if part == "bolivia" else "event",
                           "n_groups": len(groups), "blind_test": False})
    write(EVIDENCE / "metrics.json", {"protocol_sha256": fingerprint, "aggregate": aggregate, "paired_deltas": deltas, "per_chip": rows})
    timings = [read(OUT / f"{n}.json") for p in PARTS for n in protocol["parts"][p]]
    write(EVIDENCE / "inference_inputs.json", timings)
    print(json.dumps({p: {v: {k: r[k] for k in ("iou", "f1", "precision", "recall", "n_pixels")}
                          for v, r in aggregate[p].items()} for p in PARTS}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("freeze", "infer", "report"))
    args = parser.parse_args()
    {"freeze": freeze, "infer": infer, "report": report}[args.stage]()
