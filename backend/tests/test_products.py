"""Product alternatives of every footprint: optics, all-weather radar and archive context under one price function."""

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import exporter
from app.analysis import analyze
from app.main import app
from app.planning import quote
from test_case_contract import export, pack, table
from test_planning import validator

SCENE = "Nigeria_812045"
YEAR = datetime.now(timezone.utc).year  # orders are placed on the calculation date
CASE_FIELDS = {"candidate_id", "sensor_type", "resolution_m", "acquisition_type", "data_role", "observation_at",
               "available_at", "processing_level", "usage_type", "guaranteed_purchase", "area_km2",
               "base_rate_rub_km2", "base_rate_status", "base_rate_source"}


def plan(deadline: int, priority: str = "risk") -> dict:
    return analyze(None, 6500, priority, deadline, None, SCENE)


def rewrite(rows: list[dict]) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def test_every_footprint_offers_optics_radar_and_archive():
    result = plan(8)
    catalog = {row["candidate_id"]: row for row in result["catalog"]}
    footprints = [zone["id"] for zone in result["zones"]]
    assert len(catalog) == 3 * len(footprints) == len(result["catalog"])
    event = date.fromisoformat(result["chip"]["observed_at"])
    for footprint in footprints:
        optical, sar, archive = catalog[footprint], catalog[f"{footprint}-SAR"], catalog[f"{footprint}-ARC"]
        assert optical["footprint_id"] == sar["footprint_id"] == archive["footprint_id"] == footprint
        assert CASE_FIELDS <= set(optical) and CASE_FIELDS <= set(sar) and CASE_FIELDS <= set(archive)
        assert (optical["sensor_type"], optical["resolution_m"], optical["processing_level"], optical["acquisition_type"],
                optical["delivery_hours"], optical["guaranteed_purchase"], optical["all_weather"]) == (
                "optical", 1, "L2", "new", 6, True, False)
        assert (sar["sensor_type"], sar["resolution_m"], sar["processing_level"], sar["acquisition_type"],
                sar["delivery_hours"], sar["guaranteed_purchase"], sar["all_weather"]) == (
                "sar", 3, "L1", "new", 4, True, True)
        assert (archive["sensor_type"], archive["resolution_m"], archive["acquisition_type"], archive["data_role"],
                archive["guaranteed_purchase"], archive["delivery_hours"]) == ("optical", 1, "archive", "context", False, 0)
        assert (event - date.fromisoformat(archive["observation_at"])).days > 90
        assert (archive["freshness_coef"], archive["processing_coef"], sar["processing_coef"]) == (0.6, 1.2, 1.0)
        assert archive["counts_for_coverage"] is False and archive["reason"] == "context_only"
        assert sar["reason"] == "product_rule" and sar["timely"] and not sar["chosen"]
        for row in (optical, sar, archive):
            assert row["base_rate_status"] == "scenario" and row["usage_type"] == "internal"
            assert row["area_km2"] == optical["area_km2"] and row["price_alone_rub"] > 0
            assert row["basket"] in {"B", "C"} and row["basket_cost_rub"] > 0


@pytest.mark.parametrize("deadline", [8, 12])
def test_optics_on_time_keeps_the_demo_baskets(deadline):
    result = plan(deadline)
    c, b = result["strategies"]["C"], result["strategies"]["B"]
    assert c["zone_ids"] == c["candidate_ids"] == ["Z10", "Z07", "Z08"] and c["data_cost_rub"] == 6116.19
    assert b["zone_ids"] == b["candidate_ids"] == ["ZALL"] and b["timely"] and b["data_cost_rub"] == 37183.56
    assert {line["discount_group_id"] for line in c["quote"]["lines"]} == {f"C_optical_1m_{YEAR}"}
    assert {line["discount_group_id"] for line in b["quote"]["lines"]} == {f"B_optical_1m_{YEAR}"}
    information = plan(deadline, "information")["strategies"]["C"]
    assert information["candidate_ids"]
    assert information["data_cost_rub"] <= 6500
    assert information["covered_doubt_share"] >= c["covered_doubt_share"]
    assert all(zone["sensor_type"] == "optical" and zone["candidate_id"] == zone["id"] for zone in result["zones"])


def test_radar_replaces_late_optics_at_four_hours():
    result = plan(4)
    c, b = result["strategies"]["C"], result["strategies"]["B"]
    assert c["feasible"] and c["candidate_ids"] and c["data_cost_rub"] <= 6500
    assert c["candidate_ids"] == [f"{zone_id}-SAR" for zone_id in c["zone_ids"]]
    assert c["covered_expected_loss_rub"] > 0 and c["timely"]
    for line in c["quote"]["lines"]:
        assert (line["sensor_type"], line["resolution_m"], line["processing_coef"], line["freshness_coef"],
                line["discount_group_id"]) == ("sar", 3, 1.0, 1.8, f"C_sar_3m_{YEAR}")
    catalog = {row["candidate_id"]: row for row in result["catalog"]}
    assert quote([catalog[cid] for cid in c["candidate_ids"]])["total_rub"] == c["data_cost_rub"]
    assert b["candidate_ids"] == ["ZALL-SAR"] and b["timely"] is True and b["budget_feasible"] is False
    assert b["covered_asset_ids"] == [asset["id"] for asset in result["assets"]]
    zones = {zone["id"]: zone for zone in result["zones"]}
    assert len(zones) == len(result["zones"]) == len(catalog) // 3
    assert all(zone["sensor_type"] == "sar" and zone["candidate_id"] == f"{zone['id']}-SAR" for zone in zones.values())
    for row in result["zone_decisions"]:
        assert row["candidate_id"] == f"{row['zone_id']}-SAR" and row["timely"] and row["delivery_hours"] == 4
        assert row["sensor_type"] == "sar" and row["resolution_m"] == 3 and row["cost_rub"] > 0
    assert all(catalog[zone_id]["reason"] == "late" for zone_id in zones)


def test_nothing_on_time_leaves_c_empty_and_b_late():
    result = plan(3)
    c, b = result["strategies"]["C"], result["strategies"]["B"]
    assert c["candidate_ids"] == [] and c["data_cost_rub"] == 0
    assert b["candidate_ids"] == ["ZALL"] and b["timely"] is False and b["covered_asset_ids"] == []
    assert {row["reason"] for row in result["zone_decisions"]} == {"late"}


@pytest.mark.parametrize("deadline", [3, 4, 8])
def test_archive_frames_never_enter_b_or_c(deadline):
    for priority in ("risk", "information"):
        result = plan(deadline, priority)
        bought = result["strategies"]["B"]["candidate_ids"] + result["strategies"]["C"]["candidate_ids"]
        assert not any(candidate_id.endswith("-ARC") for candidate_id in bought)
        for row in result["catalog"]:
            if row["data_role"] == "context":
                assert row["counts_for_coverage"] is False and not row["in_b"] and not row["in_c"]


def test_export_at_four_hours_prices_radar_and_validates(tmp_path):
    files = export(TestClient(app), deadline=4)
    assert validator.validate(pack(files, tmp_path / "radar.zip"))["status"] == "ok"
    lines = table(files, "procurement_plan.csv")
    assert lines and {row["sensor_type"] for row in lines} == {"sar"}
    assert {row["discount_group_id"] for row in lines if row["strategy"] == "C"} == {f"C_sar_3m_{YEAR}"}
    assert {row["discount_group_id"] for row in lines if row["strategy"] == "B"} == {f"B_sar_3m_{YEAR}"}
    features = json.loads(files["candidate_orders.geojson"])["features"]
    assert all(CASE_FIELDS <= set(feature["properties"]) for feature in features)
    assert {feature["properties"]["acquisition_type"] for feature in features} == {"new", "archive"}
    plans = json.loads(files["strategy_plans.json"])
    assert plans["C"] == [row["candidate_id"] for row in lines if row["strategy"] == "C"]
    assert plans["B"] == ["ZALL-SAR"]
    decisions = table(files, "zone_decisions.csv")
    assert {row["candidate_id"] for row in decisions} == {feature["properties"]["candidate_id"] for feature in features}
    meta = json.loads(files["run_metadata.json"])
    assert meta["product_rule"] and meta["pricing"]["calculation_date"]
    assert set(meta["pricing"]["discount_formulas"]) == {"optical", "sar"}
    assert meta["pricing"]["discount_groups"]["C"] == [f"C_sar_3m_{YEAR}"]


def test_validator_rejects_a_discount_group_shared_by_both_baskets(tmp_path):
    files = export(TestClient(app))
    rows = table(files, "procurement_plan.csv")
    for row in rows:
        if row["strategy"] == "B":
            row["discount_group_id"] = row["discount_group_id"].replace("B_", "C_", 1)
    files["procurement_plan.csv"] = rewrite(rows)
    with pytest.raises(ValueError, match="group"):
        validator.validate(pack(files, tmp_path / "shared.zip"))


def test_validator_reprices_freshness_by_the_ninety_day_rule(tmp_path):
    files = export(TestClient(app))
    rows = table(files, "procurement_plan.csv")
    next(row for row in rows if row["strategy"] == "C")["freshness_coef"] = "1.0"
    files["procurement_plan.csv"] = rewrite(rows)
    with pytest.raises(ValueError, match="freshness"):
        validator.validate(pack(files, tmp_path / "freshness.zip"))

    files = export(TestClient(app))
    meta = json.loads(files["run_metadata.json"])
    catalog = json.loads(files["candidate_orders.geojson"])
    recent = date.fromisoformat(meta["pricing"]["calculation_date"]) - timedelta(days=30)
    next(feature for feature in catalog["features"]
         if feature["properties"]["acquisition_type"] == "archive")["properties"]["observation_at"] = recent.isoformat()
    files["candidate_orders.geojson"] = json.dumps(catalog).encode()
    with pytest.raises(ValueError, match="days"):
        validator.validate(pack(files, tmp_path / "age.zip"))


def test_validator_counts_no_coverage_from_archive_orders(tmp_path):
    """C with its first footprint swapped to the archive frame, priced consistently: coverage must drop."""
    files = export(TestClient(app))
    meta = json.loads(files["run_metadata.json"])
    catalog = {feature["properties"]["candidate_id"]: feature["properties"]
               for feature in json.loads(files["candidate_orders.geojson"])["features"]}
    plans = json.loads(files["strategy_plans.json"])
    plans["C"] = [f"{plans['C'][0]}-ARC"] + plans["C"][1:]
    swapped = quote([catalog[candidate_id] for candidate_id in plans["C"]], strategy="C",
                    calculation_date=meta["pricing"]["calculation_date"])
    files["strategy_plans.json"] = json.dumps(plans).encode()
    rows = table(files, "procurement_plan.csv")
    rows = [row for row in rows if row["strategy"] != "C"] + [
        dict(zip(exporter.PLAN_COLUMNS, exporter.plan_row("C", line, catalog[line["candidate_id"]])))
        for line in swapped["lines"]]
    files["procurement_plan.csv"] = rewrite(rows)
    comparison = table(files, "strategy_comparison.csv")
    for row in comparison:
        if row["strategy"] == "C":
            row["data_cost_rub"] = row["decision_cost_rub"] = str(swapped["total_rub"])
    files["strategy_comparison.csv"] = rewrite(comparison)
    decisions = table(files, "zone_decisions.csv")
    for row in decisions:
        row["selected_in_c"] = str(row["candidate_id"] in plans["C"]).lower()
    files["zone_decisions.csv"] = rewrite(decisions)
    with pytest.raises(ValueError, match="context"):
        validator.validate(pack(files, tmp_path / "archive.zip"))
