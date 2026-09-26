"""The frozen research package used by the service; no training at runtime."""
import hashlib
import json
import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECIPE = Path(os.environ.get("FLOODVALUE_MODEL_RECIPE", ROOT / "ml/artifacts/next_optical/recipe.json"))
DATES = ROOT / "ml/artifacts/next_optical/acquisition_dates.json"


def acquisition(chip_id: str) -> dict | None:
    row = json.loads(DATES.read_text()).get(chip_id.split("_")[0])
    return {key: value.replace("/", "-") for key, value in row.items()} if row else None


def optical_allowed(dates: dict | None) -> bool:
    return bool(dates and 0 <= (date.fromisoformat(dates["s1_date"]) - date.fromisoformat(dates["s2_date"])).days <= 2)


def identity() -> dict:
    recipe = json.loads(RECIPE.read_text())
    return {"id": "next_optical", "recipe_sha256": hashlib.sha256(RECIPE.read_bytes()).hexdigest(),
            "threshold": recipe["threshold"], "calibration": recipe["calibration_method"],
            "source": "ml/research/NEXT_RESULTS.md"}
