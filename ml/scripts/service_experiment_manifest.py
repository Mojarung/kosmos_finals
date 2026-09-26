"""Record data provenance and model checksums for a completed development run."""
import argparse
import hashlib
import json
from pathlib import Path

from floodvalue import paths
from floodvalue.l2a_data import ROOT as DATA, development
from floodvalue.context_sources import L2A_ASSETS


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-kuro", action="store_true")
    args = parser.parse_args()
    plan = json.loads((DATA / "acquisition.json").read_text())
    selected = {}
    for key, item in plan["items"].items():
        stac = item["stac"]
        selected[key] = {"datetime": stac["properties"].get("datetime"), "bbox": stac.get("bbox"),
                         "processing_baseline": stac["properties"].get("s2:processing_baseline"),
                         "offset": item["offset"], "chips": item["chips"],
                         "asset_hrefs": {b: stac["assets"][b]["href"] for b in (*L2A_ASSETS, "SCL")}}
    acquisition = {"plan_sha256": sha(DATA / "acquisition.json"), "rule": plan["rule"],
                   "dates": plan["dates"], "items": selected, "per_chip": plan["per_chip"],
                   "ready": json.loads((DATA / "ready.json").read_text())}
    (paths.PIPELINE_ROOT / "research/L2A_ACQUISITION_MANIFEST.json").write_text(json.dumps(acquisition, indent=2))
    models = {}
    folds = development()
    kinds = ["hgb", "lightgbm", "catboost", "terramind"] + (["terramind_kuro"] if args.include_kuro else [])
    for kind in kinds:
        root = paths.RUNS_DIR / ({"terramind": "terramind_cv", "terramind_kuro": "terramind_kuro"}.get(kind, f"l2a_boost/{kind}"))
        for k in range(5):
            folder = root / f"fold{k}"
            checkpoint = folder / ("model.safetensors" if kind.startswith("terramind") else "model.joblib")
            training = json.loads((folder / "training.json").read_text())
            expected_train = {n for n, f in folds.items() if f != k}
            expected_outer = {n for n, f in folds.items() if f == k}
            if set(training["train"]) != expected_train or set(training["outer"]) != expected_outer:
                raise ValueError(f"unexpected training/outer membership: {folder}")
            models[f"{kind}/fold{k}"] = {"path": str(checkpoint.relative_to(paths.PIPELINE_ROOT)),
                                         "bytes": checkpoint.stat().st_size, "sha256": sha(checkpoint),
                                         "train_chips": len(expected_train), "outer_chips": len(expected_outer)}
    report = {"base_commit": "cae7833", "data": "L2A_ACQUISITION_MANIFEST.json", "models": models,
              "production_artifacts_changed": False, "new_test_evaluation": False,
              "terramind_signature": json.loads((paths.RUNS_DIR / "terramind_cv/signature.json").read_text()),
              "boost_signature": json.loads((paths.RUNS_DIR / "l2a_boost/signature.json").read_text())}
    if args.include_kuro:
        report["kuro_signature"] = json.loads((paths.RUNS_DIR / "terramind_kuro/signature.json").read_text())
        report["kuro_pretraining"] = json.loads((paths.RUNS_DIR / "terramind_kuro/pretraining.json").read_text())
    (paths.PIPELINE_ROOT / "research/SERVICE_EXPERIMENT_MANIFEST.json").write_text(json.dumps(report, indent=2))
    print(f"Verified training/outer membership and checksums for {len(models)} models")


if __name__ == "__main__":
    main()
