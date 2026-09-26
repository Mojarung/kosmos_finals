"""Verify a saved development checkpoint on CPU and measure one-chip latency."""
import hashlib
import json
import time

import numpy as np
import torch
from safetensors.torch import load_file

from floodvalue import paths
from floodvalue.l2a_data import development
from floodvalue.terramind_cv import CACHE, ROOT, Segmenter


def main():
    torch.set_num_threads(4)
    names = json.loads((CACHE / "ready.json").read_text())["names"]
    name = sorted(n for n, k in development().items() if k == 0)[0]
    i = names.index(name)
    checkpoint = ROOT / "fold0/model.safetensors"
    model = Segmenter(pretrained=False).cpu().eval()
    model.load_state_dict(load_file(str(checkpoint)))
    x = torch.from_numpy(np.load(CACHE / "x.npy", mmap_mode="r")[i:i+1].astype(np.float32))
    times = []
    with torch.inference_mode():
        for _ in range(4):
            start = time.perf_counter()
            p = model(x).sigmoid()[0].numpy()
            times.append(time.perf_counter() - start)
    with np.load(ROOT / "oof" / (name + ".npz")) as z:
        expected = z["probability"]
    valid = np.load(CACHE / "valid.npy", mmap_mode="r")[i]
    result = {"chip": name, "rule": "lexicographically first outer fold0 chip",
              "device": "cpu", "threads": 4, "shape": list(x.shape),
              "seconds_cold": times[0], "seconds_warm": times[1:],
              "model_bytes": checkpoint.stat().st_size,
              "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
              "parameter_count": sum(p.numel() for p in model.parameters()),
              "max_abs_probability_difference_from_mps": float(np.abs(p-expected).max()),
              "mean_abs_probability_difference_from_mps": float(np.abs(p-expected)[valid].mean()),
              "different_labels_p05": int(np.count_nonzero(((p>=.5)!=(expected>=.5)) & valid)),
              "valid_pixels": int(valid.sum()),
              "scope": "one fold, full 512 chip; excludes data loading and five-model ensemble"}
    if not np.isfinite(p).all() or result["mean_abs_probability_difference_from_mps"] > 1e-3:
        raise ValueError(f"CPU inference verification failed: {result}")
    out = paths.PIPELINE_ROOT / "research/TERRAMIND_CPU_PROBE.json"
    out.write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
