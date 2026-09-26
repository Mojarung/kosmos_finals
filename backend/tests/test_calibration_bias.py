import csv
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.reliability import BIAS, p_check

ROOT = Path(__file__).resolve().parents[2]
RECORD = json.loads((ROOT / "ml/artifacts/evaluation/reliability_test_core.json").read_text(encoding="utf-8"))
client = TestClient(app)


def test_mid_range_p_is_flagged_as_too_high():
    check = p_check(0.75)
    assert check["status"] == "overestimated"
    assert check["observed_water_share"] < 0.75 - 0.03


def test_low_p_is_flagged_as_too_low():
    check = p_check(0.2)
    assert check["status"] == "underestimated"
    assert check["observed_water_share"] > 0.2 + 0.03


@pytest.mark.parametrize("p", [0.0, 0.01, 0.45, 0.98, 1.0])
def test_confident_or_matching_p_is_consistent(p):
    assert p_check(p)["status"] == "consistent"


def test_missing_p_has_no_check():
    assert p_check(None) is None


def test_bands_come_from_the_committed_reliability_record():
    calibrated = RECORD["test_core"]["calibrated"]
    assert BIAS["ece_mid_range"] == round(calibrated["ece_mid_range"], 4)
    assert BIAS["validation_ece_mid_range"] == round(RECORD["validation"]["calibrated"]["ece_mid_range"], 4)
    # The ML report names these bands: water is rarer than p at 0.47-0.93 and more common at 0.07-0.40.
    assert BIAS["overestimated_p"] == [0.467, 0.933]
    assert BIAS["underestimated_p"] == [0.067, 0.4]
    assert BIAS["worst_bin"]["observed_water_share"] == 0.6


def test_container_ships_the_reliability_record():
    dockerfile = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    assert ("COPY ml/artifacts/evaluation/reliability_test_core.json "
            "./ml/artifacts/evaluation/reliability_test_core.json") in dockerfile


def test_analysis_marks_each_asset_and_states_the_limit():
    result = client.get("/api/analysis", params={"scene_id": "Nigeria_812045", "budget": 6500}).json()
    for asset in result["assets"]:
        assert asset["p_check"] == p_check(asset["p_flood"])
    assert result["calibration_bias"] == BIAS
    line = next(line for line in result["limitations"] if line.startswith("Калибровка p:"))
    assert ".." not in line


def test_export_carries_the_limits_and_the_check_of_each_asset():
    result = client.get("/api/analysis", params={"scene_id": "Nigeria_812045", "budget": 6500}).json()
    response = client.get("/api/export", params={"scene_id": "Nigeria_812045", "budget": 6500})
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        meta = json.loads(archive.read("run_metadata.json"))
        rows = list(csv.DictReader(io.StringIO(archive.read("asset_loss.csv").decode())))
    assert meta["limitations"] == result["limitations"]
    assert meta["calibration_bias"] == BIAS
    by_id = {asset["id"]: asset for asset in result["assets"]}
    for row in rows:
        check = by_id[row["asset_id"]]["p_check"]
        assert row["p_calibration_status"] == (check["status"] if check else "")
