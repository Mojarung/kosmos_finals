"""Evaluate compatible development OOF files, never load test data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.special import expit, logit

from floodvalue import paths
from floodvalue.chips import Chip, read_s1, read_label, s1_valid
from floodvalue.l2a_data import ROOT as DATA, development
from floodvalue.metrics import Confusion, confusion

MEMBERS = {
    **{kind: paths.RUNS_DIR / "l2a_boost" / kind / "oof" for kind in ("hgb", "lightgbm", "catboost")},
    "terramind": paths.RUNS_DIR / "terramind_cv/oof",
    "terramind_kuro": paths.RUNS_DIR / "terramind_kuro/oof",
}


def validate_prediction(path, name, fold, shape):
    with np.load(path) as z:
        if str(z["name"]) != name or int(z["fold"]) != fold:
            raise ValueError(f"OOF identity/fold mismatch: {path}")
        p = z["probability"].astype(np.float32)
    if p.shape != shape or not np.isfinite(p).all() or p.min() < 0 or p.max() > 1:
        raise ValueError(f"invalid probabilities: {path}")
    return p


def load_truth(folds):
    records = []
    for name in sorted(folds):
        chip = Chip.from_name(name)
        label = read_label(paths.SEN1FLOODS11_DIR, chip)
        valid = s1_valid(read_s1(paths.SEN1FLOODS11_DIR, chip)) & (label != -1)
        with np.load(DATA / "chips" / (name + ".npz")) as z:
            usable = z["usable"][valid]
        records.append({"name": name, "fold": folds[name], "event": chip.event,
                        "valid": valid, "truth": label[valid] == 1, "usable": usable})
    return records


def probabilities(records, recipe, missing_recipe=None):
    if not np.isclose(sum(recipe.values()), 1) or min(recipe.values()) < 0:
        raise ValueError("mixture weights must be nonnegative and sum to one")
    if missing_recipe is not None and (not np.isclose(sum(missing_recipe.values()), 1) or min(missing_recipe.values()) < 0):
        raise ValueError("missing-optics mixture weights must be nonnegative and sum to one")
    chunks = []
    for r in records:
        p = np.zeros(r["valid"].shape, np.float32)
        for member, weight in recipe.items():
            p += weight * validate_prediction(MEMBERS[member] / (r["name"] + ".npz"),
                                              r["name"], r["fold"], p.shape)
        chunk = p[r["valid"]]
        if missing_recipe is not None:
            missing = np.zeros_like(p)
            for member, weight in missing_recipe.items():
                missing += weight * validate_prediction(MEMBERS[member] / (r["name"] + ".npz"),
                                                        r["name"], r["fold"], p.shape)
            chunk[~r["usable"]] = missing[r["valid"]][~r["usable"]]
        chunks.append(chunk)
    return np.concatenate(chunks)


def score(records, p):
    from floodvalue.context_final import fit_platt, iou_curve, THRESHOLDS

    y = np.concatenate([r["truth"] for r in records])
    z = logit(np.clip(p, 1e-6, 1-1e-6))
    a, b = fit_platt(z, y)
    calibrated = expit(a*z + b)
    threshold = float(THRESHOLDS[iou_curve(calibrated, y).argmax()])
    by_mode = {}
    for mode, predictions, t in (("raw", p, .5), ("oof_fitted", calibrated, threshold)):
        groups = {"events": {}, "folds": {}, "chips": {}, "slices": {}}
        start = 0
        total = Confusion()
        for r in records:
            end = start + len(r["truth"])
            pred = predictions[start:end] >= t
            c = confusion(pred, r["truth"], np.ones(len(pred), bool))
            total += c
            for key, group in (("events", r["event"]), ("folds", str(r["fold"])), ("chips", r["name"])):
                groups[key][group] = groups[key].get(group, Confusion()) + c
            for name, valid in (("usable_optics", r["usable"]), ("missing_optics", ~r["usable"])):
                groups["slices"][name] = groups["slices"].get(name, Confusion()) + confusion(pred, r["truth"], valid)
            start = end
        by_mode[mode] = {"pooled": total.as_dict(), "threshold": t,
                         "brier": float(np.mean((predictions-y)**2)),
                         "event_mean_iou": float(np.mean([c.iou for c in groups["events"].values() if c.iou is not None])),
                         **{key: {name: c.as_dict() for name, c in group.items()} for key, group in groups.items()}}
    return {"platt": {"a": a, "b": b}, **by_mode}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--terramind-only", action="store_true")
    selection.add_argument("--kuro-only", action="store_true")
    selection.add_argument("--routing", action="store_true")
    args = parser.parse_args()
    missing_recipes = {}
    recipes = {"terramind": {"terramind": 1.}}
    if args.routing:
        trees = {k: 1/3 for k in ("hgb", "lightgbm", "catboost")}
        clear_blend = {"terramind": .5, **{k: 1/6 for k in trees}}
        recipes = {"boost_clear_tm_missing": trees, "blend_clear_tm_missing": clear_blend,
                   "boost_clear_kuro_missing": trees, "blend_clear_kuro_missing": clear_blend}
        missing_recipes = {n: {"terramind_kuro" if "kuro" in n else "terramind": 1.} for n in recipes}
    elif args.kuro_only:
        recipes = {"terramind_kuro": {"terramind_kuro": 1.},
                   "terramind_pair": {"terramind_kuro": .5, "terramind": .5},
                   "kuro_when_optics_missing": {"terramind": 1.}}
        missing_recipes["kuro_when_optics_missing"] = {"terramind_kuro": 1.}
    elif not args.terramind_only:
        trees = ["hgb", "lightgbm", "catboost"]
        recipes.update({k: {k: 1.} for k in trees})
        recipes["boost_mean"] = {k: 1/3 for k in trees}
        for weight in (.25, .5, .75):
            recipes[f"terramind_{weight:.2f}"] = {"terramind": weight, **{k: (1-weight)/3 for k in trees}}
    folds = development()
    for member in {m for recipe in [*recipes.values(), *missing_recipes.values()] for m in recipe}:
        missing = [n for n in folds if not (MEMBERS[member] / (n + ".npz")).exists()]
        if missing:
            raise ValueError(f"incomplete {member} OOF: {len(missing)} chips missing")
    records = load_truth(folds)
    filename = "MODAL_ROUTING_RESULTS.json" if args.routing else "KURO_TRANSFER_RESULTS.json" if args.kuro_only else (
        "TERRAMIND_CV_RESULTS.json" if args.terramind_only else "SERVICE_OOF_RESULTS.json")
    out = paths.PIPELINE_ROOT / "research" / filename
    protocol = "MODAL_ROUTING_PROTOCOL.md" if args.routing else "KURO_TRANSFER_PROTOCOL.md" if args.kuro_only else "SERVICE_BLEND_PROTOCOL.md"
    report = {"protocol": "research/" + protocol, "new_test_evaluation": False,
              "calibration_evaluation": "Platt and threshold fitted to these OOF labels; development selection",
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "results": {}}
    for name, recipe in recipes.items():
        missing_recipe = missing_recipes.get(name)
        result = score(records, probabilities(records, recipe, missing_recipe))
        report["results"][name] = {"members": recipe, "missing_optics_members": missing_recipe, **result}
        out.write_text(json.dumps(report, indent=2, allow_nan=False))
        print(json.dumps({"recipe": name, "raw_iou": result["raw"]["pooled"]["iou"],
                          "oof_fitted_iou": result["oof_fitted"]["pooled"]["iou"]}), flush=True)


if __name__ == "__main__":
    main()
