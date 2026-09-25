from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .analysis import analyze
from .catalog import DATA_DIR, metadata
from .planning import as_polygon


app = FastAPI(title="FloodValue API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


def parse_bounds(bbox: str | None) -> list[float] | None:
    if not bbox:
        return None
    try:
        numbers = [float(piece) for piece in bbox.split(",")]
    except ValueError as error:
        raise HTTPException(422, "bbox must contain four decimal coordinates") from error
    if (len(numbers) != 4 or numbers[0] >= numbers[2] or numbers[1] >= numbers[3]
            or not (-180 <= numbers[0] < numbers[2] <= 180)
            or not (-85 <= numbers[1] < numbers[3] <= 85)):
        raise HTTPException(422, "bbox must be west,south,east,north within supported map bounds")
    return numbers


def response(bbox: str | None, budget: float, priority: str, deadline: int,
             must_cover: str | None) -> dict:
    bounds = parse_bounds(bbox)
    if priority not in {"risk", "information"}:
        raise HTTPException(422, "priority must be risk or information")
    if must_cover and must_cover not in {asset["id"] for asset in metadata()["assets"]}:
        raise HTTPException(422, "Unknown asset")
    return analyze(bounds, budget, priority, deadline, must_cover)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "chip_id": metadata()["chip_id"]}


@app.get("/api/analysis")
def get_analysis(bbox: str | None = None,
                 budget: float = Query(6500, ge=0, le=1000000),
                 priority: str = "risk",
                 deadline: int = Query(8, ge=1, le=48),
                 must_cover: str | None = None) -> dict:
    return response(bbox, budget, priority, deadline, must_cover)


@app.get("/api/demo/{asset_name}")
def demo_asset(asset_name: str) -> FileResponse:
    if asset_name not in {"water.png", "radar.png"}:
        raise HTTPException(404)
    return FileResponse(DATA_DIR / asset_name, media_type="image/png")


@app.get("/api/export")
def export_decision(bbox: str | None = None,
                    budget: float = Query(6500, ge=0, le=1000000),
                    priority: str = "risk",
                    deadline: int = Query(8, ge=1, le=48),
                    must_cover: str | None = None) -> StreamingResponse:
    result = response(bbox, budget, priority, deadline, must_cover)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("analysis.json", json.dumps(result, ensure_ascii=False, indent=2))
        archive.writestr("source_manifest.json", json.dumps(metadata()["source_manifest"], ensure_ascii=False, indent=2))
        archive.writestr("selected_region.geojson", json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": as_polygon(result["region"]["bounds"]),
             "properties": {"area_km2": result["region"]["area_km2"],
                            "data_status": result["region"]["data_status"]}}
        ]}, ensure_ascii=False))
        archive.writestr("README.txt", "FloodValue — сценарный экспорт интерфейса.\n"
                         "Ручная маска воды не является прогнозом. Вероятности объектов сценарные.\n"
                         "Этот архив не является итоговым комплектом ML-результатов кейса.\n")
        assets = io.StringIO()
        writer = csv.writer(assets)
        writer.writerow(["asset_id", "asset_class", "longitude", "latitude", "scenario_probability",
                         "asset_value_rub", "vulnerability_coef", "scenario_expected_loss_rub"])
        for asset in result["assets"]:
            writer.writerow([asset["id"], asset["class"], asset["longitude"], asset["latitude"],
                             asset["scenario_probability"], asset["value_rub"],
                             asset["vulnerability"], asset["expected_loss_rub"]])
        archive.writestr("assets_scenario.csv", assets.getvalue())
        archive.writestr("candidate_zones.geojson", json.dumps({
            "type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": zone["geometry"],
                 "properties": {key: value for key, value in zone.items() if key not in {"geometry", "bounds"}}}
                for zone in result["zones"]],
        }, ensure_ascii=False))
    output.seek(0)
    return StreamingResponse(output, media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="floodvalue-decision-demo.zip"'
    })
