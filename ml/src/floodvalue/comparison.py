"""Method comparison from saved per-chip counts: event tables, a chip bootstrap, weak chips.

Everything is read from ``evaluation.json`` (TP/FP/FN of both methods on identical pixels
of every chip), so no model or data access is needed and the numbers match the saved
evaluation exactly. Parts stay separate: test_core, test_adjacent and bolivia are never
pooled here.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from floodvalue.chips import Chip

PARTS = ("test_core", "test_adjacent", "bolivia")
BOOTSTRAP_PARTS = ("test_core", "test_adjacent", "bolivia")
METHODS = ("baseline", "main")
RESAMPLES = 2000
SEED = 0
DRILL_DOWN = (("test_core", "Mekong"),)
DECIMALS = 4


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), DECIMALS)


def _ratio(numerator: float, denominator: float) -> float | None:
    return _round(numerator / denominator) if denominator else None


def _scores(tp: int, fp: int, fn: int) -> dict:
    return {"iou": _ratio(tp, tp + fp + fn), "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
            "tp": int(tp), "fp": int(fp), "fn": int(fn)}


def part_counts(part: dict) -> tuple[list[str], np.ndarray]:
    """Chip names and counts [chips, method (baseline, main), (tp, fp, fn)]."""
    names = list(part["all_water/main"]["per_chip"])
    counts = np.array([[[part[f"all_water/{method}"]["per_chip"][name][key] for key in ("tp", "fp", "fn")]
                        for method in METHODS] for name in names], dtype=np.int64)
    return names, counts


def _pooled_iou(counts: np.ndarray) -> np.ndarray:
    """IoU from summed counts [..., (tp, fp, fn)]."""
    return counts[..., 0] / counts.sum(axis=-1)


def _interval(values: np.ndarray) -> list[float]:
    return [_round(v) for v in np.percentile(values, [2.5, 97.5])]


def bootstrap_difference(counts: np.ndarray, resamples: int = RESAMPLES, seed: int = SEED) -> dict:
    """Paired chip bootstrap of pooled IoU, main minus baseline, with 95% percentile intervals.

    Each resample draws chips with replacement and sums both methods' counts over the same
    chips, so the pairing of methods on a chip is kept.
    """
    rng = np.random.default_rng(seed)
    chips = counts.shape[0]
    iou = _pooled_iou(counts[rng.integers(0, chips, size=(resamples, chips))].sum(axis=1))
    difference = iou[:, 1] - iou[:, 0]
    point = _pooled_iou(counts.sum(axis=0))
    return {"chips": int(chips), "resamples": resamples, "seed": seed,
            "iou_baseline": _round(point[0]), "iou_main": _round(point[1]),
            "difference": _round(point[1] - point[0]), "ci95": _interval(difference),
            "iou_baseline_ci95": _interval(iou[:, 0]), "iou_main_ci95": _interval(iou[:, 1]),
            "share_resamples_not_above_zero": _round(np.mean(difference <= 0))}


def _event_scores(part: dict, method: str, event: str) -> dict:
    micro = part[f"all_water/{method}"]["events"][event]["micro"]
    return _scores(micro["tp"], micro["fp"], micro["fn"])


def event_table(part: dict) -> list[dict]:
    """One row per event: water pixels, both methods' IoU, precision, recall and the IoU gain."""
    rows = []
    for event, summary in part["all_water/main"]["events"].items():
        baseline, main = (_event_scores(part, method, event) for method in METHODS)
        gain = None if main["iou"] is None or baseline["iou"] is None else round(main["iou"] - baseline["iou"],
                                                                                   DECIMALS)
        rows.append({"event": event, "chips": summary["chips"], "water_pixels": summary["micro"]["n_water"],
                     "baseline": baseline, "main": main, "iou_gain": gain})
    return rows


def event_chips(part: dict, event: str) -> dict:
    """Per-chip counts of one event and each chip's share of the event's false alarms and misses."""
    names = [name for name in part["all_water/main"]["per_chip"] if Chip.from_name(name).event == event]
    main_chips = part["all_water/main"]["per_chip"]
    fp_total = sum(main_chips[name]["fp"] for name in names)
    fn_total = sum(main_chips[name]["fn"] for name in names)
    rows = []
    for name in names:
        per_method = {method: part[f"all_water/{method}"]["per_chip"][name] for method in METHODS}
        rows.append({"chip": name, "water_pixels": per_method["main"]["n_water"],
                     "pixels": per_method["main"]["n_pixels"],
                     **{method: _scores(c["tp"], c["fp"], c["fn"]) for method, c in per_method.items()},
                     "share_of_event_false_alarms": _ratio(main_chips[name]["fp"], fp_total),
                     "share_of_event_misses": _ratio(main_chips[name]["fn"], fn_total)})
    rows.sort(key=lambda row: row["main"]["fp"], reverse=True)
    return {"event": {method: _event_scores(part, method, event) for method in METHODS},
            "water_pixels": part["all_water/main"]["events"][event]["micro"]["n_water"], "chips": rows}


def baseline_wins(part: dict) -> list[dict]:
    """Chips where the threshold baseline has the higher IoU, largest water first."""
    rows = []
    for name, main in part["all_water/main"]["per_chip"].items():
        baseline = part["all_water/baseline"]["per_chip"][name]
        if main["iou"] is None or baseline["iou"] is None or baseline["iou"] <= main["iou"]:
            continue
        rows.append({"chip": name, "water_pixels": main["n_water"], "iou_main": _round(main["iou"]),
                     "iou_baseline": _round(baseline["iou"]), "iou_gap": _round(main["iou"] - baseline["iou"])})
    return sorted(rows, key=lambda row: row["water_pixels"], reverse=True)


def compare(evaluation: dict, parts: Sequence[str] = PARTS) -> dict:
    """The record for method_comparison.json."""
    record = {
        "rule": "counts from evaluation.json (all_water slice, finite VV and VH and label != -1, identical "
                "pixels for both methods); IoU pooled over the pixels of a part or event; bootstrap resamples "
                "chips with replacement, sums both methods over the same chips, 95% percentile interval",
        "threshold": evaluation["threshold"],
        "events": {part: event_table(evaluation["parts"][part]) for part in parts},
        "bootstrap_iou_difference": {part: bootstrap_difference(part_counts(evaluation["parts"][part])[1])
                                     for part in BOOTSTRAP_PARTS if part in parts},
        "baseline_wins": {part: baseline_wins(evaluation["parts"][part]) for part in parts},
        "drill_down": {f"{part}/{event}": event_chips(evaluation["parts"][part], event)
                       for part, event in DRILL_DOWN if part in parts},
    }
    return record
