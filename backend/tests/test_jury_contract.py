"""What a strict reading of the case file checks in the package: every required field filled, one boolean
spelling, B marked counterfactual whenever it exceeds the budget, dated sources, a provable GitVerse revision."""

import csv
import io
import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import exporter
from app.main import app
from test_case_contract import export, pack, table
from test_planning import validator

ROOT = Path(__file__).resolve().parents[2]
GITVERSE = "https://gitverse.ru/hackrus.experts/kosmo-moskva_misis_zhomarung_cr7mentality_21.git"
STATUSES = {"baseline", "scenario", "measured", "not_estimated"}
client = TestClient(app)


@pytest.mark.parametrize("params", [{}, {"deadline": 4}, {"deadline": 3}, {"budget": 0},
                                    {"priority": "information"}])
def test_every_sensitivity_variant_has_its_decision_cost_and_uncertainty_status(params, tmp_path):
    files = export(client, **params)
    comparison = {row["strategy"]: row for row in table(files, "strategy_comparison.csv")}
    rows = table(files, "sensitivity.csv")
    assert rows
    for row in rows:
        for column in ("scenario_id", "strategy", "changed_inputs_json", "decision_cost_rub",
                       "covered_expected_loss_rub", "result_status", "interpretation", "uncertainty_status"):
            assert row[column] != "", (row["scenario_id"], column)
        assert Decimal(row["decision_cost_rub"]) == Decimal(row["data_cost_rub"]) + Decimal(row["other_cost_rub"])
        assert row["uncertainty_status"] in STATUSES, row["scenario_id"]
        assert (row["residual_uncertainty"] == "") == (row["uncertainty_status"] == "not_estimated")
    same = next(row for row in rows if row["scenario_id"] == "plan_budget_1.0")
    for column in ("data_cost_rub", "other_cost_rub", "decision_cost_rub", "residual_uncertainty",
                   "uncertainty_status"):
        assert same[column] == comparison["C"][column], column
    assert validator.validate(pack(files, tmp_path / "package.zip"))["status"] == "ok"


def test_validator_rejects_an_empty_decision_cost_in_sensitivity(tmp_path):
    files = export(client)
    rows = table(files, "sensitivity.csv")
    rows[0]["decision_cost_rub"] = ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    files["sensitivity.csv"] = output.getvalue().encode()
    with pytest.raises(ValueError, match="decision_cost_rub"):
        validator.validate(pack(files, tmp_path / "tampered.zip"))


@pytest.mark.parametrize("params", [{}, {"deadline": 3}])
def test_csv_booleans_use_one_lowercase_spelling(params):
    files = export(client, **params)
    for name, content in files.items():
        if name.endswith(".csv"):
            for row in csv.reader(io.StringIO(content.decode("utf-8"))):
                assert not {"True", "False"} & set(row), (name, row[:3])


def test_b_over_budget_stays_counterfactual_when_it_is_also_late(tmp_path):
    files = export(client, deadline=3)
    comparison = {row["strategy"]: row for row in table(files, "strategy_comparison.csv")}
    b = comparison["B"]
    assert b["budget_feasible"] == "false"
    assert b["strategy_status"] == "counterfactual_over_budget"
    assert b["delivered_by_deadline"] == "false"
    assert comparison["A"]["delivered_by_deadline"] == comparison["C"]["delivered_by_deadline"] == "true"
    assert validator.validate(pack(files, tmp_path / "late.zip"))["status"] == "ok"


def test_every_source_carries_observation_and_publication_dates():
    manifest = json.loads(export(client)["source_manifest.json"])
    for entry in manifest:
        assert {"observed_at", "published_at", "available_at"} <= set(entry), entry["name"]
        assert entry["observed_at"] or entry.get("observed_at_note"), entry["name"]
        assert entry["published_at"] or entry["available_at"], entry["name"]
    download = json.loads((ROOT / "ml/artifacts/download_manifest.json").read_text(encoding="utf-8"))
    updated = sorted(item["updated"] for item in download["objects"])
    events = sorted(event["observed_at"] for event in json.loads(
        (ROOT / "data/catalog/sen1floods11_handlabeled.json").read_text(encoding="utf-8"))["events"])
    dataset = next(entry for entry in manifest if entry["role"] == "training_and_evaluation")
    assert dataset["observed_at"] == f"{events[0]}/{events[-1]}"
    assert dataset["published_at"] == f"{updated[0]}/{updated[-1]}"
    encoder = next(entry for entry in manifest if entry["role"] == "pretrained_initialisation")
    assert encoder["observed_at"] is None and encoder["published_at"]


def fake_git(remote: str, branches: str):
    def run(*args: str) -> str:
        if args == ("remote", "-v"):
            return f"origin\t{remote} (fetch)\norigin\t{remote} (push)\n"
        if args[:3] == ("branch", "-r", "--contains"):
            return branches
        return ""
    return run


def test_a_gitverse_clone_proves_its_commit(monkeypatch):
    head = "a" * 40
    monkeypatch.delenv("FLOODVALUE_GITVERSE_COMMIT", raising=False)
    monkeypatch.setattr(exporter, "_git_head", lambda: head)
    monkeypatch.setattr(exporter, "_git", fake_git(GITVERSE, "  origin/HEAD -> origin/master\n  origin/master\n"))
    assert exporter.gitverse_commit() == head
    assert exporter.gitverse_status() == "verified_in_gitverse_remote"
    # A local commit on top of the clone is not on GitVerse yet.
    monkeypatch.setattr(exporter, "_git", fake_git(GITVERSE, ""))
    assert exporter.gitverse_commit() is None
    assert exporter.gitverse_status() == "mirror_commit_not_verified"


def test_a_github_checkout_never_claims_a_gitverse_commit(monkeypatch):
    monkeypatch.delenv("FLOODVALUE_GITVERSE_COMMIT", raising=False)
    monkeypatch.setattr(exporter, "_git_head", lambda: "b" * 40)
    monkeypatch.setattr(exporter, "_git", fake_git("https://github.com/Mojarung/kosmos_finals.git", "  origin/main\n"))
    assert exporter.gitverse_commit() is None
    assert exporter.gitverse_status() == "mirror_commit_not_verified"


def test_an_image_keeps_the_revision_its_build_proved(monkeypatch):
    monkeypatch.setattr(exporter, "_git_head", lambda: None)
    monkeypatch.setattr(exporter, "_git", lambda *args: "")
    monkeypatch.setenv("FLOODVALUE_GITVERSE_COMMIT", "c" * 40)
    monkeypatch.setenv("FLOODVALUE_GITVERSE_STATUS", "verified_in_gitverse_remote")
    assert exporter.gitverse_commit() == "c" * 40
    assert exporter.gitverse_status() == "verified_in_gitverse_remote"
    monkeypatch.setenv("FLOODVALUE_GITVERSE_STATUS", "anything else")
    assert exporter.gitverse_status() == "declared_access_unverified"


def test_every_priced_line_names_its_rate_source_and_rounding():
    files = export(client)
    catalog = {feature["properties"]["candidate_id"]: feature["properties"]
               for feature in json.loads(files["candidate_orders.geojson"])["features"]}
    lines = table(files, "procurement_plan.csv")
    assert lines
    for line in lines:
        assert line["base_rate_source"] == catalog[line["candidate_id"]]["base_rate_source"] != ""
        assert "ROUND_HALF_UP" in line["rounding"] and "discount_coef" in line["rounding"]
