"""Fetch the context layers for any grid from open archives.

The same functions build the training layers of the Sen1Floods11 chips
(``scripts/fresh_fetch_aux.py``) and the layers of a live frame in the service, so a
frame gets exactly the inputs the model learned on:

* Copernicus DEM GLO-30 and ESA WorldCover 2021 from Microsoft Planetary Computer,
  GLO-30 HAND from the AWS open-data bucket ``glo-30-hand``;
* Sentinel-1 RTC of the event's relative orbit 5-365 days before the event, read from
  the first COG overview (20 m): per-pixel 10/50/90th percentiles and the latest date;
* Sentinel-2 L2A on the S1 date or up to two days earlier, SCL-masked, with the
  processing-baseline 04.00 reflectance offset removed.

A grid is ``{"crs", "transform", "width", "height", "bounds"}`` in the grid CRS.
"""

from __future__ import annotations

import math
import time
import warnings
from datetime import date, datetime, timedelta

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window, from_bounds

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
HAND_URL = "https://glo-30-hand.s3.amazonaws.com/v1/2021/Copernicus_DSM_COG_10_{lat}_00_{lon}_00_HAND.tif"
GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_MULTIRANGE="YES",
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    GDAL_HTTP_MAX_RETRY="6",
    GDAL_HTTP_RETRY_DELAY="3",
    GDAL_CACHEMAX=512,
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF,.tiff",
)
PRE_DATES = 10
PRE_MIN_GAP_DAYS = 5
PRE_WINDOW_DAYS = 365
S2_MAX_LAG_DAYS = 2
L2A_ASSETS = ("B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12")
SCL_UNUSABLE = (0, 1, 3, 8, 9, 10)  # no data, defective, cloud shadow, cloud medium/high, cirrus


def catalog():
    import pystac_client

    return pystac_client.Client.open(STAC)


def signed(item):
    import planetary_computer as pc

    return pc.sign(item)


# ------------------------------------------------------------------ raster reading

def read_onto(src, grid: dict, resampling: Resampling, band: int = 1) -> np.ndarray:
    """Windowed read around the grid from an open dataset, reprojected in memory; NaN outside."""
    dst = np.full((grid["height"], grid["width"]), np.nan, np.float32)
    left, bottom, right, top = transform_bounds(grid["crs"], src.crs, *grid["bounds"])
    pad = 4 * max(abs(src.transform.a), abs(src.transform.e))
    window = from_bounds(left - pad, bottom - pad, right + pad, top + pad, transform=src.transform)
    try:
        window = window.intersection(Window(0, 0, src.width, src.height)).round_offsets().round_lengths()
    except rasterio.errors.WindowError:
        return dst
    if window.width < 2 or window.height < 2:
        return dst
    data = src.read(band, window=window).astype(np.float32)
    if src.nodata is not None and not np.isnan(src.nodata):
        data[data == src.nodata] = np.nan
    reproject(data, dst, src_transform=src.window_transform(window), src_crs=src.crs, src_nodata=np.nan,
              dst_transform=grid["transform"], dst_crs=grid["crs"], dst_nodata=np.nan, resampling=resampling)
    return dst


def retry(fn, attempts: int = 5):
    for attempt in range(attempts):
        try:
            return fn()
        except rasterio.errors.RasterioIOError:
            if attempt == attempts - 1:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError


def warp(href: str, grid: dict, resampling: Resampling, **open_kwargs) -> np.ndarray:
    def run() -> np.ndarray:
        with rasterio.Env(**GDAL_ENV), rasterio.open(href, **open_kwargs) as src:
            return read_onto(src, grid, resampling)
    return retry(run)


def nanmean(layers: list[np.ndarray]) -> np.ndarray:
    if len(layers) == 1:
        return layers[0]
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return np.nanmean(np.stack(layers), axis=0)


def grid_bounds_4326(grid: dict) -> list[float]:
    return list(transform_bounds(grid["crs"], "EPSG:4326", *grid["bounds"]))


# ------------------------------------------------------------------ static layers

def tile_names(bounds: tuple[float, float, float, float] | list[float]) -> list[tuple[str, str]]:
    left, bottom, right, top = bounds
    out = []
    for lat in range(math.floor(bottom), math.floor(top - 1e-9) + 1):
        for lon in range(math.floor(left), math.floor(right - 1e-9) + 1):
            out.append((f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}", f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}"))
    return out


def fetch_static(grid: dict, client=None) -> dict[str, np.ndarray]:
    """DEM (m), HAND (m) and WorldCover 2021 class codes on the grid."""
    client = client or catalog()
    bbox = grid_bounds_4326(grid)
    dem_items = [signed(it) for it in client.search(collections=["cop-dem-glo-30"], bbox=bbox).items()]
    dem = nanmean([warp(it.assets["data"].href, grid, Resampling.bilinear) for it in dem_items])
    hand = nanmean([warp("/vsicurl/" + HAND_URL.format(lat=la, lon=lo), grid, Resampling.bilinear)
                    for la, lo in tile_names(bbox)])
    wc_items = [signed(it) for it in client.search(collections=["esa-worldcover"], bbox=bbox).items()
                if "_2021_" in it.id]
    wc_layers = [warp(it.assets["map"].href, grid, Resampling.nearest) for it in wc_items]
    wc = np.nanmax(np.stack(wc_layers), axis=0) if wc_layers else np.zeros(dem.shape, np.float32)
    return {"dem": dem.astype(np.float32), "hand": hand.astype(np.float32),
            "worldcover": np.nan_to_num(wc, nan=0).astype(np.uint8)}


# ------------------------------------------------------------------ pre-event radar

def pick_dates(dates: list[str], k: int) -> list[str]:
    """Always keep the latest date, spread the rest evenly over the year."""
    if len(dates) <= k:
        return dates
    rest = dates[:-1]
    idx = np.linspace(0, len(rest) - 1, k - 1).round().astype(int)
    return sorted({rest[i] for i in idx} | {dates[-1]})


def pre_event_items(bbox: list[float], event_day: date, relative_orbit: int, client=None) -> dict[str, list]:
    client = client or catalog()
    window = f"{event_day - timedelta(days=PRE_WINDOW_DAYS)}/{event_day - timedelta(days=PRE_MIN_GAP_DAYS)}"
    by_date: dict[str, list] = {}
    for it in client.search(collections=["sentinel-1-rtc"], bbox=bbox, datetime=window).items():
        if it.properties.get("sat:relative_orbit") == relative_orbit and {"vv", "vh"} <= set(it.assets):
            by_date.setdefault(str(it.datetime.date()), []).append(it)
    return by_date


def nan_quantiles(arr: np.ndarray, qs: tuple[float, ...]) -> np.ndarray:
    """Linear-interpolated quantiles over axis 0 ignoring NaN (same as nanpercentile, much faster)."""
    finite = np.isfinite(arr)
    count = finite.sum(axis=0)
    ordered = np.sort(np.where(finite, arr, np.inf), axis=0)
    out = []
    for q in qs:
        pos = q * np.maximum(count - 1, 0)
        lo = np.floor(pos).astype(np.int64)
        hi = np.minimum(lo + 1, np.maximum(count - 1, 0))
        a = np.take_along_axis(ordered, lo[None], axis=0)[0]
        b = np.take_along_axis(ordered, hi[None], axis=0)[0]
        with np.errstate(invalid="ignore"):
            value = a + (b - a) * (pos - lo)
        value[count == 0] = np.nan
        out.append(value)
    return np.stack(out)


def aggregate_pre(frames: list[np.ndarray]) -> dict[str, np.ndarray]:
    """Per-pixel statistics of pre-event [2, H, W] dB frames in date order."""
    if not frames:
        raise ValueError("no pre-event frames")
    arr = np.stack(frames)
    q = nan_quantiles(arr, (0.1, 0.5, 0.9))
    last = arr[-1].copy()
    for older in arr[-2::-1]:  # fill holes of the latest date from the latest date that has the pixel
        hole = ~np.isfinite(last)
        if not hole.any():
            break
        last[hole] = older[hole]
    return {"pre_med": q[1], "pre_p10": q[0], "pre_p90": q[2], "pre_last": last,
            "pre_n": np.isfinite(arr[:, 1]).sum(axis=0).astype(np.uint8)}


def fetch_pre_event(grid: dict, event_day: date, relative_orbit: int, k: int = PRE_DATES,
                    overview: int | None = 0, client=None, progress=None) -> tuple[dict[str, np.ndarray], list[str]]:
    """Pre-event RTC statistics in RTC dB; also returns the dates used."""
    from shapely.geometry import box, shape

    bbox = grid_bounds_4326(grid)
    by_date = pre_event_items(bbox, event_day, relative_orbit, client)
    footprint = box(*bbox)
    usable = sorted(d for d, items in by_date.items() if any(shape(it.geometry).intersects(footprint) for it in items))
    frames, kept = [], []
    chosen = pick_dates(usable, k)
    kwargs = {} if overview is None else {"overview_level": overview}
    for i, day in enumerate(chosen, 1):
        items = [signed(it) for it in by_date[day] if shape(it.geometry).intersects(footprint)]
        pols = []
        for pol in ("vv", "vh"):
            lin = nanmean([warp(it.assets[pol].href, grid, Resampling.bilinear, **kwargs) for it in items])
            lin[~(lin > 0)] = np.nan
            with np.errstate(all="ignore"):
                pols.append(10 * np.log10(lin))
        frame = np.stack(pols)
        if progress:
            progress(i / len(chosen), day)
        if np.isfinite(frame).all(axis=0).mean() < 0.05:
            continue
        frames.append(frame)
        kept.append(day)
    if not frames:
        raise LookupError("no Sentinel-1 RTC of this orbit in the year before the event")
    return aggregate_pre(frames), kept


# ------------------------------------------------------------------ Sentinel-2 L2A

def l2a_offset(item) -> float:
    """Reflectance offset: baseline 04.00+ products store DN = 10000 * reflectance + 1000."""
    baseline = str(item.properties.get("s2:processing_baseline", "00.00"))
    return 1000.0 if baseline >= "04.00" else 0.0


def merge_l2a(tiles: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """Combine (reflectance [12,H,W], usable [H,W]) tiles: clearest tile first, then fill its gaps."""
    shape_ = tiles[0][0].shape
    reflectance = np.zeros(shape_, np.float32)
    usable = np.zeros(shape_[1:], bool)
    for bands, ok in sorted(tiles, key=lambda t: -int(t[1].sum())):
        take = ok & ~usable
        reflectance[:, take] = bands[:, take]
        usable |= take
    return reflectance, usable


def fetch_s2_l2a(grid: dict, s1_day: date, client=None) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """L2A reflectance [12, H, W] and usable mask from the latest date in [S1 - 2 days, S1 date]."""
    client = client or catalog()
    bbox = grid_bounds_4326(grid)
    window = f"{s1_day - timedelta(days=S2_MAX_LAG_DAYS)}/{s1_day}"
    items = list(client.search(collections=["sentinel-2-l2a"], bbox=bbox, datetime=window).items())
    empty = (np.zeros((len(L2A_ASSETS), grid["height"], grid["width"]), np.float32),
             np.zeros((grid["height"], grid["width"]), bool), [])
    if not items:
        return empty
    latest = max(str(it.datetime.date()) for it in items)
    tiles, used = [], []
    for it in (signed(it) for it in items if str(it.datetime.date()) == latest):
        scl = warp(it.assets["SCL"].href, grid, Resampling.nearest)
        if not (np.nan_to_num(scl) > 0).any():
            continue
        offset = l2a_offset(it)
        bands = np.stack([warp(it.assets[b].href, grid, Resampling.bilinear) for b in L2A_ASSETS])
        ok = ~np.isin(np.nan_to_num(scl, nan=0).astype(np.uint8), SCL_UNUSABLE) & (np.nan_to_num(bands) > offset).all(axis=0)
        tiles.append(((np.nan_to_num(bands) - offset) / 10000.0, ok))
        used.append(it.id)
    if not tiles:
        return empty
    reflectance, usable = merge_l2a(tiles)
    return reflectance, usable, used


def as_day(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10].replace("/", "-"))
