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


# A frame drawn on the map has no S2Hand. There the context model runs: event radar plus a year of
# same-orbit radar, HAND/DEM, JRC and WorldCover, and Sentinel-2 L2A when a clear pass exists 0-2 days
# before the radar (ml/research/fresh/RESULTS.md). FLOODVALUE_LIVE_MODEL=next_optical keeps the S1 fallback.
CONTEXT_RECIPE = Path(os.environ.get("CONTEXT_RECIPE", ROOT / "ml/artifacts/context_model/recipe.json"))


def context_enabled() -> bool:
    return os.environ.get("FLOODVALUE_LIVE_MODEL", "context") == "context" and CONTEXT_RECIPE.is_file()


def context_identity(info: dict) -> dict:
    """The live scene's model block, in the same shape as identity()."""
    track = info["track"]
    return {"id": "context", "recipe_sha256": hashlib.sha256(CONTEXT_RECIPE.read_bytes()).hexdigest(),
            "threshold": info["threshold"], "calibration": "platt_on_grouped_oof",
            "source": "ml/research/fresh/RESULTS.md",
            "mode": "S1 context + S2 L2A" if track == "optical" else "S1 context",
            "track": track, "members": info["members"], "optical_used_pixels": info["optical_used_pixels"],
            "pre_event_dates": info.get("pre_event_dates"), "s2_items": info.get("s2_items", [])}
