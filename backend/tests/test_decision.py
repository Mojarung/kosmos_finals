"""Procurement decision: coverage, doubt zones, permanent water and the export built from them."""

import io
import json
import zipfile
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app.analysis import analyze, context
from app.live import GRID, _portfolio
from app.main import app
from app.planning import contains, enrich_assets, quote
from app.scene_builder import SceneSpec, build_scene
from app.zoning import DOUBT_THRESHOLD
from synthetic import PERMANENT_ROWS, make_chip
from test_planning import validator

ROOT = Path(__file__).resolve().parents[2]
REGION = "5.801,9.067,5.83,9.096"


def test_broad_strategy_reports_the_assets_it_covers():
    result = analyze(None, 6500, "risk", 8, None, "Nigeria_812045")
    broad = result["strategies"]["B"]
    zone = next(zone for zone in result["zones"] if zone["id"] == "ZALL")
    covered = [asset["id"] for asset in result["assets"] if contains(zone["bounds"], asset)]
    assert broad["covered_asset_ids"] == covered and len(covered) == 10
    assert broad["coverage_share"] == 1.0 and broad["covered_doubt_share"] == 1.0


def test_coverage_share_is_empty_without_estimated_exposure():
    region = [5.7952, 9.07, 5.8081, 9.0828]
    ctx = context("Nigeria_812045", region)
    assert ctx.exposure == 0
    result = analyze(region, 6500, "risk", 8, None, "Nigeria_812045")
    for key in ("A", "B", "C"):
        assert result["strategies"][key]["coverage_share"] is None
        assert result["strategies"][key]["coverage_status"] == "no_estimated_exposure_in_region"


def test_plan_is_exact_best_basket_and_explains_every_zone():
    result = analyze(None, 6500, "risk", 8, None, "Nigeria_812045")
    c = result["strategies"]["C"]
    assert c["data_cost_rub"] <= 6500
    assert c["data_cost_rub"] == quote([z for z in result["zones"] if z["id"] in c["zone_ids"]])["total_rub"]
    decisions = {row["zone_id"]: row for row in result["zone_decisions"]}
    assert set(decisions) == {zone["id"] for zone in result["zones"]}
    assert {key for key, row in decisions.items() if row["selected"]} == set(c["zone_ids"])
    for row in decisions.values():
        if row["reason"] == "over_budget":
            assert row["price_with_rub"] > 6500
        if row["reason"] == "nothing_new":
            assert row["gain_loss_rub"] == 0


def test_uncertainty_zones_are_orderable_and_cover_doubt_cheaper_than_broad():
    for chip in ("Nigeria_812045", "Ghana_313799", "Mekong_333434"):
        result = analyze(None, 6500, "information", 8, None, chip)
        zones = [zone for zone in result["zones"] if zone["kind"] == "uncertainty"]
        assert 1 <= len(zones) <= 5 and all(zone["area_km2"] >= 1 for zone in zones)
        for a in zones:
            for b in zones:
                if a is not b:
                    overlap = min(a["bounds"][2], b["bounds"][2]) - max(a["bounds"][0], b["bounds"][0])
                    height = min(a["bounds"][3], b["bounds"][3]) - max(a["bounds"][1], b["bounds"][1])
                    assert overlap <= 1e-9 or height <= 1e-9
        every = result["doubt"]["all_uncertainty_zones"]
        assert every["data_cost_rub"] < result["strategies"]["B"]["data_cost_rub"]
        assert every["covered_doubt_share"] > 0.5
        frontier = result["doubt"]["frontier"]
        assert all(a["cost_rub"] <= b["cost_rub"] and a["doubt_share"] < b["doubt_share"]
                   for a, b in zip(frontier, frontier[1:]))
        assert frontier[-1]["doubt_share"] == 1.0


def test_doubt_threshold_is_fitted_on_validation_only():
    fitted = json.loads((ROOT / "ml/artifacts/evaluation/doubt_threshold.json").read_text())
    assert fitted["fitted_on"] == "valid" and fitted["quantile"] == 0.9
    assert DOUBT_THRESHOLD == fitted["doubt_threshold"]
    # On the independent part the doubtful pixels must hold errors far above the average.
    check = fitted["test_core"]
    assert check["error_rate_doubtful"] > 4 * check["error_rate"]


def test_permanent_water_is_split_and_keeps_the_case_loss_model(tmp_path):
    files = make_chip(tmp_path / "source", "X_2")
    spec = SceneSpec(chip_id="X_2", event="Тест", observed_at="2018-09-21", seed=3, s1_path=files["s1"],
                     label_path=files["label"], inference_dir=files["inference"], split_part="test_core",
                     jrc_path=files["jrc"])
    build_scene(spec, tmp_path / "out")
    meta = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert meta["permanent_water_pixels"] > 0
    assert meta["flood_water_area_km2"] < meta["model_water_area_km2"]
    for asset in enrich_assets(meta["assets"]):
        assert asset["permanent_water_at_point"] == (asset["row"] < PERMANENT_ROWS)
        assert asset["loss_status"] in {"ok", "no_data"}
        if asset["p_flood"] is not None:
            expected = round(asset["p_flood"] * asset["value_rub"] * asset["vulnerability"], 2)
            assert asset["expected_loss_rub"] == expected
            assert asset["flood_expected_loss_rub"] == (0 if asset["permanent_water_at_point"] else expected)
    nigeria = analyze(None, 6500, "risk", 8, None, "Nigeria_812045")["region"]
    assert nigeria["permanent_status"] == "jrc_waterhand"
    total = nigeria["flood_water_area_km2"] + nigeria["permanent_water_area_km2"]
    assert abs(total - nigeria["model_water_area_km2"]) < 0.002


def test_live_portfolio_sits_on_the_pixel_it_reads():
    rng = np.random.default_rng(1)
    probability = rng.random((GRID, GRID)).astype("float32")
    spread = rng.random((GRID, GRID)).astype("float32") * 0.1
    valid = np.ones((GRID, GRID), dtype=bool)
    valid[:, :100] = False
    bounds = [30.0, 50.0, 30.05, 50.04]
    assets = _portfolio(bounds, probability, spread, probability > 0.9, valid)
    coords = {(round(a["longitude"], 7), round(a["latitude"], 7)) for a in assets}
    assert len(coords) == 10 and len({a["class"] for a in assets}) == 10
    grid_x = {round((a["longitude"] - bounds[0]) / (bounds[2] - bounds[0]), 2) for a in assets}
    assert len(grid_x) > 5, "points must not sit on a regular 5x2 grid"
    for asset in assets:
        col = int((asset["longitude"] - bounds[0]) / (bounds[2] - bounds[0]) * GRID)
        row = int((bounds[3] - asset["latitude"]) / (bounds[3] - bounds[1]) * GRID)
        assert (row, col) == (asset["row"], asset["col"]) and valid[row, col]
        assert asset["p_flood"] == round(float(probability[row, col]), 5)


def test_region_export_matches_the_screen_and_validates(tmp_path):
    client = TestClient(app)
    shown = client.get("/api/analysis", params={"bbox": REGION}).json()
    exported = client.get("/api/export", params={"bbox": REGION})
    path = tmp_path / "region.zip"
    path.write_bytes(exported.content)
    report = validator.validate(path)
    assert report["status"] == "ok"
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        meta = json.loads(archive.read("run_metadata.json"))
        losses = archive.read("asset_loss.csv").decode().splitlines()
        assert "zone_decisions.csv" in archive.namelist() and "permanent_water.tif" in archive.namelist()
    ranked = [line.split(",")[0] for line in losses[1:] if line.split(",")[5]]
    assert ranked == [asset["id"] for asset in shown["assets"] if asset["loss_status"] != "no_data"]
    assert meta["pricing"]["discount_group_id"] == shown["strategies"]["C"]["quote"]["discount_group_id"]
    assert {row["name"] for row in meta["parameters"]} >= {"doubt_threshold", "freshness_coef_T", "budget_rub"}


def test_rtc_harmonization_maps_bands_and_keeps_nodata(tmp_path, monkeypatch):
    from app import live
    vv = np.array([[-20.0, np.nan], [-10.0, -5.0]], dtype=np.float32)
    vh = np.array([[-24.0, -15.0], [np.nan, -12.0]], dtype=np.float32)
    monkeypatch.setattr(live, "HARMONIZATION", tmp_path / "missing.json")
    same_vv, same_vh = live.harmonize(vv, vh)
    assert np.array_equal(same_vv, vv, equal_nan=True) and np.array_equal(same_vh, vh, equal_nan=True)
    table = {"vv": {"rtc_db": [-25.0, -5.0], "grd_db": [-27.0, -6.0]},
             "vh": {"rtc_db": [-30.0, -10.0], "grd_db": [-36.0, -12.0]}}
    path = tmp_path / "harmonization.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setattr(live, "HARMONIZATION", path)
    mapped_vv, mapped_vh = live.harmonize(vv, vh)
    assert np.isnan(mapped_vv[0, 1]) and np.isnan(mapped_vh[1, 0])
    assert mapped_vh[0, 0] < -24 and mapped_vv[1, 1] == np.float32(-6.0)
    assert mapped_vh[0, 0] < mapped_vh[0, 1] < mapped_vh[1, 1]


def test_vector_prices_match_exact_quotes_for_every_basket():
    from app.planning import quote
    ctx = context("Nigeria_812045", None)
    space = ctx.space
    prices = space.prices(1.0)
    for index in np.random.default_rng(3).choice(len(space.subsets), size=400, replace=False):
        assert abs(prices[index] - quote(space.selected(int(index)))["total_rub"]) < 0.011


def test_plan_never_exceeds_budget_near_price_boundaries():
    ctx = context("Nigeria_812045", None)
    prices = np.unique(np.round(ctx.space.prices(1.0), 2))
    for budget in prices[1:40]:
        index = ctx.space.best(float(budget), "risk", 8, None)
        assert index is not None
        assert quote(ctx.space.selected(index))["total_rub"] <= float(budget)
