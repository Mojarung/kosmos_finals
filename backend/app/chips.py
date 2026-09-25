"""Official Sen1Floods11 hand-labeled chips the operator can send to the frozen model."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(os.environ.get(
    "FLOODVALUE_CHIP_CATALOG",
    Path(__file__).resolve().parents[2] / "data" / "catalog" / "sen1floods11_handlabeled.json"))


@lru_cache(maxsize=1)
def _catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def official_chips() -> list[dict]:
    return _catalog()["chips"]


def official_events() -> list[dict]:
    return _catalog()["events"]


@lru_cache(maxsize=1)
def _by_id() -> dict[str, dict]:
    return {chip["chip_id"]: chip for chip in official_chips()}


def chip_record(chip_id: str) -> dict:
    """Return the catalog entry; KeyError for anything outside the official hand-labeled set."""
    return _by_id()[chip_id]
