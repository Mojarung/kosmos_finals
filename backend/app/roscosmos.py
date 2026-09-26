"""Roscosmos Geoportal (ЕТРИС ДЗЗ, ЦБГД) as a live imagery catalog next to SkyFi.

The API is documented in the ЦБГД programming guide
(https://s3.gptl.ru/geoportal-public/pro-guide/v1/index.html, edition 2025-09-09):

* optical Resurs-P and Kanopus-V sessions: GET https://api.gptl.ru/catalog/v2/search;
* Kondor-FKA radar: STAC-API https://api.gptl.ru/stac/api/v1/catalogs/stac-archive/search;
* both need an OAuth 2.0 access token of an ETRIS account (https://auth.gptl.ru, realm etris).

The service never orders. A price here is the PP RF 840 formula of the case (the same
planning.quote as the ТЗ calculation, scenario base rate); the Order API returns the
supplier's own price in roubles and a payment link only when a real order is placed.

Credentials, like SKYFI_API_KEY: ETRIS_ACCESS_TOKEN, or ETRIS_CLIENT_ID (+ ETRIS_CLIENT_SECRET)
with ETRIS_USERNAME and ETRIS_PASSWORD for the password grant.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException
from shapely import from_wkt
from shapely.geometry import mapping, shape

from .catalog_offers import make_offer, pp840_price  # noqa: F401 - pp840_price kept for callers

API = "https://api.gptl.ru"
OPTICAL_SEARCH = API + "/catalog/v2/search"
SAR_SEARCH = API + "/stac/api/v1/catalogs/stac-archive/search"
TOKEN_URL = "https://auth.gptl.ru/auth/realms/etris/protocol/openid-connect/token"
QUICKLOOK = API + "/transform/v1/quicklook?url="
ORDER_URL = "https://gptl.ru/"
GUIDE = "https://s3.gptl.ru/geoportal-public/pro-guide/v1/index.html"
PLATFORMS = {"RP": "Ресурс-П", "KV": "Канопус-В"}
_token = {"value": None, "expires": 0.0}
_lock = threading.Lock()


def configured() -> bool:
    env = os.environ
    return bool(env.get("ETRIS_ACCESS_TOKEN")
                or (env.get("ETRIS_CLIENT_ID") and env.get("ETRIS_USERNAME") and env.get("ETRIS_PASSWORD")))


def access_token() -> str:
    """A static token, or the password grant refreshed a minute before it expires."""
    env = os.environ
    if env.get("ETRIS_ACCESS_TOKEN"):
        return env["ETRIS_ACCESS_TOKEN"]
    if not configured():
        raise HTTPException(503, "Для каталога Роскосмоса настройте ETRIS_ACCESS_TOKEN или учётную запись ЕТРИС "
                                 "(ETRIS_CLIENT_ID, ETRIS_USERNAME, ETRIS_PASSWORD) на сервере.")
    with _lock:
        if _token["value"] and time.time() < _token["expires"] - 60:
            return _token["value"]
        form = {"grant_type": "password", "client_id": env["ETRIS_CLIENT_ID"],
                "username": env["ETRIS_USERNAME"], "password": env["ETRIS_PASSWORD"], "scope": "openid"}
        if env.get("ETRIS_CLIENT_SECRET"):
            form["client_secret"] = env["ETRIS_CLIENT_SECRET"]
        payload = _call(Request(TOKEN_URL, data=urlencode(form).encode(), method="POST",
                                headers={"Content-Type": "application/x-www-form-urlencoded"}), "авторизация ЕТРИС")
        _token.update(value=payload["access_token"], expires=time.time() + float(payload.get("expires_in", 300)))
        return _token["value"]


def _call(request: Request, what: str) -> dict:
    try:
        with urlopen(request, timeout=40) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code in (401, 403):
            raise HTTPException(502, f"Геопортал Роскосмоса не принял учётную запись ({what}, HTTP {error.code}).") from None
        if error.code == 429:
            raise HTTPException(429, "Геопортал Роскосмоса ограничил частоту запросов. Повторите позже.") from None
        raise HTTPException(502, f"Геопортал Роскосмоса отклонил запрос ({what}, HTTP {error.code}).") from None
    except (URLError, TimeoutError, ValueError):
        raise HTTPException(502, "Геопортал Роскосмоса временно не отвечает. Повторите обновление каталога.") from None


def search_optical(aoi, start: date, end: date, limit: int = 100) -> list[dict]:
    query = urlencode({"geometry": aoi.wkt, "acquisition_date_after": f"{start}T00:00:00Z",
                       "acquisition_date_before": f"{end}T23:59:59Z", "limit": limit,
                       "ordering": "-acquisition_date_begin"})
    request = Request(f"{OPTICAL_SEARCH}?{query}", headers={"Authorization": f"Bearer {access_token()}"})
    return _call(request, "каталог Ресурс-П и Канопус-В").get("results", [])


def search_sar(aoi, start: date, end: date, limit: int = 100) -> list[dict]:
    body = {"intersects": mapping(aoi), "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z", "limit": limit}
    request = Request(SAR_SEARCH, data=json.dumps(body).encode(), method="POST",
                      headers={"Authorization": f"Bearer {access_token()}", "Content-Type": "application/json"})
    return _call(request, "каталог Кондор-ФКА").get("features", [])


def normalize_optical(item: dict, aoi, event_date: str | None, on: date) -> dict | None:
    platform = item.get("platform_name") or PLATFORMS.get(item.get("platform_type_identifier"), "Роскосмос")
    captured = (item.get("acquisition_date_begin") or item["date_time_stamp"])[:10]
    previews = [p["url"] for p in item.get("previews") or [] if isinstance(p.get("url"), str)]
    preview = previews[0].replace("http://", "https://", 1) if previews else None
    instrument = item.get("instrument_name") or item.get("instrument_identifier") or ""
    return make_offer(provider="Роскосмос", identifier=item["identifier"], title=f"{platform} {instrument}".strip(),
                      sensor="optical", footprint=from_wkt(item["geometry"]), aoi=aoi,
                      resolution_m=float(item["resolution"]), captured=captured,
                      timestamp=item.get("acquisition_date_begin") or item["date_time_stamp"],
                      cloud=item.get("cloudiness"), preview=preview, event_date=event_date, on=on, order_url=ORDER_URL,
                      price_note="цену и ссылку на оплату выставляет Order API Геопортала",
                      extra={"off_nadir_angle": item.get("nadir_tilt_angle"),
                             "platform_identifier": item.get("platform_identifier")})


def normalize_sar(feature: dict, aoi, event_date: str | None, on: date) -> dict | None:
    props = feature.get("properties", {})
    timestamp = props.get("datetime") or props.get("start_datetime")
    quicklook = next((asset.get("href") for key, asset in (feature.get("assets") or {}).items()
                      if "ql" in key.lower() or "quicklook" in key.lower() or "thumbnail" in key.lower()), None)
    resolution = props.get("gsd") or props.get("sar:resolution_range") or 1.0
    modes = props.get("sar:instrument_mode") or ""
    return make_offer(provider="Роскосмос", identifier=feature["id"], title=f"Кондор-ФКА {modes}".strip(),
                      sensor="sar", footprint=shape(feature["geometry"]), aoi=aoi, resolution_m=float(resolution),
                      captured=timestamp[:10], timestamp=timestamp, cloud=None,
                      preview=QUICKLOOK + quicklook if quicklook else None, event_date=event_date, on=on,
                      order_url=ORDER_URL, price_note="цену и ссылку на оплату выставляет Order API Геопортала",
                      extra={"polarizations": props.get("sar:polarizations"), "platform_identifier": props.get("platform")})
