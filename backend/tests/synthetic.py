"""Synthetic 512x512 Sentinel-1 chip, label and frozen-model rasters for tests."""

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

NODATA_COLUMNS = 40
PERMANENT_ROWS = 60


def _raster(path: Path, array: np.ndarray, dtype: str, nodata, count: int = 1) -> None:
    profile = {"driver": "GTiff", "width": 512, "height": 512, "count": count, "dtype": dtype,
               "crs": "EPSG:4326", "transform": from_origin(5.0, 9.1, 0.0000898, 0.0000898),
               "nodata": nodata}
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array if count > 1 else array[None])


def make_chip(folder: Path, chip_id: str, with_label: bool = True) -> dict:
    """Write <chip>_S1Hand.tif, <chip>_LabelHand.tif and inference/<four rasters>; idempotent."""
    folder.mkdir(parents=True, exist_ok=True)
    files = {"s1": folder / f"{chip_id}_S1Hand.tif", "inference": folder / f"{chip_id}_inference",
             "label": folder / f"{chip_id}_LabelHand.tif" if with_label else None,
             "jrc": folder / f"{chip_id}_JRCWaterHand.tif"}
    rng = np.random.default_rng(0)
    vv = rng.normal(-12, 3, (512, 512)).astype("float32")
    vh = rng.normal(-20, 3, (512, 512)).astype("float32")
    vv[:, :NODATA_COLUMNS] = np.nan
    vh[:, :NODATA_COLUMNS] = np.nan
    prob = np.clip(rng.random((512, 512)), 0, 1).astype("float32")
    prob[:, :NODATA_COLUMNS] = -9999
    files["prob"] = prob
    if files["s1"].exists():
        return files
    mask = np.where(prob >= 0.41, 1, 0).astype("uint8")
    mask[:, :NODATA_COLUMNS] = 255
    unc = (rng.random((512, 512)) * 0.2).astype("float32")
    unc[:, :NODATA_COLUMNS] = -9999
    base = (vh < -23.6).astype("uint8")
    base[:, :NODATA_COLUMNS] = 255
    inference = files["inference"]
    inference.mkdir(exist_ok=True)
    _raster(inference / "flood_probability.tif", prob, "float32", -9999)
    _raster(inference / "flood_mask.tif", mask, "uint8", 255)
    _raster(inference / "flood_uncertainty.tif", unc, "float32", -9999)
    _raster(inference / "baseline_mask.tif", base, "uint8", 255)
    if with_label:
        _raster(files["label"], np.where(prob > 0.5, 1, 0).astype("int16"), "int16", -1)
    # A permanent river along the top rows: 1 = water in 1984 and 2018.
    permanent = np.zeros((512, 512), dtype="uint8")
    permanent[:PERMANENT_ROWS] = 1
    _raster(files["jrc"], permanent, "uint8", None)
    _raster(files["s1"], np.stack([vv, vh]), "float32", None, count=2)
    return files
