"""The report is reproducible evidence; these checks protect its decision arithmetic."""
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[2]


def test_reference_is_revealed_only_in_purchased_valid_points():
    from scripts.backtest_procurement import point_result

    p = np.array([.8, .2, .8, .2])
    y = np.array([0., 1., 0., 1.])
    exposure = np.array([100., 200., 300., 400.])
    purchased = np.array([True, True, True, False])
    valid = np.array([True, True, False, True])
    row = point_result(p, y, exposure, purchased, valid)
    assert row["error_before_rub"] == pytest.approx(560)
    assert row["error_after_rub"] == pytest.approx(320)
    assert row["corrected_statuses"] == 2
    assert row["covered_labelled_assets"] == 2


def test_report_covers_fixed_split_and_uses_same_points_for_each_strategy():
    report = json.loads((ROOT / "data/backtest/procurement_70.json").read_text())
    assert report["summary"]["chips"] == 70
    assert report["parts"]["test_core"]["chips"] == 55
    assert report["parts"]["bolivia"]["chips"] == 15
    assert len({row["chip_id"] for row in report["chips"]}) == 70
    assert report["summary"]["labelled_assets"] + report["summary"]["invalid_assets"] == 700
    for row in report["chips"]:
        assert row["C"]["data_cost_rub"] <= report["budget_rub"]
        assert row["A"]["error_after_rub"] == pytest.approx(row["B"]["error_before_rub"])
        assert row["A"]["error_after_rub"] == pytest.approx(row["C"]["error_before_rub"])
        assert row["B"]["error_after_rub"] <= row["C"]["error_after_rub"] + 0.01
        assert row["C"]["error_after_rub"] <= row["A"]["error_after_rub"] + 0.01
        assert row["B"]["covered_labelled_assets"] == row["labelled_assets"]
        assert len(row["s1_sha256"]) == len(row["prediction_sha256"]) == 64


def test_report_is_available_to_frontend():
    response = TestClient(app).get("/api/backtest")
    assert response.status_code == 200
    assert response.json()["summary"]["chips"] == 70
