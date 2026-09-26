"""Reliability of the calibrated probability on test_core, with three ECE variants.

evaluation.json keeps 15 equal-width reliability bins per part, which fix the curve, the
equal-width ECE and Brier but not the equal-mass ECE or the ECE away from the extremes.
This module runs the frozen v1 ensemble chip by chip on test_core, and on valid (the
part Platt and the mask threshold were fitted on) for comparison, keeps the mean logit
of every evaluated pixel and scores raw and Platt-calibrated p.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from scipy.special import expit

from floodvalue.calibration import RELIABILITY_BINS, Platt, reliability

MID_RANGE = (0.05, 0.95)
DECIMALS = 4
PARTS = ("valid", "test_core")
# Categorical slots 1-3 of the dataviz reference palette (validated for CVD on a light surface).
COLORS = {"test_core/calibrated": "#2a78d6", "test_core/raw": "#eb6834", "valid/calibrated": "#1baf7a"}
MARKERS = {"test_core/calibrated": "o", "test_core/raw": "s", "valid/calibrated": "^"}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def ece_equal_mass(prob: np.ndarray, target: np.ndarray, n_bins: int = RELIABILITY_BINS) -> dict:
    """ECE over bins of equal pixel count: sort by p, split into ``n_bins`` runs differing by at most one."""
    order = np.argsort(prob, kind="stable")
    bins, gap = [], 0.0
    for chunk in np.array_split(order, n_bins):
        if chunk.size == 0:
            continue
        p = prob[chunk].astype(np.float64)
        y = target[chunk]
        gap += abs(float(y.sum()) - float(p.sum()))
        bins.append({"lo": float(p[0]), "hi": float(p[-1]), "count": int(chunk.size),
                     "mean_p": float(p.mean()), "water_fraction": float(y.mean())})
    return {"bins": bins, "ece": gap / prob.size if prob.size else None, "n_pixels": int(prob.size)}


def ece_mid_range(prob: np.ndarray, target: np.ndarray, low: float = MID_RANGE[0], high: float = MID_RANGE[1],
                  n_bins: int = RELIABILITY_BINS) -> dict:
    """Equal-width ECE on the pixels with low < p < high only, normalised by their count."""
    keep = (prob > low) & (prob < high)
    table = reliability(prob[keep], target[keep], n_bins)
    return {"range": [low, high], "pixels": int(keep.sum()),
            "share_of_pixels": round(float(keep.mean()), DECIMALS) if prob.size else None,
            "ece": table["ece"], "brier": table["brier"], "bins": [b for b in table["bins"] if b["count"]]}


def calibration_metrics(prob: np.ndarray, target: np.ndarray) -> dict:
    """Equal-width curve, ECE (equal width, equal mass, mid range) and Brier for one set of p."""
    table = reliability(prob, target)
    mass = ece_equal_mass(prob, target)
    middle = ece_mid_range(prob, target)
    return {"mean_p": round(float(prob.mean(dtype=np.float64)), DECIMALS),
            "ece_equal_width": table["ece"], "ece_equal_mass": mass["ece"], "ece_mid_range": middle["ece"],
            "brier": table["brier"], "mid_range_pixels": middle["pixels"],
            "mid_range_share_of_pixels": middle["share_of_pixels"], "brier_mid_range": middle["brier"],
            "equal_width_bins": table["bins"], "equal_mass_bins": mass["bins"]}


def part_logits(root: Path, names: Sequence[str], members: Sequence) -> tuple[np.ndarray, np.ndarray]:
    """Ensemble mean logit and water label of the evaluated pixels, one chip at a time."""
    from floodvalue.chips import LABEL_INVALID, LABEL_WATER, Chip, read_label, read_s1, s1_valid
    from floodvalue.ensemble import run_ensemble

    logits, targets = [], []
    for number, name in enumerate(names, 1):
        chip = Chip.from_name(name)
        s1, label = read_s1(root, chip), read_label(root, chip)
        valid = s1_valid(s1) & (label != LABEL_INVALID)
        logits.append(run_ensemble(members, [s1], platt=None).mean_logit[0][valid].astype(np.float32))
        targets.append(label[valid] == LABEL_WATER)
        if number % 10 == 0 or number == len(names):
            print(f"  {number}/{len(names)} chips", file=sys.stderr, flush=True)
    return np.concatenate(logits), np.concatenate(targets)


def part_record(logits: np.ndarray, target: np.ndarray, platt: Platt, chips: int) -> dict:
    return {"chips": chips, "pixels": int(target.size), "water_share": round(float(target.mean()), DECIMALS),
            "calibrated": calibration_metrics(platt.apply(logits), target),
            "raw": calibration_metrics(expit(logits).astype(np.float32), target)}


def saved_probability_checks(evaluation: dict, part: str) -> dict:
    """The GPU evaluation's own equal-width numbers for the same part, to show the CPU rerun matches."""
    block = evaluation["probability"][part]
    return {"source": "artifacts/evaluation/evaluation.json",
            "ece_equal_width_calibrated": block["reliability_calibrated"]["ece"],
            "brier_calibrated": block["reliability_calibrated"]["brier"],
            "ece_equal_width_raw": block["reliability_raw"]["ece"], "brier_raw": block["reliability_raw"]["brier"],
            "pixels": block["reliability_calibrated"]["n_pixels"]}


def _shown(bins: list[dict]) -> list[dict]:
    return [b for b in bins if b["count"]]


def draw_reliability(record: dict, path: Path) -> None:
    """Curve, gap per bin and pixel count per bin; equal-width bins of calibrated and raw p."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    series = [("test_core/calibrated", "test_core, после Platt", "-"),
              ("test_core/raw", "test_core, до калибровки", "--"),
              ("valid/calibrated", "valid (здесь подобран Platt)", "-")]
    blocks = {"test_core": record["test_core"], "valid": record["validation"]}
    figure, (curve, gap, mass) = plt.subplots(1, 3, figsize=(15, 4.8), gridspec_kw={"width_ratios": [1.1, 1.1, 1]})
    curve.plot([0, 1], [0, 1], color=MUTED, linewidth=1, linestyle=":")
    gap.axhline(0, color=MUTED, linewidth=1, linestyle=":")
    for key, label, style in series:
        part, variant = key.split("/")
        metrics = blocks[part][variant]
        bins = _shown(metrics["equal_width_bins"])
        x = [b["mean_p"] for b in bins]
        legend = (f"{label}: ECE {metrics['ece_equal_width']:.4f}, равная масса {metrics['ece_equal_mass']:.4f}, "
                  f"при 0.05 < p < 0.95 {metrics['ece_mid_range']:.4f}")
        for axis, values in ((curve, [b["water_fraction"] for b in bins]),
                             (gap, [b["water_fraction"] - b["mean_p"] for b in bins])):
            axis.plot(x, values, linestyle=style, linewidth=2, marker=MARKERS[key], markersize=5,
                      color=COLORS[key], label=legend if axis is curve else None)
    bins = _shown(record["test_core"]["calibrated"]["equal_width_bins"])
    mass.bar([(b["lo"] + b["hi"]) / 2 for b in bins], [b["count"] for b in bins], width=0.9 / RELIABILITY_BINS,
             color=COLORS["test_core/calibrated"])
    mass.set_yscale("log")
    for axis, title, xlabel, ylabel in (
            (curve, "Надёжность p, test_core (15 корзин равной ширины)", "средняя p в корзине",
             "наблюдаемая доля воды"),
            (gap, "Доля воды минус средняя p", "средняя p в корзине", "доля воды - p"),
            (mass, "Пикселей в корзине, test_core (после Platt)", "p", "пикселей, лог. шкала")):
        axis.set_title(title, fontsize=10, color=INK)
        axis.set_xlabel(xlabel, color=MUTED)
        axis.set_ylabel(ylabel, color=MUTED)
        axis.set_xlim(0, 1)
        axis.grid(color=GRID, linewidth=0.8)
        axis.set_axisbelow(True)
        for spine in ("top", "right"):
            axis.spines[spine].set_visible(False)
    curve.set_ylim(0, 1)
    figure.legend(loc="lower center", ncol=1, fontsize=8.5, frameon=False)
    figure.tight_layout(rect=(0, 0.15, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=130)
    plt.close(figure)
