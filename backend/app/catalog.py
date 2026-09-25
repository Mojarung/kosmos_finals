"""Read the attributed, generated demo data. No external services at request time."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path


DATA_DIR = Path(os.environ.get("DEMO_DATA_DIR", Path(__file__).resolve().parents[2] / "data" / "demo"))


@lru_cache(maxsize=1)
def metadata() -> dict:
    return json.loads((DATA_DIR / "metadata.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def water_mask() -> bytes:
    mask = (DATA_DIR / "water_mask.bin").read_bytes()
    info = metadata()
    if len(mask) != info["width"] * info["height"]:
        raise ValueError("Demo water mask dimensions do not match metadata")
    return mask
