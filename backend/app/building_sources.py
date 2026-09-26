"""Small OSM map extracts, complete footprints, immutable source snapshots."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, BoundedSemaphore
from urllib.error import HTTPError, URLError
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

import numpy as np
from pyproj import Geod
from shapely import get_coordinates, STRtree
from shapely.errors import GEOSException
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

from .building_rates import classify, RATES
from .planning import MAX_REGION_AREA_KM2, rectangle_area_km2

ROOT = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ.get("FLOODVALUE_BUILDINGS_DIR", ROOT / "data/buildings-cache"))
GEOD = Geod(ellps="WGS84")
MAX_BYTES = 12_000_000
MAX_BUILDINGS = 5000
# Remote OSM XML is much more verbose than uploaded GeoJSON.
MAX_OSM_BYTES = 48_000_000
MAX_OSM_BUILDINGS = 200_000
MAX_OSM_VERTICES = 8_000_000
OSM_LOAD_SECONDS = 900
OSM_TILE_AREA_M2 = 4_000_000
_LOAD_LOCK = Lock()
_AREA_LOCKS = {}
_DOWNLOAD_LIMIT = BoundedSemaphore(2)


class OSMUnavailable(ValueError):
    pass


class OSMDataError(ValueError):
    """A local validation failure must never be disguised as source downtime."""
    pass


class OSMSplitRequired(ValueError):
    pass


def area_m2(geometry) -> float:
    polygons = geometry.geoms if geometry.geom_type == "MultiPolygon" else [geometry]
    return sum(abs(GEOD.geometry_area_perimeter(orient(p, sign=1))[0]) for p in polygons)


def normalize(data: dict, *, max_buildings: int = MAX_BUILDINGS, max_vertices: int = 250000) -> dict:
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise ValueError("Нужен GeoJSON FeatureCollection с контурами зданий")
    if len(data["features"]) > max_buildings:
        raise ValueError(f"Не больше {max_buildings} зданий за расчёт; уменьшите область")
    result, seen, geometries, vertices = [], set(), set(), 0
    for index, feature in enumerate(data["features"]):
        try:
            geometry = shape(feature["geometry"])
            coordinates = get_coordinates(geometry)
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            raise ValueError(f"Некорректная геометрия здания {index + 1}") from error
        vertices += len(coordinates)
        if (geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid
                or len(coordinates) > 10000 or vertices > max_vertices or not np.isfinite(coordinates).all()
                or np.any(np.abs(coordinates[:, 0]) > 180) or np.any(np.abs(coordinates[:, 1]) > 85)):
            raise ValueError(f"Здание {index + 1}: нужен корректный Polygon/MultiPolygon в WGS84")
        if not 1 <= area_m2(geometry) <= 2_000_000:
            raise ValueError(f"Здание {index + 1}: площадь должна быть от 1 до 2 000 000 м²")
        props = feature.get("properties") or {}
        if not isinstance(props, dict):
            raise ValueError("properties здания должны быть объектом")
        identifier = str(feature.get("id") or props.get("id") or f"import/{index + 1}")[:150]
        digest = geometry.normalize().wkb_hex
        if identifier in seen:
            raise ValueError(f"Повторяется ID здания: {identifier}")
        seen.add(identifier)
        if digest in geometries:
            continue
        geometries.add(digest)
        raw_tags = props.get("tags", props)
        if not isinstance(raw_tags, dict):
            raise ValueError("tags здания должны быть объектом")
        tags = {str(k)[:100]: str(v)[:500] for k, v in raw_tags.items() if isinstance(v, (str, int, float))}
        source_url = str(props.get("source_url", ""))
        if not source_url.startswith("https://www.openstreetmap.org/"):
            source_url = ""
        result.append({"type": "Feature", "id": identifier, "geometry": mapping(geometry), "properties": {
            "id": identifier, "tags": tags, "name": tags.get("name", identifier),
            "source_url": source_url, "source_updated_at": str(props.get("osm_updated_at", props.get("source_updated_at", "")))[:100],
            "contained_uses": [{"id": str(use.get("id", ""))[:100], "kind": use["kind"], "evidence": str(use.get("evidence", ""))[:500]}
                               for use in (props.get("contained_uses") or []) if isinstance(use, dict) and use.get("kind") in RATES][:50],
        }})
    return {"type": "FeatureCollection", "features": result, "source": data.get("source", {})}


def parse_osm(content: bytes, source: dict) -> dict:
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("Неподдерживаемый XML")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError("OSM вернул некорректные данные; повторите загрузку") from error
    if root.find("remark") is not None:
        raise ValueError("Сервер OSM не завершил выборку зданий")
    nodes = {n.attrib["id"]: (float(n.attrib["lon"]), float(n.attrib["lat"])) for n in root.findall("node")}
    ways = {w.attrib["id"]: w for w in root.findall("way")}
    features, consumed, rejected = [], set(), {}

    def tags(element):
        return {tag.attrib["k"]: tag.attrib["v"] for tag in element.findall("tag")}

    def points(way):
        refs = [n.attrib["ref"] for n in way.findall("nd")]
        return [nodes[r] for r in refs] if all(r in nodes for r in refs) else []

    def reject(element, kind, reason):
        identifier = f"{kind}/{element.attrib['id']}"
        rejected[identifier] = {"id": identifier, "reason": reason}

    def add(element, kind, geometry, values):
        coordinates = get_coordinates(geometry)
        if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            reject(element, kind, "invalid_geometry")
            return
        if len(coordinates) > 10000 or not np.isfinite(coordinates).all():
            reject(element, kind, "unsupported_coordinates")
            return
        area = area_m2(geometry)
        if not 1 <= area <= 2_000_000:
            reject(element, kind, "area_out_of_range")
            return
        identifier = element.attrib["id"]
        features.append({"type": "Feature", "id": f"{kind}/{identifier}", "geometry": mapping(geometry),
                         "properties": {"tags": values, "source_url": f"https://www.openstreetmap.org/{kind}/{identifier}",
                                        "osm_updated_at": element.attrib.get("timestamp", "")}})

    for relation in root.findall("relation"):
        values = tags(relation)
        if values.get("type") != "multipolygon" or values.get("building", "no") in {"no", "construction", "ruins"}:
            continue
        members = [m for m in relation.findall("member") if m.attrib.get("type") == "way"]
        if not members or any(m.attrib["ref"] not in ways for m in members):
            reject(relation, "relation", "incomplete_relation")
            continue
        rings = {"outer": [], "inner": []}
        for member in members:
            pts = points(ways[member.attrib["ref"]])
            role = member.attrib.get("role") or "outer"
            if len(pts) >= 2 and role in rings:
                rings[role].append(LineString(pts))
        outer = list(polygonize(rings["outer"]))
        if outer:
            try:
                geometry = unary_union(outer).difference(unary_union(list(polygonize(rings["inner"]))))
            except GEOSException:
                reject(relation, "relation", "invalid_geometry")
                continue
            before = len(features)
            add(relation, "relation", geometry, values)
            if len(features) > before:
                consumed.update(m.attrib["ref"] for m in members)
    for identifier, way in ways.items():
        values = tags(way)
        if identifier in consumed or values.get("building", "no") in {"no", "construction", "ruins"}:
            continue
        pts = points(way)
        if len(pts) >= 4 and pts[0] == pts[-1]:
            add(way, "way", Polygon(pts), values)
        else:
            reject(way, "way", "incomplete_geometry")
    # A tagged POI inside a footprint can establish use when building=yes says nothing.
    # Conflicting POI categories remain unknown; neighbouring POIs are never borrowed.
    pois = []
    for node in root.findall("node"):
        kind, evidence = classify(tags(node))
        if kind != "unknown":
            pois.append((Point(nodes[node.attrib["id"]]), {"id": f"node/{node.attrib['id']}", "kind": kind, "evidence": evidence}))
    if pois:
        tree = STRtree([point for point, _ in pois])
        for feature in features:
            geometry = shape(feature["geometry"])
            feature["properties"]["contained_uses"] = [pois[int(i)][1] for i in tree.query(geometry, predicate="contains")]
    if source.get("bounds"):
        region = box(*source["bounds"])
        features = [f for f in features if shape(f["geometry"]).intersects(region)]
    # Members may reappear as standalone ways in a neighbouring tile.
    source = {**source, "relation_member_way_ids": sorted(consumed), "excluded_features": list(rejected.values()), "excluded_count": len(rejected)}
    try:
        return normalize({"type": "FeatureCollection", "features": features, "source": source},
                         max_buildings=MAX_OSM_BUILDINGS, max_vertices=MAX_OSM_VERTICES)
    except ValueError as error:
        raise OSMDataError(f"Не удалось обработать данные зданий: {error}") from error


def save(data: dict) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    identifier = hashlib.sha256(encoded).hexdigest()[:24]
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=CACHE, delete=False) as handle:
        handle.write(encoded)
    os.replace(handle.name, CACHE / f"{identifier}.json")
    return identifier


def read(identifier: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{24}", identifier):
        raise ValueError("Некорректный ID набора зданий")
    try:
        return json.loads((CACHE / f"{identifier}.json").read_text())
    except FileNotFoundError as error:
        raise ValueError("Набор зданий не найден. Загрузите контуры ещё раз") from error


def _cache_path(bounds, version="v3"):
    key = hashlib.sha256(json.dumps(bounds).encode()).hexdigest()[:24]
    return CACHE / f"osm-use-{version}-{key}.json"


def _cached(bounds):
    for version in ("v3", "v2"):
        path = _cache_path(bounds, version)
        if path.exists():
            return json.loads(path.read_text())
    return None


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OSMUnavailable("Загрузка зданий заняла слишком много времени. Уже полученные части сохранены — нажмите «Повторить загрузку», чтобы продолжить.")
    return remaining


def _quarters(bounds):
    w, s, e, n = bounds
    x, y = (w + e) / 2, (s + n) / 2
    return [[w, s, x, y], [x, s, e, y], [w, y, x, n], [x, y, e, n]]


def _initial_tiles(bounds):
    if area_m2(box(*bounds)) <= OSM_TILE_AREA_M2:
        return [bounds]
    w, s, e, n = bounds
    side = math.sqrt(OSM_TILE_AREA_M2)
    width = max(GEOD.inv(w, s, e, s)[2], GEOD.inv(w, n, e, n)[2])
    height = GEOD.inv(w, s, w, n)[2]
    columns, rows = max(1, math.ceil(width / side)), max(1, math.ceil(height / side))
    return [[w + (e-w)*x/columns, s + (n-s)*y/rows,
             e if x+1 == columns else w + (e-w)*(x+1)/columns,
             n if y+1 == rows else s + (n-s)*(y+1)/rows]
            for y in range(rows) for x in range(columns)]


def _download(request, timeout, deadline):
    if not _DOWNLOAD_LIMIT.acquire(timeout=_remaining(deadline)):
        raise OSMUnavailable("Источники зданий заняты. Повторите загрузку.")
    try:
        return _download_response(request, timeout, deadline)
    finally:
        _DOWNLOAD_LIMIT.release()


def _download_response(request, timeout, deadline):
    with urlopen(request, timeout=min(timeout, _remaining(deadline))) as response:
        chunks, size = [], 0
        while True:
            _remaining(deadline)
            chunk = response.read(min(262144, MAX_OSM_BYTES + 1 - size))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_OSM_BYTES:
                raise OSMSplitRequired("Ответ OSM больше 48 МБ")


def _fetch_tile(bounds, deadline, can_split):
    url = "https://api.openstreetmap.org/api/0.6/map?bbox=" + ",".join(map(str, bounds))
    west, south, east, north = bounds
    bbox = f"{south},{west},{north},{east}"
    query = f'[out:xml][timeout:20];(way["building"]({bbox});relation["building"]["type"="multipolygon"]({bbox}););(._;>;);out body;'
    headers = {"User-Agent": "FloodValue/0.2 building-research"}
    requests = [Request(url, headers=headers)] + [
        Request(endpoint, data=urlencode({"data": query}).encode(), headers=headers)
        for endpoint in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
    ]
    for index, request in enumerate(requests):
        try:
            content = _download(request, 8 if index == 0 else 25, deadline)
            data = parse_osm(content, osm_source(request.full_url, bounds, "map" if index == 0 else "overpass"))
            return cache_extract(data, bounds)
        except (OSMUnavailable, OSMDataError):
            raise
        except (HTTPError, URLError, OSError, ValueError) as error:
            logging.getLogger(__name__).warning("Building tile %s from %s failed: %s", bounds, request.full_url.split('?')[0], error)
            # A dense map request will exceed the node limit on every retry.
            # Split immediately instead of waiting through both full-area fallbacks.
            if can_split and (isinstance(error, OSMSplitRequired) or isinstance(error, HTTPError) and error.code == 400):
                raise OSMSplitRequired(str(error)) from error
    raise OSMUnavailable("Серверы OSM временно не отдают часть зданий. Полученные части сохранены — нажмите «Повторить загрузку», чтобы продолжить.")


def _merge_tiles(parts, bounds):
    members, by_id, rejected = set(), {}, {}
    for part in parts:
        rejected.update({row["id"]: row for row in part["source"].get("excluded_features", [])})
        members.update(part["source"].get("relation_member_way_ids", []))
        for feature in part["features"]:
            identifier = feature["id"]
            previous = by_id.get(identifier)
            if previous:
                # Both tiles contain the entire geometry. Prefer the newest version,
                # while combining POIs discovered on either side of the tile boundary.
                uses = {u["id"]: u for row in (previous, feature) for u in row["properties"].get("contained_uses", [])}
                if previous["properties"].get("source_updated_at", "") > feature["properties"].get("source_updated_at", ""):
                    feature = previous
                feature = {**feature, "properties": {**feature["properties"], "contained_uses": list(uses.values())}}
            by_id[identifier] = feature
    features = [by_id[key] for key in sorted(by_id) if not (key.startswith("way/") and key[4:] in members)]
    source = osm_source("https://www.openstreetmap.org", bounds, "tiled")
    rejected = {key: value for key, value in rejected.items() if key not in by_id}
    source.update(relation_member_way_ids=sorted(members), excluded_features=list(rejected.values()), excluded_count=len(rejected),
                  parts=[part["source"] for part in parts],
                  scope="Область загружена по частям; полные контуры объединены по ID, дубли и части OSM relations учтены один раз.")
    data = normalize({"type": "FeatureCollection", "features": features, "source": source},
                     max_buildings=MAX_OSM_BUILDINGS, max_vertices=MAX_OSM_VERTICES)
    return cache_extract(data, bounds)


def _load_tile(bounds, deadline, depth=0, progress=None):
    cached = _cached(bounds)
    if cached is not None:
        if progress:
            progress(bounds, len(cached["features"]))
        return cached
    _remaining(deadline)
    can_split = depth < 3 and area_m2(box(*bounds)) > 125000
    try:
        data = _fetch_tile(bounds, deadline, can_split)
        if progress:
            progress(bounds, len(data["features"]))
        return data
    except OSMSplitRequired:
        if not can_split:
            raise
        _remaining(deadline)
        return _merge_tiles([_load_tile(child, deadline, depth + 1, progress) for child in _quarters(bounds)], bounds)


def validate_area(bounds):
    if rectangle_area_km2(bounds) > MAX_REGION_AREA_KM2:
        raise ValueError("Область больше 300 км² — такой же предел, как для анализа снимка")


def load_osm(bounds: list[float], progress=None, deadline=None) -> dict:
    validate_area(bounds)
    cached = _cached(bounds)
    if cached is not None:
        if progress:
            progress(bounds, len(cached["features"]))
        return cached
    with _LOAD_LOCK:
        area_lock = _AREA_LOCKS.setdefault(tuple(bounds), Lock())
    # Only identical regions wait for each other. Source requests across all
    # background jobs share the two-request semaphore.
    with area_lock:
        cached = _cached(bounds)
        if cached is not None:
            if progress:
                progress(bounds, len(cached["features"]))
            return cached
        deadline = deadline or time.monotonic() + OSM_LOAD_SECONDS
        tiles = _initial_tiles(bounds)
        if len(tiles) == 1:
            return _load_tile(bounds, deadline, progress=progress)
        with ThreadPoolExecutor(max_workers=2) as executor:
            parts = list(executor.map(lambda tile: _load_tile(tile, deadline, progress=progress), tiles))
        return _merge_tiles(parts, bounds)


def osm_source(url: str, bounds: list[float], provider: str) -> dict:
    return {"name": "OpenStreetMap", "url": url, "provider": provider, "bounds": bounds, "license": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors", "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "temporal_scope": "Текущие контуры; существование и назначение на дату снимка не проверены",
        "scope": "Полные доступные контуры OSM в области запроса; неполные отношения пропущены"}


def cache_extract(data: dict, bounds: list[float]) -> dict:
    # A bbox response includes incidental objects; retain only intersecting buildings.
    region = box(*bounds)
    data["features"] = [f for f in data["features"] if shape(f["geometry"]).intersects(region)]
    save(data)
    cached = _cache_path(bounds)
    with tempfile.NamedTemporaryFile(dir=CACHE, mode="w", delete=False) as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(handle.name, cached)
    return data
