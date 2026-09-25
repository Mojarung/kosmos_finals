"""Automatic dataset checks for criterion 1: bands, dtype, CRS, grid agreement, label values."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import rasterio

from floodvalue.chips import Chip, layer_path

EXPECTED_SIZE = 512
EXPECTED_CRS = "EPSG:4326"
LABEL_VALUES = {-1, 0, 1}
# Sentinel-1 GRD backscatter in dB normally stays inside this band; values outside are reported as warnings.
DB_SANE_RANGE = (-80.0, 40.0)
# S1 and label transforms differ only in the 15th digit of the pixel size; compare with a tolerance.
TRANSFORM_PRECISION = 1e-9


def check_chip(root: Path, chip: Chip) -> dict:
    """Return per-chip facts and a list of problems (empty list means the chip passed)."""
    problems: list[str] = []
    warnings: list[str] = []
    s1_path, label_path = layer_path(root, chip, "S1Hand"), layer_path(root, chip, "LabelHand")
    if not s1_path.exists() or not label_path.exists():
        return {"chip": chip.name, "problems": ["missing S1Hand or LabelHand"]}
    with rasterio.open(s1_path) as s1_src, rasterio.open(label_path) as label_src:
        s1, label = s1_src.read(), label_src.read(1)
        if s1_src.count != 2 or s1_src.dtypes != ("float32", "float32"):
            problems.append(f"S1 bands/dtype {s1_src.count} {s1_src.dtypes}")
        if (s1_src.width, s1_src.height) != (EXPECTED_SIZE, EXPECTED_SIZE):
            problems.append(f"S1 size {s1_src.width}x{s1_src.height}")
        if str(s1_src.crs) != EXPECTED_CRS or str(label_src.crs) != EXPECTED_CRS:
            problems.append(f"CRS {s1_src.crs} / {label_src.crs}")
        same_grid = s1_src.transform.almost_equals(label_src.transform, precision=TRANSFORM_PRECISION)
        if not same_grid or s1_src.shape != label_src.shape:
            problems.append("S1 and label grids differ")
        transform, bounds = s1_src.transform, tuple(s1_src.bounds)
    finite = np.isfinite(s1).all(axis=0)
    values = set(np.unique(label).tolist())
    if not values <= LABEL_VALUES:
        problems.append(f"label values {sorted(values)}")
    if finite.any():
        low, high = float(np.nanmin(s1)), float(np.nanmax(s1))
        if low < DB_SANE_RANGE[0] or high > DB_SANE_RANGE[1]:
            warnings.append(f"S1 range {low:.1f}..{high:.1f} dB")
    labelled = label != -1
    return {
        "chip": chip.name,
        "event": chip.event,
        "pixel_deg": [transform.a, -transform.e],
        "bounds": bounds,
        "s1_invalid_share": float(1 - finite.mean()),
        "label_invalid_share": float(1 - labelled.mean()),
        "water_share_of_labelled": float((label == 1).sum() / max(labelled.sum(), 1)),
        "labelled_but_s1_invalid": int((labelled & ~finite).sum()),
        "jrc_present": layer_path(root, chip, "JRCWaterHand").exists(),
        "problems": problems,
        "warnings": warnings,
    }


def check_dataset(root: Path, names: Sequence[str], out_path: Path) -> dict:
    chips = [check_chip(root, Chip.from_name(name)) for name in names]
    failed = [c for c in chips if c["problems"]]
    report = {
        "chips_checked": len(chips),
        "chips_failed": len(failed),
        "failed": failed,
        "warned": [{"chip": c["chip"], "warnings": c["warnings"]} for c in chips if c.get("warnings")],
        "mean_water_share": float(np.mean([c.get("water_share_of_labelled", 0.0) for c in chips])),
        "chips_with_s1_nan": sum(1 for c in chips if c.get("s1_invalid_share", 0) > 0),
        "chips": chips,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
