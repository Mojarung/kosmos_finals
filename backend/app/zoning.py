"""Where the calibrated water map is most likely wrong, grouped into orderable zones.

Map error chance of a pixel is the probability that the map colours it wrongly:
1 - p where the mask says water and p where it says dry land (p is calibrated).
A pixel is doubtful when that chance is at least DOUBT_THRESHOLD, the edge of its
top decile on validation pixels; no test part takes part in the choice. On the
independent test_core 29.4% of doubtful pixels are wrong against 4.6% overall, and
the same ranking finds errors with AUROC 0.888 (ensemble spread: 0.863). Doubtful
area is what the zones try to cover.
"""

from __future__ import annotations

from functools import lru_cache
from math import ceil, cos, floor, pi

import numpy as np

from .catalog import float_map, mask, metadata, on_refresh

# ml/artifacts/evaluation/doubt_threshold.json → doubt_threshold, fitted on validation
# by ml/scripts/doubt_threshold.py; the file also holds the test_core and Bolivia checks.
DOUBT_THRESHOLD = 0.0822
DOUBT_CHECK = "test_core: 29.4% of doubtful pixels wrong vs 4.6% overall, 63% of all errors inside"
# Zone sides are the 1.12 km object square (1.25 km², above the 1 km² minimum) and
# its 1.5× and 2× multiples. Larger rectangles cover a river in one piece but no
# longer fit a 6–7 thousand rouble budget, so the grouping stops at 2.24 km.
ZONE_SIDES_KM = (1.12, 1.68, 2.24)
# Exact basket search stays at 2^16 combinations with 10 object zones, the broad
# zone and at most five uncertainty zones; this is a compute limit, not a tuned value.
MAX_UNCERTAINTY_ZONES = 5
STRIDE_KM = 0.14
DOUBT_RULE = (f"map error chance = 1 - p where flood_mask = 1 and p where flood_mask = 0 (calibrated p); "
              f"doubtful pixel: chance >= {DOUBT_THRESHOLD} (top decile of the chance on validation; "
              f"{DOUBT_CHECK}); expected error area = sum of chances")
ZONE_RULE = (f"greedy non-overlapping rectangles with sides {ZONE_SIDES_KM} km on a {STRIDE_KM} km stride "
             "maximising doubtful area above the analysed area's average density (the density a broad "
             f"purchase gets); stop after {MAX_UNCERTAINTY_ZONES} zones or when no rectangle beats the average")


@lru_cache(maxsize=16)
def doubt_map(scene_id: str) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel map error chance (0 outside valid pixels) and the valid-pixel mask."""
    info = metadata(scene_id)
    shape = (info["height"], info["width"])
    model = np.frombuffer(mask(scene_id, "model"), dtype=np.uint8).reshape(shape)
    probability = np.frombuffer(float_map(scene_id, "probability"), dtype=np.float32).reshape(shape)
    valid = model != 255
    doubt = np.where(model == 1, 1.0 - probability, probability).astype(np.float64)
    return np.clip(np.where(valid, doubt, 0.0), 0.0, 1.0), valid


def doubtful(scene_id: str) -> np.ndarray:
    doubt, valid = doubt_map(scene_id)
    return valid & (doubt >= DOUBT_THRESHOLD)


def _table(values: np.ndarray) -> np.ndarray:
    table = np.zeros((values.shape[0] + 1, values.shape[1] + 1))
    table[1:, 1:] = values.cumsum(0).cumsum(1)
    return table


@lru_cache(maxsize=16)
def _integral(scene_id: str, kind: str) -> np.ndarray:
    doubt, valid = doubt_map(scene_id)
    values = {"doubtful": doubtful(scene_id), "chance": doubt, "valid": valid}[kind]
    return _table(values.astype(np.float64))


def box_sum(table: np.ndarray, r0: int, r1: int, c0: int, c1: int) -> float:
    """Sum over rows r0..r1-1 and columns c0..c1-1 of the integral image."""
    return float(table[r1, c1] - table[r0, c1] - table[r1, c0] + table[r0, c0])


def window_sum(scene_id: str, kind: str, window: tuple[int, int, int, int]) -> float:
    return box_sum(_integral(scene_id, kind), *window)


def pixel_window(info: dict, bounds: list[float]) -> tuple[int, int, int, int]:
    """Row/column window of the scene grid that intersects lon/lat bounds."""
    west, south, east, north = info["bbox"]
    width, height = info["width"], info["height"]
    c0 = max(0, floor((bounds[0] - west) / (east - west) * width))
    c1 = min(width, ceil((bounds[2] - west) / (east - west) * width))
    r0 = max(0, floor((north - bounds[3]) / (north - south) * height))
    r1 = min(height, ceil((north - bounds[1]) / (north - south) * height))
    return r0, r1, c0, c1


def pixel_edges(info: dict, bounds: list[float]) -> tuple[int, int, int, int]:
    """Nearest pixel edges of lon/lat bounds, clipped to the grid."""
    west, south, east, north = info["bbox"]
    width, height = info["width"], info["height"]
    c0 = min(width, max(0, round((bounds[0] - west) / (east - west) * width)))
    c1 = min(width, max(0, round((bounds[2] - west) / (east - west) * width)))
    r0 = min(height, max(0, round((north - bounds[3]) / (north - south) * height)))
    r1 = min(height, max(0, round((north - bounds[1]) / (north - south) * height)))
    return r0, r1, c0, c1


def pixel_size_km(info: dict) -> tuple[float, float]:
    """Pixel height and width in km; live RTC grids are not square."""
    west, south, east, north = info["bbox"]
    middle = cos((south + north) / 2 * pi / 180)
    return ((north - south) * 110.574 / info["height"], (east - west) * 111.32 * middle / info["width"])


def _to_bounds(info: dict, r0: int, r1: int, c0: int, c1: int) -> list[float]:
    west, south, east, north = info["bbox"]
    dx = (east - west) / info["width"]
    dy = (north - south) / info["height"]
    return [west + c0 * dx, north - r1 * dy, west + c1 * dx, north - r0 * dy]


def _positions(start: int, stop: int, size: int, stride: int) -> np.ndarray:
    values = np.arange(start, stop - size + 1, stride)
    return values if values[-1] == stop - size else np.append(values, stop - size)


def uncertainty_zones(scene_id: str, window: tuple[int, int, int, int], area_km2) -> list[dict]:
    """Group doubtful pixels of the window into disjoint rectangles of at least 1 km².

    area_km2 maps lon/lat bounds to an equal-area km² value; a rectangle that
    comes out below 1 km² is skipped rather than accepted.
    """
    info = metadata(scene_id)
    r0, r1, c0, c1 = window
    if r1 <= r0 or c1 <= c0:
        return []
    table = _integral(scene_id, "doubtful")
    total = box_sum(table, r0, r1, c0, c1)
    valid_count = window_sum(scene_id, "valid", window)
    if total <= 0 or valid_count <= 0:
        return []
    density = total / valid_count
    pixel_h, pixel_w = pixel_size_km(info)
    row_stride = max(1, round(STRIDE_KM / pixel_h))
    col_stride = max(1, round(STRIDE_KM / pixel_w))
    shapes = sorted({(ceil(a / pixel_h), ceil(b / pixel_w)) for a in ZONE_SIDES_KM for b in ZONE_SIDES_KM})
    taken = np.zeros((info["height"], info["width"]), dtype=np.float64)
    zones: list[dict] = []
    while len(zones) < MAX_UNCERTAINTY_ZONES:
        taken_table = _table(taken)
        best = None
        for height, width in shapes:
            if height > r1 - r0 or width > c1 - c0:
                continue
            top, left = np.meshgrid(_positions(r0, r1, height, row_stride),
                                    _positions(c0, c1, width, col_stride), indexing="ij")
            bottom, right = top + height, left + width

            def boxes(source: np.ndarray) -> np.ndarray:
                return source[bottom, right] - source[top, right] - source[bottom, left] + source[top, left]

            mass = boxes(table)
            excess = np.where(boxes(taken_table) == 0, mass - density * height * width, -np.inf)
            index = np.unravel_index(int(np.argmax(excess)), excess.shape)
            if best is None or excess[index] > best[0]:
                best = (float(excess[index]), int(top[index]), int(bottom[index]),
                        int(left[index]), int(right[index]), float(mass[index]))
        if best is None or best[0] <= 0:
            break
        _, top, bottom, left, right, mass = best
        taken[top:bottom, left:right] = 1
        bounds = _to_bounds(info, top, bottom, left, right)
        if area_km2(bounds) < 1:
            continue
        zones.append({"bounds": bounds, "doubt_share": mass / total,
                      "density_vs_mean": mass / ((bottom - top) * (right - left)) / density})
    return zones


def doubt_atoms(scene_id: str, window: tuple[int, int, int, int],
                zones: list[dict]) -> tuple[np.ndarray, np.ndarray, float]:
    """Split the window by every zone edge; each piece gets its doubtful area and the zones over it.

    Union coverage of any basket is then the sum over pieces touched by the basket,
    with no double counting where zones overlap.
    """
    info = metadata(scene_id)
    r0, r1, c0, c1 = window
    table = _integral(scene_id, "doubtful")
    total = box_sum(table, r0, r1, c0, c1)
    rects = []
    for zone in zones:
        zr0, zr1, zc0, zc1 = pixel_edges(info, zone["bounds"])
        rects.append((max(zr0, r0), min(zr1, r1), max(zc0, c0), min(zc1, c1)))
    rows = np.array(sorted({r0, r1} | {v for rect in rects for v in rect[:2] if r0 <= v <= r1}))
    cols = np.array(sorted({c0, c1} | {v for rect in rects for v in rect[2:] if c0 <= v <= c1}))
    top, bottom = rows[:-1, None], rows[1:, None]
    left, right = cols[None, :-1], cols[None, 1:]
    mass = table[bottom, right] - table[top, right] - table[bottom, left] + table[top, left]
    masks = np.zeros(mass.shape, dtype=np.int64)
    for k, (zr0, zr1, zc0, zc1) in enumerate(rects):
        if zr1 <= zr0 or zc1 <= zc0:
            continue
        inside = (top >= zr0) & (bottom <= zr1) & (left >= zc0) & (right <= zc1)
        masks |= inside.astype(np.int64) << k
    unique, inverse = np.unique(masks.ravel(), return_inverse=True)
    sums = np.bincount(inverse, weights=mass.ravel())
    keep = (unique != 0) & (sums > 0)
    return unique[keep], sums[keep], total


on_refresh(doubt_map.cache_clear)
on_refresh(_integral.cache_clear)
