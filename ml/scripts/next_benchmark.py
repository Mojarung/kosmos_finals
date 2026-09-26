"""CPU optical-component latency on the first chronologically eligible validation chip."""
import argparse
import json
from pathlib import Path
import time

import joblib
import numpy as np

from floodvalue import paths
from floodvalue.chips import Chip, read_s1
from floodvalue.next_boost import CONFIGS, permitted
from floodvalue.optical import read_optical, predict_optical
from floodvalue.split import load_split


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cnn", action="store_true")
    args = parser.parse_args()
    name = permitted(load_split(paths.ARTIFACTS_DIR/"split.json")["valid"])[0]
    chip = Chip.from_name(name)
    s1, s2 = read_s1(paths.SEN1FLOODS11_DIR, chip), read_optical(paths.SEN1FLOODS11_DIR, chip)
    out = Path("runs/next/benchmark.json")
    report = json.loads(out.read_text()) if out.exists() else {"chip": name, "device": "CPU", "runs": {}}
    if report["chip"] != name:
        raise ValueError("benchmark chip changed")
    for member in ["old", *CONFIGS, *(["cnn"] if args.cnn else [])]:
        if member in report["runs"]:
            continue
        directory = Path("runs/goal072/optical") if member == "old" else Path("runs/next/boost")/member/"full"
        if member == "cnn":
            from floodvalue.optical_cnn import load_cnn, predict_cnn
            from floodvalue.train import DEVICE
            if DEVICE != "cpu":
                raise ValueError("CPU benchmark requires FLOODVALUE_DEVICE=cpu")
            model, stats = load_cnn(Path("runs/next/cnn/full"))
            predict = lambda: predict_cnn(model, stats, [s1], [s2])
        else:
            model = joblib.load(directory/"model.joblib")
            predict = lambda: predict_optical(model, s1, s2)
        predict()  # warmup; disk I/O and weight loading excluded
        times = []
        for _ in range(3):
            start = time.perf_counter()
            p, mask = predict()
            times.append(time.perf_counter()-start)
            if not np.isfinite(p).all():
                raise ValueError("nonfinite inference output")
        report["runs"][member] = {"seconds": times, "median_seconds": float(np.median(times)),
                                  "note": "input arrays/weights in memory; includes optical features, excludes radar and file I/O; concurrent research processes active"}
        out.write_text(json.dumps(report, indent=2))
        print(member, report["runs"][member]["median_seconds"], flush=True)
        del model


if __name__ == "__main__":
    main()
