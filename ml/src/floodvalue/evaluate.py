"""Calibrate on validation, then score baseline and main method on identical pixels.

Order of operations (nothing below looks at a test part before step 3):
1. ensemble mean logits on validation -> Platt parameters;
2. calibrated validation probabilities -> binary threshold (max pooled IoU);
3. frozen baseline + frozen main method on valid, test_core, test_adjacent, bolivia.
The pixel set for both methods is ``finite(VV) & finite(VH) & label != -1``.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.special import expit

from floodvalue.baseline import ThresholdModel
from floodvalue.calibration import Platt, best_prob_threshold, fit_platt, prob_threshold_curve, reliability
from floodvalue.chips import LABEL_INVALID, LABEL_WATER, Chip, read_jrc, s1_valid
from floodvalue.dataset import load_raw
from floodvalue.ensemble import Member, run_ensemble
from floodvalue.metrics import Confusion, confusion, pooled, summarize
from floodvalue.postprocess import MIN_COMPONENT_SIZES, remove_small_components
from floodvalue.uncertainty import binary_entropy, error_detection

EVAL_PARTS = ("valid", "test_core", "test_adjacent", "bolivia")
TEST_PARTS = ("test_core", "test_adjacent")
METHODS = ("baseline", "main")
SLICES = ("all_water", "no_jrc_permanent_water")


@dataclass(frozen=True)
class PartPrediction:
    names: tuple[str, ...]
    target: np.ndarray  # [N, H, W] bool
    valid: np.ndarray  # [N, H, W] bool, identical for both methods
    permanent: np.ndarray  # [N, H, W] bool, JRC permanent water
    raw_prob: np.ndarray  # sigmoid(mean logit), before calibration
    prob: np.ndarray  # calibrated
    member_std: np.ndarray
    baseline_water: np.ndarray
    member_confusions: tuple[Confusion, ...]  # each member alone at p = 0.5, pooled over the part


def _pooled(water: np.ndarray, target: np.ndarray, valid: np.ndarray) -> Confusion:
    return confusion(water[valid], target[valid], np.ones(int(valid.sum()), dtype=bool))


def predict_part(root: Path, names: Sequence[str], members: Sequence[Member], platt: Platt,
                 baseline: ThresholdModel) -> PartPrediction:
    raw = load_raw(root, names)
    out = run_ensemble(members, raw.s1, platt)
    labels = np.stack(raw.labels)
    valid = np.stack([s1_valid(s1) for s1 in raw.s1]) & (labels != LABEL_INVALID)
    target = labels == LABEL_WATER
    return PartPrediction(
        names=raw.names,
        target=target,
        valid=valid,
        permanent=np.stack([read_jrc(root, Chip.from_name(n)) == 1 for n in names]),
        raw_prob=expit(out.mean_logit).astype(np.float32),
        prob=out.prob,
        member_std=out.member_std,
        baseline_water=np.stack([baseline.predict(s1)[0] for s1 in raw.s1]),
        member_confusions=tuple(_pooled(logits > 0, target, valid) for logits in out.member_logits),
    )


def postprocessing_study(prob: np.ndarray, target: np.ndarray, valid: np.ndarray, threshold: float) -> list[dict]:
    """Validation IoU if small water components were removed from the thresholded mask."""
    rows = []
    for min_pixels in MIN_COMPONENT_SIZES:
        water = np.stack([remove_small_components(chip >= threshold, min_pixels) for chip in prob])
        rows.append({"min_component_px": min_pixels, **_pooled(water, target, valid).as_dict()})
    return rows


def calibrate(root: Path, valid_names: Sequence[str], members: Sequence[Member]) -> dict:
    """Platt scaling and threshold, both from validation pixels only."""
    raw = load_raw(root, valid_names)
    out = run_ensemble(members, raw.s1, platt=None)
    labels = np.stack(raw.labels)
    valid = np.stack([s1_valid(s1) for s1 in raw.s1]) & (labels != LABEL_INVALID)
    target = labels[valid] == LABEL_WATER
    platt = fit_platt(out.mean_logit[valid], target)
    prob = platt.apply(out.mean_logit[valid])
    threshold, conf = best_prob_threshold(prob, target)
    thresholds, curve = prob_threshold_curve(prob, target)
    return {
        "platt": platt.as_dict(),
        "threshold": threshold,
        "rule": "Platt on ensemble mean logit, threshold maximises pooled IoU; validation pixels only",
        "valid_at_threshold": conf.as_dict(),
        "valid_curve": [{"threshold": float(t), **c.as_dict()} for t, c in zip(thresholds, curve)],
        "postprocessing_valid": postprocessing_study(
            platt.apply(out.mean_logit), labels == LABEL_WATER, valid, threshold),
        "postprocessing_applied": False,
        "postprocessing_note": "flood_mask must equal p >= threshold, so no filter is applied to shipped masks",
    }


def _per_chip(pred: PartPrediction, threshold: float, method: str, drop_permanent: bool) -> dict[str, Confusion]:
    water = pred.baseline_water if method == "baseline" else pred.prob >= threshold
    valid = pred.valid & ~pred.permanent if drop_permanent else pred.valid
    return {name: confusion(water[i], pred.target[i], valid[i]) for i, name in enumerate(pred.names)}


def score_part(pred: PartPrediction, threshold: float) -> dict:
    result: dict[str, dict] = {}
    for slice_name in SLICES:
        for method in METHODS:
            per_chip = _per_chip(pred, threshold, method, slice_name != "all_water")
            events: dict[str, dict[str, Confusion]] = {}
            for name, conf in per_chip.items():
                events.setdefault(Chip.from_name(name).event, {})[name] = conf
            result[f"{slice_name}/{method}"] = {
                "overall": summarize(per_chip),
                "events": {event: summarize(chips) for event, chips in sorted(events.items())},
                "per_chip": {name: conf.as_dict() for name, conf in per_chip.items()},
            }
    return result


def probability_checks(pred: PartPrediction, threshold: float) -> dict:
    valid = pred.valid
    target = pred.target[valid]
    prob = pred.prob[valid]
    error = (prob >= threshold) != target
    return {
        "reliability_raw": reliability(pred.raw_prob[valid], target),
        "reliability_calibrated": reliability(prob, target),
        "uncertainty_member_std": error_detection(pred.member_std[valid], error),
        "uncertainty_entropy": error_detection(binary_entropy(prob), error),
    }


def merge_parts(parts: Sequence[PartPrediction]) -> PartPrediction:
    return PartPrediction(
        names=tuple(n for p in parts for n in p.names),
        **{field: np.concatenate([getattr(p, field) for p in parts])
           for field in ("target", "valid", "permanent", "raw_prob", "prob", "member_std", "baseline_water")},
        member_confusions=tuple(pooled(c) for c in zip(*(p.member_confusions for p in parts))),
    )


def metrics_rows(part: str, scored: dict) -> list[dict]:
    rows = []
    for key, block in scored.items():
        slice_name, method = key.split("/")
        for event, summary in [("ALL", block["overall"]), *block["events"].items()]:
            rows.append({"part": part, "slice": slice_name, "method": method, "event": event,
                         **summary["micro"], "chip_mean_iou": summary["chip_mean_iou"], "chips": summary["chips"]})
    return rows


def write_metrics_csv(rows: Sequence[dict], path: Path) -> None:
    if not rows:
        raise ValueError("no metric rows to write: every evaluation part is empty")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if v is None else v for k, v in row.items()})


def evaluate(root: Path, split: dict[str, list[str]], members: Sequence[Member], baseline: ThresholdModel,
             out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    calibration = calibrate(root, split["valid"], members)
    (out_dir / "calibration.json").write_text(json.dumps(calibration, indent=2), encoding="utf-8")
    platt, threshold = Platt(**calibration["platt"]), calibration["threshold"]
    non_empty = [part for part in EVAL_PARTS if split.get(part)]
    predictions = {part: predict_part(root, split[part], members, platt, baseline) for part in non_empty}
    test_parts = [predictions[p] for p in TEST_PARTS if p in predictions]
    if test_parts:
        predictions["test_all"] = merge_parts(test_parts)
    report = {"threshold": threshold, "platt": calibration["platt"], "parts": {}, "probability": {},
              "members_alone_p05": {}, "postprocessing_valid": calibration["postprocessing_valid"],
              "skipped_empty_parts": [part for part in EVAL_PARTS if part not in non_empty]}
    rows: list[dict] = []
    for part, pred in predictions.items():
        scored = score_part(pred, threshold)
        report["parts"][part] = scored
        report["members_alone_p05"][part] = [c.as_dict() for c in pred.member_confusions]
        rows.extend(metrics_rows(part, scored))
        if part != "valid":
            report["probability"][part] = probability_checks(pred, threshold)
    write_metrics_csv(rows, out_dir / "metrics.csv")
    (out_dir / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
