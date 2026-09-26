"""Reproduce the fixed-budget A/B/C retrospective on the saved S1 audit outputs.

The only read of ``target``/``valid`` occurs after BasketSpace.best has selected C.
No imagery is ordered: the manual 10 m S2-derived label is a retrospective proxy.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import catalog  # noqa: E402
from app.planning import BasketSpace, candidates, enrich_assets, quote, rectangle_area_km2  # noqa: E402
from app.zoning import doubt_atoms, uncertainty_zones  # noqa: E402

PROTOCOL = ROOT / "ml/research/audit_20260926/protocol.json"
OUTPUTS = ROOT / "ml/outputs/audit_20260926"
S1 = ROOT / "ml/data/sen1floods11/data/flood_events/HandLabeled/S1Hand"
RESULT = ROOT / "data/backtest/procurement_70.json"
EXPOSURES = np.array([2.5, 8, 1.5, 1.6, 4.2, 4.5, 3.6, 2.7, 2, 4.8]) * 1e6
SEED = 260926
BUDGET = 6500.0
DEADLINE = 8
THRESHOLD = 0.41
CALCULATION_DATE = date(2026, 9, 26)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def point_result(probability, label, exposure, covered, labelled):
    """Score all strategies on the same labelled control points, retaining untouched p elsewhere."""
    current = np.where(covered & labelled, label, probability)
    baseline_error = np.abs(probability[labelled] - label[labelled]) * exposure[labelled]
    after_error = np.abs(current[labelled] - label[labelled]) * exposure[labelled]
    changed = labelled & covered & ((probability >= THRESHOLD) != (current >= THRESHOLD))
    return {"error_before_rub": float(baseline_error.sum()),
            "error_after_rub": float(after_error.sum()),
            "corrected_statuses": int(changed.sum()),
            "covered_labelled_assets": int((covered & labelled).sum())}


def summarize(rows, seed=SEED):
    if not rows:
        return None
    before = sum(r["A"]["error_after_rub"] for r in rows)
    after = sum(r["C"]["error_after_rub"] for r in rows)
    c_cost = sum(r["C"]["data_cost_rub"] for r in rows)
    b_cost = sum(r["B"]["data_cost_rub"] for r in rows)
    before_values = np.array([r["A"]["error_after_rub"] for r in rows])
    after_values = np.array([r["C"]["error_after_rub"] for r in rows])
    picks = np.random.default_rng(seed).integers(0, len(rows), (2000, len(rows)))
    sampled_before = before_values[picks].sum(axis=1)
    sampled_after = after_values[picks].sum(axis=1)
    reduction = np.divide(sampled_before - sampled_after, sampled_before,
                          out=np.zeros_like(sampled_before), where=sampled_before > 0)
    return {"chips": len(rows), "labelled_assets": sum(r["labelled_assets"] for r in rows),
            "invalid_assets": sum(r["invalid_assets"] for r in rows),
            "error_a_rub": round(before, 2), "error_b_rub": round(sum(r["B"]["error_after_rub"] for r in rows), 2),
            "error_c_rub": round(after, 2),
            "c_error_reduction_percent": round(100 * (before - after) / before, 2) if before else None,
            "c_reduction_chip_bootstrap_95_percent": [round(float(v) * 100, 2) for v in np.quantile(reduction, [.025, .975])],
            "c_price_share_of_b_percent": round(100 * c_cost / b_cost, 2) if b_cost else None,
            "c_cost_rub": round(c_cost, 2), "b_cost_rub": round(b_cost, 2),
            "c_corrected_statuses": sum(r["C"]["corrected_statuses"] for r in rows),
            "b_corrected_statuses": sum(r["B"]["corrected_statuses"] for r in rows)}


def run_chip(name: str, part: str, scratch: Path) -> dict:
    # Decision inputs: S1, its saved frozen p, the seeded portfolio and geometry.
    s1_path = S1 / f"{name}_S1Hand.tif"
    with rasterio.open(s1_path) as image:
        radar_valid = np.isfinite(image.read()).all(axis=0)
        bounds = list(image.bounds)
        transform = image.transform
        height, width = image.height, image.width
    saved_path = OUTPUTS / f"{name}.npz"
    with np.load(saved_path) as saved:
        p_grid = saved["service_s1"].copy()
        source_fingerprint = str(saved["fingerprint"])
    seed = SEED + int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
    available = np.flatnonzero(radar_valid)
    pool = available if len(available) >= 10 else np.arange(radar_valid.size)
    points = np.random.default_rng(seed).choice(pool, 10, replace=False)
    rows, cols = np.divmod(points, width)
    assets = []
    for k, (row, col, exposure) in enumerate(zip(rows, cols, EXPOSURES), 1):
        lon, lat = transform * (int(col) + .5, int(row) + .5)
        assets.append({"id": f"A{k:02d}", "name": f"Объект {k}", "longitude": lon, "latitude": lat,
                       "p_flood": float(p_grid[row, col]) if radar_valid[row, col] else None,
                       "value_rub": float(exposure), "vulnerability": 1.0})
    assets = enrich_assets(assets, THRESHOLD)

    scene = scratch / name
    scene.mkdir()
    (scene / "metadata.json").write_text(json.dumps({"width": width, "height": height, "bbox": bounds}))
    model = np.where(radar_valid, p_grid >= THRESHOLD, 255).astype("uint8")
    (scene / "model_mask.bin").write_bytes(model.tobytes())
    (scene / "probability.f32").write_bytes(p_grid.astype("float32").tobytes())
    catalog.refresh()
    window = (0, height, 0, width)
    uncertain = uncertainty_zones(name, window, rectangle_area_km2)
    zones = candidates(assets, bounds, bounds, uncertain, CALCULATION_DATE)
    atoms, mass, total = doubt_atoms(name, window, zones)
    space = BasketSpace(zones, assets, atoms, mass, total)
    c_index = space.best(BUDGET, "risk", DEADLINE, None)
    c_orders = space.orders(c_index, DEADLINE)
    b_index = space.index(["ZALL"])
    b_orders = space.orders(b_index, DEADLINE)
    selected = {"A": [], "B": ["ZALL"], "C": [z["id"] for z in space.selected(c_index)] if c_index is not None else []}
    prices = {"A": 0.0, "B": quote(b_orders, strategy="B", calculation_date=CALCULATION_DATE)["total_rub"],
              "C": quote(c_orders, strategy="C", calculation_date=CALCULATION_DATE)["total_rub"]}
    if prices["C"] > BUDGET:
        raise AssertionError(f"C over budget on {name}")

    # Evaluation boundary: the optical manual label is first accessed here.
    with np.load(OUTPUTS / f"{name}.npz") as saved:
        labelled = saved["valid"].ravel()[points].copy()
        truth = saved["target"].ravel()[points].astype(float).copy()
    probability = p_grid.ravel()[points].astype(float)
    for asset, valid in zip(assets, labelled):
        if valid and asset["p_flood"] is None:
            raise AssertionError(f"labelled point without p: {name}")
    scored = {}
    for key, zone_ids in selected.items():
        covered = np.array([any((z["bounds"][0] <= a["longitude"] <= z["bounds"][2]
                                     and z["bounds"][1] <= a["latitude"] <= z["bounds"][3])
                                for z in zones if z["id"] in zone_ids) for a in assets])
        scored[key] = {**point_result(probability, truth, EXPOSURES, covered, labelled),
                       "data_cost_rub": prices[key], "zone_ids": zone_ids}
    return {"chip_id": name, "part": part, "s1_sha256": sha256(s1_path),
            "prediction_sha256": sha256(saved_path), "prediction_fingerprint": source_fingerprint,
            "labelled_assets": int(labelled.sum()),
            "invalid_assets": int((~labelled).sum()), "candidate_zones": len(zones), **scored}


def main():
    protocol = json.loads(PROTOCOL.read_text())
    ids = [(name, part) for part in ("test_core", "bolivia") for name in protocol["parts"][part]]
    if [sum(part == p for _, part in ids) for p in ("test_core", "bolivia")] != [55, 15]:
        raise AssertionError("Unexpected fixed split")
    with tempfile.TemporaryDirectory(prefix="floodvalue-backtest-") as temporary:
        scratch = Path(temporary)
        (scratch / "scenes.json").write_text(json.dumps({"default_chip_id": ids[0][0],
                                                         "scenes": [{"chip_id": name} for name, _ in ids]}))
        catalog.DATA_DIR = scratch
        catalog.refresh()
        rows = []
        for number, (name, part) in enumerate(ids, 1):
            rows.append(run_chip(name, part, scratch))
            print(f"{number:02d}/{len(ids)} {name}", flush=True)
    result = {"version": 1, "model": "service_s1", "protocol_sha256": sha256(PROTOCOL),
              "budget_rub": BUDGET, "deadline_hours": DEADLINE, "priority": "risk",
              "calculation_date": CALCULATION_DATE.isoformat(),
              "control_point_rule": "audit 13: ten S1-valid seeded positions and fixed case V×q, independent of p and labels",
              "evaluation_rule": "replace p with binary manual water label only for labelled asset points inside timely ordered zones",
              "label_source": "Sen1Floods11 v1.1 LabelHand, manual S2-derived label at approximately 10 m",
              "limitation": "Retrospective proxy, not a purchased 1 m image, measured damage or a blind test. B may exceed the fixed C budget.",
              "interval_method": "2000 descriptive bootstrap resamples of chips within each displayed group; chips within events are dependent",
              "source": "ml/research/audit_20260926/protocol.json + saved ml/outputs/audit_20260926/*.npz + official S1Hand",
              "reproduce": "backend/.venv/bin/python scripts/backtest_procurement.py",
              "summary": summarize(rows),
              "parts": {part: summarize([r for r in rows if r["part"] == part]) for part in ("test_core", "bolivia")},
              "chips": rows}
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
