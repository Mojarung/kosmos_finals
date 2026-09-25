"""Report figures from saved evaluation JSON (no model or data access needed)."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BASELINE_COLOR = "#8a8a8a"
MAIN_COLOR = "#2a6f97"
RAW_COLOR = "#c47f17"


def _save(fig, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def reliability_figure(evaluation: dict, part: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], color="#bbbbbb", linestyle="--", linewidth=1)
    for key, label, color in (("reliability_raw", "до калибровки", RAW_COLOR),
                              ("reliability_calibrated", "после Platt", MAIN_COLOR)):
        table = evaluation["probability"][part][key]
        bins = [b for b in table["bins"] if b["count"]]
        ax.plot([b["mean_p"] for b in bins], [b["water_fraction"] for b in bins], marker="o", color=color,
                label=f"{label}: ECE {table['ece']:.3f}, Brier {table['brier']:.3f}")
    ax.set_xlabel("средняя вероятность в корзине")
    ax.set_ylabel("наблюдаемая доля воды")
    ax.set_title(f"Надежность вероятностей, {part}")
    ax.legend(loc="upper left", fontsize=8)
    _save(fig, path)


def threshold_figure(calibration: dict, baseline: dict, path: Path) -> None:
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4))
    curve = calibration["valid_curve"]
    for metric in ("iou", "f1", "precision", "recall"):
        left.plot([c["threshold"] for c in curve], [c[metric] for c in curve], label=metric)
    left.axvline(calibration["threshold"], color="black", linewidth=1)
    left.set_xlabel("порог по откалиброванной p")
    left.set_title(f"Основной метод, validation (порог {calibration['threshold']:.2f})")
    left.legend(fontsize=8)
    chosen = baseline["model"]["channel"]
    for channel, curve_b in baseline["valid_curves"].items():
        right.plot(curve_b["threshold_db"], curve_b["valid_iou"], label=channel,
                   color=MAIN_COLOR if channel == chosen else BASELINE_COLOR)
    right.axvline(baseline["model"]["threshold_db"], color="black", linewidth=1)
    right.set_xlim(-35, -5)
    right.set_xlabel("порог, dB")
    right.set_title(f"Baseline, validation IoU ({chosen} < {baseline['model']['threshold_db']} dB)")
    right.legend(fontsize=8)
    _save(fig, path)


def events_figure(evaluation: dict, part: str, path: Path) -> None:
    block = evaluation["parts"][part]
    events = sorted(block["all_water/main"]["events"])
    fig, ax = plt.subplots(figsize=(10, 4))
    width = 0.4
    for offset, method, color in ((-width / 2, "baseline", BASELINE_COLOR), (width / 2, "main", MAIN_COLOR)):
        values = [block[f"all_water/{method}"]["events"][e]["micro"]["iou"] or 0.0 for e in events]
        ax.bar([i + offset for i in range(len(events))], values, width, label=method, color=color)
    ax.set_xticks(range(len(events)), events, rotation=30)
    ax.set_ylabel("IoU (pooled)")
    ax.set_title(f"IoU по событиям, {part}")
    ax.legend()
    _save(fig, path)


def uncertainty_figure(evaluation: dict, part: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(6, 4))
    for key, label, color in (("uncertainty_member_std", "разброс ансамбля", MAIN_COLOR),
                              ("uncertainty_entropy", "энтропия p", RAW_COLOR)):
        block = evaluation["probability"][part][key]
        ax.plot(range(1, 11), block["error_rate_by_decile"], marker="o", color=color,
                label=f"{label}: AUROC {block['auroc']:.3f}")
    ax.set_xlabel("дециль неопределенности (1 = самые уверенные)")
    ax.set_ylabel("доля ошибок")
    ax.set_title(f"Ошибки против неопределенности, {part}")
    ax.legend(fontsize=8)
    _save(fig, path)


def make_figures(evaluation_dir: Path, baseline_path: Path) -> list[Path]:
    evaluation = json.loads((evaluation_dir / "evaluation.json").read_text(encoding="utf-8"))
    calibration = json.loads((evaluation_dir / "calibration.json").read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    out = evaluation_dir / "figures"
    out.mkdir(exist_ok=True)
    made = [out / "thresholds_valid.png"]
    threshold_figure(calibration, baseline, made[0])
    for part in ("test_all", "bolivia"):
        for name, draw in (("reliability", reliability_figure), ("events", events_figure),
                           ("uncertainty", uncertainty_figure)):
            path = out / f"{name}_{part}.png"
            draw(evaluation, part, path)
            made.append(path)
    return made
