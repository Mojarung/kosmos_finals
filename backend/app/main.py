"""FloodValue API: frozen model evidence, regional analysis, and decision export."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from .analysis import analyze, inspect_pixel
from .catalog import DATA_DIR, default_scene_id, known_scene_ids, metadata, scene_catalog
from .exporter import package

app = FastAPI(title="FloodValue API", version="0.2.0")


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
             must_cover: str | None, scene_id: str | None) -> dict:
    if priority not in {"risk", "information"}:
        raise HTTPException(422, "priority must be risk or information")
    scene_id = scene_id or default_scene_id()
    if scene_id not in known_scene_ids():
        raise HTTPException(404, "Unknown scene")
    if must_cover and must_cover not in {asset["id"] for asset in metadata(scene_id)["assets"]}:
        raise HTTPException(422, "Unknown asset")
    return analyze(parse_bounds(bbox), budget, priority, deadline, must_cover, scene_id)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "default_chip_id": default_scene_id(),
            "scenes": len(known_scene_ids()), "model": "frozen_ensemble"}


@app.get("/api/scenes")
def scenes() -> dict:
    return scene_catalog()


@app.get("/api/pixel")
def pixel(
    longitude: float = Query(..., ge=-180, le=180),
    latitude: float = Query(..., ge=-85, le=85),
    scene_id: str | None = None,
) -> dict:
    scene_id = scene_id or default_scene_id()
    if scene_id not in known_scene_ids():
        raise HTTPException(404, "Unknown scene")
    return inspect_pixel(longitude, latitude, scene_id)


@app.get("/api/analysis")
def get_analysis(
    bbox: str | None = None,
    budget: float = Query(6500, ge=0, le=1000000),
    priority: str = "risk",
    deadline: int = Query(8, ge=1, le=48),
    must_cover: str | None = None,
    scene_id: str | None = None,
) -> dict:
    return response(bbox, budget, priority, deadline, must_cover, scene_id)


@app.get("/api/demo/{scene_id}/{asset_name}")
def demo_asset(scene_id: str, asset_name: str) -> FileResponse:
    if scene_id not in known_scene_ids() or asset_name not in {
        "radar.png", "model.png", "baseline.png", "reference.png",
        "probability.png", "uncertainty.png",
    }:
        raise HTTPException(404)
    return FileResponse(DATA_DIR / scene_id / asset_name, media_type="image/png")


@app.get("/api/export")
def export_decision(
    bbox: str | None = None,
    budget: float = Query(6500, ge=0, le=1000000),
    priority: str = "risk",
    deadline: int = Query(8, ge=1, le=48),
    must_cover: str | None = None,
    scene_id: str | None = None,
) -> StreamingResponse:
    result = response(bbox, budget, priority, deadline, must_cover, scene_id)
    return StreamingResponse(package(result), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="floodvalue-{result["chip"]["chip_id"]}.zip"'
    })
