"""Read the reproduced historical procurement experiment; it never drives live plans."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .catalog import ROOT

router = APIRouter(prefix="/api/backtest", tags=["backtest"])
REPORT = ROOT / "data/backtest/procurement_70.json"


@lru_cache(maxsize=1)
def report() -> dict:
    try:
        data = json.loads(REPORT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HTTPException(503, "Ретроспективный расчёт пока недоступен") from error
    if data.get("summary", {}).get("chips") != 70 or len(data.get("chips", [])) != 70:
        raise HTTPException(503, "Ретроспективный расчёт неполный")
    return data


@router.get("")
def get_report() -> dict:
    return report()
