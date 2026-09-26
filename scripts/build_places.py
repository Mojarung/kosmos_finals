"""Build frontend/src/data/places.json for the map search from Natural Earth (public domain).

    uv run scripts/build_places.py <ne_50m_admin_0_countries.geojson> <ne_10m_populated_places.geojson>

Both files come from https://github.com/nvkelso/natural-earth-vector/tree/master/geojson. A
country keeps the bounds of its big polygons (islands at least 20% of the largest one), so
France fits its mainland and Indonesia its main islands; a span over 180° falls back to the
largest polygon (Russia, Fiji). Rows: [name_ru, name_en, country_ru, lon, lat, population, bbox?].
"""
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "data" / "places.json"
MIN_POPULATION = 20_000


def ring_area(ring: list) -> float:
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:]))) / 2


def bounds(rings: list) -> list[float]:
    xs = [x for ring in rings for x, _ in ring]
    ys = [y for ring in rings for _, y in ring]
    return [min(xs), min(ys), max(xs), max(ys)]


def country_box(geometry: dict) -> list[float]:
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    outers = sorted((polygon[0] for polygon in polygons), key=ring_area, reverse=True)
    big = [ring for ring in outers if ring_area(ring) >= 0.2 * ring_area(outers[0])]
    box = bounds(big)
    if box[2] - box[0] > 180:
        box = bounds(outers[:1])
    return [round(v, 3) for v in box]


def main(countries_path: str, places_path: str) -> None:
    countries = json.loads(Path(countries_path).read_text(encoding="utf-8"))["features"]
    names = {}
    rows = []
    for feature in countries:
        p = feature["properties"]
        ru = p.get("NAME_RU") or p["NAME"]
        names[p["ADM0_A3"]] = ru
        box = country_box(feature["geometry"])
        rows.append([ru, p["NAME"], "", round((box[0] + box[2]) / 2, 3), round((box[1] + box[3]) / 2, 3),
                     int(p.get("POP_EST") or 0), box])
    places = json.loads(Path(places_path).read_text(encoding="utf-8"))["features"]
    for feature in places:
        p = feature["properties"]
        population = int(p.get("POP_MAX") or 0)
        if population < MIN_POPULATION and not p.get("NAME_RU"):
            continue
        rows.append([p.get("NAME_RU") or p["NAME"], p["NAME"], names.get(p["ADM0_A3"], p.get("ADM0NAME") or ""),
                     round(p["LONGITUDE"], 3), round(p["LATITUDE"], 3), population])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(countries)} countries, {len(rows) - len(countries)} cities, {OUT.stat().st_size / 1024:.0f} KB -> {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
