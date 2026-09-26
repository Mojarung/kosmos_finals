"""FloodValue API: frozen model evidence, regional analysis, and decision export."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import ingest
from .analysis import analyze, inspect_pixel
from .catalog import default_scene_id, known_scene_ids, metadata, scene_catalog, scene_dir
from .chips import official_chips, official_events
from .exporter import package
from .live import LiveError, discover, prepare
from . import live_jobs
from .layers import doubt_png
from .permanent import separate_live
from . import projects
from .procurement import router as procurement_router
from .backtest import router as backtest_router

app = FastAPI(title="FloodValue API", version="0.2.0")
app.include_router(procurement_router)
app.include_router(backtest_router)


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


class ProjectCreate(BaseModel):
    name: str
    scene_id: str


class AssetImport(BaseModel):
    format: str
    content: str


class DecisionCreate(BaseModel):
    bbox: str | None = None
    budget: float = 6500
    priority: str = "risk"
    deadline: int = 8
    must_cover: str | None = None


@app.get("/api/projects")
def list_projects() -> dict:
    return {"projects": projects.list_projects()}


@app.post("/api/projects", status_code=201)
def create_project(request: ProjectCreate) -> dict:
    try:
        return projects.create(request.name, request.scene_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict:
    try:
        return projects.get(project_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/api/projects/{project_id}/assets/import")
def import_project_assets(project_id: str, request: AssetImport) -> dict:
    try:
        return projects.import_assets(project_id, request.content, request.format)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (ValueError, TypeError, UnicodeError) as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/projects/{project_id}/decisions", status_code=201)
def save_project_decision(project_id: str, request: DecisionCreate) -> dict:
    try:
        detail = projects.get(project_id)
        analysis = response(request.bbox, request.budget, request.priority, request.deadline,
                            request.must_cover, detail["scene_id"])
        return projects.save_decision(project_id, analysis)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/scenes")
def scenes() -> dict:
    return scene_catalog()


class IngestRequest(BaseModel):
    chip_id: str


@app.get("/api/chips")
def chips() -> dict:
    return {"events": official_events(), "chips": official_chips(), "prepared": sorted(known_scene_ids())}


@app.post("/api/scenes", status_code=202)
def create_scene(request: IngestRequest, response: Response) -> dict:
    try:
        job = ingest.start(request.chip_id)
    except KeyError as error:
        raise HTTPException(404, "Unknown official Sen1Floods11 hand-labeled chip") from error
    if job["id"] is None:
        response.status_code = 200
    return job


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = ingest.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job")
    return job


@app.get("/api/live/discover")
def discover_scenes(bbox: str) -> dict:
    bounds = parse_bounds(bbox)
    try:
        return {"bbox": bounds, "scenes": discover(bounds)}
    except LiveError as error:
        raise HTTPException(422 if "Область" in str(error) or "рамка" in str(error) else 503,
                            str(error)) from error


@app.post("/api/live/prepare")
def prepare_scene(bbox: str, item_id: str) -> dict:
    bounds = parse_bounds(bbox)
    try:
        info = prepare(bounds, item_id)
    except LiveError as error:
        raise HTTPException(422 if "Область" in str(error) or "рамка" in str(error) or "снимок" in str(error)
                            else 503, str(error)) from error
    return {"scene_id": info["chip_id"], "observed_at": info["observed_at"],
            "source_item_id": info["source_item_id"],
            "source_item_ids": info.get("source_item_ids", [info["source_item_id"]]),
            "valid_pixels": info["valid_pixels"]}


class LiveJobCreate(BaseModel):
    bbox: str
    item_id: str


@app.post("/api/live/jobs", status_code=202)
def create_live_job(request: LiveJobCreate) -> dict:
    bounds = parse_bounds(request.bbox)
    try:
        from .live import validate_area
        validate_area(bounds)
    except LiveError as error:
        raise HTTPException(422, str(error)) from error
    return live_jobs.start(bounds, request.item_id)


@app.get("/api/live/jobs/{job_id}")
def get_live_job(job_id: str) -> dict:
    job = live_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown live job")
    return job


@app.post("/api/live/permanent")
def separate_permanent(scene_id: str) -> dict:
    """Permanent water of a live region from the JRC Global Surface Water archive (1984-2021)."""
    if scene_id not in known_scene_ids():
        raise HTTPException(404, "Unknown scene")
    try:
        return separate_live(scene_id)
    except LiveError as error:
        raise HTTPException(422 if "области" in str(error) else 503, str(error)) from error


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


LAYER_FILES = {"radar.png", "model.png", "baseline.png", "reference.png", "probability.png", "uncertainty.png",
               "permanent.png"}
CACHE = {"Cache-Control": "public, max-age=3600"}


@app.get("/api/demo/{scene_id}/{asset_name}")
def demo_asset(scene_id: str, asset_name: str) -> Response:
    if scene_id not in known_scene_ids():
        raise HTTPException(404)
    if asset_name == "doubt.png":
        return Response(doubt_png(scene_id), media_type="image/png", headers=CACHE)
    path = scene_dir(scene_id) / asset_name
    if asset_name not in LAYER_FILES or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png", headers=CACHE)


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
