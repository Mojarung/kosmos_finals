"""Build a single, internally consistent case package for the selected chip."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlencode

from .analysis import context, raster_assets
from .layers import overlay_png
from .catalog import ROOT, metadata, scene_dir
from .planning import (ARCHIVE_LEAD_DAYS, BASE_RATE, OPTICAL_DELIVERY_HOURS, SAR_DELIVERY_HOURS, SUITABILITY_RULE,
                       as_polygon, enrich_assets)
from .scene_builder import PROBABILITY_RULE
from .sensitivity import asset_plan_sensitivity
from .zoning import DOUBT_BASIS, DOUBT_RULE, DOUBT_SOURCE, DOUBT_THRESHOLD, ZONE_RULE

REASONS = {
    "selected": "in basket C",
    "late": "delivery after the decision deadline",
    "over_budget": "adding it to basket C exceeds the budget",
    "nothing_new": "adds nothing to the goal: its assets and doubtful area are already covered",
    "cheaper_equal": "an equal-value basket is cheaper",
    "plan_infeasible": "no basket satisfies the constraints",
    "context_only": ("archive frame: shows the state before the flood, not current water; "
                     "never bought as an observation and never counted in coverage"),
    "product_rule": "the optical 1 m order of this footprint arrives by the deadline, so the product rule takes optics",
}
PLAN_COLUMNS = ["strategy", "candidate_id", "footprint_id", "sensor_type", "resolution_m", "acquisition_type",
                "data_role", "observation_at", "age_days", "area_km2", "base_rate_rub_km2", "base_rate_status",
                "processing_level", "usage_type", "guaranteed_purchase", "processing_coef", "usage_coef",
                "freshness_coef", "discount_coef", "discount_group_id", "previous_group_area_km2",
                "basket_group_area_km2", "group_area_km2", "discount_formula", "unit_price_rub_km2", "cost_rub",
                "formula", "legal_edition"]
DECISION_COLUMNS = ["candidate_id", "footprint_id", "kind", "sensor_type", "resolution_m", "acquisition_type",
                    "data_role", "delivery_hours", "timely", "counts_for_coverage", "area_km2", "selected_in_c",
                    "selected_in_b", "reason", "reason_text", "price_alone_rub", "unit_price_rub_km2", "cost_rub",
                    "basket", "basket_price_with_zone_rub", "asset_ids", "expected_loss_rub",
                    "flood_expected_loss_rub", "doubt_share", "gain_loss_rub", "gain_doubt_share"]
# Every number the plan depends on, where it comes from and why this value.
PARAMETERS = [
    {"name": "model_threshold", "value": 0.41, "source": "ml/REPORT.md §7",
     "basis": "max IoU on validation; IoU curve is flat between 0.3 and 0.5"},
    {"name": "baseline_threshold_db", "value": -23.6, "source": "ml/artifacts/baseline/baseline.json",
     "basis": "VH threshold fitted on train, channel chosen on validation"},
    {"name": "doubt_threshold", "value": DOUBT_THRESHOLD, "source": DOUBT_SOURCE, "basis": DOUBT_BASIS},
    {"name": "uncertainty_zone_sides_km", "value": "1.12, 1.68, 2.24", "source": "app/zoning.py",
     "basis": "1.12 km square is the 1.25 km² object zone above the 1 km² minimum; 1.5x and 2x keep zones within a 6-7 thousand RUB budget"},
    {"name": "max_uncertainty_zones", "value": 5, "source": "app/zoning.py",
     "basis": "compute limit: exact search over 2^16 baskets with 10 object zones and the broad zone"},
    {"name": "base_rate_rub_km2", "value": float(BASE_RATE), "source": "case control example",
     "basis": "scenario rate; no supplier quote; status scenario everywhere"},
    {"name": "processing_coef_O", "value": "L2 1.2 optics; L1 1 radar", "source": "PP RF 840 table: L0 1, L1 1, L2 1.2",
     "basis": "optics is bought orthorectified and ready for analysis; the radar order is a calibrated L1 product "
              "the team processes itself, as it does with Sentinel-1"},
    {"name": "usage_coef_P", "value": 1, "source": "PP RF 840 table: internal 1, limited 1.2, unrestricted 1.5",
     "basis": "the imagery stays inside the operator; results are shared, the image is not"},
    {"name": "freshness_coef_T", "value": "1.8 new; 0.6 archive",
     "source": "PP RF 840 table: new with guaranteed purchase 1.8, up to 90 days 1, older 0.6",
     "basis": "the flood is happening now, so observations are new acquisitions; the archive frame is older than "
              "90 days and serves only as pre-flood context"},
    {"name": "resolution_m", "value": "1 optics; 3 radar", "source": "team choice",
     "basis": "1 m optics separates a building from water at its wall; 3 m radar sees water through cloud; "
              "Sentinel-1 at 10 m cannot resolve a single building"},
    {"name": "delivery_hours", "value": f"{OPTICAL_DELIVERY_HOURS} optics; {SAR_DELIVERY_HOURS} radar; 0 archive",
     "source": "scenario",
     "basis": "assumed tasking and delivery time inside the 6-12 h decision window of the case; an all-weather "
              "radar order is faster and does not wait for clear sky"},
    {"name": "archive_lead_days", "value": ARCHIVE_LEAD_DAYS, "source": "scenario",
     "basis": "the last clear dry-season optical frame before the event; any lead above 90 days gives T = 0.6"},
    {"name": "budget_rub", "value": None, "source": "operator input",
     "basis": "default 6500 RUB, about one sixth of a broad purchase of the chip; the frontier shows other budgets"},
]
# License facts read on the publishers' own pages; nothing here is inferred from third-party mirrors.
LICENSE_CHECKED_AT = "2026-09-26"
SEN1FLOODS11_TERMS = {
    "license": "not_declared_by_publisher",
    "license_note": (
        "Checked 2026-09-26: the publisher's repository github.com/cloudtostreet/Sen1Floods11 has no LICENSE file "
        "(GitHub API /repos/cloudtostreet/Sen1Floods11/license answers 404, repository license null); its README "
        "asks to cite Bonafilia et al. 2020 and states no license; the v1.1 STAC collections in the publisher's "
        "bucket (sen1floods11_hand_labeled_source, sen1floods11_hand_labeled_label) set \"license\": \"proprietary\" "
        "without a license link or text. Used as the dataset the case prescribes, with the citation the authors ask for."),
    "license_checked_urls": [
        "https://github.com/cloudtostreet/Sen1Floods11",
        "https://api.github.com/repos/cloudtostreet/Sen1Floods11/license",
        "https://storage.googleapis.com/sen1floods11/v1.1/catalog/sen1floods11_hand_labeled_source/collection.json",
        "https://storage.googleapis.com/sen1floods11/v1.1/catalog/sen1floods11_hand_labeled_label/collection.json",
    ],
    "license_checked_at": LICENSE_CHECKED_AT,
    "citation": ("Bonafilia, D., Tellman, B., Anderson, T., Issenberg, E. 2020. Sen1Floods11: a georeferenced dataset "
                 "to train and test deep learning flood algorithms for Sentinel-1. CVPR Workshops 2020, pp. 210-211"),
}
COPERNICUS_SENTINEL_TERMS = {
    "name": "Legal notice on the use of Copernicus Sentinel Data and Service Information (European Commission)",
    "url": "https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice",
    "status": "free, full and open access",
    "quote": ("the law provides that users shall have a free, full and open access to Copernicus Sentinel Data and "
              "Service Information without any express or implied warranty, including as regards quality and "
              "suitability for any purpose"),
    "legal_basis": "Regulation (EU) No 377/2014 and Commission Delegated Regulation (EU) No 1159/2013",
    "required_notice": "Contains modified Copernicus Sentinel data [Year]",
    "checked_at": LICENSE_CHECKED_AT,
}
JRC_GSW_TERMS = {
    "name": "JRC Global Surface Water (Pekel et al., Nature 540, 418-422, 2016)",
    "url": "https://global-surface-water.appspot.com/download",
    "status": "Copernicus Programme data, free of charge, without restriction of use",
    "quote": ("All data here is produced under the Copernicus Programme and is provided free of charge, "
              "without restriction of use."),
    "required_acknowledgement": ("Publications, models and data products that make use of these datasets must include "
                                 "proper acknowledgement, including citing datasets and the journal article: Pekel, J.-F., "
                                 "Cottam, A., Gorelick, N., Belward, A. S. High-resolution mapping of global surface water "
                                 "and its long-term changes. Nature 540, 418-422 (2016), doi:10.1038/nature20584"),
    "checked_at": LICENSE_CHECKED_AT,
}
PLANETARY_COMPUTER_RTC_TERMS = {
    "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "license_checked_urls": ["https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-rtc"],
    "license_checked_at": LICENSE_CHECKED_AT,
}
SEN1FLOODS11_LAYERS = ("S1Hand", "LabelHand", "JRCWaterHand")


def _git_head() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else None


def code_commit() -> str | None:
    """Commit of the running code: git in the repository, else FLOODVALUE_COMMIT (images carry no .git)."""
    return _git_head() or os.environ.get("FLOODVALUE_COMMIT") or None


def _sentinel_terms(observed_at: object) -> dict:
    year = str(observed_at or "")[:4]
    if not year.isdigit():
        return dict(COPERNICUS_SENTINEL_TERMS)
    return {**COPERNICUS_SENTINEL_TERMS, "required_notice": f"Contains modified Copernicus Sentinel data {year}"}


def manifest_entry(item: dict, info: dict, live: bool) -> dict:
    """One source_manifest.json row: the scene passport of the source plus the license facts checked above.

    Checked facts override a license stored in older scene metadata.
    """
    entry = {
        "version": "Sentinel-1 RTC" if live else "Sen1Floods11_v1.1",
        "observed_at": info["observed_at"], "published_at": None, "available_at": None,
        "role": "model_input" if live or "S1Hand" in item["name"] else "diagnostic_label_only",
        "limitation": "No field-verified flood label for this region" if live else "Historical 512x512 chip; not a live flood observation",
        **item,
    }
    url = str(item.get("url") or "")
    layer = item.get("layer") or next((name for name in SEN1FLOODS11_LAYERS if name in item["name"]), None)
    if item.get("collection") == "sentinel-1-rtc":
        entry.update(PLANETARY_COMPUTER_RTC_TERMS, underlying_data_license=_sentinel_terms(entry["observed_at"]))
    elif "global-surface-water" in url:
        entry.update(license=JRC_GSW_TERMS["status"], license_url=JRC_GSW_TERMS["url"],
                     license_quote=JRC_GSW_TERMS["quote"],
                     license_acknowledgement=JRC_GSW_TERMS["required_acknowledgement"],
                     license_checked_at=LICENSE_CHECKED_AT)
    elif layer in SEN1FLOODS11_LAYERS or "sen1floods11" in url:
        entry.update(SEN1FLOODS11_TERMS)
        if layer == "S1Hand":
            entry["underlying_data_license"] = _sentinel_terms(entry["observed_at"])
        elif layer == "LabelHand":
            entry["label_origin"] = ("hand-labeled ground truth (Water / NoWater / NoData) made by the dataset authors, "
                                     "Cloud to Street; used only for diagnostic metrics, never as a model input")
        elif layer == "JRCWaterHand":
            entry["derived_from"] = ("JRC Global Surface Water; the Sen1Floods11 docs README describes the layer as "
                                     "'JRC Permement Water overlapping labeled data'")
            entry["underlying_data_license"] = dict(JRC_GSW_TERMS)
    else:
        entry.update(license="not_verified", license_checked_urls=[url] if url else [],
                     license_checked_at=LICENSE_CHECKED_AT)
    return entry


def _number(value: float) -> object:
    return int(value) if float(value).is_integer() else value


def _commands(scene_id: str, info: dict, inputs: dict, live: bool) -> list[str]:
    """Cross-platform reproduction of this package: git, uv and curl, no shell-specific paths."""
    query = {"scene_id": scene_id, "budget": _number(inputs["budget_rub"]),
             "deadline": inputs["deadline_hours"], "priority": inputs["priority"]}
    if inputs.get("must_cover"):
        query["must_cover"] = inputs["must_cover"]
    if inputs.get("bbox"):
        query["bbox"] = ",".join(str(value) for value in inputs["bbox"])
    archive = f"floodvalue-{scene_id}.zip"
    commands = ["git lfs install --local", "git lfs pull", "uv sync --project ml",
                "uv sync --project backend --extra demo"]
    if not live:
        commands += [f"curl --create-dirs -o data/source/{item['name']} {item['url']}"
                     for item in info["source_manifest"] if item.get("url")]
        commands.append(f"uv run --project ml floodvalue infer --s1 data/source/{scene_id}_S1Hand.tif "
                        f"--out data/inference/{scene_id}")
        if info.get("origin", "demo") == "demo":
            commands.append("uv run --project backend --extra demo python scripts/prepare_demo.py")
    commands += ["uv run --project ml floodvalue download",
                 "uv run --project ml floodvalue infer --chip <CHIP> --out data/inference/<CHIP>",
                 "podman compose up --build"]
    if live:
        bbox = ",".join(str(value) for value in info["bbox"])
        commands.append(f'curl -X POST "http://localhost:18000/api/live/prepare?bbox={bbox}'
                        f'&item_id={info.get("source_item_id")}"')
        if info.get("permanent_status") == "jrc_gsw":
            commands.append(f'curl -X POST "http://localhost:18000/api/live/permanent?scene_id={scene_id}"')
    elif info.get("origin", "demo") != "demo":
        commands.append(f"uv run --project backend python scripts/e2e_official.py http://127.0.0.1:18000 {scene_id}")
    commands += ["uv run --project backend python scripts/e2e_official.py http://127.0.0.1:18000 <CHIP>",
                 f'curl -o {archive} "http://localhost:18080/api/export?{urlencode(query, safe=",")}"',
                 f"uv run --project backend --extra demo python scripts/validate_package.py {archive}"]
    return commands


def _csv(headers: list[str], rows: list[list[object]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue()


REPOSITORY = "https://gitverse.ru/hackrus.experts/kosmo-moskva_misis_zhomarung_cr7mentality_21"
MIRROR = "https://github.com/Mojarung/kosmos_finals"
# Sources behind the frozen model rather than behind this chip: its training data and encoder init.
MODEL_SOURCES = [
    {"name": "Sen1Floods11 v1.1 hand-labelled chips (train, valid, test_core, test_adjacent, bolivia)",
     "version": "Sen1Floods11_v1.1", "url": "https://github.com/cloudtostreet/Sen1Floods11",
     "role": "training_and_evaluation", "split": "ml/artifacts/split.json",
     "checksums": "ml/artifacts/download_manifest.json (md5 of 1343 bucket objects, downloaded 2026-09-25)",
     **SEN1FLOODS11_TERMS},
    {"name": "ResNet34 ImageNet encoder weights", "version": "segmentation-models-pytorch 0.5.0",
     "url": "https://huggingface.co/smp-hub/resnet34.imagenet", "role": "pretrained_initialisation",
     "license": "other_without_text",
     "license_note": ("Checked 2026-09-26: the model card of smp-hub/resnet34.imagenet declares license: other and "
                      "gives no license text. Used only to initialise training; the shipped weights are ours."),
     "license_checked_urls": ["https://huggingface.co/api/models/smp-hub/resnet34.imagenet",
                              "https://huggingface.co/smp-hub/resnet34.imagenet/raw/main/README.md"],
     "license_checked_at": LICENSE_CHECKED_AT},
]


def model_weights() -> list[dict]:
    card = ROOT / "ml/artifacts/model/model_card.json"
    if not card.is_file():
        return []
    return [{"dir": member["dir"], "sha256": member["sha256"]}
            for member in json.loads(card.read_text(encoding="utf-8")).get("members", [])]


def _strategy_status(key: str, item: dict) -> str:
    if key == "A":
        return "no_paid_data"
    if not item.get("timely", True):
        return "late_for_deadline"
    if not item["budget_feasible"]:
        return "counterfactual_over_budget"
    return "within_budget" if item.get("constraints_feasible", True) else "constraints_infeasible"


def _plan_change(base: list[str], new: list[str]) -> str:
    added = [order for order in new if order not in base]
    dropped = [order for order in base if order not in new]
    if not added and not dropped:
        return "plan C unchanged"
    return "plan C " + "; ".join(part for part in (added and "adds " + ", ".join(added),
                                                   dropped and "drops " + ", ".join(dropped)) if part)


def _blank(value: object) -> object:
    return "" if value is None else value


def _flag(value: object) -> str:
    return str(bool(value)).lower()


def plan_row(strategy: str, line: dict, product: dict) -> list:
    """One procurement_plan.csv row: the priced line next to the catalog conditions that explain it."""
    values = {**line, "strategy": strategy, "footprint_id": product["footprint_id"],
              "data_role": product["data_role"], "guaranteed_purchase": _flag(line["guaranteed_purchase"])}
    return [_blank(values[column]) for column in PLAN_COLUMNS]


def decision_row(row: dict, case_loss: float, decision: dict) -> list:
    """One zone_decisions.csv row per catalog order: why it is in basket C or not, with its prices."""
    chosen = row["chosen"]
    values = {
        **row, "timely": _flag(row["timely"]), "counts_for_coverage": _flag(row["counts_for_coverage"]),
        "selected_in_c": _flag(row["in_c"]), "selected_in_b": _flag(row["in_b"]),
        "reason_text": REASONS[row["reason"]], "unit_price_rub_km2": row["basket_unit_price_rub_km2"],
        "cost_rub": row["basket_cost_rub"],
        "basket_price_with_zone_rub": row["basket_total_rub"] if row["basket_hypothetical"] else None,
        "asset_ids": "|".join(row["asset_ids"]), "expected_loss_rub": case_loss,
        "flood_expected_loss_rub": decision["expected_loss_rub"],
        # Gains belong to the footprint under the product the rule takes; other products show none.
        "gain_loss_rub": decision.get("gain_loss_rub") if chosen else None,
        "gain_doubt_share": decision.get("gain_doubt_share") if chosen else None,
    }
    return [_blank(values[column]) for column in DECISION_COLUMNS]


def package(result: dict) -> io.BytesIO:
    scene_id = result["chip"]["chip_id"]
    info = metadata(scene_id)
    inputs = result["inputs"]
    ctx = context(scene_id, inputs.get("bbox"))
    # Case files carry the mandatory EL = p·V·q; the flood-only reading the plan uses sits in *flood* columns.
    all_assets = sorted(enrich_assets(raster_assets(scene_id, info), info["model_threshold"]),
                        key=lambda asset: (-(asset["case_expected_loss_rub"] or 0), asset["id"]))
    region_ids = [asset["id"] for asset in result["assets"]]
    region_rank = {asset["id"]: index for index, asset in enumerate(sorted(
        (asset for asset in result["assets"] if asset["case_loss_status"] != "no_data"),
        key=lambda asset: (-asset["case_expected_loss_rub"], asset["id"])), 1)}
    case_loss = {asset["id"]: asset["case_expected_loss_rub"] or 0 for asset in result["assets"]}

    def case_covered(asset_ids: list[str], changed: dict[str, float] | None = None) -> float:
        losses = {**case_loss, **(changed or {})}
        return round(sum(losses[asset_id] for asset_id in asset_ids), 2)

    strategies = result["strategies"]
    pricing = result["pricing"]
    zone_by_id = {zone["id"]: zone for zone in result["zones"]}
    catalog = result["catalog"]
    product_by_id = {row["candidate_id"]: row for row in catalog}
    decisions = {row["zone_id"]: row for row in result["zone_decisions"]}
    live = info.get("source_kind") == "live_sentinel1_rtc"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        def json_file(name: str, value: object) -> None:
            archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))

        json_file("analysis.json", result)
        json_file("source_manifest.json", [manifest_entry(item, info, live) for item in info["source_manifest"]]
                  + MODEL_SOURCES)
        json_file("selected_region.geojson", {"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": as_polygon(result["region"]["bounds"]),
            "properties": {"area_km2": result["region"]["area_km2"],
                           "data_status": result["region"]["data_status"],
                           "permanent_status": result["region"]["permanent_status"]},
        }]})
        json_file("assets.geojson", {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point",
              "coordinates": [asset["longitude"], asset["latitude"]]},
             "properties": {"asset_id": asset["id"], "chip_id": scene_id,
                            "asset_class": asset["class"], "asset_value_rub": asset["value_rub"],
                            "vulnerability_coef": asset["vulnerability"],
                            "in_region": asset["id"] in region_ids}}
            for asset in info["assets"]]})
        archive.writestr("assets.csv", _csv(
            ["asset_id", "chip_id", "asset_class", "asset_value_rub", "vulnerability_coef",
             "longitude", "latitude"],
            [[asset["id"], scene_id, asset["class"], asset["value_rub"], asset["vulnerability"],
              asset["longitude"], asset["latitude"]] for asset in info["assets"]]))
        archive.writestr("asset_loss.csv", _csv(
            ["asset_id", "chip_id", "p_flood", "expected_loss_rub", "uncertainty", "rank", "in_region", "status",
             "p_window3", "permanent_water_at_point", "flood_expected_loss_rub", "ensemble_spread",
             "exposure_rub", "map_doubtful"],
            [[asset["id"], scene_id, _blank(asset["p_flood"]), _blank(asset["case_expected_loss_rub"]),
              _blank(asset["map_error_probability"]), region_rank.get(asset["id"], ""),
              str(asset["id"] in region_ids).lower(), asset["case_loss_status"], _blank(asset.get("p_window3")),
              _blank(asset.get("permanent_water_at_point")), _blank(asset["flood_expected_loss_rub"]),
              _blank(asset["uncertainty"]), asset["exposure_rub"], _blank(asset["map_doubtful"])]
             for asset in all_assets]))
        asset_rows = result["sensitivity"]["asset"]
        replans = asset_plan_sensitivity(ctx.space, asset_rows, inputs["budget_rub"], inputs["priority"],
                                         inputs["deadline_hours"], inputs["must_cover"])
        sensitivity_rows = []
        base_orders = strategies["C"]["candidate_ids"]
        for row, selected in zip(asset_rows, replans):
            changed_inputs = {"asset_id": row["asset_id"], "input": row["input"],
                              "factor": row["factor"], "input_value": row["input_value"]}
            interpretation = (f"EL {row['case_expected_loss_rub']} RUB; rank {row['case_baseline_rank']}→"
                              f"{row['case_rank']} among region assets; "
                              f"{_plan_change(base_orders, selected['candidate_ids'])}; fixed p-map")
            if row["expected_loss_rub"] != row["case_expected_loss_rub"]:
                interpretation += (f"; point on permanent water: the basket is re-optimised on its flood-only "
                                   f"EL {row['expected_loss_rub']} RUB")
            sensitivity_rows.append([
                f"asset_{row['asset_id']}_{row['input']}_{row['factor']}", "C",
                json.dumps(changed_inputs, ensure_ascii=False), "", selected["quote"]["total_rub"],
                case_covered(selected["covered_asset_ids"], {row["asset_id"]: row["case_expected_loss_rub"]}),
                "feasible" if selected["feasible"] else "infeasible", interpretation,
                row["case_expected_loss_rub"], row["case_rank"], "|".join(selected["candidate_ids"]),
                _blank(selected["covered_doubt_share"]), selected["covered_expected_loss_rub"],
            ])
        for row in result["sensitivity"]["plan"]:
            changed_inputs = {"input": row["input"], "factor": row["factor"],
                              "budget_rub": row["budget_rub"],
                              "tariff_multiplier": row["tariff_multiplier"]}
            sensitivity_rows.append([
                f"plan_{row['input']}_{row['factor']}", "C",
                json.dumps(changed_inputs, ensure_ascii=False), "", row["data_cost_rub"],
                case_covered(row["covered_asset_ids"]),
                "feasible" if row["feasible"] else "infeasible",
                f"{row['input']} x{row['factor']}: {_plan_change(base_orders, row['candidate_ids'])}; "
                "covered EL is not avoided loss",
                "", "", "|".join(row["candidate_ids"]), _blank(row["covered_doubt_share"]),
                row["covered_expected_loss_rub"],
            ])
        archive.writestr("sensitivity.csv", _csv(
            ["scenario_id", "strategy", "changed_inputs_json", "decision_cost_rub", "data_cost_rub",
             "covered_expected_loss_rub", "result_status", "interpretation",
             "asset_expected_loss_rub", "rank", "zone_ids", "covered_doubt_share", "covered_flood_loss_rub"],
            sensitivity_rows))
        # Every product of every footprint is its own order in the one catalog B and C choose from.
        json_file("candidate_orders.geojson", {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": zone_by_id[row["footprint_id"]]["geometry"],
             "properties": {**{key: value for key, value in row.items() if key != "name"},
                            "c_selected": row["in_c"], "b_selected": row["in_b"], "c_reason": row["reason"]}}
            for row in catalog]})
        archive.writestr("zone_decisions.csv", _csv(DECISION_COLUMNS, [
            decision_row(row, case_covered(row["asset_ids"]), decisions[row["footprint_id"]]) for row in catalog]))
        archive.writestr("procurement_plan.csv", _csv(PLAN_COLUMNS, [
            plan_row(strategy, line, product_by_id[line["candidate_id"]])
            for strategy in ("B", "C") for line in strategies[strategy]["quote"]["lines"]]))
        json_file("strategy_plans.json", {"A": [], "B": strategies["B"]["candidate_ids"],
                                          "C": strategies["C"]["candidate_ids"]})
        archive.writestr("strategy_comparison.csv", _csv(
            ["strategy", "strategy_status", "data_cost_rub", "other_cost_rub", "other_cost_status", "decision_cost_rub",
             "budget_rub", "budget_feasible", "covered_expected_loss_rub", "coverage_share", "coverage_status",
             "covered_asset_count", "covered_doubt_share",
             "residual_uncertainty", "uncertainty_status", "uncertainty_basis", "covered_flood_loss_rub"],
            [[key, _strategy_status(key, item), item["data_cost_rub"], "", "not_estimated", "", inputs["budget_rub"],
              str(item["budget_feasible"]).lower(), item["case_covered_expected_loss_rub"],
              _blank(item["case_coverage_share"]), _blank(item["case_coverage_status"]),
              len(item.get("covered_asset_ids", [])), _blank(item.get("covered_doubt_share")),
              _blank(item["residual_uncertainty"]), item["uncertainty_status"],
              "mean_map_error_probability_of_region" if key == "A" else "no_new_observation",
              item["covered_expected_loss_rub"]]
             for key, item in strategies.items()]))
        quality = result["quality"]
        metrics_rows = []

        def metric_row(part: str, event: str, water: str, method: str, scored: dict, pixels: int) -> list:
            return [part, event, water, method, scored["iou"], scored["f1"], scored["precision"], scored["recall"],
                    pixels, _blank(scored.get("tp")), _blank(scored.get("fp")), _blank(scored.get("fn"))]

        for part in ("test_core", "bolivia"):
            for method in ("baseline", "model"):
                scored = quality["parts"][part][method]
                metrics_rows.append(metric_row(part, "ALL", "all_water", method, scored, scored["n_pixels"]))
            for event, block in (quality.get("events") or {}).get(part, {}).items():
                for method in ("baseline", "model"):
                    metrics_rows.append(metric_row(part, event, "all_water", method, block[method],
                                                   block[method]["n_pixels"]))
        for part, methods in (quality.get("flood_only") or {}).items():
            for method in ("baseline", "model"):
                scored = methods[method]
                metrics_rows.append(metric_row(part, "ALL", "without_jrc_permanent_water", method, scored,
                                               scored["n_pixels"]))
        for method, key, water in (("baseline", "baseline_quality", "all_water"), ("model", "model_quality", "all_water"),
                                   ("baseline", "baseline_quality_flood", "without_jrc_permanent_water"),
                                   ("model", "model_quality_flood", "without_jrc_permanent_water")):
            scored = result["region"][key]
            if scored:
                metrics_rows.append(metric_row("selected_region", scene_id, water, method, scored,
                                               scored["valid_pixels"]))
        archive.writestr("metrics.csv", _csv(
            ["part", "event", "water", "method", "iou", "f1", "precision", "recall", "n_pixels", "tp", "fp", "fn"],
            metrics_rows))
        run_signature = json.dumps({"chip": scene_id, "inputs": inputs,
                                    "region": result["region"]["bounds"]}, sort_keys=True)
        json_file("run_metadata.json", {
            "run_id": hashlib.sha256(run_signature.encode()).hexdigest()[:16],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chip_id": scene_id, "event_id": scene_id if live else scene_id.split("_")[0],
            "event_name": info["event"],
            "s1_source_files": [item["name"] for item in info["source_manifest"]
                                if live or item.get("layer") == "S1Hand" or "S1Hand" in item["name"]],
            "portfolio_seed": info["portfolio_seed"],
            "portfolio_rule": info.get("portfolio_rule", "one of each case asset type; seeded coordinates independent of p and label"),
            "model_threshold": info["model_threshold"],
            "baseline_threshold_db": info["baseline_threshold_db"],
            "probability_rule": info.get("probability_rule", PROBABILITY_RULE),
            "permanent_water": {"status": result["region"]["permanent_status"],
                                "rule": info.get("permanent_rule"), "period_note": info.get("permanent_period_note"),
                                "file": "permanent_water.tif" if info.get("permanent_rule") else None},
            "doubt_rule": DOUBT_RULE, "uncertainty_zone_rule": ZONE_RULE,
            "model": "5 member S1-only U-Net ResNet34 ensemble, Platt calibrated on validation",
            "model_weights": {"card": "ml/artifacts/model/model_card.json", "sha256": model_weights()},
            "baseline": ("VH < -23.6 dB: threshold fitted on train, channel chosen on validation "
                         "(ml/artifacts/baseline/baseline.json)"),
            "repository": REPOSITORY, "mirror": MIRROR,
            "metrics_files": ["metrics.csv", "ml/artifacts/evaluation/metrics.csv",
                              "ml/artifacts/evaluation/evaluation.json", "ml/artifacts/evaluation/doubt_threshold.json",
                              "ml/artifacts/evaluation/summary.md"],
            "code_commit": code_commit(),
            "code_commit_rule": ("git rev-parse HEAD in the repository at export time; the FLOODVALUE_COMMIT build "
                                 "variable in images without .git; null when neither is available"),
            "gitverse_commit": code_commit(),
            "gitverse_status": "published" if code_commit() else "commit_not_recorded_in_this_build",
            "other_cost_rule": ("processing runs on a CPU in seconds and analyst review or field checks are not priced, "
                                "so other_cost_rub and decision_cost_rub stay empty with other_cost_status=not_estimated; "
                                "strategies are compared by data_cost_rub"),
            "residual_uncertainty_rule": ("A: mean map error chance over valid pixels of the analysed region (share 0-1, "
                                          "status baseline); B and C: empty with status not_estimated, because no new "
                                          "image has been received"),
            "counterfactual_rule": "B above the budget has strategy_status=counterfactual_over_budget, for comparison only",
            "priority_rule": ("rank by expected_loss_rub; map_doubtful=true flags assets whose map error chance at the "
                              "point is >= doubt_threshold, they need a look whatever their rank; no_data assets are "
                              "listed with their exposure as unestimated risk"),
            "suitability_rule": SUITABILITY_RULE,
            "split": "ml/artifacts/split.json: authors' v1.1 lists; test chips touching train chips moved to test_adjacent",
            "split_part": info.get("split_part", "test_core"),
            "metrics_independent": info.get("metrics_independent", True),
            "scene_origin": info.get("origin", "demo"),
            "budget_rub": inputs["budget_rub"],
            "deadline_hours": inputs["deadline_hours"],
            "priority": inputs["priority"],
            "expected_loss_model": "p_flood * asset_value_rub * vulnerability_coef",
            "expected_loss_scope": ("every asset with p_flood, including points on permanent water; status ok, "
                                    "partial or no_data; p_flood, expected_loss_rub and rank stay empty when not estimated"),
            "expected_loss_units": "RUB; scenario asset values and vulnerabilities",
            "flood_loss_model": ("additional model, not the case EL: flood_expected_loss_rub = 0 when "
                                 "permanent_water_at_point is true (JRC permanent water), otherwise expected_loss_rub"),
            "decision_loss_rule": (
                "The basket search scores loss with flood_expected_loss_rub: a point on JRC permanent water adds "
                "nothing to the goal of plan C. Every case field reports the mandatory EL: asset_loss.csv "
                "expected_loss_rub and rank, strategy_comparison.csv covered_expected_loss_rub and coverage_share, "
                "sensitivity.csv covered_expected_loss_rub, asset_expected_loss_rub and rank, zone_decisions.csv "
                "expected_loss_rub. covered_flood_loss_rub, "
                "flood_expected_loss_rub and zone_decisions.csv gain_loss_rub show the flood-only numbers the plan "
                "used. analysis.json is the screen state: there expected_loss_rub and covered_expected_loss_rub are "
                "flood-only and the case_* fields carry the mandatory EL."),
            "covered_loss_rule": ("sum of unique expected_loss_rub (p*V*q) of region assets inside the selected "
                                  "event_observation orders delivered by the deadline; context (archive) and late "
                                  "orders add no coverage; covered_flood_loss_rub sums flood_expected_loss_rub the same way"),
            "coverage_share_rule": "covered / estimated EL of region assets; empty with coverage_status when the denominator is 0",
            "covered_doubt_rule": "doubtful area inside the union of selected zones / doubtful area of the analysed region",
            "unestimated_exposure_rub": result["metrics"]["unestimated_exposure_rub"],
            "uncertainty_rule": ("asset_loss.uncertainty is the map error chance at the asset pixel: 1 - p where "
                                 "flood_mask = 1, p where flood_mask = 0 (calibrated p, share 0-1); "
                                 "ensemble_spread and flood_uncertainty.tif keep the std of the five members "
                                 "as a diagnostic"),
            "candidate_rule": pricing["candidate_rule"],
            "product_rule": pricing["product_rule"],
            "zone_area_crs": "EPSG:6933",
            "proxy_check": result["asset_proxy_check"],
            "pricing": {**{key: value for key, value in pricing.items() if key not in ("candidate_rule", "product_rule")},
                        "base_rate_rub_km2": float(BASE_RATE),
                        "legal_checked_at": "2026-09-25",
                        "legal_source_url": "https://government.ru/docs/all/160697/",
                        "legal_scope": "case_edition_verified; current procurement requires edition and provider check",
                        "calculation_date_rule": ("orders are priced on calculation_date (UTC day of the analysis): it "
                                                  "sets the order calendar year of every discount group and the age "
                                                  "of existing imagery"),
                        "discount_group_id": strategies["C"]["quote"]["discount_group_id"],
                        "discount_groups": {key: [group["discount_group_id"] for group in strategies[key]["quote"]["groups"]]
                                            for key in ("B", "C")},
                        "groups": {key: strategies[key]["quote"]["groups"] for key in ("B", "C")},
                        "availability_status": (f"scenario: new optics {OPTICAL_DELIVERY_HOURS} h and new radar "
                                                f"{SAR_DELIVERY_HOURS} h after the order; the archive frame is "
                                                "available at once")},
            "parameters": [{**row, "value": inputs["budget_rub"]} if row["name"] == "budget_rub" else row
                           for row in PARAMETERS],
            "sensitivity_method": result["sensitivity"]["method"],
            "decision_evidence": result["decision_evidence"],
            "interpretation": "Covered expected loss is observation priority, not avoided physical loss.",
            "commands": _commands(scene_id, info, inputs, live),
            "commands_note": ("Run from the repository root on Windows, Linux or macOS. <CHIP> is any official "
                              "Sen1Floods11 hand-labeled chip, e.g. Bolivia_103757; floodvalue download fetches "
                              "the official v1.1 files with md5 checks. docker compose works the same as podman "
                              "compose; in an image without .git pass FLOODVALUE_COMMIT to record code_commit."),
            "inference_manifest": info.get("inference_manifest", {"stac_item_id": info.get("source_item_id"),
                                                                  "processed_at": info.get("processed_at")}),
        })
        names = ["flood_probability.tif", "flood_mask.tif", "flood_uncertainty.tif", "baseline_mask.tif"]
        if (scene_dir(scene_id) / "permanent_water.tif").is_file():
            names.append("permanent_water.tif")
        for name in names:
            archive.write(scene_dir(scene_id) / name, arcname=name)
        selected_footprints = sorted({product_by_id[order]["footprint_id"] for order in strategies["C"]["candidate_ids"]})
        archive.writestr("zones_overlay.png", overlay_png(scene_id, info["bbox"], result["zones"], selected_footprints,
                                                          all_assets))
        archive.writestr("README.txt",
                         "FloodValue: один согласованный комплект для выбранного чипа и бюджета.\n"
                         "Вероятности и маски получены замороженной моделью по Sentinel-1.\n"
                         "asset_loss.csv: обязательный EL = p × V × q для всех объектов, в том числе на постоянной воде.\n"
                         "permanent_water.tif (если есть): постоянная вода JRC. Ущерб только от паводка (0 на ней) "
                         "лежит в колонках flood_expected_loss_rub и covered_flood_loss_rub, по нему выбирается корзина C.\n"
                         "Точки и V/q учебные. Ставка и срок платной съёмки сценарные.\n"
                         "Ручная метка использована только для проверки качества.\n"
                         "candidate_orders.geojson: каждая зона в трёх вариантах: новая оптика 1 м (ID зоны), "
                         "новый радар 3 м (-SAR) и архивная оптика старше 90 дней (-ARC, только контекст).\n"
                         "zone_decisions.csv: почему каждая позиция каталога вошла или не вошла в заказ C.\n"
                         "zones_overlay.png: зоны каталога и план C поверх исходного снимка VH.\n"
                         "procurement_plan.csv: коэффициенты О, П, Т, Р каждой строки и группа скидки своей корзины.\n"
                         "sensitivity.csv: каждый вход меняется на ±20% по одному при фиксированной карте и портфеле.\n"
                         "Проверка перед реальным заказом: актуальная редакция ПП 840 и котировка поставщика.\n")
    output.seek(0)
    return output
