"""Free Sentinel-1 and Sentinel-2 from the Copernicus Data Space STAC-API, no key needed to search.

https://stac.dataspace.copernicus.eu/v1 lists Sentinel-1 GRD (radar, 10 m) and Sentinel-2 L2A
(optics, 10 m) with cloud cover and a thumbnail. Downloading a product needs a free Copernicus
account; the data are open, so the offer price is 0 (strategy A of the case).

Copernicus Data Space resets connections from some Russian data centres (a Timeweb server in
Moscow on 2026-09-26), so the same collections are then searched on Microsoft Planetary Computer.
"""
from __future__ import annotations

import json
from datetime import date
from urllib.request import Request, urlopen

from fastapi import HTTPException
from shapely.geometry import mapping, shape

from .catalog_offers import make_offer

SEARCH = "https://stac.dataspace.copernicus.eu/v1/search"
BROWSER = "https://browser.dataspace.copernicus.eu/"
MIRROR = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
MIRROR_DATASET = "https://planetarycomputer.microsoft.com/dataset/"
MIRROR_NOTE = (" Copernicus Data Space с этого сервера не ответил, поэтому поиск прошёл по тем же снимкам Sentinel "
               "в Microsoft Planetary Computer, скачивание там без регистрации.")
COLLECTIONS = ("sentinel-2-l2a", "sentinel-1-grd")


def _search(url: str, aoi, start: date, end: date, limit: int) -> list[dict]:
    features = []
    for collection in COLLECTIONS:
        body = {"collections": [collection], "intersects": mapping(aoi), "limit": limit,
                "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z", "sortby": [{"field": "datetime", "direction": "desc"}]}
        request = Request(url, data=json.dumps(body).encode(), method="POST",
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=60) as response:
            features += json.load(response).get("features", [])
    return features


def search(aoi, start: date, end: date, limit: int = 100) -> list[dict]:
    try:
        return _search(SEARCH, aoi, start, end, limit)
    except (OSError, ValueError):  # HTTP errors, resets and timeouts are all OSError
        pass
    try:
        return [{**feature, "_endpoint": MIRROR} for feature in _search(MIRROR, aoi, start, end, limit)]
    except (OSError, ValueError):
        raise HTTPException(502, "Ни Copernicus Data Space, ни Planetary Computer не отвечают. "
                                 "Повторите обновление каталога.") from None


def normalize(feature: dict, aoi, event_date: str | None, on: date) -> dict | None:
    props = feature.get("properties", {})
    sar = props.get("constellation") == "sentinel-1" or feature["id"].startswith("S1")
    timestamp = props["datetime"]
    raw = props.get("platform") or ""
    # Copernicus writes "sentinel-2b", Planetary Computer "Sentinel-2B" and "SENTINEL-1A".
    platform = "Sentinel-" + raw.split("-", 1)[1].upper() if raw.lower().startswith("sentinel-") else raw
    mode = props.get("sar:instrument_mode")
    assets = feature.get("assets", {})
    thumbnail = (assets.get("rendered_preview") or assets.get("thumbnail") or {}).get("href")
    resolution = props.get("gsd") or props.get("sar:pixel_spacing_range") or 10
    mirror = feature.get("_endpoint") == MIRROR
    return make_offer(provider="Copernicus", identifier=feature["id"],
                      title=f"{platform} {'GRD ' + mode if sar and mode else 'MSI L2A' if not sar else 'GRD'}".strip(),
                      sensor="sar" if sar else "optical", footprint=shape(feature["geometry"]), aoi=aoi,
                      resolution_m=float(resolution), captured=timestamp[:10], timestamp=timestamp,
                      cloud=None if sar else props.get("eo:cloud_cover"),
                      preview=thumbnail if thumbnail and thumbnail.startswith("https://") else None,
                      event_date=event_date, on=on,
                      order_url=MIRROR_DATASET + feature.get("collection", COLLECTIONS[0]) if mirror else BROWSER, free=True,
                      extra={"polarizations": props.get("sar:polarizations"), "platform_identifier": props.get("platform")})
