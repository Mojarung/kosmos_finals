"""Package real Sen1Floods11 S1 and frozen ensemble inference for the web demo.

Run after the frozen model wrote data/inference/<chip>/ for each chip and the three
S1Hand/LabelHand/JRCWaterHand files are in data/source
(uv run --project ml floodvalue infer ...). This script never trains or tunes
the model. Manual labels enter only the diagnostic metrics. The scene itself is
built by backend/app/scene_builder.py, the same code the live ingest API uses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.scene_builder import SceneSpec, build_scene, load_source_meta  # noqa: E402

SOURCE = ROOT / "data" / "source"
INFERENCE = ROOT / "data" / "inference"
OUTPUT = ROOT / "data" / "demo"
DOWNLOAD_MANIFEST = ROOT / "ml" / "data" / "sen1floods11" / "download_manifest.json"
SCENES = [
    ("Nigeria_812045", "Нигерия", "2018-09-21", 2026),
    ("Ghana_313799", "Гана", "2018-09-18", 2027),
    ("Mekong_333434", "Меконг · Камбоджа", "2018-08-05", 2028),
]


def prepare_scene(chip: str, label_name: str, observed_at: str, seed: int, source_meta: dict) -> dict:
    evaluation = json.loads((ROOT / "ml/artifacts/evaluation/evaluation.json").read_text())
    per_chip = evaluation["parts"]["test_core"]["all_water/main"]["per_chip"]
    assert chip in per_chip, "Demo chips must be independent test_core examples"
    spec = SceneSpec(chip_id=chip, event=label_name, observed_at=observed_at, seed=seed,
                     s1_path=SOURCE / f"{chip}_S1Hand.tif", label_path=SOURCE / f"{chip}_LabelHand.tif",
                     inference_dir=INFERENCE / chip, split_part="test_core", origin="demo",
                     source_meta=source_meta, jrc_path=SOURCE / f"{chip}_JRCWaterHand.tif")
    return build_scene(spec, OUTPUT / chip)


def write_quality() -> None:
    """Independent metrics shown in the service, copied from the ML evaluation."""
    evaluation = json.loads((ROOT / "ml/artifacts/evaluation/evaluation.json").read_text())
    quality_path = OUTPUT / "quality.json"
    previous = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else {}
    quality = {"threshold": evaluation["threshold"], "calibration": evaluation["platt"],
               "parts": {}, "flood_only": {}}
    for part in ("test_core", "bolivia"):
        methods = evaluation["parts"][part]
        probability = evaluation["probability"][part]
        bins = probability["reliability_calibrated"]["bins"]
        pixels = sum(item["count"] for item in bins)
        quality["parts"][part] = {
            "baseline": methods["all_water/baseline"]["overall"]["micro"],
            "model": methods["all_water/main"]["overall"]["micro"],
            "ece_raw": probability["reliability_raw"]["ece"],
            "ece_calibrated": probability["reliability_calibrated"]["ece"],
            "brier": probability["reliability_calibrated"]["brier"],
            "uncertainty_error_auroc": probability["uncertainty_member_std"]["auroc"],
            "uncertainty_error_rate_by_decile": probability["uncertainty_member_std"]["error_rate_by_decile"],
            "doubt_error_auroc": probability["uncertainty_entropy"]["auroc"],
            "doubt_error_rate_by_decile": probability["uncertainty_entropy"]["error_rate_by_decile"],
            "doubt_top_decile_bits": probability["uncertainty_entropy"]["decile_edges"][9],
            "error_rate": probability["uncertainty_entropy"]["error_rate"],
            "expected_error_rate": sum(item["count"] * min(item["mean_p"], 1 - item["mean_p"]) for item in bins) / pixels,
            "chips": methods["all_water/main"]["overall"]["chips"],
        }
        quality["flood_only"][part] = {
            "baseline": methods["no_jrc_permanent_water/baseline"]["overall"]["micro"],
            "model": methods["no_jrc_permanent_water/main"]["overall"]["micro"],
            "rule": "pixels of JRCWaterHand permanent water removed from evaluation",
        }
    transfer_path = ROOT / "ml/artifacts/evaluation/rtc_transfer.json"
    if transfer_path.exists():
        transfer = json.loads(transfer_path.read_text(encoding="utf-8"))
        quality["rtc_transfer"] = {key: transfer[key] for key in ("rules", "fit_chips", "check_chips", "water_weighted_iou")}
        quality["rtc_transfer"]["mapping"] = transfer.get("shipped_mapping")
    errors_path = ROOT / "ml/artifacts/evaluation/error_analysis.json"
    if errors_path.exists():
        errors = json.loads(errors_path.read_text(encoding="utf-8"))
        quality["errors"] = errors.get("summary")
        quality["proxy_test_core"] = errors.get("proxy_test_core")
    else:
        for key in ("errors", "proxy_test_core"):
            if key in previous:
                quality[key] = previous[key]
    quality_path.write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    if "--quality-only" in sys.argv:
        write_quality()
        return
    source_meta = load_source_meta(DOWNLOAD_MANIFEST)
    scenes = [prepare_scene(*scene, source_meta) for scene in SCENES]
    (OUTPUT / "scenes.json").write_text(json.dumps({"default_chip_id": scenes[0]["chip_id"],
                                                   "scenes": scenes}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_quality()
    print(json.dumps(scenes, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
