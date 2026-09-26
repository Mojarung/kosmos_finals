"""Explain where and why the frozen ensemble misses water on one chip.

Diagnostics are computed from the same inputs the evaluation uses (S1 VV/VH,
manual label, JRC permanent water, the baseline mask) plus the ensemble's
calibrated p and member spread. Nothing is tuned; the manual label is only read.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import numpy as np
from scipy import ndimage
from sklearn.metrics import roc_auc_score

from floodvalue.chips import s1_valid

VH_BINS = [("< -26", -np.inf, -26.0), ("-26 .. -23.6", -26.0, -23.6),
           ("-23.6 .. -21", -23.6, -21.0), (">= -21", -21.0, np.inf)]
SMALL_COMPONENT_PX = 100
SHORE_PX = 3
EIGHT = np.ones((3, 3), dtype=bool)


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(float(numerator) / float(denominator), 3) if denominator else None


def _scores(pred: np.ndarray, water: np.ndarray, valid: np.ndarray) -> dict:
    tp = int((pred & water).sum())
    fp = int((pred & valid & ~water).sum())
    fn = int((~pred & water).sum())
    return {"iou": _ratio(tp, tp + fp + fn), "recall": _ratio(tp, tp + fn), "precision": _ratio(tp, tp + fp),
            "tp": tp, "fp": fp, "fn": fn}


def _median(values: np.ndarray) -> float | None:
    return round(float(np.median(values)), 2) if values.size else None


def local_std(band: np.ndarray, size: int = 5) -> np.ndarray:
    """Standard deviation of a dB band in a size x size window; invalid pixels take the band median."""
    filled = np.where(np.isfinite(band), band, np.nanmedian(band)).astype(np.float64)
    mean = ndimage.uniform_filter(filled, size)
    square = ndimage.uniform_filter(filled ** 2, size)
    return np.sqrt(np.maximum(square - mean ** 2, 0))


def chip_diagnostics(s1: np.ndarray, label: np.ndarray, p: np.ndarray, member_std: np.ndarray,
                     baseline_water: np.ndarray, jrc: np.ndarray, threshold: float, baseline_db: float) -> dict:
    """Counts and shares that say which water the model finds, which it misses, and how sure it was."""
    valid = s1_valid(s1) & (label != -1)
    water = (label == 1) & valid
    model = (p >= threshold) & valid
    baseline_water = baseline_water.astype(bool) & valid
    vv, vh = s1[0], s1[1]
    rows = []
    for name, low, high in VH_BINS:
        in_bin = water & (vh >= low) & (vh < high)
        rows.append({"vh_bin": name, "water": int(in_bin.sum()),
                     "model_found": int((in_bin & model).sum()), "baseline_found": int((in_bin & baseline_water).sum()),
                     "share_of_water": _ratio(in_bin.sum(), water.sum()),
                     "model_recall": _ratio((in_bin & model).sum(), in_bin.sum()),
                     "baseline_recall": _ratio((in_bin & baseline_water).sum(), in_bin.sum())})
    components, count = ndimage.label(water, structure=EIGHT)
    sizes = np.bincount(components.ravel())[1:] if count else np.array([], dtype=int)
    small = np.isin(components, np.flatnonzero(sizes < SMALL_COMPONENT_PX) + 1) & water
    shore = ndimage.distance_transform_edt(water) <= SHORE_PX
    missed = water & ~model
    found = water & model
    texture = local_std(vh)
    return {
        "valid_pixels": int(valid.sum()), "water_pixels": int(water.sum()),
        "water_share": _ratio(water.sum(), valid.sum()),
        "model": _scores(model, water, valid), "baseline": _scores(baseline_water, water, valid),
        "baseline_threshold_db": baseline_db,
        "vh_water_median": _median(vh[water]), "vv_minus_vh_water_median": _median((vv - vh)[water]),
        "recall_by_vh": rows,
        "fragments": {
            "components": int(count),
            "largest_component_px": int(sizes.max()) if sizes.size else 0,
            "share_in_components_below_100px": _ratio(small.sum(), water.sum()),
            "share_within_3px_of_land": _ratio((shore & water).sum(), water.sum()),
        },
        "missed": {"pixels": int(missed.sum()), "vh_median": _median(vh[missed]),
                   "member_std_mean": round(float(member_std[missed].mean()), 3) if missed.any() else None,
                   "baseline_catches_share": _ratio((missed & baseline_water).sum(), missed.sum()),
                   "p_median": round(float(np.median(p[missed])), 3) if missed.any() else None},
        "found": {"pixels": int(found.sum()), "vh_median": _median(vh[found]),
                  "member_std_mean": round(float(member_std[found].mean()), 3) if found.any() else None},
        "permanent_water_share": _ratio(((jrc == 1) & water).sum(), water.sum()),
        "separability": {
            "auc_vh": round(float(roc_auc_score(water[valid], -vh[valid])), 3) if 0 < water.sum() < valid.sum() else None,
            "auc_p": round(float(roc_auc_score(water[valid], p[valid])), 3) if 0 < water.sum() < valid.sum() else None,
        },
        "texture": {"window_px": 5,
                    "water_local_std_median": _median(texture[water]),
                    "found_local_std_median": _median(texture[found]),
                    "missed_local_std_median": _median(texture[missed]),
                    "dry_darker_than_-26_share": _ratio((valid & ~water & (vh < -26)).sum(), (valid & ~water).sum())},
    }


def pooled_recall_by_vh(diagnostics: Iterable[dict]) -> list[dict]:
    """Sum the per-bin counts of several chips and recompute recall."""
    totals = {name: {"water": 0, "model_found": 0, "baseline_found": 0} for name, _, _ in VH_BINS}
    for item in diagnostics:
        for row in item["recall_by_vh"]:
            for key in totals[row["vh_bin"]]:
                totals[row["vh_bin"]][key] += row[key]
    water_total = sum(bin_["water"] for bin_ in totals.values())
    return [{"vh_bin": name, **counts, "share_of_water": _ratio(counts["water"], water_total),
             "model_recall": _ratio(counts["model_found"], counts["water"]),
             "baseline_recall": _ratio(counts["baseline_found"], counts["water"])}
            for name, counts in totals.items()]


def draw(path: Path, chip_name: str, s1: np.ndarray, label: np.ndarray, p: np.ndarray,
         baseline_water: np.ndarray, threshold: float) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    valid = s1_valid(s1) & (label != -1)
    water = (label == 1) & valid
    model = (p >= threshold) & valid
    errors = np.zeros((*label.shape, 3), dtype=np.float32) + 0.93
    errors[model & water] = (0.13, 0.45, 0.78)
    errors[water & ~model] = (0.84, 0.19, 0.15)
    errors[model & ~water] = (0.95, 0.65, 0.2)
    panels = [
        ("VH, дБ", np.where(np.isfinite(s1[1]), s1[1], np.nan), "gray", (-30, -10)),
        ("Ручная метка: вода", np.where(valid, water, np.nan), "Blues", (0, 1)),
        ("p ансамбля", np.where(valid, p, np.nan), "Blues", (0, 1)),
        ("Порог VH < −23,6 дБ", np.where(valid, baseline_water, np.nan), "Oranges", (0, 1)),
    ]
    figure, axes = plt.subplots(1, 5, figsize=(17, 3.9))
    for axis, (title, image, cmap, limits) in zip(axes, panels):
        shown = axis.imshow(image, cmap=cmap, vmin=limits[0], vmax=limits[1])
        axis.set_title(title, fontsize=10)
        if title.startswith("VH") or title.startswith("p "):
            figure.colorbar(shown, ax=axis, fraction=0.046, pad=0.03)
    axes[4].imshow(errors)
    axes[4].set_title("Синий: найдено, красный: пропуск,\nжёлтый: ложная тревога", fontsize=9)
    for axis in axes:
        axis.set_xticks([])
        axis.set_yticks([])
    figure.suptitle(chip_name, fontsize=11)
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=130)
    plt.close(figure)


def run_failure(chip_name: str, reference_part: str, out_dir: Path) -> dict:
    """Diagnose one chip against the other chips of a split part; write JSON and a figure."""
    from floodvalue import paths
    from floodvalue.baseline_run import load_baseline
    from floodvalue.chips import Chip, read_jrc, read_label, read_s1
    from floodvalue.cli import BASELINE_PATH, CALIBRATION_PATH, MODEL_DIR
    from floodvalue.ensemble import load_members, run_ensemble
    from floodvalue.infer import load_calibration
    from floodvalue.package import member_dirs

    platt, threshold = load_calibration(CALIBRATION_PATH)
    members = load_members(member_dirs(MODEL_DIR))
    baseline = load_baseline(BASELINE_PATH)
    split = json.loads((paths.ARTIFACTS_DIR / "split.json").read_text(encoding="utf-8"))
    names = [name for name in split["parts"][reference_part] if name != chip_name]
    results = {}
    for name in [chip_name, *names]:
        chip = Chip.from_name(name)
        s1 = read_s1(paths.SEN1FLOODS11_DIR, chip)
        label = read_label(paths.SEN1FLOODS11_DIR, chip)
        out = run_ensemble(members, [s1], platt)
        water, _ = baseline.predict(s1)
        results[name] = chip_diagnostics(s1, label, out.prob[0], out.member_std[0], water,
                                         read_jrc(paths.SEN1FLOODS11_DIR, chip), threshold, baseline.threshold_db)
        if name == chip_name:
            draw(out_dir / "figures" / f"failure_{chip_name}.png", chip_name, s1, label, out.prob[0], water, threshold)
    reference = [results[name] for name in names if results[name]["water_pixels"] >= 1000]
    report = {
        "chip": chip_name, "reference_part": reference_part, "threshold": threshold,
        "baseline_threshold_db": baseline.threshold_db,
        "target": results[chip_name],
        "reference_chips": [name for name in names if results[name]["water_pixels"] >= 1000],
        "reference_recall_by_vh": pooled_recall_by_vh(reference),
        "reference_per_chip": {name: {key: results[name][key] for key in (
            "water_pixels", "vh_water_median", "vv_minus_vh_water_median", "fragments", "missed", "found", "texture", "separability")}
            | {"model_iou": results[name]["model"]["iou"], "baseline_iou": results[name]["baseline"]["iou"]}
            for name in names},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"failure_{chip_name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
