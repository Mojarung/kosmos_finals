"""Where to buy the imagery of a plan, and the imagery an operator received from a supplier.

Providers come from data/catalog/imagery_providers.json, each with its source URLs and
check date. A delivery is what the operator got after buying outside the service: the
files of a folder, uploaded one by one. Every raster gets its footprint and the share
of each order zone of the scene it covers, so the operator sees which zones the
purchase actually closed. Nothing here places an order.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import rasterio
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from rasterio.warp import transform_bounds
from shapely.geometry import box, mapping, shape
from shapely.ops import transform

from . import procurement
from .catalog import ROOT, known_scene_ids
from .planning import TO_EQUAL_AREA

router = APIRouter(prefix="/api/procurement", tags=["procurement"])
PROVIDERS = ROOT / "data" / "catalog" / "imagery_providers.json"
STORE = ROOT / "data" / "runtime" / "deliveries"
MAX_FILE_BYTES = 2 * 1024 ** 3
MAX_FILES = 200
RASTER_SUFFIXES = (".tif", ".tiff", ".jp2", ".img", ".ntf", ".nitf")
SAFE_PART = re.compile(r"^[\w.\- ()\[\]А-Яа-яЁё]+$")


def providers() -> dict:
    return json.loads(PROVIDERS.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db() -> sqlite3.Connection:
    procurement.DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(procurement.DB)
    db.execute("CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, scene_id TEXT, payload TEXT)")
    return db


def _save(record: dict) -> dict:
    with _db() as db:
        db.execute("INSERT OR REPLACE INTO deliveries VALUES (?, ?, ?)",
                   (record["id"], record["scene_id"], json.dumps(record, ensure_ascii=False, allow_nan=False)))
    return record


def _load(delivery_id: str) -> dict:
    with _db() as db:
        row = db.execute("SELECT payload FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Поставка не найдена.")
    return json.loads(row[0])


def safe_relative(path: str) -> Path:
    """A relative path inside the delivery folder: no drive, no '..', only plain names."""
    parts = [part for part in re.split(r"[\\/]+", path.strip()) if part not in ("", ".")]
    if not parts or len(parts) > 8 or any(part == ".." or not SAFE_PART.match(part) for part in parts):
        raise HTTPException(422, "Недопустимое имя файла.")
    return Path(*parts)


def zone_coverage(footprint, zones: list[dict]) -> list[dict]:
    """Share of every order zone inside the raster footprint (areas in EPSG:6933)."""
    rows = []
    for zone in zones:
        geometry = shape(zone["geometry"])
        inside = footprint.intersection(geometry)
        if inside.is_empty:
            continue
        share = transform(TO_EQUAL_AREA.transform, inside).area / transform(TO_EQUAL_AREA.transform, geometry).area
        rows.append({"zone_id": zone["id"], "share": round(min(share, 1.0), 4)})
    return sorted(rows, key=lambda row: -row["share"])


def describe_raster(path: Path, zones: list[dict]) -> dict:
    """Georeference, size and zone coverage of one delivered raster; 'not_georeferenced' when it has none."""
    with rasterio.open(path) as src:
        info = {"width": src.width, "height": src.height, "bands": src.count, "dtype": src.dtypes[0],
                "crs": src.crs.to_string() if src.crs else None}
        if src.crs is None or src.transform.is_identity:
            return {**info, "status": "not_georeferenced", "zones": []}
        west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
        resolution = abs(src.res[0]) * (111_320 if src.crs.is_geographic else 1)
    footprint = box(west, south, east, north)
    return {**info, "status": "georeferenced", "resolution_m": round(resolution, 2),
            "footprint": mapping(footprint), "bbox": [west, south, east, north],
            "area_km2": round(transform(TO_EQUAL_AREA.transform, footprint).area / 1_000_000, 3),
            "zones": zone_coverage(footprint, zones)}


def scene_zones(scene_id: str) -> list[dict]:
    from .analysis import analyze

    result = analyze(None, 0, "risk", 12, None, scene_id=scene_id)
    return [{"id": zone["id"], "geometry": zone["geometry"]} for zone in result["zones"]]


class DeliveryRequest(BaseModel):
    scene_id: str
    provider_id: str
    acquired_at: date | None = None
    note: str = Field(default="", max_length=500)


@router.get("/providers")
def list_providers() -> dict:
    return providers()


@router.post("/deliveries", status_code=201)
def create_delivery(request: DeliveryRequest) -> dict:
    if request.scene_id not in known_scene_ids():
        raise HTTPException(404, "Сцена не найдена.")
    known = {row["id"]: row for row in providers()["providers"]}
    if request.provider_id not in known and request.provider_id != "other":
        raise HTTPException(422, "Неизвестный поставщик.")
    record = {"id": uuid.uuid4().hex, "scene_id": request.scene_id, "provider_id": request.provider_id,
              "provider_name": known.get(request.provider_id, {}).get("name", "Другой поставщик"),
              "acquired_at": request.acquired_at.isoformat() if request.acquired_at else None,
              "note": request.note, "created_at": _now(), "files": [],
              "rule": "files the operator received after buying outside the service; coverage = share of each "
                      "order zone inside the raster footprint (bounding box in EPSG:4326, areas in EPSG:6933)"}
    return _save(record)


@router.post("/deliveries/{delivery_id}/files")
async def upload_file(delivery_id: str, request: Request, path: str) -> dict:
    """One file of the chosen folder, streamed to disk; rasters are described at once."""
    record = _load(delivery_id)
    if len(record["files"]) >= MAX_FILES:
        raise HTTPException(413, f"В одной поставке не больше {MAX_FILES} файлов.")
    relative = safe_relative(path)
    target = STORE / record["scene_id"] / delivery_id / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with target.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise HTTPException(413, "Файл больше 2 ГБ.")
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    entry = {"path": relative.as_posix(), "bytes": size, "kind": "other"}
    if relative.suffix.lower() in RASTER_SUFFIXES:
        try:
            entry.update(kind="raster", **describe_raster(target, record["plan_zones"] if "plan_zones" in record else scene_zones(record["scene_id"])))
        except rasterio.errors.RasterioIOError:
            entry.update(kind="raster", status="unreadable", zones=[])
    record["files"] = [row for row in record["files"] if row["path"] != entry["path"]] + [entry]
    _save(record)
    return entry


@router.get("/deliveries")
def list_deliveries(scene_id: str) -> dict:
    with _db() as db:
        rows = db.execute("SELECT payload FROM deliveries WHERE scene_id=?", (scene_id,)).fetchall()
    records = sorted((json.loads(row[0]) for row in rows), key=lambda r: r["created_at"], reverse=True)
    covered = sorted({zone["zone_id"] for record in records for entry in record["files"]
                      for zone in entry.get("zones", []) if zone["share"] >= 0.95})
    return {"scene_id": scene_id, "deliveries": records, "zones_covered": covered}
