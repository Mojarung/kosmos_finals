"""Screen new radar models, alone and under the frozen optical model, on validation only."""
import gc
import json
from pathlib import Path
import time

import numpy as np
from scipy.special import expit, logit

from floodvalue.next_boost import radar_arrays
from floodvalue.calibration import best_prob_threshold
from floodvalue.cv import score_arrays
from floodvalue.cv_calibration import fit_calibrator


def main():
    out = Path("runs/next/radar_screen")
    out.mkdir(parents=True, exist_ok=True)
    base = radar_arrays(None, "valid")
    with np.load("runs/goal072/optical/valid_predictions.npz") as a:
        assert np.array_equal(a["names"], base["names"])
        optical, available = a["probability"], a["optical_available"]
    mask = base["valid"]
    for name in ["unet_sar50", "unet_mitb2"]:
        for tta in ["none", "rot4"]:
            path = Path(f"runs/goal072/predictions/{name}_seed0_{tta}_valid.npz")
            while not path.with_suffix(".json").exists():
                time.sleep(10)
            with np.load(path) as a:
                for key in ["names", "target", "valid"]:
                    if not np.array_equal(a[key], base[key]):
                        raise ValueError("radar screen pixels differ")
                new = expit(a["logits"])
            for weight in [.25, .5, 1.]:
                radar = (1-weight)*base["probability"]+weight*new
                for mode in ["radar", "hybrid"]:
                    dest = out/f"{name}_{tta}_{weight:.2f}_{mode}.json"
                    if dest.exists():
                        continue
                    p = radar if mode == "radar" else np.where(available, .75*optical+.25*radar, radar)
                    z = logit(np.clip(p, 1e-6, 1-1e-6)).astype(np.float32)
                    cal = fit_calibrator(z[mask], base["target"][mask], "isotonic")
                    threshold, _ = best_prob_threshold(cal.apply(z[mask]), base["target"][mask])
                    score = score_arrays(base["names"].tolist(), z, base["target"], mask, cal, threshold)
                    result = {"model": name, "tta": tta, "new_weight": weight, "mode": mode,
                              "calibration_method": "isotonic", "calibration": cal.as_dict(),
                              "threshold": threshold, "score": score, "selection_part": "original valid89 only"}
                    dest.write_text(json.dumps(result, indent=2, allow_nan=False))
                    print(json.dumps({"model": name, "tta": tta, "weight": weight, "mode": mode,
                                      "iou": score["overall"]["micro"]["iou"],
                                      "event_mean_iou": score["event_mean_iou"]}), flush=True)
                    del z, p
                gc.collect()


if __name__ == "__main__":
    main()
