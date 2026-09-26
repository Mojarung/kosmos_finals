"""The RTC -> GRD mapping is chosen by cross-validation on train chips, never on test chips."""

import importlib.util
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location(
    "fit_rtc_harmonization", Path(__file__).resolve().parents[2] / "scripts" / "fit_rtc_harmonization.py")
rtc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rtc)


def _chip(index: int, event: str, water: int = 10) -> dict:
    marker = np.full(4, float(index))
    return {"chip_id": f"C_{index}", "event": event, "water_pixels": water,
            "pairs": {"vv": (marker, marker), "vh": (marker, marker)}}


def test_cross_validation_never_fits_on_the_held_out_group():
    chips = [_chip(0, "A"), _chip(1, "A"), _chip(2, "B"), _chip(3, "C")]
    seen = {}

    def fitter(pairs):
        return {"fitted_on": sorted({int(rtc_db[0]) for rtc_db, _ in pairs["vv"]})}

    def score(mapping, chip):
        seen[chip["chip_id"]] = mapping["fitted_on"]
        return 0.5

    by_event = rtc.cross_validate(chips, {"median": fitter}, score, group="event")

    assert seen == {"C_0": [2, 3], "C_1": [2, 3], "C_2": [0, 1, 3], "C_3": [0, 1, 2]}
    assert by_event["folds"] == 3 and by_event["water_weighted_iou"] == {"median": 0.5}
    assert [row["held_out"] for row in by_event["chips"]] == ["A", "A", "B", "C"]

    by_chip = rtc.cross_validate(chips, {"median": fitter}, score, group="chip_id")

    assert by_chip["folds"] == 4 and seen["C_0"] == [1, 2, 3]


def test_pooled_iou_weights_chips_by_water_pixels():
    rows = [{"median": 0.2, "water_pixels": 100}, {"median": 0.8, "water_pixels": 300},
            {"median": None, "water_pixels": 50}]

    assert rtc.pooled(rows, "median") == 0.65
    assert rtc.pooled([{"median": None, "water_pixels": 5}], "median") is None


def test_selection_takes_the_higher_cv_score_and_keeps_median_on_a_tie():
    assert rtc.select_mapping({"median": 0.58, "quantile": 0.61}) == "quantile"
    assert rtc.select_mapping({"median": 0.61, "quantile": 0.58}) == "median"
    assert rtc.select_mapping({"median": 0.6, "quantile": 0.6}) == "median"
    assert rtc.select_mapping({"median": None, "quantile": 0.1}) == "quantile"
