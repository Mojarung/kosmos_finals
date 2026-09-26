"""Explicit adapters for research samples; external data are not admitted to training here."""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import numpy as np


def linear_power_db(power: np.ndarray) -> np.ndarray:
    """Kuro Siwo GRD is linear sigma0 power, not amplitude and not dB."""
    power = np.asarray(power, np.float32)
    valid = np.isfinite(power) & (power > 0)
    result = np.full(power.shape, np.nan, np.float32)
    result[valid] = 10*np.log10(power[valid])
    return result


def kuro_water_target(mask: np.ndarray, source_valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Merge permanent water (1) and flood (2); 3 is original MLU nodata."""
    mask = np.asarray(mask)
    valid = np.asarray(source_valid) == 1
    if mask.shape != valid.shape:
        raise ValueError("label and validity grids differ")
    if np.any(valid & ~np.isin(mask, [0, 1, 2, 3])):
        raise ValueError("unknown Kuro Siwo label in a valid pixel")
    valid &= np.isin(mask, [0, 1, 2])
    return np.isin(mask, [1, 2]) & valid, valid


def first_kuro_sample(archive: Path) -> dict:
    """Read one complete sample from a bounded tar prefix without extracting paths or pickle."""
    arrays, info, key = {}, None, None
    with tarfile.open(archive, mode="r|") as stream:
        for member in stream:
            if not member.isfile():
                continue
            prefix, separator, suffix = member.name.partition(".")
            if not separator:
                continue
            if key is not None and prefix != key:
                break
            key = prefix
            if member.size > 4_000_000:
                raise ValueError("unexpected sample member size")
            payload = stream.extractfile(member).read()
            if suffix == "info.json":
                info = json.loads(payload)
            elif suffix.endswith(".npy"):
                arrays[suffix[:-4]] = np.load(io.BytesIO(payload), allow_pickle=False)
    required = {"flood_vv", "flood_vh", "sec1_vv", "sec1_vh", "sec2_vv", "sec2_vh", "mask", "valid_mask", "dem"}
    if not required <= arrays.keys() or info is None:
        raise ValueError("incomplete Kuro Siwo sample; increase bounded prefix")
    target, valid = kuro_water_target(arrays["mask"][0], arrays["valid_mask"][0])
    s1 = linear_power_db(np.concatenate([arrays["flood_vv"], arrays["flood_vh"]]))
    valid &= np.isfinite(s1).all(axis=0)
    return {"key": key, "info": info, "s1": s1, "target": target, "valid": valid,
            "arrays": arrays}


def geographic_overlaps(geometry_4326: dict, protected: dict[str, tuple], buffer_degrees: float = .02) -> list[str]:
    """Conservative screening against protected chip boxes, including a ~2 km latitude buffer.

    A pass screens geometry only, not shared events or annotation provenance. Degrees
    are explicit: this is not a uniform-distance geodesic buffer.
    """
    from shapely.geometry import box, shape

    candidate = shape(geometry_4326).buffer(buffer_degrees)
    return sorted(name for name, bounds in protected.items() if candidate.intersects(box(*bounds)))
