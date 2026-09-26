"""Fit, choose and check the Sentinel-1 RTC -> Sen1Floods11 GRD radiometric mapping.

Usage (needs torch and network, e.g. inside the backend container):
    python scripts/fit_rtc_harmonization.py [OUT_DIR] [CACHE_DIR]

Without OUT_DIR the shipped files are rewritten: ml/artifacts/rtc/harmonization.json
and ml/artifacts/evaluation/rtc_transfer.json. CACHE_DIR (default ml/outputs/rtc_cache)
keeps the fetched GRD, label and RTC arrays per chip so a rerun needs no network.

The frozen model was trained on Sen1Floods11 GRD (GEE sigma0 dB). Planetary
Computer RTC of the same pass shows water about 4 dB brighter in VH, so the
model misses it. Two per-band mappings are fitted on pixel pairs of the same
acquisition (pass chosen by VH correlation, not by labels):
- median: median GRD dB per 0.5 dB bin of RTC dB, made monotonic;
- quantile: RTC dB quantiles 0.5..99.5% mapped onto GRD dB quantiles of the same pixels.

The shipped one is chosen on train chips only, before any test chip is read:
leave-one-event-out over the train chips (both mappings fitted on the other
events' pairs, the frozen ensemble on the held-out chip's mapped RTC, IoU against
the manual label pooled with water-pixel weights, the metric of the test check).
The higher score ships; a tie keeps median. Leave-one-chip-out is reported beside
it. The chosen mapping is refitted on all train pairs.

Only then are independent test_core chips read and scored once with the shipped
mapping, raw RTC and the GRD chip itself (report only).
"""

from __future__ import annotations

import io
import json
import sys
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
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
MAPPINGS = ("median", "quantile")  # order breaks ties: the first one stays
REPORT_ROLE = "report_only_not_used_for_selection"
SELECTION_RULE = ("leave-one-event-out over train chips: both mappings fitted on the other events' pixel pairs, "
                  "frozen ensemble on the held-out chip's mapped RTC, IoU against the manual label pooled with "
                  "water-pixel weights; the higher score ships, a tie keeps median; fixed before any test chip is read")
# The version before this rule scored both mappings on test_core and shipped the better one.
PREVIOUS_SELECTION = {"selection_status": "post_hoc_on_test_core", "shipped_mapping": "quantile",
                      "test_core_water_weighted_iou": {"rtc_model_median": 0.5808, "rtc_model_quantile": 0.6374},
                      "note": "kept for the audit trail; replaced by the train cross-validation below"}
QUANTILES = np.linspace(0.005, 0.995, 100)


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


def load_chip(chip: dict, token: Callable[[], str], cache: Path) -> dict | None:
    """GRD, manual label and the same-pass RTC of one chip; None when no pass matches."""
    path = cache / f"{chip['chip_id']}.npz"
    if not path.exists():
        grd, bounds = read_tif("S1Hand", chip["chip_id"])
        label, _ = read_tif("LabelHand", chip["chip_id"])
        match = same_pass(chip, grd, bounds, token())
        if match is None:
            np.savez_compressed(path, matched=False)
        else:
            corr, item_id, vv, vh, both = match
            np.savez_compressed(path, matched=True, grd=grd, label=label[0].astype(np.int8), vv=vv, vh=vh,
                                both=both, corr=corr, item_id=item_id)
    with np.load(path) as data:
        if not bool(data["matched"]):
            return None
        label = data["label"]
        truth, valid = label == 1, data["both"] & (label >= 0)
        return {"chip_id": chip["chip_id"], "event": chip["event_key"], "rtc_item": str(data["item_id"]),
                "vh_corr": round(float(data["corr"]), 3), "grd": data["grd"], "vv": data["vv"], "vh": data["vh"],
                "both": data["both"], "truth": truth, "valid": valid, "water_pixels": int((truth & valid).sum()),
                "valid_pixels": int(valid.sum())}


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


FITTERS = {"median": fit, "quantile": fit_quantiles}
RULES = {"median": "median GRD dB per 0.5 dB bin of same-pass RTC dB, monotonic; train chips only",
         "quantile": "RTC dB quantiles 0.5..99.5% mapped onto GRD dB quantiles of the same pixels; train chips only"}


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


def pooled(rows: Sequence[Mapping], key: str) -> float | None:
    """IoU of the rows averaged with water-pixel weights; rows without an IoU are left out."""
    values = [(row[key], row["water_pixels"]) for row in rows if row[key] is not None]
    weight = sum(w for _, w in values)
    return round(sum(v * w for v, w in values) / weight, 4) if weight else None


def cross_validate(chips: Sequence[Mapping], fitters: Mapping[str, Callable[[dict], dict]],
                   score: Callable[[dict, Mapping], float | None], group: str = "event") -> dict:
    """Hold out each group of chips, fit every mapping on the pairs of the rest, score the held-out chips."""
    groups = list(dict.fromkeys(chip[group] for chip in chips))
    rows = []
    for held in groups:
        rest = [chip for chip in chips if chip[group] != held]
        pairs = {band: [chip["pairs"][band] for chip in rest] for band in ("vv", "vh")}
        mappings = {name: fitter(pairs) for name, fitter in fitters.items()}
        for chip in (chip for chip in chips if chip[group] == held):
            rows.append({"chip_id": chip["chip_id"], "held_out": held, "water_pixels": chip["water_pixels"],
                         **{name: score(mapping, chip) for name, mapping in mappings.items()}})
    return {"folds": len(groups), "water_weighted_iou": {name: pooled(rows, name) for name in fitters},
            "chips": rows}


def select_mapping(scores: Mapping[str, float | None], order: Sequence[str] = MAPPINGS) -> str | None:
    """The mapping with the higher CV score; on a tie the earlier one in ``order`` stays."""
    best = None
    for name in order:
        if scores.get(name) is not None and (best is None or scores[name] > scores[best]):
            best = name
    return best


def _chips(catalog: dict, part: str) -> list[dict]:
    by_event = defaultdict(list)
    for chip in catalog["chips"]:
        if chip["split_part"] == part:
            by_event[chip["event_key"]].append(chip)
    return [chip for _, chips in sorted(by_event.items()) for chip in chips[:PER_EVENT]]


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    cache = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "ml" / "outputs" / "rtc_cache"
    cache.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((ROOT / "data" / "catalog" / "sen1floods11_handlabeled.json").read_text(encoding="utf-8"))
    token_cache: dict[str, str] = {}

    def token() -> str:
        if "value" not in token_cache:
            token_cache["value"] = _json(SAS)["token"]
        return token_cache["value"]

    import torch
    from floodvalue.ensemble import run_ensemble
    from floodvalue.infer import load_calibration
    calibration, threshold = load_calibration(CALIBRATION)
    members = _ensemble()

    def water(vv, vh):
        with torch.inference_mode():
            result = run_ensemble(members, [np.stack([vv, vh])], calibration)
        return np.asarray(result.prob[0]) >= threshold

    # 1. Train chips only: pixel pairs, cross-validated choice, refit on all of them.
    with ThreadPoolExecutor(WORKERS) as pool:
        loaded = list(pool.map(lambda chip: load_chip(chip, token, cache), _chips(catalog, "train")))
    rng = np.random.default_rng(0)
    train = []
    for chip in loaded:
        if chip is None:
            continue
        rows, cols = np.nonzero(chip["both"])
        pick = rng.choice(len(rows), size=min(PAIRS_PER_CHIP, len(rows)), replace=False)
        chip["pairs"] = {band: (chip[band][rows[pick], cols[pick]], chip["grd"][index][rows[pick], cols[pick]])
                         for index, band in enumerate(("vv", "vh"))}
        train.append(chip)
        print("fit", chip["chip_id"], chip["rtc_item"], chip["vh_corr"], flush=True)

    def score(mapping, chip):
        return iou(water(*harmonize(chip["vv"], chip["vh"], mapping)), chip["truth"], chip["valid"])

    cv = {scheme: cross_validate(train, FITTERS, score, group)
          for scheme, group in (("leave_one_event_out", "event"), ("leave_one_chip_out", "chip_id"))}
    for scheme, result in cv.items():
        print(scheme, json.dumps(result["water_weighted_iou"]), flush=True)
    shipped = select_mapping(cv["leave_one_event_out"]["water_weighted_iou"])
    fitted = [{key: chip[key] for key in ("chip_id", "event", "rtc_item", "vh_corr")} for chip in train]
    mapping = FITTERS[shipped]({band: [chip["pairs"][band] for chip in train] for band in ("vv", "vh")})
    selection = {"selection_part": "train_cv", "selection_rule": SELECTION_RULE, "shipped_mapping": shipped,
                 "cv_scores": {scheme: {"folds": result["folds"], **result["water_weighted_iou"]}
                               for scheme, result in cv.items()}}
    harmonization = {"rule": RULES[shipped], "mapping": shipped, **selection, "fit_chips": fitted, **mapping}

    # 2. Independent test_core chips, scored once with the shipped mapping (report only).
    with ThreadPoolExecutor(WORKERS) as pool:
        loaded_tests = list(pool.map(lambda chip: load_chip(chip, token, cache), _chips(catalog, "test_core")))
    checks = []
    for chip in loaded_tests:
        if chip is None:
            continue
        vv, vh, grd, truth, valid = chip["vv"], chip["vh"], chip["grd"], chip["truth"], chip["valid"]
        mv, mh = harmonize(vv, vh, mapping)
        row = {"chip_id": chip["chip_id"], "event": chip["event"], "rtc_item": chip["rtc_item"],
               "vh_corr": chip["vh_corr"],
               "grd_model": iou(water(grd[0], grd[1]), truth, valid),
               "grd_baseline": iou(grd[1] < -23.6, truth, valid),
               "rtc_model_raw": iou(water(vv, vh), truth, valid),
               f"rtc_model_{shipped}": iou(water(mv, mh), truth, valid),
               "rtc_baseline_raw": iou(vh < -23.6, truth, valid),
               f"rtc_baseline_{shipped}": iou(mh < -23.6, truth, valid),
               "water_pixels": chip["water_pixels"], "valid_pixels": chip["valid_pixels"]}
        checks.append(row)
        print("check", json.dumps(row), flush=True)
    keys = ("grd_model", "grd_baseline", "rtc_model_raw", f"rtc_model_{shipped}", "rtc_baseline_raw",
            f"rtc_baseline_{shipped}")
    transfer = {"rules": RULES, "fit_chips": len(fitted), "check_chips": len(checks), **selection,
                "cv_chips": {scheme: result["chips"] for scheme, result in cv.items()},
                "test_part": "test_core", "test_role": REPORT_ROLE,
                "water_weighted_iou": {key: pooled(checks, key) for key in keys},
                "previous_selection": PREVIOUS_SELECTION, "chips": checks}
    targets = ((out / "harmonization.json", out / "rtc_transfer.json") if out else
               (ROOT / "ml/artifacts/rtc/harmonization.json", ROOT / "ml/artifacts/evaluation/rtc_transfer.json"))
    for path, record in zip(targets, (harmonization, transfer)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps({"shipped_mapping": shipped, "cv_scores": selection["cv_scores"],
                      "test_core_report_only": transfer["water_weighted_iou"]}))


if __name__ == "__main__":
    main()
