"""Rotation TTA on an existing event-CV member; recalibrate on inner valid only."""
import argparse
import gc
import json
from pathlib import Path
import time

import numpy as np
import torch

from floodvalue import paths
from floodvalue.calibration import best_prob_threshold, fit_platt
from floodvalue.cv import score_arrays
from floodvalue.dataset import load_raw
from floodvalue.ensemble import load_member
from floodvalue.features import normalize
from floodvalue.train import predict_logits


def average_rotation_logits(model, x: np.ndarray, identity: np.ndarray) -> np.ndarray:
    total = identity.astype(np.float32).copy()
    for k in (1, 2, 3):
        rotated = np.ascontiguousarray(np.rot90(x, k, axes=(-2, -1)))
        logits = predict_logits(model, rotated).cpu().numpy()
        total += np.rot90(logits, -k, axes=(-2, -1))
    return total / 4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve() == args.run.resolve():
        raise ValueError("TTA output must be separate from source")
    source = json.loads((args.run/"cv_result.json").read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    member = load_member(args.run)
    parts, timing = {}, {}
    for part in ("valid", "score"):
        with np.load(args.run/(part+"_predictions.npz")) as data:
            arrays = {k: data[k] for k in data.files}
        raw = load_raw(paths.SEN1FLOODS11_DIR, arrays["names"].tolist())
        x = np.stack([normalize(s1, member.stats)[0] for s1 in raw.s1])
        started = time.monotonic()
        arrays["logits"] = average_rotation_logits(member.model, x, arrays["logits"])
        timing[part] = time.monotonic() - started
        np.savez_compressed(args.out/(part+"_predictions.npz"), **arrays)
        parts[part] = arrays
        del raw, x
        gc.collect()
    inner = parts["valid"]
    v = inner["valid"]
    platt = fit_platt(inner["logits"][v], inner["target"][v])
    threshold, _ = best_prob_threshold(platt.apply(inner["logits"][v]), inner["target"][v])
    outer = parts["score"]
    scored = score_arrays(outer["names"].tolist(), outer["logits"], outer["target"], outer["valid"], platt, threshold)
    result = {**source, "config": {**source["config"], "name": source["config"]["name"]+"_rot4"},
              "source_run": str(args.run), "tta": "mean logits, rotations 0/90/180/270, inverse before averaging",
              "calibration": {"platt": platt.as_dict(), "threshold": threshold}, "score": scored,
              "training_seconds": 0, "additional_prediction_seconds": timing}
    (args.out/"cv_result.json").write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps({"fold": result["fold"], "event_mean_iou": scored["event_mean_iou"],
                      "pooled_iou": scored["overall"]["micro"]["iou"]}))


if __name__ == "__main__":
    main()
