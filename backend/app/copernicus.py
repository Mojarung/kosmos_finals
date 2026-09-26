"""Free Sentinel-1 and Sentinel-2 from the Copernicus Data Space STAC-API, no key needed to search.

https://stac.dataspace.copernicus.eu/v1 lists Sentinel-1 GRD (radar, 10 m) and Sentinel-2 L2A
(optics, 10 m) with cloud cover and a thumbnail. Downloading a product needs a free Copernicus
account; the data are open, so the offer price is 0 (strategy A of the case).
"""
from __future__ import annotations

import json
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException
from shapely.geometry import mapping, shape

from .catalog_offers import make_offer

SEARCH = "https://stac.dataspace.copernicus.eu/v1/search"
BROWSER = "https://browser.dataspace.copernicus.eu/"
COLLECTIONS = ("sentinel-2-l2a", "sentinel-1-grd")


def search(aoi, start: date, end: date, limit: int = 100) -> list[dict]:
    features = []
    for collection in COLLECTIONS:
        body = {"collections": [collection], "intersects": mapping(aoi), "limit": limit,
                "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z", "sortby": [{"field": "datetime", "direction": "desc"}]}
        request = Request(SEARCH, data=json.dumps(body).encode(), method="POST",
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=60) as response:
                features += json.load(response).get("features", [])
        except HTTPError as error:
            raise HTTPException(502, f"Copernicus Data Space отклонил запрос (HTTP {error.code}).") from None
        except (URLError, TimeoutError, ValueError):
            raise HTTPException(502, "Copernicus Data Space временно не отвечает. Повторите обновление каталога.") from None
    return features


def normalize(feature: dict, aoi, event_date: str | None, on: date) -> dict | None:
    props = feature.get("properties", {})
    sar = props.get("constellation") == "sentinel-1" or feature["id"].startswith("S1")
    timestamp = props["datetime"]
    raw = props.get("platform") or ""
    platform = "Sentinel-" + raw.split("-", 1)[1].upper() if raw.startswith("sentinel-") else raw
    mode = props.get("sar:instrument_mode")
    thumbnail = (feature.get("assets", {}).get("thumbnail") or {}).get("href")
    resolution = props.get("gsd") or props.get("sar:pixel_spacing_range") or 10
    return make_offer(provider="Copernicus", identifier=feature["id"],
                      title=f"{platform} {'GRD ' + mode if sar and mode else 'MSI L2A' if not sar else 'GRD'}".strip(),
                      sensor="sar" if sar else "optical", footprint=shape(feature["geometry"]), aoi=aoi,
                      resolution_m=float(resolution), captured=timestamp[:10], timestamp=timestamp,
                      cloud=None if sar else props.get("eo:cloud_cover"),
                      preview=thumbnail if thumbnail and thumbnail.startswith("https://") else None,
                      event_date=event_date, on=on, order_url=BROWSER, free=True,
                      extra={"polarizations": props.get("sar:polarizations"), "platform_identifier": props.get("platform")})
