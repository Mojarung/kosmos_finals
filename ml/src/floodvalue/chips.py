"""Chip naming and raster readers for the Sen1Floods11 hand-labeled layers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio

HAND_DIR = Path("data") / "flood_events" / "HandLabeled"
LABEL_WATER = 1
LABEL_DRY = 0
LABEL_INVALID = -1


@dataclass(frozen=True)
class Chip:
    """A chip is identified by EVENT_CHIPID, e.g. ``Sri-Lanka_101973``."""

    event: str
    chip_id: str

    @property
    def name(self) -> str:
        return f"{self.event}_{self.chip_id}"

    @classmethod
    def from_filename(cls, filename: str) -> Chip:
        stem = Path(filename).name.removesuffix(".tif")
        event, chip_id, _layer = stem.rsplit("_", 2)
        return cls(event, chip_id)

    @classmethod
    def from_name(cls, name: str) -> Chip:
        event, chip_id = name.rsplit("_", 1)
        return cls(event, chip_id)


def layer_path(root: Path, chip: Chip, layer: str) -> Path:
    return root / HAND_DIR / layer / f"{chip.name}_{layer}.tif"


def read_s1(root: Path, chip: Chip) -> np.ndarray:
    """VV and VH backscatter in dB as float32 [2, H, W]; invalid pixels are NaN."""
    with rasterio.open(layer_path(root, chip, "S1Hand")) as src:
        return src.read().astype(np.float32)


def read_label(root: Path, chip: Chip) -> np.ndarray:
    """Hand label as int8 [H, W]: -1 invalid, 0 dry, 1 water."""
    with rasterio.open(layer_path(root, chip, "LabelHand")) as src:
        return src.read(1).astype(np.int8)


def read_jrc(root: Path, chip: Chip) -> np.ndarray:
    with rasterio.open(layer_path(root, chip, "JRCWaterHand")) as src:
        return src.read(1)


def s1_valid(s1: np.ndarray) -> np.ndarray:
    """Pixels where both polarisations are finite."""
    return np.isfinite(s1).all(axis=0)


def s1_profile(root: Path, chip: Chip) -> dict:
    with rasterio.open(layer_path(root, chip, "S1Hand")) as src:
        return dict(src.profile)


def s1_bounds(root: Path, chip: Chip) -> tuple[float, float, float, float]:
    with rasterio.open(layer_path(root, chip, "S1Hand")) as src:
        return tuple(src.bounds)
