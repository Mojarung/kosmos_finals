"""Post-audit slices, fixed-portfolio EL proxy and a compact scientific figure.

Exploratory diagnostics only: no model or threshold selection.
"""
import csv
import hashlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from audit_models import EVIDENCE, OUT, ROOT, VERSIONS, PARTS, SEED, counts, ratios, read, write
from floodvalue import paths
from floodvalue.chips import Chip, read_s1, s1_valid

# Exact ten V*q exposures from the case, in the fixed type-table order.
EXPOSURES = np.array([2.5, 8, 1.5, 1.6, 4.2, 4.5, 3.6, 2.7, 2, 4.8]) * 1e6


def main():
    metrics = read(EVIDENCE/"metrics.json")
    protocol = read(EVIDENCE/"protocol.json")
    spatial = read(EVIDENCE/"spatial_provenance.json")
    names_far = set(spatial["test_core_without_train_or_validation_neighbours"])
    rows = metrics["per_chip"]
    grouped, portfolios = {}, []
    for part in PARTS:
        for name in protocol["parts"][part]:
            # Seeded positions use S1 validity only, never model outputs or labels.
            s1_mask = s1_valid(read_s1(paths.SEN1FLOODS11_DIR, Chip.from_name(name)))
            seed = SEED + int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
            available_positions = np.flatnonzero(s1_mask)
            # Retain ten unassessed assets even for an entirely nodata chip.
            pool = available_positions if len(available_positions) >= 10 else np.arange(s1_mask.size)
            positions = np.random.default_rng(seed).choice(pool, 10, replace=False)
            with np.load(OUT/f"{name}.npz") as a:
                valid, truth = a["valid"], a["target"]
                masks = {part + ":optical_available": valid & a["optical_available"],
                         part + ":optical_missing": valid & ~a["optical_available"]}
                if name in names_far:
                    masks["test_core_no_development_neighbours"] = valid
                labelled = valid.ravel()[positions]
                for model in VERSIONS:
                    p = a[model]
                    for scope, selected in masks.items():
                        if not selected.any():
                            continue
                        pr, y = p[selected], truth[selected]
                        g = grouped.setdefault((scope, model), {"confusion": np.zeros(4, np.int64),
                                                               "brier_sum": 0., "bins": np.zeros((15, 3))})
                        g["confusion"] += counts(pr >= protocol["thresholds"][model], y)
                        g["brier_sum"] += float(np.sum((pr.astype(np.float64)-y)**2))
                        idx = np.minimum((pr*15).astype(int), 14)
                        g["bins"] += np.stack([np.bincount(idx, minlength=15), np.bincount(idx, weights=pr, minlength=15),
                                                np.bincount(idx, weights=y, minlength=15)], axis=1)
                    error = (p.ravel()[positions][labelled]-truth.ravel()[positions][labelled])*EXPOSURES[labelled]
                    portfolios.append({"part": part, "chip": name, "model": model,
                                       "labelled_points": int(labelled.sum()), "points": 10,
                                       "sum_absolute_proxy_error_rub": float(np.abs(error).sum()),
                                       "signed_proxy_error_rub": float(error.sum()),
                                       "labelled_exposure_rub": float(EXPOSURES[labelled].sum())})
    slices = {}
    for (scope, model), data in grouped.items():
        r = ratios(data["confusion"])
        r.update(brier=data["brier_sum"]/r["n_pixels"],
                 ece=float(np.abs(data["bins"][:, 1]-data["bins"][:, 2]).sum()/r["n_pixels"]))
        slices.setdefault(scope, {})[model] = r
    portfolio_summary = {}
    for part in PARTS:
        for model in VERSIONS:
            group = [p for p in portfolios if p["part"] == part and p["model"] == model]
            total = sum(p["sum_absolute_proxy_error_rub"] for p in group)
            count = sum(p["labelled_points"] for p in group)
            exposure = sum(p["labelled_exposure_rub"] for p in group)
            portfolio_summary.setdefault(part, {})[model] = {
                "labelled_points": count, "points": 10*len(group),
                "mae_per_labelled_asset_rub": total/count if count else None,
                "exposure_weighted_absolute_probability_error": total/exposure if exposure else None,
                "signed_proxy_error_rub": sum(p["signed_proxy_error_rub"] for p in group)}
    write(EVIDENCE/"diagnostics.json", {"status": "exploratory; never used for tuning",
        "slices": slices, "fixed_portfolio_proxy": portfolio_summary,
        "proxy_rule": "10 seeded points on finite S1; if <10 finite pixels, sample the full grid and keep unassessed slots. Independent of labels/p; compare p*V*q with y*V*q only at labelled points; no claim of actual loss",
        "worst_latest_chips_by_iou": sorted([r for r in rows if r["model"] == "latest_s1s2"], key=lambda r: r["iou"] if r["iou"] is not None else 1)[:10]})
    columns = ["part", "chip", "event", "model", "n_pixels", "tp", "fp", "fn", "tn", "iou", "f1", "precision", "recall", "brier", "ece"]
    with (EVIDENCE/"per_chip_metrics.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, columns)
        writer.writeheader()
        writer.writerows(rows)
    labels = ["S1 сервиса", "Предыдущая S1+S2", "Последняя S1+S2", "Последняя без S2"]
    colors = ["#537895", "#de9853", "#258574", "#8b78ae"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    for i, (model, label, color) in enumerate(zip(VERSIONS, labels, colors)):
        values = [metrics["aggregate"][part][model]["iou"] for part in PARTS]
        axes[0].bar(np.arange(3)+(i-1.5)*.18, values, .18, color=color, label=label)
        reliability = metrics["aggregate"]["test_core"][model]["reliability"]
        nonempty = [r for r in reliability if r["count"]]
        axes[1].plot([r["mean_p"] for r in nonempty], [r["water_fraction"] for r in nonempty], "o-", color=color, label=label, markersize=3)
    axes[0].set(xticks=range(3), xticklabels=["test_core · 55", "Соседние · 35", "Боливия · 15"],
                ylim=(0, 1), ylabel="IoU воды", title="Свежий расчёт на одинаковых пикселях")
    axes[1].plot([0, 1], [0, 1], "--", color="#aaaaaa", linewidth=1)
    axes[1].set(xlim=(0, 1), ylim=(0, 1), xlabel="Предсказанная вероятность", ylabel="Наблюдаемая доля воды",
                title="Калибровка · test_core")
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=.15)
    axes[0].legend(loc="upper left", fontsize=8)
    fig.suptitle("Аудит зафиксированных моделей · известный test, не новый слепой набор", fontsize=11)
    fig.savefig(EVIDENCE/"comparison.png", dpi=180)
    fig.savefig(EVIDENCE/"comparison.pdf")
    print("Diagnostics, CSV and figure saved")


if __name__ == "__main__":
    main()
