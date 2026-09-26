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
from .costs import decision_costs
from .planning import BasketSpace, candidates

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
        CREATE TABLE IF NOT EXISTS operator_plans (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS building_plans (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL, payload TEXT NOT NULL, archive BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS imagery_requests (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL, payload TEXT NOT NULL);
    """)
    return db


def _project(db: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if project is None:
        raise KeyError("Проект не найден.")
    return project


def create(name: str, scene_id: str) -> dict:
    if scene_id not in known_scene_ids():
        raise ValueError("Сцена не найдена.")
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise ValueError("Название проекта: от 1 до 100 символов.")
    project = {"id": uuid.uuid4().hex[:12], "name": name, "scene_id": scene_id,
               "created_at": _now(), "updated_at": _now()}
    with _connect() as db:
        db.execute("INSERT INTO projects VALUES (:id,:name,:scene_id,:created_at,:updated_at)", project)
    return {**project, "asset_count": 0, "decision_count": 0}


def list_projects() -> list[dict]:
    with _connect() as db:
        return [dict(row) for row in db.execute("""
          SELECT p.*, (SELECT count(*) FROM assets a WHERE a.project_id=p.id) asset_count,
          (SELECT count(*) FROM decisions d WHERE d.project_id=p.id) decision_count,
          (SELECT count(*) FROM operator_plans op WHERE op.project_id=p.id) operator_plan_count,
          (SELECT count(*) FROM imagery_requests ir WHERE ir.project_id=p.id) imagery_request_count
          , (SELECT count(*) FROM building_plans bp WHERE bp.project_id=p.id) building_plan_count
          FROM projects p ORDER BY p.updated_at DESC""")]


def get(project_id: str) -> dict:
    with _connect() as db:
        project = dict(_project(db, project_id))
        assets = [json.loads(row["payload"]) for row in db.execute(
            "SELECT payload FROM assets WHERE project_id=? ORDER BY id", (project_id,))]
        decisions = [{"id": row["id"], "created_at": row["created_at"], **json.loads(row["payload"])}
                     for row in db.execute("SELECT * FROM decisions WHERE project_id=? ORDER BY created_at DESC",
                                           (project_id,))]
        operator_plans = [{"id": row["id"], "created_at": row["created_at"], **json.loads(row["payload"])}
                          for row in db.execute("SELECT * FROM operator_plans WHERE project_id=? "
                                                "ORDER BY created_at DESC LIMIT 50", (project_id,))]
        building_plans = [{"id": row["id"], "created_at": row["created_at"], **json.loads(row["payload"])}
                          for row in db.execute("SELECT id, created_at, payload FROM building_plans WHERE project_id=? ORDER BY created_at DESC LIMIT 50", (project_id,))]
        imagery_requests = []
        for row in db.execute("SELECT * FROM imagery_requests WHERE project_id=? ORDER BY created_at DESC LIMIT 50", (project_id,)):
            payload = json.loads(row["payload"])
            imagery_requests.append({"id": row["id"], "created_at": row["created_at"],
                                     **{key: payload.get(key) for key in ("status", "catalog_id", "catalog_captured_at",
                                         "currency", "cost", "price_verified", "purchase_submitted", "deadline_hours",
                                         "covered_expected_loss_rub", "covered_asset_count", "covered_el_percent",
                                         "selected_ids", "blockers")}})
    return {**project, "assets": assets, "decisions": decisions, "operator_plans": operator_plans,
            "imagery_requests": imagery_requests, "building_plans": building_plans}


def _rows(content: str, fmt: str) -> list[dict]:
    if len(content.encode("utf-8")) > 2_000_000:
        raise ValueError("Файл объектов больше 2 МБ.")
    if fmt == "csv":
        return list(csv.DictReader(io.StringIO(content)))
    if fmt == "geojson":
        data = json.loads(content)
        if data.get("type") != "FeatureCollection":
            raise ValueError("Нужен GeoJSON FeatureCollection.")
        rows = []
        for feature in data.get("features", []):
            geometry = feature.get("geometry") or {}
            if geometry.get("type") != "Point" or len(geometry.get("coordinates", [])) < 2:
                raise ValueError("Объекты должны быть точками GeoJSON (Point).")
            lon, lat = geometry["coordinates"][:2]
            rows.append({**(feature.get("properties") or {}), "longitude": lon, "latitude": lat})
        return rows
    raise ValueError("Формат файла: csv или geojson.")


def import_assets(project_id: str, content: str, fmt: str) -> dict:
    rows = _rows(content, fmt)
    if not 1 <= len(rows) <= 500:
        raise ValueError("Импортируйте от 1 до 500 объектов.")
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
                raise ValueError(f"Строка {number}: нужны longitude, latitude, value_rub и vulnerability.") from error
            name = str(row.get("name") or "").strip()
            source = str(row.get("value_source") or "").strip()
            if not name or not source or not (-180 <= lon <= 180 and -85 <= lat <= 85):
                raise ValueError(f"Строка {number}: нужны name, value_source и корректные координаты.")
            if not (0 < value <= 1e13 and 0 <= q <= 1):
                raise ValueError(f"Строка {number}: value_rub больше нуля, vulnerability от 0 до 1.")
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
            raise ValueError("Проект создан для другой сцены, чем текущий расчёт.")
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


def operator_plan(project_id: str, budget_rub: float, deadline_hours: int) -> dict:
    """Separate A/B/C on imported assets; the case's seeded ten remain untouched."""
    with _connect() as db:
        project = dict(_project(db, project_id))
        assets = [json.loads(row["payload"]) for row in db.execute(
            "SELECT payload FROM assets WHERE project_id=? ORDER BY id", (project_id,))]
    if not assets:
        raise ValueError("Сначала импортируйте объекты проекта.")
    estimated = [asset for asset in assets if asset["expected_loss_rub"] is not None]
    if not estimated:
        raise ValueError("Ни один объект не попал в покрытие снимка; план нельзя оценить.")
    if not 0 <= budget_rub <= 1_000_000 or not 1 <= deadline_hours <= 48:
        raise ValueError("Бюджет должен быть 0–1 000 000 ₽, срок — 1–48 часов.")
    info = metadata(project["scene_id"])
    # The exact basket search supports at most 16 zones. Ten highest-EL assets
    # seed candidate footprints; every imported, observed asset is still scored
    # for coverage and can benefit from a zone around another asset.
    anchors = sorted(estimated, key=lambda asset: (-(asset["expected_loss_rub"] or 0), asset["id"]))[:10]
    zones = candidates(anchors, info["bbox"], info["bbox"], [], info["observed_at"])
    if len({zone["id"] for zone in zones}) != len(zones):
        raise ValueError("Не удалось построить уникальные зоны; повторите импорт.")
    space = BasketSpace(zones, estimated)
    broad = space.summary(space.index(["ZALL"]), deadline_hours, strategy="B")
    broad_timely = space.chosen(deadline_hours)[-1]["delivery_hours"] <= deadline_hours
    chosen = space.summary(space.best(budget_rub, "risk", deadline_hours, None), deadline_hours)
    total = round(sum(asset["expected_loss_rub"] for asset in estimated), 2)

    def strategy(label: str, summary: dict, timely: bool) -> dict:
        covered = summary["covered_expected_loss_rub"] if timely else 0.0
        ids = summary["covered_asset_ids"] if timely else []
        price = summary["quote"]["total_rub"]
        return {"label": label, "zone_ids": summary["zone_ids"], "candidate_ids": summary["candidate_ids"],
                "covered_asset_ids": ids, "covered_expected_loss_rub": covered,
                "coverage_share": round(covered / total, 4) if total else None,
                "data_cost_rub": price, "budget_feasible": price <= budget_rub,
                "timely": timely, "quote": summary["quote"],
                **decision_costs(price, summary["quote"]["lines"])}

    empty = space.summary(space.index([]), deadline_hours, strategy="A")
    return {
        "scope": "imported_operator_assets_separate_from_fixed_case_portfolio",
        "project_id": project_id, "project_name": project["name"], "scene_id": project["scene_id"],
        "observed_at": info["observed_at"], "inputs": {"budget_rub": budget_rub, "deadline_hours": deadline_hours},
        "asset_count": len(assets), "estimated_asset_count": len(estimated),
        "unestimated_asset_count": len(assets) - len(estimated), "expected_loss_rub": total,
        "anchor_count": len(anchors),
        "anchor_rule": "Top 10 observed imported assets by p×V×q seed zones; all observed assets count in coverage",
        "price_status": "PP_RF_840_scenario_not_supplier_quote",
        "delivery_status": "scenario_not_confirmed_by_provider",
        "interpretation": "Covered expected loss is a priority for a new observation, not avoided damage.",
        "asset_snapshot": assets,
        "zones": [{key: value for key, value in zone.items() if key != "products"} for zone in zones],
        "strategies": {"A": strategy("Без платной съёмки", empty, True),
                       "B": strategy("Широкая съёмка", broad, broad_timely),
                       "C": strategy("Выборочная съёмка", chosen, chosen["feasible"])},
    }


def save_operator_plan(project_id: str, budget_rub: float, deadline_hours: int) -> dict:
    plan = operator_plan(project_id, budget_rub, deadline_hours)
    record = {"id": uuid.uuid4().hex[:12], "created_at": _now(), **plan}
    with _connect() as db:
        _project(db, project_id)
        db.execute("INSERT INTO operator_plans VALUES (?,?,?,?)",
                   (record["id"], project_id, record["created_at"], json.dumps(plan, ensure_ascii=False)))
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (_now(), project_id))
    return record


def save_building_plan(project_id, request):
    from . import building_plans
    with _connect() as db:
        project = _project(db, project_id)
        if project['scene_id'] != request.buildings.scene_id:
            raise ValueError("Проект относится к другому снимку")
    result = building_plans.calculate(request)
    archive = building_plans.export_bundle(request.buildings, result, 'scenario').body
    record = {'id': uuid.uuid4().hex[:12], 'created_at': _now()}
    payload = {'count': result['scope']['count'], 'expected_loss_rub': result['scope']['total_expected_loss_rub'],
               'budget_rub': request.budget_rub, 'deadline_hours': request.deadline_hours,
               'data_cost_rub': result['strategies'][request.strategy]['data_cost_rub'], 'strategy': request.strategy,
               'covered_count': len(result['strategies'][request.strategy]['covered_building_ids'])}
    with _connect() as db:
        _project(db, project_id)
        db.execute('INSERT INTO building_plans VALUES (?,?,?,?,?)',
                   (record['id'], project_id, record['created_at'], json.dumps(payload, ensure_ascii=False), archive))
        db.execute('UPDATE projects SET updated_at=? WHERE id=?', (_now(), project_id))
    return {**record, **payload}


def building_plan_archive(project_id, plan_id):
    with _connect() as db:
        _project(db, project_id)
        row = db.execute('SELECT archive FROM building_plans WHERE id=? AND project_id=?', (plan_id, project_id)).fetchone()
        if row is None:
            raise KeyError('Building plan not found')
        return row['archive']
