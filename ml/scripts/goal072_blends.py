"""Validation-only mixtures of new architectures with the shipped ensemble."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
from scipy.special import expit, logit

from floodvalue import paths
from floodvalue.calibration import best_prob_threshold
from floodvalue.cv import score_arrays
from floodvalue.cv_calibration import fit_calibrator
from floodvalue.refine import combine
from floodvalue.split import load_split

ROOT = Path("runs/goal072")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", nargs="+", required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    split = load_split(paths.ARTIFACTS_DIR/"split.json")
    source = json.loads((ROOT/"recipes/v1_weighted_probability_mean.json").read_text())
    base_paths = [Path(f"runs/refine/predictions/v1_m{i}_rot4_valid.npz") for i in range(5)]
    base_weights = np.asarray(source["weights"], dtype=float)
    base_weights /= base_weights.sum()
    bases = {space: combine(base_paths, base_weights, space)
             for space in ["logits", "probabilities"]}
    results_path = ROOT/f"architecture_blends_seed{args.seed}.json"
    rows = json.loads(results_path.read_text()) if results_path.exists() else []
    for name in args.names:
        for tta in ["none", "rot4"]:
            archive = ROOT/"predictions"/f"{name}_seed{args.seed}_{tta}_valid.npz"
            metadata = archive.with_suffix(".json")
            while not metadata.exists():
                time.sleep(10)
            meta = json.loads(metadata.read_text())
            if meta["part"] != "valid" or meta["names"] != split["valid"]:
                raise ValueError("mixture selection can only use original validation")
            with np.load(archive) as data:
                new = {key: data[key] for key in data.files}
            for key in ["names", "target", "valid"]:
                if not np.array_equal(new[key], bases["logits"][key]):
                    raise ValueError(f"different prediction pixels: {key}")
            mask = new["valid"]
            for space, base in bases.items():
                b = expit(base["logits"]) if space == "probabilities" else base["logits"]
                n = expit(new["logits"]) if space == "probabilities" else new["logits"]
                for alpha in [.25, .5, .75]:
                    label = f"v1_{name}_seed{args.seed}_{tta}_{space}_{alpha:.2f}"
                    destination = ROOT/"recipes"/(label+".json")
                    if destination.exists():
                        continue
                    z = (1-alpha)*b+alpha*n
                    if space == "probabilities":
                        z = logit(np.clip(z, 1e-6, 1-1e-6)).astype(np.float32)
                    cal = fit_calibrator(z[mask], new["target"][mask], "isotonic")
                    threshold, _ = best_prob_threshold(cal.apply(z[mask]), new["target"][mask])
                    score = score_arrays(new["names"].tolist(), z, new["target"], mask, cal, threshold)
                    recipe = {"members": source["members"]+[meta],
                              "weights": (base_weights*(1-alpha)).tolist()+[alpha],
                              "space": space, "calibration_method": "isotonic",
                              "calibration": cal.as_dict(), "threshold": threshold,
                              "validation": score,
                              "rule": "original validation only; no test predictions in this search"}
                    destination.write_text(json.dumps(recipe, indent=2, allow_nan=False))
                    row = {"recipe": str(destination), "model": name, "tta": tta,
                           "space": space, "new_weight": alpha,
                           "iou": score["overall"]["micro"]["iou"],
                           "event_mean_iou": score["event_mean_iou"],
                           "brier": score["probability"]["brier"]}
                    rows.append(row)
                    results_path.write_text(json.dumps(rows, indent=2))
                    print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
