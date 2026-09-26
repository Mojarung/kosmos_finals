"""Event-held-out development CV, isolated from the shipped model and final test.

The original train/valid events are balanced into outer folds using chip counts.
Within each fold the original valid chips of the remaining events select the
checkpoint, calibration and threshold. An outer event is never seen in training.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import numpy as np
from scipy.special import expit

from floodvalue import paths
from floodvalue.calibration import Platt, best_prob_threshold, fit_platt, reliability
from floodvalue.chips import Chip, s1_valid
from floodvalue.metrics import Confusion, confusion, summarize


def event(name: str) -> str:
    return Chip.from_name(name).event


def make_plan(split: dict, n_folds: int = 3) -> dict:
    parts = split.get("parts", split)
    train, valid = parts["train"], parts["valid"]
    excluded = sorted(n for key in ("test_core", "test_adjacent", "bolivia") for n in parts[key])
    development = sorted(train + valid)
    if len(set(development)) != len(development) or set(development) & set(excluded):
        raise ValueError("duplicate chips or final-test contamination")
    counts = Counter(map(event, development))
    if not 2 <= n_folds <= len(counts):
        raise ValueError("invalid number of event folds")
    groups, sizes = [[] for _ in range(n_folds)], [0] * n_folds
    for name, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        k = min(range(n_folds), key=lambda i: (sizes[i], i))
        groups[k].append(name)
        sizes[k] += count
    folds = []
    for k, group in enumerate(groups):
        fold = {"fold": k, "held_out_events": sorted(group),
                "train": sorted(n for n in train if event(n) not in group),
                "valid": sorted(n for n in valid if event(n) not in group),
                "score": sorted(n for n in development if event(n) in group)}
        if not all(fold[p] for p in ("train", "valid", "score")):
            raise ValueError("empty training, inner validation or scoring part")
        folds.append(fold)
    return {"version": 1, "rule": "Original train+valid only; whole events greedily balanced by chip count, "
            "descending count then alphabetical; ties go to lowest fold. Original valid of training events "
            "selects checkpoint/calibration/threshold. Final test is excluded.",
            "selection_metric": "mean held-out event IoU; pooled IoU and Brier as secondary checks",
            "seed": None, "event_counts": dict(sorted(counts.items())), "excluded_chips": excluded,
            "folds": folds}


def score_arrays(names, logits, target, valid, platt: Platt, threshold: float) -> dict:
    prob = platt.apply(logits)
    per_chip = {name: confusion(prob[i] >= threshold, target[i], valid[i]) for i, name in enumerate(names)}
    by_event = {}
    for name, conf in per_chip.items():
        by_event.setdefault(event(name), {})[name] = conf
    result = {"overall": summarize(per_chip),
              "events": {e: summarize(chips) for e, chips in by_event.items()},
              "per_chip": {n: c.as_dict() for n, c in per_chip.items()},
              "raw_probability": reliability(expit(logits[valid]), target[valid]),
              "probability": reliability(prob[valid], target[valid])}
    event_ious = [v["micro"]["iou"] for v in result["events"].values() if v["micro"]["iou"] is not None]
    result["event_mean_iou"] = float(np.mean(event_ious)) if event_ious else None
    return result


def predict_and_save(member, root: Path, names: list[str], destination: Path) -> tuple:
    from floodvalue.dataset import load_raw
    from floodvalue.ensemble import member_logits

    raw = load_raw(root, names)
    start = time.monotonic()
    logits = member_logits(member, raw.s1)
    seconds = time.monotonic() - start
    labels = np.stack(raw.labels)
    valid = np.stack([s1_valid(s1) for s1 in raw.s1]) & (labels != -1)
    target = labels == 1
    np.savez_compressed(destination, names=np.asarray(names), logits=logits.astype(np.float32),
                        target=target, valid=valid)
    return logits, target, valid, seconds


def run_fold(plan: dict, config, fold_id: int, root: Path, out: Path) -> dict:
    import torch
    from floodvalue.train import run
    from floodvalue.ensemble import load_member
    from floodvalue.baseline_run import fit_baseline, load_baseline
    from floodvalue.chips import read_label, read_s1

    fold = plan["folds"][fold_id]
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    out.mkdir(parents=True, exist_ok=True)
    result_path = out / "cv_result.json"
    if result_path.exists():
        result = json.loads(result_path.read_text())
        if type(config)(**result["config"]) != config or result["plan_sha256"] != plan_hash:
            raise ValueError("existing run has a different config or CV plan")
        print(f"Already complete: {out}", flush=True)
        return result
    (out / "fold.json").write_text(json.dumps(fold, indent=2))
    summary_path = out / "train_summary.json"
    if summary_path.exists():
        training = json.loads(summary_path.read_text())
        if (type(config)(**training["config"]) != config or training["train_chips"] != fold["train"]
                or training["valid_chips"] != fold["valid"]):
            raise ValueError("saved training does not match this fold")
    else:
        training = run(config, root, fold, out)
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    member = load_member(out)
    logits, target, valid, seconds_valid = predict_and_save(member, root, fold["valid"], out / "valid_predictions.npz")
    platt = fit_platt(logits[valid], target[valid])
    threshold, calibration_conf = best_prob_threshold(platt.apply(logits[valid]), target[valid])
    calibration = {"platt": platt.as_dict(), "threshold": threshold,
                   "rule": "inner validation only; outer scoring events excluded",
                   "valid_at_threshold": calibration_conf.as_dict()}
    (out / "calibration.json").write_text(json.dumps(calibration, indent=2))
    del logits, target, valid
    logits, target, valid, seconds_score = predict_and_save(member, root, fold["score"], out / "score_predictions.npz")
    scored = score_arrays(fold["score"], logits, target, valid, platt, threshold)
    fit_baseline(root, fold, out / "baseline.json")
    baseline = load_baseline(out / "baseline.json")
    baseline_chips = {}
    for name in fold["score"]:
        chip = Chip.from_name(name)
        s1, label = read_s1(root, chip), read_label(root, chip)
        pred, s1_ok = baseline.predict(s1)
        baseline_chips[name] = confusion(pred, label == 1, s1_ok & (label != -1))
    result = {"config": asdict(config), "fold": fold_id, "held_out_events": fold["held_out_events"],
              "plan_sha256": plan_hash, "calibration": calibration, "score": scored,
              "baseline": summarize(baseline_chips), "best_epoch": training["best"]["epoch"],
              "inner_valid_best_iou": training["best"]["iou"], "device": training["device"],
              "training_seconds": training["history"][-1]["seconds"],
              "prediction_seconds": {"valid": seconds_valid, "score": seconds_score}}
    result_path.write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps({"run": str(out), "score_iou": scored["overall"]["micro"]["iou"],
                      "event_mean_iou": scored["event_mean_iou"]}), flush=True)
    return result


def summarize_runs(root: Path) -> dict:
    groups = {}
    for path in sorted(root.glob("*/seed*/fold*/cv_result.json")):
        r = json.loads(path.read_text())
        key = (r["config"]["name"], r["config"]["seed"], r["plan_sha256"])
        groups.setdefault(key, []).append(r)
    rows = []
    for (name, seed, plan_hash), records in groups.items():
        events = {e: v for r in records for e, v in r["score"]["events"].items()}
        total = Confusion()
        brier_sum = ece_sum = 0.
        for r in records:
            micro = r["score"]["overall"]["micro"]
            total += Confusion(**{k: micro[k] for k in ("tp", "fp", "fn", "tn")})
            brier_sum += r["score"]["probability"]["brier"] * micro["n_pixels"]
            ece_sum += r["score"]["probability"]["ece"] * micro["n_pixels"]
        rows.append({"name": name, "seed": seed, "folds": sorted(r["fold"] for r in records),
                     "plan_sha256": plan_hash, "pooled": total.as_dict(),
                     "event_mean_iou": float(np.mean([e["micro"]["iou"] for e in events.values()
                                                        if e["micro"]["iou"] is not None])),
                     "brier": brier_sum / total.n_pixels, "weighted_fold_ece": ece_sum / total.n_pixels,
                     "events": {e: v["micro"]["iou"] for e, v in events.items()},
                     "training_seconds": sum(r["training_seconds"] for r in records)})
    return {"note": "Compare only identical completed folds, seeds and plan hashes. CV is development, not final test.",
            "runs": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("plan")
    p.add_argument("--split", type=Path, default=paths.ARTIFACTS_DIR / "split.json")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--folds", type=int, default=3)
    r = commands.add_parser("run")
    r.add_argument("--plan", type=Path, required=True)
    r.add_argument("--config", type=Path, required=True)
    r.add_argument("--fold", type=int, required=True)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--out", type=Path, default=paths.RUNS_DIR / "cv")
    s = commands.add_parser("summarize")
    s.add_argument("--root", type=Path, default=paths.RUNS_DIR / "cv")
    s.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        result = make_plan(json.loads(args.split.read_text()), args.folds)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2))
        print(json.dumps([{k: len(v) if isinstance(v, list) and k != "held_out_events" else v
                           for k, v in f.items()} for f in result["folds"]], indent=2))
    elif args.command == "run":
        from floodvalue.train import TrainConfig
        config = TrainConfig.from_json(args.config, seed=args.seed)
        plan = json.loads(args.plan.read_text())
        if not 0 <= args.fold < len(plan["folds"]):
            raise ValueError("fold is out of range")
        run_fold(plan, config, args.fold, paths.SEN1FLOODS11_DIR,
                 args.out / config.name / f"seed{config.seed}" / f"fold{args.fold}")
    else:
        result = summarize_runs(args.root)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, allow_nan=False))
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
