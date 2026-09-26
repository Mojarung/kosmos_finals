"""Fit and check the Sentinel-1 RTC → Sen1Floods11 GRD radiometric mapping.

Usage (needs torch and network, e.g. inside the backend container):
    python scripts/fit_rtc_harmonization.py OUT_DIR

The frozen model was trained on Sen1Floods11 GRD (GEE sigma0 dB). Planetary
Computer RTC of the same pass shows water about 4 dB brighter in VH, so the
model misses it. For each band the mapping is the median GRD dB per 0.5 dB bin
of RTC dB over pixel pairs of the same acquisition, made monotonic. It is fitted
on train chips only and checked on independent test_core chips against the
manual label, with and without the mapping.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live import CALIBRATION, SAS, _ensemble, _groups, _json, _read_band  # noqa: E402
from app.permanent import _read_pass  # noqa: E402

BUCKET = "https://storage.googleapis.com/sen1floods11/v1.1/data/flood_events/HandLabeled"
EDGES = np.arange(-40.0, 10.5, 0.5)
PER_EVENT = 2
WORKERS = 4
PAIRS_PER_CHIP = 30000
MIN_BIN = 100
MIN_CORR = 0.6


def read_tif(layer: str, chip: str) -> tuple[np.ndarray, list[float]]:
    with urllib.request.urlopen(f"{BUCKET}/{layer}/{chip}_{layer}.tif", timeout=60) as response:
        data = response.read()
    with rasterio.open(io.BytesIO(data)) as src:
        return src.read().astype("float32"), list(src.bounds)


def _read_vh(group: dict, token: str, bounds: list[float]) -> np.ndarray:
    vh = np.full((512, 512), np.nan, dtype=np.float32)
    for source in group["features"]:
        band = _read_band(source["assets"]["vh"]["href"], token, bounds)
        fill = np.isfinite(band) & ~np.isfinite(vh)
        vh[fill] = band[fill]
        if np.isfinite(vh).all():
            break
    return vh


def same_pass(chip: dict, grd: np.ndarray, bounds: list[float], token: str):
    """RTC pass of the chip's own date that matches the GRD chip best (VH correlation, not labels)."""
    day = datetime.fromisoformat(chip["observed_at"])
    period = f"{(day - timedelta(days=1)).isoformat()}Z/{(day + timedelta(days=2)).isoformat()}Z"
    best = None
    for group in [g for g in _groups(bounds, period, 20) if g["full_bbox"]][:2]:
        vh = _read_vh(group, token, bounds)
        both = np.isfinite(vh) & np.isfinite(grd[0]) & np.isfinite(grd[1])
        if both.sum() < 1000:
            continue
        corr = float(np.corrcoef(vh[both], grd[1][both])[0, 1])
        if best is None or corr > best[0]:
            best = (corr, group)
    if not best or best[0] < MIN_CORR:
        return None
    vv, vh, valid = _read_pass(best[1], token, bounds)
    both = valid & np.isfinite(grd[0]) & np.isfinite(grd[1])
    return best[0], best[1]["features"][0]["id"], vv, vh, both


QUANTILES = np.linspace(0.005, 0.995, 100)


def fit_quantiles(pairs: dict) -> dict:
    """Map RTC quantiles onto GRD quantiles of the same pixels: keeps the water tail and the contrast."""
    mapping = {}
    for band in ("vv", "vh"):
        rtc = np.concatenate([item[0] for item in pairs[band]])
        grd = np.concatenate([item[1] for item in pairs[band]])
        mapping[band] = {"rtc_db": np.quantile(rtc, QUANTILES).round(4).tolist(),
                         "grd_db": np.quantile(grd, QUANTILES).round(4).tolist()}
    return mapping


def fit(pairs: dict) -> dict:
    mapping = {}
    for band in ("vv", "vh"):
        rtc = np.concatenate([item[0] for item in pairs[band]])
        grd = np.concatenate([item[1] for item in pairs[band]])
        index = np.digitize(rtc, EDGES) - 1
        centers, values = [], []
        for bin_index in range(len(EDGES) - 1):
            chosen = grd[index == bin_index]
            if chosen.size >= MIN_BIN:
                centers.append(float((EDGES[bin_index] + EDGES[bin_index + 1]) / 2))
                values.append(float(np.median(chosen)))
        mapping[band] = {"rtc_db": centers, "grd_db": np.maximum.accumulate(values).round(4).tolist()}
    return mapping


def harmonize(vv: np.ndarray, vh: np.ndarray, mapping: dict) -> tuple[np.ndarray, np.ndarray]:
    out = []
    for band, values in (("vv", vv), ("vh", vh)):
        table = mapping[band]
        mapped = np.interp(values, table["rtc_db"], table["grd_db"]).astype("float32")
        out.append(np.where(np.isfinite(values), mapped, np.nan))
    return out[0], out[1]


def iou(prediction: np.ndarray, truth: np.ndarray, valid: np.ndarray) -> float | None:
    p, t = prediction[valid], truth[valid]
    union = int((p | t).sum())
    return round(int((p & t).sum()) / union, 4) if union else None


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    catalog = json.loads((ROOT / "data" / "catalog" / "sen1floods11_handlabeled.json").read_text(encoding="utf-8"))
    by_part = defaultdict(lambda: defaultdict(list))
    for chip in catalog["chips"]:
        by_part[chip["split_part"]][chip["event_key"]].append(chip)
    token = _json(SAS)["token"]
    pairs = {"vv": [], "vh": []}
    fitted = []
    rng = np.random.default_rng(0)

    def load(chip):
        grd, bounds = read_tif("S1Hand", chip["chip_id"])
        return chip, grd, same_pass(chip, grd, bounds, token)

    train = [chip for _, chips in sorted(by_part["train"].items()) for chip in chips[:PER_EVENT]]
    with ThreadPoolExecutor(WORKERS) as pool:
        loaded = list(pool.map(load, train))
    for chip, grd, match in loaded:
            event = chip["event_key"]
            if not match:
                print("skip", chip["chip_id"], flush=True)
                continue
            corr, item_id, vv, vh, both = match
            rows, cols = np.nonzero(both)
            pick = rng.choice(len(rows), size=min(PAIRS_PER_CHIP, len(rows)), replace=False)
            pairs["vv"].append((vv[rows[pick], cols[pick]], grd[0][rows[pick], cols[pick]]))
            pairs["vh"].append((vh[rows[pick], cols[pick]], grd[1][rows[pick], cols[pick]]))
            fitted.append({"chip_id": chip["chip_id"], "event": event, "rtc_item": item_id, "vh_corr": round(corr, 3)})
            print("fit", chip["chip_id"], item_id, round(corr, 3), flush=True)
    mappings = {"median": fit(pairs), "quantile": fit_quantiles(pairs)}
    rules = {"median": "median GRD dB per 0.5 dB bin of same-pass RTC dB, monotonic; train chips only",
             "quantile": "RTC dB quantiles 0.5..99.5% mapped onto GRD dB quantiles of the same pixels; train chips only"}
    for name, mapping in mappings.items():
        record = {"rule": rules[name], "fit_chips": fitted, **mapping}
        (out / f"rtc_harmonization_{name}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    import torch
    from floodvalue.ensemble import run_ensemble
    from floodvalue.infer import load_calibration
    calibration, threshold = load_calibration(CALIBRATION)
    members = _ensemble()

    def water(vv, vh):
        with torch.inference_mode():
            result = run_ensemble(members, [np.stack([vv, vh])], calibration)
        return np.asarray(result.prob[0]) >= threshold

    checks = []

    def load_test(chip):
        grd, bounds = read_tif("S1Hand", chip["chip_id"])
        label, _ = read_tif("LabelHand", chip["chip_id"])
        return chip, grd, label, same_pass(chip, grd, bounds, token)

    tests = [chip for _, chips in sorted(by_part["test_core"].items()) for chip in chips[:PER_EVENT]]
    with ThreadPoolExecutor(WORKERS) as pool:
        loaded_tests = list(pool.map(load_test, tests))
    for chip, grd, label, match in loaded_tests:
            event = chip["event_key"]
            if not match:
                print("skip", chip["chip_id"], flush=True)
                continue
            corr, item_id, vv, vh, both = match
            truth = label[0] == 1
            valid = both & (label[0] >= 0)
            mv, mh = harmonize(vv, vh, mappings["median"])
            qv, qh = harmonize(vv, vh, mappings["quantile"])
            row = {"chip_id": chip["chip_id"], "event": event, "rtc_item": item_id, "vh_corr": round(corr, 3),
                   "grd_model": iou(water(grd[0], grd[1]), truth, valid),
                   "grd_baseline": iou(grd[1] < -23.6, truth, valid),
                   "rtc_model_raw": iou(water(vv, vh), truth, valid),
                   "rtc_model_median": iou(water(mv, mh), truth, valid),
                   "rtc_model_quantile": iou(water(qv, qh), truth, valid),
                   "rtc_baseline_raw": iou(vh < -23.6, truth, valid),
                   "rtc_baseline_quantile": iou(qh < -23.6, truth, valid),
                   "water_pixels": int((truth & valid).sum()), "valid_pixels": int(valid.sum())}
            checks.append(row)
            print("check", json.dumps(row), flush=True)
    def pooled(key):
        values = [(row[key], row["water_pixels"]) for row in checks if row[key] is not None]
        return round(sum(v * w for v, w in values) / sum(w for _, w in values), 4) if values else None
    summary = {"rules": rules, "fit_chips": len(fitted), "check_chips": len(checks),
               "water_weighted_iou": {key: pooled(key) for key in ("grd_model", "grd_baseline", "rtc_model_raw",
                                                                  "rtc_model_median", "rtc_model_quantile",
                                                                  "rtc_baseline_raw", "rtc_baseline_quantile")},
               "chips": checks}
    (out / "rtc_transfer.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary["water_weighted_iou"]))


if __name__ == "__main__":
    main()
