"""Inference on one official S1 chip: probability, mask and uncertainty rasters on the chip grid.

Output contract from the case (flood_probability.tif, flood_mask.tif):
- probability: Float32, one band, p in [0, 1], nodata -9999;
- mask: UInt8, 1 water, 0 dry, 255 nodata, and mask == 1 exactly where p >= threshold.
CRS, transform, size and pixel grid are copied from the input S1 file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import rasterio

from floodvalue.baseline import ThresholdModel
from floodvalue.calibration import Platt
from floodvalue.chips import s1_valid
from floodvalue.ensemble import Member, run_ensemble

PROB_NODATA = -9999.0
MASK_NODATA = 255


def _write(path: Path, profile: dict, array: np.ndarray, dtype: str, nodata: float) -> None:
    out_profile = {**profile, "count": 1, "dtype": dtype, "nodata": nodata, "compress": "deflate"}
    with rasterio.open(path, "w", **out_profile) as dst:
        dst.write(array.astype(dtype), 1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def infer_s1(s1_path: Path, members: Sequence[Member], platt: Platt, threshold: float, out_dir: Path,
             baseline: ThresholdModel | None = None) -> dict:
    """Write rasters for one S1 GeoTIFF and return a small report with shares and checksums."""
    with rasterio.open(s1_path) as src:
        s1, profile = src.read().astype(np.float32), dict(src.profile)
    valid = s1_valid(s1)
    out = run_ensemble(members, [s1], platt)
    prob = np.where(valid, out.prob[0], PROB_NODATA)
    mask = np.where(valid, (out.prob[0] >= threshold).astype(np.uint8), MASK_NODATA)
    spread = np.where(valid, out.member_std[0], PROB_NODATA)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "flood_probability.tif": (prob, "float32", PROB_NODATA),
        "flood_mask.tif": (mask, "uint8", MASK_NODATA),
        "flood_uncertainty.tif": (spread, "float32", PROB_NODATA),
    }
    if baseline is not None:
        water, _ = baseline.predict(s1)
        files["baseline_mask.tif"] = (np.where(valid, water.astype(np.uint8), MASK_NODATA), "uint8", MASK_NODATA)
    for name, (array, dtype, nodata) in files.items():
        _write(out_dir / name, profile, array, dtype, nodata)
    report = {
        "s1": str(s1_path),
        "threshold": threshold,
        "platt": platt.as_dict(),
        "members": [str(m.source) for m in members],
        "valid_pixels": int(valid.sum()),
        "water_share_of_valid": float(mask[valid].mean()) if valid.any() else None,
        "mean_probability": float(out.prob[0][valid].mean()) if valid.any() else None,
        "files": {name: sha256(out_dir / name) for name in files},
    }
    (out_dir / "inference.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def load_calibration(path: Path) -> tuple[Platt, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Platt(**data["platt"]), float(data["threshold"])
