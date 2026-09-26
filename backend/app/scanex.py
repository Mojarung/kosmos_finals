"""SCANEX «Космоснимки — Поиск» as a live catalog that needs no account.

search.kosmosnimki.ru answers anonymous scene searches through its GeoMixer backend
(VectorLayer/Search.ashx on the scene layer of the public search page): date, cloudiness,
tilt, corner coordinates, platform and a preview. This is the interface of the public site,
not a documented API, so it can change without notice; the offer says so. Imagery is ordered
from SCANEX by request; the price shown is the case's PP 840 estimate, Landsat is free.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException
from shapely.geometry import Polygon, mapping

from .catalog_offers import make_offer

SEARCH = "https://search.kosmosnimki.ru/gis/VectorLayer/Search.ashx"
LAYER = "9B4733A8CBE942CE9F5E70DCAA6C1FBE"
ORDER_URL = "https://search.kosmosnimki.ru/"
# Nominal best (panchromatic) resolution of each platform, metres; unknown platforms stay empty.
RESOLUTION = {
    "LANDSAT_8": 15, "LANDSAT_9": 15, "SPOT6": 1.5, "SPOT7": 1.5, "PHR1A": 0.5, "PHR1B": 0.5,
    "WV01": 0.5, "WV02": 0.46, "WV03": 0.31, "WV04": 0.31, "GE01": 0.41, "GF1": 2, "GF2": 0.8, "GF3": 1,
    "ZY3": 2.1, "KOMPSAT3": 0.7, "KOMPSAT3A": 0.55, "Аист-2Д": 1.5,
}
PREFIXES = {"Канопус-В": 2.5, "Ресурс-П": 0.7, "TripleSat": 0.8, "GJ1": 0.5}
SAR = {"GF3"}
FREE = {"LANDSAT_8", "LANDSAT_9"}


def resolution(platform: str) -> float | None:
    if platform in RESOLUTION:
        return float(RESOLUTION[platform])
    return next((float(v) for prefix, v in PREFIXES.items() if platform.startswith(prefix)), None)


def search(aoi, start: date, end: date, limit: int = 300) -> list[dict]:
    geometry = json.dumps(mapping(aoi))
    query = (f"intersects([geomixergeojson], GeometryFromGeoJson('{geometry}', 4326)) "
             f"AND acqdate >= '{start}' AND acqdate <= '{end}'")
    form = {"layer": LAYER, "geometry": "false", "pagesize": limit, "page": 0, "count": "add",
            "orderby": "acqdate", "orderdirection": "desc", "query": query}
    request = Request(SEARCH, data=urlencode(form).encode(), method="POST",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise HTTPException(502, f"Каталог СКАНЭКС отклонил запрос (HTTP {error.code}).") from None
    except (URLError, TimeoutError, ValueError):
        raise HTTPException(502, "Каталог СКАНЭКС временно не отвечает. Повторите обновление каталога.") from None
    if payload.get("Status") != "ok":
        raise HTTPException(502, "Каталог СКАНЭКС не вернул список снимков.")
    fields = payload["Result"]["fields"]
    return [dict(zip(fields, values)) for values in payload["Result"]["values"]]


def normalize(row: dict, aoi, event_date: str | None, on: date) -> dict | None:
    platform = row.get("platform") or "СКАНЭКС"
    corners = [(row[f"x{i}"], row[f"y{i}"]) for i in range(1, 5)]
    captured = datetime.fromtimestamp(row["acqdate"], timezone.utc).date().isoformat()
    free = platform in FREE
    return make_offer(provider="СКАНЭКС", identifier=str(row["sceneid"]), title=platform,
                      sensor="sar" if platform in SAR else "optical", footprint=Polygon(corners), aoi=aoi,
                      resolution_m=resolution(platform), captured=captured, timestamp=captured,
                      cloud=row.get("cloudness"), preview=row.get("url"), event_date=event_date, on=on,
                      order_url=ORDER_URL, free=free,
                      price_note=None if free else "цена поставщика по заявке в СКАНЭКС",
                      extra={"off_nadir_angle": row.get("tilt"), "platform_identifier": platform,
                             "resolution_status": "nominal_by_platform"})
