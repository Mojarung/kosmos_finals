"""Operator projects: sourced asset inventory and immutable decision snapshots."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .analysis import inspect_pixel
from .catalog import known_scene_ids, metadata

DB = Path(os.environ.get("FLOODVALUE_PROJECT_DB", Path(__file__).resolve().parents[2] / "data" / "projects" / "projects.sqlite3"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, scene_id TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS assets (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS decisions (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL, payload TEXT NOT NULL);
    """)
    return db


def _project(db: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if project is None:
        raise KeyError("Project not found")
    return project


def create(name: str, scene_id: str) -> dict:
    if scene_id not in known_scene_ids():
        raise ValueError("Unknown scene")
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise ValueError("Project name must contain 1–100 characters")
    project = {"id": uuid.uuid4().hex[:12], "name": name, "scene_id": scene_id,
               "created_at": _now(), "updated_at": _now()}
    with _connect() as db:
        db.execute("INSERT INTO projects VALUES (:id,:name,:scene_id,:created_at,:updated_at)", project)
    return {**project, "asset_count": 0, "decision_count": 0}


def list_projects() -> list[dict]:
    with _connect() as db:
        return [dict(row) for row in db.execute("""
          SELECT p.*, (SELECT count(*) FROM assets a WHERE a.project_id=p.id) asset_count,
          (SELECT count(*) FROM decisions d WHERE d.project_id=p.id) decision_count
          FROM projects p ORDER BY p.updated_at DESC""")]


def get(project_id: str) -> dict:
    with _connect() as db:
        project = dict(_project(db, project_id))
        assets = [json.loads(row["payload"]) for row in db.execute(
            "SELECT payload FROM assets WHERE project_id=? ORDER BY id", (project_id,))]
        decisions = [{"id": row["id"], "created_at": row["created_at"], **json.loads(row["payload"])}
                     for row in db.execute("SELECT * FROM decisions WHERE project_id=? ORDER BY created_at DESC",
                                           (project_id,))]
    return {**project, "assets": assets, "decisions": decisions}


def _rows(content: str, fmt: str) -> list[dict]:
    if len(content.encode("utf-8")) > 2_000_000:
        raise ValueError("Asset file exceeds 2 MB")
    if fmt == "csv":
        return list(csv.DictReader(io.StringIO(content)))
    if fmt == "geojson":
        data = json.loads(content)
        if data.get("type") != "FeatureCollection":
            raise ValueError("Expected GeoJSON FeatureCollection")
        rows = []
        for feature in data.get("features", []):
            geometry = feature.get("geometry") or {}
            if geometry.get("type") != "Point" or len(geometry.get("coordinates", [])) < 2:
                raise ValueError("Assets must be GeoJSON Points")
            lon, lat = geometry["coordinates"][:2]
            rows.append({**(feature.get("properties") or {}), "longitude": lon, "latitude": lat})
        return rows
    raise ValueError("Use csv or geojson")


def import_assets(project_id: str, content: str, fmt: str) -> dict:
    rows = _rows(content, fmt)
    if not 1 <= len(rows) <= 500:
        raise ValueError("Import 1–500 assets")
    with _connect() as db:
        project = _project(db, project_id)
        scene_id = project["scene_id"]
        prepared = []
        for number, row in enumerate(rows, 1):
            try:
                lon = float(row.get("longitude", row.get("lon")))
                lat = float(row.get("latitude", row.get("lat")))
                value = float(row["value_rub"])
                q = float(row["vulnerability"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"Row {number}: longitude, latitude, value_rub and vulnerability are required") from error
            name = str(row.get("name") or "").strip()
            source = str(row.get("value_source") or "").strip()
            if not name or not source or not (-180 <= lon <= 180 and -85 <= lat <= 85):
                raise ValueError(f"Row {number}: name, value_source and valid coordinates are required")
            if not (0 < value <= 1e13 and 0 <= q <= 1):
                raise ValueError(f"Row {number}: value_rub must be positive and vulnerability 0–1")
            reading = inspect_pixel(lon, lat, scene_id)
            p = reading.get("p_flood") if reading["status"] == "ok" else None
            prepared.append({"id": uuid.uuid4().hex[:12], "name": name[:120],
                             "asset_class": str(row.get("asset_class") or "operator_asset")[:80],
                             "longitude": lon, "latitude": lat, "value_rub": round(value, 2),
                             "vulnerability": q, "value_source": source[:300],
                             "source_date": str(row.get("source_date") or "")[:30],
                             "p_water": p, "expected_loss_rub": round(p * value * q, 2) if p is not None else None,
                             "observation_status": reading["status"],
                             "observed_at": metadata(scene_id)["observed_at"]})
        # Replace as one transaction so malformed imports cannot erase the previous inventory.
        db.execute("DELETE FROM assets WHERE project_id=?", (project_id,))
        db.executemany("INSERT INTO assets VALUES (?,?,?)",
                       [(asset["id"], project_id, json.dumps(asset, ensure_ascii=False)) for asset in prepared])
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (_now(), project_id))
    return {"count": len(prepared), "estimated": sum(a["expected_loss_rub"] is not None for a in prepared),
            "expected_loss_rub": round(sum(a["expected_loss_rub"] or 0 for a in prepared), 2)}


def save_decision(project_id: str, analysis: dict) -> dict:
    with _connect() as db:
        project = _project(db, project_id)
        if analysis["chip"]["chip_id"] != project["scene_id"]:
            raise ValueError("Project scene differs from analysis scene")
        assets = [json.loads(row["payload"]) for row in db.execute(
            "SELECT payload FROM assets WHERE project_id=?", (project_id,))]
        snapshot = {"scene_id": project["scene_id"], "observed_at": analysis["chip"]["observed_at"],
                    "inputs": analysis["inputs"], "metrics": analysis["metrics"],
                    "strategies": analysis["strategies"], "asset_count": len(assets),
                    "operator_expected_loss_rub": round(sum(a["expected_loss_rub"] or 0 for a in assets), 2),
                    "operator_unestimated_count": sum(a["expected_loss_rub"] is None for a in assets),
                    "asset_snapshot": assets}
        decision = {"id": uuid.uuid4().hex[:12], "created_at": _now(), **snapshot}
        db.execute("INSERT INTO decisions VALUES (?,?,?,?)", (decision["id"], project_id,
                                                           decision["created_at"], json.dumps(snapshot, ensure_ascii=False)))
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (_now(), project_id))
    return decision
