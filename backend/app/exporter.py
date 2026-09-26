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

from .analysis import context, raster_assets, residual_for
from .catalog import ROOT, metadata, scene_dir
from .costs import RULE as COST_RULE
from .costs import decision_costs
from .layers import overlay_png
from .planning import (ARCHIVE_LEAD_DAYS, BASE_RATE, OPTICAL_DELIVERY_HOURS, ROUNDING, SAR_DELIVERY_HOURS,
                       SUITABILITY_RULE, as_polygon, contains, enrich_assets)
from .scene_builder import PROBABILITY_RULE, current_rule
from .sensitivity import asset_plan_sensitivity
from .temporal import scene_timing
from .zoning import DOUBT_BASIS, DOUBT_RULE, DOUBT_SOURCE, DOUBT_THRESHOLD, ZONE_RULE

REASONS = {
    "selected": "в корзине C",
    "late": "поставка позже срока решения",
    "over_budget": "добавление в корзину C превышает бюджет",
    "nothing_new": "ничего не добавляет к цели: её объекты и сомнительная площадь уже покрыты",
    "cheaper_equal": "корзина той же ценности дешевле",
    "plan_infeasible": "ни одна корзина не удовлетворяет ограничениям",
    "context_only": ("архивный кадр: показывает состояние до паводка, а не текущую воду; "
                     "не покупается как наблюдение и не засчитывается в покрытие"),
    "product_rule": "оптический заказ 1 м по этому контуру приходит к сроку, поэтому правило продукта берёт оптику",
}
PLAN_COLUMNS = ["strategy", "candidate_id", "footprint_id", "sensor_type", "resolution_m", "acquisition_type",
                "data_role", "observation_at", "age_days", "area_km2", "base_rate_rub_km2", "base_rate_status",
                "base_rate_source", "processing_level", "usage_type", "guaranteed_purchase", "processing_coef",
                "usage_coef", "freshness_coef", "discount_coef", "discount_group_id", "previous_group_area_km2",
                "basket_group_area_km2", "group_area_km2", "discount_formula", "unit_price_rub_km2", "cost_rub",
                "formula", "legal_edition", "rounding"]
# Every priced line states how its numbers were rounded, next to the formula (case: "скидка и округление").
LINE_ROUNDING = "; ".join(f"{key}: {ROUNDING[key]}" for key in ("area_km2", "discount_coef", "unit_price_rub_km2",
                                                                   "cost_rub"))
DECISION_COLUMNS = ["candidate_id", "footprint_id", "kind", "sensor_type", "resolution_m", "acquisition_type",
                    "data_role", "delivery_hours", "timely", "counts_for_coverage", "area_km2", "selected_in_c",
                    "selected_in_b", "reason", "reason_text", "price_alone_rub", "unit_price_rub_km2", "cost_rub",
                    "basket", "basket_price_with_zone_rub", "asset_ids", "expected_loss_rub",
                    "flood_expected_loss_rub", "doubt_share", "gain_loss_rub", "gain_doubt_share"]
# Every number the plan depends on, where it comes from and why this value.
PARAMETERS = [
    {"name": "model_threshold", "value": 0.41, "source": "ml/REPORT.md §7",
     "basis": "максимум IoU на validation; кривая IoU плоская между 0.3 и 0.5"},
    {"name": "baseline_threshold_db", "value": -23.6, "source": "ml/artifacts/baseline/baseline.json",
     "basis": "порог VH подобран на train, канал выбран на validation"},
    {"name": "doubt_threshold", "value": DOUBT_THRESHOLD, "source": DOUBT_SOURCE, "basis": DOUBT_BASIS},
    {"name": "uncertainty_zone_sides_km", "value": "1.12, 1.68, 2.24", "source": "app/zoning.py",
     "basis": "квадрат 1.12 км - это зона объекта 1.25 км², больше минимума 1 км²; 1.5x и 2x держат зоны в бюджете 6-7 тысяч рублей"},
    {"name": "max_uncertainty_zones", "value": 5, "source": "app/zoning.py",
     "basis": "предел вычислений: точный перебор 2^16 корзин с 10 зонами объектов и широкой зоной"},
    {"name": "base_rate_rub_km2", "value": float(BASE_RATE), "source": "контрольный пример кейса",
     "basis": "сценарная ставка; котировки поставщика нет; везде статус scenario"},
    {"name": "processing_coef_O", "value": "L2 1.2 оптика; L1 1 радар", "source": "таблица ПП РФ 840: L0 1, L1 1, L2 1.2",
     "basis": "оптика покупается ортотрансформированной и готовой к анализу; радарный заказ - калиброванный продукт L1, "
              "который команда обрабатывает сама, как и Sentinel-1"},
    {"name": "usage_coef_P", "value": 1, "source": "таблица ПП РФ 840: внутреннее 1, ограниченное 1.2, без ограничений 1.5",
     "basis": "снимок остаётся внутри оператора; наружу передаются результаты, а не снимок"},
    {"name": "freshness_coef_T", "value": "1.8 новая съёмка; 0.6 архив",
     "source": "таблица ПП РФ 840: новая с гарантированной покупкой 1.8, до 90 дней 1, старше 0.6",
     "basis": "паводок идёт сейчас, поэтому наблюдения - это новая съёмка; архивный кадр старше 90 дней и служит "
              "только контекстом до паводка"},
    {"name": "resolution_m", "value": "1 оптика; 3 радар", "source": "выбор команды",
     "basis": "оптика 1 м отделяет здание от воды у его стены; радар 3 м видит воду сквозь облака; "
              "Sentinel-1 с 10 м не различает отдельное здание"},
    {"name": "delivery_hours", "value": f"{OPTICAL_DELIVERY_HOURS} оптика; {SAR_DELIVERY_HOURS} радар; 0 архив",
     "source": "scenario",
     "basis": "допущение о времени съёмки и поставки внутри окна решения кейса 6-12 ч; радарный заказ в любую "
              "погоду быстрее и не ждёт ясного неба"},
    {"name": "archive_lead_days", "value": ARCHIVE_LEAD_DAYS, "source": "scenario",
     "basis": "последний ясный оптический кадр сухого сезона до события; любой срок больше 90 дней даёт T = 0.6"},
    {"name": "budget_rub", "value": None, "source": "ввод оператора",
     "basis": "по умолчанию 6500 рублей, около шестой части широкой покупки чипа; кривая показывает другие бюджеты"},
]
# License facts read on the publishers' own pages; nothing here is inferred from third-party mirrors.
LICENSE_CHECKED_AT = "2026-09-26"
SEN1FLOODS11_TERMS = {
    "license": "not_declared_by_publisher",
    "license_note": (
        "Проверено 2026-09-26: в репозитории издателя github.com/cloudtostreet/Sen1Floods11 нет файла LICENSE "
        "(GitHub API /repos/cloudtostreet/Sen1Floods11/license отвечает 404, лицензия репозитория null); README "
        "просит ссылаться на Bonafilia et al. 2020 и лицензию не называет; STAC-коллекции v1.1 в бакете издателя "
        "(sen1floods11_hand_labeled_source, sen1floods11_hand_labeled_label) указывают \"license\": \"proprietary\" "
        "без ссылки на лицензию и её текста. Используется как набор данных, который задаёт кейс, со ссылкой, о которой просят авторы."),
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


def _git(*args: str) -> str:
    """Output of a read-only git command in the repository; empty when git or the repository is absent."""
    try:
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _git_head() -> str | None:
    return _git("rev-parse", "HEAD").strip() or None


def code_commit() -> str | None:
    """Commit of the running code: git in the repository, else FLOODVALUE_COMMIT (images carry no .git)."""
    return _git_head() or os.environ.get("FLOODVALUE_COMMIT") or None


GITVERSE_HOST, GITVERSE_REPO = "gitverse.ru", "hackrus.experts/kosmo-moskva_misis_zhomarung_cr7mentality_21"
VERIFIED = "verified_in_gitverse_remote"


def _is_gitverse(url: str) -> bool:
    """HTTPS and SSH (git@gitverse.ru:owner/repo) addresses of the submission repository."""
    return GITVERSE_HOST in url.lower() and GITVERSE_REPO in url.lower()


def _gitverse_remote_commit() -> str | None:
    """HEAD when a remote-tracking branch of the GitVerse repository contains it, as in a clone from GitVerse.

    A GitHub checkout or a local commit not yet pushed to GitVerse proves nothing and gives None.
    """
    head = _git_head()
    remotes = {line.split()[0] for line in _git("remote", "-v").splitlines()
               if len(line.split()) > 1 and _is_gitverse(line.split()[1])}
    if not head or not remotes:
        return None
    branches = [line.strip() for line in _git("branch", "-r", "--contains", head).splitlines()]
    return head if any(branch.split("/", 1)[0] in remotes for branch in branches if branch) else None


def gitverse_commit() -> str | None:
    """The GitVerse revision: proven by the checkout itself, or declared by the image build (scripts/compose.sh).

    A GitHub commit alone is never taken as proof of a GitVerse push.
    """
    return _gitverse_remote_commit() or os.environ.get("FLOODVALUE_GITVERSE_COMMIT") or None


def gitverse_status() -> str:
    if _gitverse_remote_commit():
        return VERIFIED
    if os.environ.get("FLOODVALUE_GITVERSE_COMMIT"):
        # compose.sh proves the build commit on a GitVerse remote branch before it passes this status on.
        return VERIFIED if os.environ.get("FLOODVALUE_GITVERSE_STATUS") == VERIFIED else "declared_access_unverified"
    return "mirror_commit_not_verified"


def _sentinel_terms(observed_at: object) -> dict:
    year = str(observed_at or "")[:4]
    if not year.isdigit():
        return dict(COPERNICUS_SENTINEL_TERMS)
    return {**COPERNICUS_SENTINEL_TERMS, "required_notice": f"Contains modified Copernicus Sentinel data {year}"}


def manifest_entry(item: dict, info: dict, live: bool) -> dict:
    """One source_manifest.json row: the scene passport of the source plus the license facts checked above.

    Checked facts override a license stored in older scene metadata.
    """
    uploaded = info.get("source_kind") == "uploaded_sentinel1_geotiff"
    entry = {
        "version": "Sentinel-1 RTC" if live else "Sen1Floods11_v1.1",
        "observed_at": info["observed_at"], "published_at": None, "available_at": None,
        "role": "model_input" if live or "S1Hand" in item["name"] else "diagnostic_label_only",
        "limitation": "Для этой области нет разметки паводка, проверенной на месте" if live else "Исторический чип 512x512; не оперативное наблюдение паводка",
        **item,
    }
    if uploaded:
        return {**entry, "version": "user_supplied_geotiff", "license": "user_supplied_unverified",
                "limitation": ("Двухканальный GeoTIFF VV/VH от оператора; дата съёмки, уровень обработки "
                               "и права независимо не проверены.")}
    if item.get("layer") == "S2Hand":
        entry["observed_at"] = info.get("model", {}).get("dates", {}).get("s2_date")
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
            entry["label_origin"] = ("ручная эталонная разметка (Water / NoWater / NoData) авторов набора, "
                                     "Cloud to Street; только для диагностических метрик, никогда не вход модели")
        elif layer == "JRCWaterHand":
            entry["derived_from"] = ("JRC Global Surface Water; README документации Sen1Floods11 описывает слой как "
                                     "'JRC Permement Water overlapping labeled data'")
            # The layer is not observed on the event day: it marks water seen in both 1984 and 2018.
            entry["observed_at"] = "1984/2018"
            entry["observed_at_note"] = "период наблюдений JRC GSW для слоя, а не дата съёмки S1"
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
    commands = ["git lfs install --local", "git lfs pull", "uv sync --project ml --group research",
                "uv sync --project backend --extra demo"]
    if not live:
        commands += [f"curl --create-dirs -o data/source/{item['name']} {item['url']}"
                     for item in info["source_manifest"] if item.get("url")]
        commands.append(f"uv run --project ml floodvalue infer --s1 data/source/{scene_id}_S1Hand.tif "
                        f"--out data/inference/{scene_id}" +
                        (" --recipe ml/artifacts/next_optical/recipe.json" +
                         (f" --s2 data/source/{scene_id}_S2Hand.tif --s1-date {info['model']['dates']['s1_date']} "
                          f"--s2-date {info['model']['dates']['s2_date']}" if info['model']['mode'] == 'S1+S2' else " --no-optical")
                         if info.get("model") else ""))
        if info.get("origin", "demo") == "demo":
            commands.append("uv run --project backend --extra demo python scripts/prepare_demo.py")
    commands += ["uv run --project ml floodvalue download",
                 "uv run --project ml floodvalue infer --chip <CHIP> --out data/inference/<CHIP>",
                 "./scripts/compose.sh up --build"]
    if info.get("source_kind") == "uploaded_sentinel1_geotiff":
        commands.append(f'curl --data-binary @source_s1.tif -H "Content-Type: image/tiff" '
                        f'"http://localhost:18000/api/uploads?observed_at={info["observed_at"]}'
                        f'&radiometry={info["source_radiometry"]}&filename=source_s1.tif"')
    elif live:
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
    {"name": "Sen1Floods11 v1.1, чипы с ручной разметкой (train, valid, test_core, test_adjacent, bolivia)",
     "version": "Sen1Floods11_v1.1", "url": "https://github.com/cloudtostreet/Sen1Floods11",
     # First and last event s1_date of data/catalog/sen1floods11_handlabeled.json, and the `updated` range
     # of the 1343 v1.1 bucket objects in ml/artifacts/download_manifest.json (a test checks both).
     "observed_at": "2016-08-12/2019-09-17",
     "published_at": "2020-10-01T17:16:58.738Z/2020-10-15T16:16:25.190Z", "available_at": None,
     "role": "training_and_evaluation", "split": "ml/artifacts/split.json",
     "limitation": "11 исторических событий; test_core делит события с train, Bolivia - отложенное событие",
     "checksums": "ml/artifacts/download_manifest.json (md5 1343 объектов бакета, скачано 2026-09-25)",
     **SEN1FLOODS11_TERMS},
    {"name": "Веса энкодера ResNet34 (ImageNet)", "version": "segmentation-models-pytorch 0.5.0",
     "url": "https://huggingface.co/smp-hub/resnet34.imagenet", "role": "pretrained_initialisation",
     "observed_at": None, "observed_at_note": "не наблюдение: предобученные веса классификации изображений",
     # lastModified of revision fa54a662fb7bb5149049a30c11fac0deb86cf7e7 in the Hugging Face model API.
     "published_at": "2025-01-15T17:51:52Z", "available_at": None,
     "revision": "fa54a662fb7bb5149049a30c11fac0deb86cf7e7",
     "limitation": "только инициализация; данных о паводках и Sentinel-1 в этих весах нет",
     "license": "other_without_text",
     "license_note": ("Проверено 2026-09-26: карточка модели smp-hub/resnet34.imagenet указывает license: other и "
                      "не даёт текста лицензии. Используется только для инициализации обучения; поставляемые веса наши."),
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
    """Over the budget always reads counterfactual (the case rule); lateness has its own column."""
    if key == "A":
        return "no_paid_data"
    if not item["budget_feasible"]:
        return "counterfactual_over_budget"
    if not item.get("timely", True):
        return "late_for_deadline"
    return "within_budget" if item.get("constraints_feasible", True) else "constraints_infeasible"


def _plan_change(base: list[str], new: list[str]) -> str:
    added = [order for order in new if order not in base]
    dropped = [order for order in base if order not in new]
    if not added and not dropped:
        return "план C без изменений"
    return "план C " + "; ".join(part for part in (added and "добавляет " + ", ".join(added),
                                                   dropped and "убирает " + ", ".join(dropped)) if part)


def _blank(value: object) -> object:
    return "" if value is None else value


def _flag(value: object) -> str:
    return str(bool(value)).lower()


def _bool(value: object) -> str:
    """CSV boolean: true/false like every other flag of the package; empty when unknown."""
    return "" if value is None else _flag(value)


def plan_row(strategy: str, line: dict, product: dict) -> list:
    """One procurement_plan.csv row: the priced line next to the catalog conditions that explain it."""
    values = {**line, "strategy": strategy, "footprint_id": product["footprint_id"],
              "data_role": product["data_role"], "guaranteed_purchase": _flag(line["guaranteed_purchase"]),
              "base_rate_source": product["base_rate_source"], "rounding": LINE_ROUNDING}
    return [_blank(values[column]) for column in PLAN_COLUMNS]


def decision_row(row: dict, case_loss: float, flood_loss: float, decision: dict) -> list:
    """One zone_decisions.csv row per catalog order: why it is in basket C or not, with its prices."""
    chosen = row["chosen"]
    values = {
        **row, "timely": _flag(row["timely"]), "counts_for_coverage": _flag(row["counts_for_coverage"]),
        "selected_in_c": _flag(row["in_c"]), "selected_in_b": _flag(row["in_b"]),
        "reason_text": REASONS[row["reason"]], "unit_price_rub_km2": row["basket_unit_price_rub_km2"],
        "cost_rub": row["basket_cost_rub"],
        "basket_price_with_zone_rub": row["basket_total_rub"] if row["basket_hypothetical"] else None,
        "asset_ids": "|".join(row["asset_ids"]), "expected_loss_rub": case_loss,
        "flood_expected_loss_rub": flood_loss,
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
    # Case files and the basket use mandatory EL = p·V·q; flood-only is supplementary.
    all_assets = sorted(enrich_assets(raster_assets(scene_id, info), info["model_threshold"]),
                        key=lambda asset: (-(asset["case_expected_loss_rub"] or 0), asset["id"]))
    if info.get("model"):
        for asset in all_assets:
            asset["p_check"] = None
    region_ids = [asset["id"] for asset in result["assets"]
                  if contains(result["region"]["bounds"], asset)]
    region_rank = {asset["id"]: index for index, asset in enumerate(sorted(
        (asset for asset in result["assets"] if asset["case_loss_status"] != "no_data"),
        key=lambda asset: (-asset["case_expected_loss_rub"], asset["id"])), 1)}
    case_loss = {asset["id"]: asset["case_expected_loss_rub"] or 0 for asset in result["assets"]}
    flood_loss = {asset["id"]: asset["flood_expected_loss_rub"] or 0 for asset in result["assets"]}

    def case_covered(asset_ids: list[str], changed: dict[str, float] | None = None) -> float:
        losses = {**case_loss, **(changed or {})}
        return round(sum(losses[asset_id] for asset_id in asset_ids), 2)

    strategies = result["strategies"]
    pricing = result["pricing"]
    zone_by_id = {zone["id"]: zone for zone in result["zones"]}
    catalog = result["catalog"]
    product_by_id = {row["candidate_id"]: row for row in catalog}
    decisions = {row["zone_id"]: row for row in result["zone_decisions"]}
    live = info.get("source_kind") in {"live_sentinel1_rtc", "uploaded_sentinel1_geotiff"}
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
             "exposure_rub", "map_doubtful", "p_calibration_status"],
            [[asset["id"], scene_id, _blank(asset["p_flood"]), _blank(asset["case_expected_loss_rub"]),
              _blank(asset["map_error_probability"]), region_rank.get(asset["id"], ""),
              str(asset["id"] in region_ids).lower(), asset["case_loss_status"], _blank(asset.get("p_window3")),
              _bool(asset.get("permanent_water_at_point")), _blank(asset["flood_expected_loss_rub"]),
              _blank(asset["uncertainty"]), asset["exposure_rub"], _bool(asset["map_doubtful"]),
              (asset.get("p_check") or {}).get("status", "")]
             for asset in all_assets]))
        asset_rows = result["sensitivity"]["asset"]
        replans = asset_plan_sensitivity(ctx.space, asset_rows, inputs["budget_rub"], inputs["priority"],
                                         inputs["deadline_hours"], inputs["must_cover"])
        sensitivity_rows = []
        base_orders = strategies["C"]["candidate_ids"]
        for row, selected in zip(asset_rows, replans):
            changed_inputs = {"asset_id": row["asset_id"], "input": row["input"],
                              "factor": row["factor"], "input_value": row["input_value"]}
            interpretation = (f"EL {row['case_expected_loss_rub']} руб.; место {row['case_baseline_rank']}→"
                              f"{row['case_rank']} среди десяти фиксированных объектов; "
                              f"{_plan_change(base_orders, selected['candidate_ids'])}; карта p неизменна")
            if row["asset_id"] in flood_loss and flood_loss[row["asset_id"]] != case_loss[row["asset_id"]]:
                interpretation += "; поправка на постоянную воду JRC показана отдельно"
            changed_flood = dict(flood_loss)
            if changed_flood[row["asset_id"]] != 0 or case_loss[row["asset_id"]] == 0:
                changed_flood[row["asset_id"]] = row["expected_loss_rub"]
            # Same full-cost and residual-uncertainty rules as strategy_comparison.csv, for the re-planned C.
            costs = decision_costs(selected["quote"]["total_rub"], selected["quote"]["lines"])
            residual = residual_for(ctx, selected["zone_ids"])
            sensitivity_rows.append([
                f"asset_{row['asset_id']}_{row['input']}_{row['factor']}", "C",
                json.dumps(changed_inputs, ensure_ascii=False), costs["decision_cost_rub"],
                selected["quote"]["total_rub"], costs["other_cost_rub"],
                case_covered(selected["covered_asset_ids"], {row["asset_id"]: row["case_expected_loss_rub"]}),
                "feasible" if selected["feasible"] else "infeasible", interpretation,
                _blank(residual["residual_uncertainty"]), residual["uncertainty_status"],
                row["case_expected_loss_rub"], row["case_rank"], "|".join(selected["candidate_ids"]),
                _blank(selected["covered_doubt_share"]),
                round(sum(changed_flood[item] for item in selected["covered_asset_ids"]), 2),
            ])
        for row in result["sensitivity"]["plan"]:
            changed_inputs = {"input": row["input"], "factor": row["factor"],
                              "budget_rub": row["budget_rub"],
                              "tariff_multiplier": row["tariff_multiplier"]}
            sensitivity_rows.append([
                f"plan_{row['input']}_{row['factor']}", "C",
                json.dumps(changed_inputs, ensure_ascii=False), row["decision_cost_rub"], row["data_cost_rub"],
                row["other_cost_rub"], case_covered(row["covered_asset_ids"]),
                "feasible" if row["feasible"] else "infeasible",
                f"{row['input']} x{row['factor']}: {_plan_change(base_orders, row['candidate_ids'])}; "
                "покрытый EL не равен предотвращённому ущербу",
                _blank(row["residual_uncertainty"]), row["uncertainty_status"],
                "", "", "|".join(row["candidate_ids"]), _blank(row["covered_doubt_share"]),
                round(sum(flood_loss[item] for item in row["covered_asset_ids"]), 2),
            ])
        archive.writestr("sensitivity.csv", _csv(
            ["scenario_id", "strategy", "changed_inputs_json", "decision_cost_rub", "data_cost_rub", "other_cost_rub",
             "covered_expected_loss_rub", "result_status", "interpretation", "residual_uncertainty",
             "uncertainty_status", "asset_expected_loss_rub", "rank", "zone_ids", "covered_doubt_share",
             "covered_flood_loss_rub"],
            sensitivity_rows))
        # Every product of every footprint is its own order in the one catalog B and C choose from.
        json_file("candidate_orders.geojson", {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": zone_by_id[row["footprint_id"]]["geometry"],
             "properties": {**{key: value for key, value in row.items() if key != "name"},
                            "c_selected": row["in_c"], "b_selected": row["in_b"], "c_reason": row["reason"]}}
            for row in catalog]})
        archive.writestr("zone_decisions.csv", _csv(DECISION_COLUMNS, [
            decision_row(row, case_covered(row["asset_ids"]),
                         round(sum(flood_loss[item] for item in row["asset_ids"]), 2),
                         decisions[row["footprint_id"]]) for row in catalog]))
        archive.writestr("procurement_plan.csv", _csv(PLAN_COLUMNS, [
            plan_row(strategy, line, product_by_id[line["candidate_id"]])
            for strategy in ("B", "C") for line in strategies[strategy]["quote"]["lines"]]))
        json_file("strategy_plans.json", {"A": [], "B": strategies["B"]["candidate_ids"],
                                          "C": strategies["C"]["candidate_ids"]})
        archive.writestr("strategy_comparison.csv", _csv(
            ["strategy", "strategy_status", "data_cost_rub", "other_cost_rub", "other_cost_status", "decision_cost_rub",
             "budget_rub", "budget_feasible", "delivered_by_deadline", "covered_expected_loss_rub", "coverage_share",
             "coverage_status", "covered_asset_count", "covered_doubt_share",
             "residual_uncertainty", "uncertainty_status", "uncertainty_basis", "covered_flood_loss_rub",
             "other_cost_basis", "compute_rub", "review_rub", "processing_rub", "field_rub", "covered_chance_share"],
            [[key, _strategy_status(key, item), item["data_cost_rub"], item["other_cost_rub"],
              item["other_costs"]["status"], item["decision_cost_rub"], inputs["budget_rub"],
              _flag(item["budget_feasible"]), _flag(item.get("timely", True)), item["case_covered_expected_loss_rub"],
              _blank(item["case_coverage_share"]), _blank(item["case_coverage_status"]),
              len(item.get("covered_asset_ids", [])), _blank(item.get("covered_doubt_share")),
              _blank(item["residual_uncertainty"]), item["uncertainty_status"], item["uncertainty_basis"],
              round(sum(flood_loss[asset_id] for asset_id in item["covered_asset_ids"]), 2),
              item["other_costs"]["basis"], item["other_costs"]["compute_rub"],
              item["other_costs"]["review_rub"], item["other_costs"]["processing_rub"],
              _blank(item["other_costs"]["field_rub"]), _blank(item.get("covered_chance_share"))]
             for key, item in strategies.items()]))
        quality = result["quality"]
        metrics_rows = []

        def metric_row(part: str, event: str, water: str, method: str, scored: dict, pixels: int) -> list:
            return [part, event, water, method, (info.get("model", {}).get("id", "service_s1") if part == "selected_region" else "service_s1") if method == "model" else "baseline_vh", scored["iou"], scored["f1"], scored["precision"], scored["recall"],
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
            ["part", "event", "water", "method", "model_id", "iou", "f1", "precision", "recall", "n_pixels", "tp", "fp", "fn"],
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
            "portfolio_rule": current_rule(info.get("portfolio_rule", "по одному объекту каждого типа кейса; координаты из seed не зависят от p и разметки")),
            "model_threshold": info["model_threshold"],
            "baseline_threshold_db": info["baseline_threshold_db"],
            "probability_rule": current_rule(info.get("probability_rule", PROBABILITY_RULE)),
            "permanent_water": {"status": result["region"]["permanent_status"],
                                "rule": current_rule(info.get("permanent_rule")),
                                "period_note": current_rule(info.get("permanent_period_note")),
                                "file": "permanent_water.tif" if info.get("permanent_rule") else None},
            "source_timing": scene_timing(info),
            "doubt_rule": DOUBT_RULE, "uncertainty_zone_rule": ZONE_RULE,
            "model": info.get("model", "5 member S1-only U-Net ResNet34 ensemble, Platt calibrated on validation"),
            "model_weights": ({"card": "ml/artifacts/next_optical/MODEL_CARD.md",
                               "recipe": "ml/artifacts/next_optical/recipe.json",
                               "recipe_sha256": info["model"]["recipe_sha256"]} if info.get("model") else
                              {"card": "ml/artifacts/model/model_card.json", "sha256": model_weights()}),
            "metrics_scope": "строки test_core/bolivia: прежняя service_s1; selected_region: текущая модель",
            "baseline": ("VH < -23.6 dB: порог подобран на train, канал выбран на validation "
                         "(ml/artifacts/baseline/baseline.json)"),
            "repository": REPOSITORY, "mirror": MIRROR,
            "metrics_files": ["metrics.csv", "ml/artifacts/evaluation/metrics.csv",
                              "ml/artifacts/evaluation/evaluation.json", "ml/artifacts/evaluation/doubt_threshold.json",
                              "ml/artifacts/evaluation/summary.md"],
            "code_commit": code_commit(),
            "code_commit_rule": ("git rev-parse HEAD в репозитории в момент экспорта; в образах без .git переменная "
                                 "сборки FLOODVALUE_COMMIT; null, если нет ни того, ни другого"),
            "gitverse_commit": gitverse_commit(),
            "gitverse_status": gitverse_status(),
            "gitverse_rule": ("verified_in_gitverse_remote: ветка отслеживания репозитория GitVerse содержит "
                              "исходный commit (клон из GitVerse или проверка scripts/compose.sh до сборки образа); "
                              "одного commit на GitHub для заполнения gitverse_commit недостаточно"),
            "counterfactual_rule": ("B сверх бюджета получает strategy_status=counterfactual_over_budget и служит только "
                                    "для сравнения, в том числе при опоздании; опоздание отличает delivered_by_deadline"),
            "priority_rule": ("ранг по expected_loss_rub; map_doubtful=true помечает объекты, где шанс ошибки карты в "
                              "точке >= doubt_threshold, их стоит проверить при любом ранге; объекты no_data "
                              "перечислены с их экспозицией как неоценённый риск"),
            "suitability_rule": SUITABILITY_RULE,
            "split": "ml/artifacts/split.json: списки авторов v1.1; test-чипы, касающиеся train-чипов, вынесены в test_adjacent",
            "split_part": info.get("split_part", "test_core"),
            "metrics_independent": info.get("metrics_independent", True),
            "scene_origin": info.get("origin", "demo"),
            "budget_rub": inputs["budget_rub"],
            "deadline_hours": inputs["deadline_hours"],
            "priority": inputs["priority"],
            "expected_loss_model": "p_flood * asset_value_rub * vulnerability_coef",
            "expected_loss_scope": ("все десять фиксированных объектов чипа с p_flood, включая точки на постоянной воде; "
                                    "статус ok, partial или no_data; p_flood, expected_loss_rub и rank пусты, если не оценены"),
            "expected_loss_units": "RUB; сценарные стоимости и уязвимости объектов",
            "flood_loss_model": ("дополнительная модель, не EL кейса: flood_expected_loss_rub = 0, если "
                                 "permanent_water_at_point истинно (постоянная вода JRC), иначе expected_loss_rub"),
            "decision_loss_rule": ("Перебор корзин, экран, A/B/C и CSV-колонки кейса используют обязательный "
                                   "EL=p*V*q для тех же десяти объектов чипа, включая точки на постоянной воде. "
                                   "Колонки flood_* показывают отдельную интерпретацию с поправкой JRC."),
            "covered_loss_rule": ("сумма уникальных expected_loss_rub (p*V*q) всех десяти объектов чипа внутри "
                                  "выбранных заказов event_observation, поставленных к сроку; архивные и опоздавшие "
                                  "заказы покрытия не дают; ROI меняет сводки по пикселям, а не портфель"),
            "coverage_share_rule": "покрытый / оценённый EL тех же десяти объектов чипа; пусто с coverage_status, если знаменатель 0",
            "covered_doubt_rule": "сомнительная площадь внутри объединения выбранных зон / сомнительная площадь анализируемой области",
            "unestimated_exposure_rub": result["metrics"]["unestimated_exposure_rub"],
            "uncertainty_rule": ("asset_loss.uncertainty - шанс ошибки карты в пикселе объекта: 1 - p там, где "
                                 "flood_mask = 1, p там, где flood_mask = 0 (калиброванная p, доля 0-1); "
                                 "ensemble_spread и flood_uncertainty.tif хранят std пяти моделей "
                                 "для диагностики"),
            "candidate_rule": pricing["candidate_rule"],
            "product_rule": pricing["product_rule"],
            "zone_area_crs": "EPSG:6933",
            "proxy_check": result["asset_proxy_check"],
            "pricing": {**{key: value for key, value in pricing.items() if key not in ("candidate_rule", "product_rule")},
                        "base_rate_rub_km2": float(BASE_RATE),
                        "legal_checked_at": "2026-09-25",
                        "legal_source_url": "https://government.ru/docs/all/160697/",
                        "legal_scope": "case_edition_verified; для реальной закупки нужно сверить редакцию и поставщика",
                        "calculation_date_rule": ("заказы оцениваются на calculation_date (день анализа по UTC): она "
                                                  "задаёт календарный год заказа каждой группы скидки и возраст "
                                                  "существующих снимков"),
                        "discount_group_id": strategies["C"]["quote"]["discount_group_id"],
                        "discount_groups": {key: [group["discount_group_id"] for group in strategies[key]["quote"]["groups"]]
                                            for key in ("B", "C")},
                        "groups": {key: strategies[key]["quote"]["groups"] for key in ("B", "C")},
                        "availability_status": (f"сценарий: новая оптика через {OPTICAL_DELIVERY_HOURS} ч и новый радар "
                                                f"через {SAR_DELIVERY_HOURS} ч после заказа; архивный кадр "
                                                "доступен сразу")},
            "parameters": [{**row, "value": inputs["budget_rub"]} if row["name"] == "budget_rub" else
                           {**row, "value": info["model_threshold"], "source": info.get("model", {}).get("source", row["source"])}
                           if row["name"] == "model_threshold" else row
                           for row in PARAMETERS],
            "sensitivity_method": result["sensitivity"]["method"],
            "sensitivity_rule": ("каждая строка заново планирует C на фиксированной карте p и портфеле; decision_cost_rub "
                                 "= data_cost_rub + other_cost_rub по decision_cost_rule; residual_uncertainty и "
                                 "uncertainty_status следуют правилу A/B/C из strategy_comparison.csv"),
            "decision_evidence": result["decision_evidence"],
            "decision_cost_rule": COST_RULE,
            "calibration_bias": result["calibration_bias"],
            "limitations": result["limitations"],
            "interpretation": "Покрытый ожидаемый ущерб - это приоритет наблюдения, а не предотвращённый физический ущерб.",
            "commands": _commands(scene_id, info, inputs, live),
            "commands_note": ("Запускать из корня репозитория в Linux или macOS (Windows: передайте FLOODVALUE_COMMIT=HEAD "
                              "в docker compose). <CHIP> - любой официальный чип Sen1Floods11 с ручной разметкой, "
                              "например Bolivia_103757; floodvalue download скачивает официальные файлы v1.1 с проверкой "
                              "md5. docker compose работает так же, как podman compose; scripts/compose.sh записывает "
                              "code_commit в образ без .git. Commit зеркала GitVerse записывается, только если он "
                              "передан отдельно."),
            "inference_manifest": info.get("inference_manifest", {"stac_item_id": info.get("source_item_id"),
                                                                  "processed_at": info.get("processed_at")}),
        })
        names = ["flood_probability.tif", "flood_mask.tif", "flood_uncertainty.tif", "baseline_mask.tif"]
        if (scene_dir(scene_id) / "permanent_water.tif").is_file():
            names.append("permanent_water.tif")
        for name in names:
            archive.write(scene_dir(scene_id) / name, arcname=name)
        if info.get("source_kind") == "uploaded_sentinel1_geotiff":
            archive.write(scene_dir(scene_id) / "source_s1.tif", arcname="source_s1.tif")
        selected_footprints = sorted({product_by_id[order]["footprint_id"] for order in strategies["C"]["candidate_ids"]})
        archive.writestr("zones_overlay.png", overlay_png(scene_id, info["bbox"], result["zones"], selected_footprints,
                                                          all_assets))
        archive.writestr("README.txt",
                         "FloodValue: один согласованный комплект для выбранного чипа и бюджета.\n"
                         "Вероятности и маски получены замороженной моделью, режим входа: "
                         f"{(info.get('model') or {}).get('mode') or 'Sentinel-1'} "
                         "(S1+S2 — радар и допустимая оптика Sentinel-2; S1 fallback — только Sentinel-1).\n"
                         "asset_loss.csv: обязательный EL = p × V × q для всех объектов, в том числе на постоянной воде.\n"
                         "p_calibration_status: где на независимых снимках калиброванная p выше (overestimated) "
                         "или ниже (underestimated) доли воды; правило в run_metadata.json, calibration_bias.\n"
                         "run_metadata.json, limitations: ограничения расчёта, те же, что в интерфейсе.\n"
                         "permanent_water.tif (если есть): постоянная вода JRC. Ущерб только от паводка (0 на ней) "
                         "лежит отдельно в колонках flood_expected_loss_rub и covered_flood_loss_rub.\n"
                         "Точки и V/q учебные. Ставка и срок платной съёмки сценарные.\n"
                         "Для исторического чипа это ретроспективный разбор: слой JRC может включать "
                         "наблюдения после события. Точный статус указан в run_metadata.json/source_timing.\n"
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
