"""Input stack of the context model: event S1 plus what the ground looked like before.

The v1 model sees only the event VV/VH. Dark radar pixels that are not flood water
(sand, tarmac, radar shadow, smooth fields) and flood water roughened by wind look
alike in one image. The context model adds layers that exist before the event and are
available globally, so the same stack can be built for any new frame:

* pre-event Sentinel-1 RTC of the same relative orbit (5-365 days earlier): median,
  10th/90th percentile and the latest date, mapped onto the Sen1Floods11 GRD scale
  with the train-only quantile table ``artifacts/rtc/harmonization.json``; and the
  change of the event image against the median and the latest date;
* terrain: GLO-30 HAND (height above nearest drainage), elevation above the chip's
  2nd percentile, slope;
* JRC permanent water (the dataset's own JRCWaterHand layer);
* ESA WorldCover 2021 classes as one-hot channels.

Layers are fetched by ``scripts/fresh_fetch_aux.py`` into ``data/aux``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from floodvalue import paths
from floodvalue.chips import LABEL_INVALID, LABEL_WATER, Chip, read_jrc, read_label, read_s1

AUX_DIR = paths.DATA_DIR / "aux"
CACHE_DIR = paths.DATA_DIR / "context_cache"
HARMONIZATION = paths.ARTIFACTS_DIR / "rtc" / "harmonization.json"
WORLDCOVER_CLASSES = (10, 20, 30, 40, 50, 60, 80, 90, 95)

GROUPS: dict[str, tuple[str, ...]] = {
    "s1": ("vv", "vh", "vv_minus_vh", "s1_valid"),
    "pre": ("pre_med_vv", "pre_med_vh", "pre_p10_vv", "pre_p10_vh", "pre_p90_vv", "pre_p90_vh",
            "pre_last_vv", "pre_last_vh", "d_med_vv", "d_med_vh", "d_last_vv", "d_last_vh", "pre_valid"),
    "terrain": ("hand", "rel_elev", "slope"),
    "jrc": ("jrc",),
    "worldcover": tuple(f"wc_{c}" for c in WORLDCOVER_CLASSES),
}
ALL_CHANNELS: tuple[str, ...] = tuple(c for g in GROUPS.values() for c in g)

# Fixed affine scaling (value - centre) / scale; physical ranges, not fitted on any split.
SCALING: dict[str, tuple[float, float]] = {
    "vv": (-10.0, 4.0), "vh": (-17.0, 4.0), "vv_minus_vh": (7.0, 3.0),
    "pre_med_vv": (-10.0, 4.0), "pre_med_vh": (-17.0, 4.0), "pre_p10_vv": (-12.0, 4.0),
    "pre_p10_vh": (-19.0, 4.0), "pre_p90_vv": (-8.0, 4.0), "pre_p90_vh": (-15.0, 4.0),
    "pre_last_vv": (-10.0, 4.0), "pre_last_vh": (-17.0, 4.0),
    "d_med_vv": (0.0, 3.0), "d_med_vh": (0.0, 3.0), "d_last_vv": (0.0, 3.0), "d_last_vh": (0.0, 3.0),
    "hand": (1.5, 1.2), "rel_elev": (2.0, 1.2), "slope": (3.0, 5.0),
}
DB_CLIP = (-40.0, 5.0)


@dataclass(frozen=True)
class ChipStack:
    name: str
    x: np.ndarray  # [C, H, W] float16, channels in ALL_CHANNELS order, NaN-free
    label: np.ndarray  # [H, W] int8: -1 invalid, 0 dry, 1 water
    valid: np.ndarray  # [H, W] bool: finite S1 and labelled


@lru_cache(maxsize=1)
def _harmonization() -> dict[str, tuple[np.ndarray, np.ndarray]]:
    table = json.loads(HARMONIZATION.read_text(encoding="utf-8"))
    return {pol: (np.asarray(table[pol]["rtc_db"], np.float64), np.asarray(table[pol]["grd_db"], np.float64))
            for pol in ("vv", "vh")}


def rtc_to_grd(db: np.ndarray, pol: str) -> np.ndarray:
    """Map RTC dB onto the GRD dB scale of Sen1Floods11 (monotone, train-fitted table)."""
    rtc, grd = _harmonization()[pol]
    out = np.interp(db, rtc, grd).astype(np.float32)
    # Outside the table keep the offset of the nearest end instead of clamping.
    low, high = db < rtc[0], db > rtc[-1]
    out[low] = db[low] + (grd[0] - rtc[0])
    out[high] = db[high] + (grd[-1] - rtc[-1])
    out[~np.isfinite(db)] = np.nan
    return out


def slope_degrees(dem: np.ndarray, transform, lat: float) -> np.ndarray:
    dy = abs(transform.e) * 111_320.0
    dx = abs(transform.a) * 111_320.0 * np.cos(np.radians(lat))
    filled = np.where(np.isfinite(dem), dem, np.nanmedian(dem) if np.isfinite(dem).any() else 0.0)
    gy, gx = np.gradient(filled, dy, dx)
    return np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)


def _raw_layers(name: str) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray]:
    import rasterio

    from floodvalue.chips import layer_path

    root = paths.SEN1FLOODS11_DIR
    chip = Chip.from_name(name)
    s1 = read_s1(root, chip)
    label = read_label(root, chip)
    jrc = read_jrc(root, chip).astype(np.float32)
    with rasterio.open(layer_path(root, chip, "S1Hand")) as src:
        transform, bounds = src.transform, src.bounds
    static = np.load(AUX_DIR / "static" / f"{name}.npz")
    pre = np.load(AUX_DIR / "pre" / f"{name}.npz")

    layers, s1_ok = assemble_layers(s1, pre, static, jrc, transform, (bounds.top + bounds.bottom) / 2)
    return layers, label, s1_ok


def assemble_layers(s1: np.ndarray, pre, static, jrc: np.ndarray, transform, lat: float) -> tuple[dict, np.ndarray]:
    """Named layers of one grid from raw sources; shared by training chips and live frames.

    ``s1``: event VV/VH in GRD dB [2, H, W]; ``pre``: RTC dB statistics from
    ``context_sources.aggregate_pre``; ``static``: dem, hand, worldcover; ``jrc``: 0/1.
    Stored training layers are float16, so float inputs are rounded the same way here.
    """
    f16 = lambda a: np.asarray(a, np.float32).astype(np.float16).astype(np.float32)  # noqa: E731
    s1_ok = np.isfinite(s1).all(axis=0)
    vv, vh = (np.clip(s1[i], *DB_CLIP) for i in range(2))
    layers: dict[str, np.ndarray] = {"vv": vv, "vh": vh, "vv_minus_vh": vv - vh, "s1_valid": s1_ok.astype(np.float32)}

    pre_ok = np.asarray(pre["pre_n"]) > 0
    for stat in ("med", "p10", "p90", "last"):
        for i, pol in enumerate(("vv", "vh")):
            db = f16(pre[f"pre_{stat}"][i])
            layers[f"pre_{stat}_{pol}"] = np.clip(rtc_to_grd(db, pol), *DB_CLIP)
    for pol in ("vv", "vh"):
        layers[f"d_med_{pol}"] = layers[pol] - layers[f"pre_med_{pol}"]
        layers[f"d_last_{pol}"] = layers[pol] - layers[f"pre_last_{pol}"]
    pre_ok &= np.isfinite(layers["pre_med_vv"]) & np.isfinite(layers["pre_med_vh"])
    layers["pre_valid"] = pre_ok.astype(np.float32)

    hand = f16(static["hand"])
    dem = np.asarray(static["dem"], np.float32)
    layers["hand"] = np.log1p(np.clip(hand, 0, 200))
    base = np.nanpercentile(dem, 2) if np.isfinite(dem).any() else 0.0
    layers["rel_elev"] = np.log1p(np.clip(dem - base, 0, 500))
    layers["slope"] = slope_degrees(dem, transform, lat)
    layers["jrc"] = np.asarray(jrc, np.float32)
    wc = np.asarray(static["worldcover"])
    for c in WORLDCOVER_CLASSES:
        layers[f"wc_{c}"] = (wc == c).astype(np.float32)
    return layers, s1_ok


def stack_layers(layers: dict[str, np.ndarray]) -> np.ndarray:
    """[C, H, W] float16 in ALL_CHANNELS order, scaled, NaN-free."""
    channels = []
    for key in ALL_CHANNELS:
        arr = layers[key].astype(np.float32)
        if key in SCALING:
            centre, scale = SCALING[key]
            arr = (arr - centre) / scale
        # Missing values become the neutral 0 (= scaling centre); validity flags carry the rest.
        channels.append(np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0))
    return np.stack(channels).astype(np.float16)


def build_chip(name: str) -> ChipStack:
    layers, label, s1_ok = _raw_layers(name)
    valid = s1_ok & (label != LABEL_INVALID)
    return ChipStack(name, stack_layers(layers), label, valid)


def load_chip(name: str) -> ChipStack:
    """Cached stack of one chip; the cache is rebuilt when missing."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}.npz"
    if path.exists():
        z = np.load(path)
        if tuple(z["channels"].tolist()) == ALL_CHANNELS:
            return ChipStack(name, z["x"], z["label"], z["valid"])
    stack = build_chip(name)
    np.savez(path, x=stack.x, label=stack.label, valid=stack.valid, channels=np.asarray(ALL_CHANNELS))
    return stack


def channel_index(groups: tuple[str, ...]) -> list[int]:
    """Positions in the base stack; the optional ``s2`` group is appended separately (see load_s2)."""
    wanted = [c for g in groups if g != "s2" for c in GROUPS[g]]
    return [ALL_CHANNELS.index(c) for c in wanted]


def target_of(stack: ChipStack) -> np.ndarray:
    return (stack.label == LABEL_WATER).astype(np.float32)


# ------------------------------------------------------------------ optional optics (S2 track)
#
# The optical input is Sentinel-2 L2A from Planetary Computer on the Sen1Floods11 S2 date,
# i.e. the same product a service can fetch for any frame. Pixels that SCL marks as no data,
# defective, cloud shadow, cloud or cirrus are unavailable (all optical channels 0).

S2_BANDS = ("b01", "b02", "b03", "b04", "b05", "b06", "b07", "b08", "b8a", "b09", "b11", "b12")
L2A_ASSETS = ("B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12")
S2_CHANNELS: tuple[str, ...] = S2_BANDS + ("ndwi", "mndwi", "ndvi", "awei", "s2_valid")
S2_CACHE_DIR = paths.DATA_DIR / "context_cache_l2a"
SCL_UNUSABLE = (0, 1, 3, 8, 9, 10)


def s2_allowed(event: str) -> bool:
    """Optics are used only when taken on the S1 date or at most two days earlier."""
    from floodvalue.optical import event_dates, optical_date_allowed

    dates = event_dates(paths.SEN1FLOODS11_DIR)[event]
    return optical_date_allowed(dates["s1_date"], dates["s2_date"])


def s2_features(reflectance: np.ndarray, usable: np.ndarray) -> np.ndarray:
    """[17, H, W] from L2A reflectance [12, H, W] (DN / 10000, offset already removed) and a usable mask."""
    x = np.clip(np.nan_to_num(reflectance), 0, 1.5).astype(np.float32)
    blue, green, red, nir, swir1, swir2 = x[[1, 2, 3, 7, 10, 11]]
    eps = 1e-6
    ndwi = (green - nir) / (green + nir + eps)
    mndwi = (green - swir1) / (green + swir1 + eps)
    ndvi = (nir - red) / (nir + red + eps)
    awei = (blue + 2.5 * green - 1.5 * (nir + swir1) - 0.25 * swir2) * 3
    out = np.concatenate([(x - 0.15) / 0.1, np.stack([ndwi, mndwi, ndvi, awei]), usable[None].astype(np.float32)])
    out[:, ~usable] = 0.0
    return out.astype(np.float16)


def merge_l2a(name: str) -> tuple[np.ndarray, np.ndarray]:
    """Reflectance [12, H, W] and usable mask from the fetched L2A tiles; clearest tile first."""
    folder = AUX_DIR / "s2l2a" / name
    tags = sorted({p.name.split("__")[0] for p in folder.glob("*__SCL.npy")}) if folder.exists() else []
    candidates = []
    for tag in tags:
        paths_ = [folder / f"{tag}__{band}.npy" for band in L2A_ASSETS]
        if not all(p.exists() for p in paths_):
            continue
        scl = np.load(folder / f"{tag}__SCL.npy")
        bands = np.stack([np.load(p) for p in paths_]).astype(np.float32)
        usable = ~np.isin(scl, SCL_UNUSABLE) & (bands > 0).all(axis=0)
        candidates.append((int(usable.sum()), bands / 10000.0, usable))
    reflectance = np.zeros((len(L2A_ASSETS), 512, 512), np.float32)
    usable = np.zeros((512, 512), bool)
    for _, bands, ok in sorted(candidates, key=lambda c: -c[0]):
        take = ok & ~usable
        reflectance[:, take] = bands[:, take]
        usable |= take
    return reflectance, usable


def build_s2(name: str) -> np.ndarray:
    if not s2_allowed(Chip.from_name(name).event):
        return np.zeros((len(S2_CHANNELS), 512, 512), np.float16)
    return s2_features(*merge_l2a(name))


def load_s2(name: str) -> np.ndarray:
    S2_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = S2_CACHE_DIR / f"{name}.npy"
    if path.exists():
        return np.load(path)
    x = build_s2(name)
    np.save(path, x)
    return x


S2_USED: tuple[str, ...] = S2_CHANNELS
S2_USED_INDEX = [S2_CHANNELS.index(c) for c in S2_USED]


def group_channels(group: str) -> tuple[str, ...]:
    return S2_USED if group == "s2" else GROUPS[group]


def n_channels(groups: tuple[str, ...]) -> int:
    return sum(len(group_channels(g)) for g in groups)
