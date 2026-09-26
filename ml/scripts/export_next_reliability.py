"""Reliability of the service model next_optical on the independent parts, from NEXT_RESULTS.json.

Nothing is recomputed or tuned here: the bins, ECE and Brier were written by next_evaluate.py
after the recipe (isotonic calibration and threshold 0.46, both chosen on valid) was frozen.
Writes artifacts/next_optical/reliability_test.json and the reliability diagram.
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/"research/NEXT_RESULTS.json"
OUT = ROOT/"artifacts/next_optical"
PARTS = ("test_core", "test_adjacent", "bolivia")
MODES = {"candidate": "next_optical (S1+S2, при наличии S2)", "s1_fallback": "S1 fallback (без S2)"}


def main():
    results = json.loads(SOURCE.read_text())
    auroc = results["uncertainty_error_auroc"]
    record = {"source": "research/NEXT_RESULTS.json (test.parts.*.{candidate,s1_fallback})",
              "role": "report_only_not_used_for_selection",
              "calibration": "isotonic, fitted on valid (recipe.json calibration)", "parts": {}}
    for part in PARTS:
        record["parts"][part] = {}
        for mode in MODES:
            row = results["test"]["parts"][part][mode]
            record["parts"][part][mode] = {
                "pixels": row["probability"]["n_pixels"],
                "ece_raw": row["raw_probability"]["ece"], "ece_calibrated": row["probability"]["ece"],
                "brier_raw": row["raw_probability"]["brier"], "brier_calibrated": row["probability"]["brier"],
                "bins": row["probability"]["bins"],
            }
        record["parts"][part]["candidate"]["error_auroc_model_disagreement"] = auroc[part]["candidate_error_auroc"]
    (OUT/"reliability_test.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    fig, axes = plt.subplots(1, len(PARTS), figsize=(13, 4.2), sharey=True)
    for ax, part in zip(axes, PARTS):
        ax.plot([0, 1], [0, 1], color="#999", lw=1, ls="--")
        for mode, label in MODES.items():
            row = record["parts"][part][mode]
            bins = [b for b in row["bins"] if b["count"]]
            ax.plot([b["mean_p"] for b in bins], [b["water_fraction"] for b in bins], marker="o", ms=3,
                    label=f"{label}: ECE {row['ece_calibrated']:.4f}")
        ax.set_title(part)
        ax.set_xlabel("средняя p в корзине")
        ax.legend(fontsize=7, loc="upper left")
    axes[0].set_ylabel("наблюдаемая доля воды")
    fig.tight_layout()
    fig.savefig(OUT/"reliability_test.png", dpi=110)
    print(json.dumps({p: {m: round(record["parts"][p][m]["ece_calibrated"], 4) for m in MODES} for p in PARTS}))


if __name__ == "__main__":
    main()
