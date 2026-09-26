"""Small OSM map extracts, complete footprints, immutable source snapshots."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

import numpy as np
from pyproj import Geod
from shapely import get_coordinates, STRtree
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

from .building_rates import classify, RATES

ROOT = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ.get("FLOODVALUE_BUILDINGS_DIR", ROOT / "data/buildings-cache"))
GEOD = Geod(ellps="WGS84")
MAX_BYTES = 12_000_000
MAX_BUILDINGS = 5000


def area_m2(geometry) -> float:
    polygons = geometry.geoms if geometry.geom_type == "MultiPolygon" else [geometry]
    return sum(abs(GEOD.geometry_area_perimeter(orient(p, sign=1))[0]) for p in polygons)


def normalize(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise ValueError("Нужен GeoJSON FeatureCollection с контурами зданий")
    if len(data["features"]) > MAX_BUILDINGS:
        raise ValueError(f"Не больше {MAX_BUILDINGS} зданий за расчёт; уменьшите область")
    result, seen, geometries, vertices = [], set(), set(), 0
    for index, feature in enumerate(data["features"]):
        try:
            geometry = shape(feature["geometry"])
            coordinates = get_coordinates(geometry)
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            raise ValueError(f"Некорректная геометрия здания {index + 1}") from error
        vertices += len(coordinates)
        if (geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid
                or len(coordinates) > 10000 or vertices > 250000 or not np.isfinite(coordinates).all()
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
            "source_url": source_url, "source_updated_at": str(props.get("osm_updated_at", ""))[:100],
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
    nodes = {n.attrib["id"]: (float(n.attrib["lon"]), float(n.attrib["lat"])) for n in root.findall("node")}
    ways = {w.attrib["id"]: w for w in root.findall("way")}
    features, consumed = [], set()

    def tags(element):
        return {tag.attrib["k"]: tag.attrib["v"] for tag in element.findall("tag")}

    def points(way):
        refs = [n.attrib["ref"] for n in way.findall("nd")]
        return [nodes[r] for r in refs] if all(r in nodes for r in refs) else []

    def add(element, kind, geometry, values):
        if not geometry.is_empty and geometry.is_valid and geometry.geom_type in {"Polygon", "MultiPolygon"}:
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
            continue
        rings = {"outer": [], "inner": []}
        for member in members:
            pts = points(ways[member.attrib["ref"]])
            role = member.attrib.get("role") or "outer"
            if len(pts) >= 2 and role in rings:
                rings[role].append(LineString(pts))
        outer = list(polygonize(rings["outer"]))
        if outer:
            geometry = unary_union(outer).difference(unary_union(list(polygonize(rings["inner"]))))
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
    return normalize({"type": "FeatureCollection", "features": features, "source": source})


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


def load_osm(bounds: list[float]) -> dict:
    if area_m2(box(*bounds)) > 60_000_000:
        raise ValueError("Для OSM выберите область до 60 км² или загрузите свой GeoJSON")
    key = hashlib.sha256(json.dumps(bounds).encode()).hexdigest()[:24]
    cached = CACHE / f"osm-use-v2-{key}.json"
    if cached.exists():
        return json.loads(cached.read_text())
    url = "https://api.openstreetmap.org/api/0.6/map?bbox=" + ",".join(map(str, bounds))
    try:
        with urlopen(Request(url, headers={"User-Agent": "FloodValue/0.2 building-research"}), timeout=40) as response:
            content = response.read(MAX_BYTES + 1)
    except Exception as error:
        raise ValueError("OSM сейчас не отвечает. Повторите или загрузите GeoJSON; сохранённый пример Пайпорты доступен без OSM") from error
    if len(content) > MAX_BYTES:
        raise ValueError("Слишком много данных OSM; уменьшите область")
    data = parse_osm(content, {"name": "OpenStreetMap", "url": url, "license": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors", "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "temporal_scope": "Текущие контуры; существование и назначение на дату снимка не проверены",
        "scope": "Полные доступные контуры OSM в области запроса; неполные отношения пропущены"})
    # A bbox response includes incidental objects; retain only intersecting buildings.
    region = box(*bounds)
    data["features"] = [f for f in data["features"] if shape(f["geometry"]).intersects(region)]
    save(data)
    with tempfile.NamedTemporaryFile(dir=CACHE, mode="w", delete=False) as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(handle.name, cached)
    return data
