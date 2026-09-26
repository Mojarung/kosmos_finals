"""Immutable procurement snapshots. No endpoint in this module places an order."""
from __future__ import annotations

import copy
from collections import Counter
import hashlib
import io
import json
import math
import os
import sqlite3
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely import from_wkt
from shapely.errors import GEOSException
from shapely.geometry import Point, box, mapping, shape
from shapely.ops import transform

from .catalog import ROOT, known_scene_ids, metadata
from .planning import TO_EQUAL_AREA, square_around

router = APIRouter(prefix="/api/procurement", tags=["procurement"])
DB = Path(os.environ.get("FLOODVALUE_PROCUREMENT_DB", ROOT / "data/runtime/procurement.sqlite3"))
SKYFI = "https://app.skyfi.com/platform-api"


def now():
    return datetime.now(timezone.utc).isoformat()


def area(g):
    return transform(TO_EQUAL_AREA.transform, g).area / 1_000_000


def persist(kind, payload):
    record = {**payload, "id": uuid.uuid4().hex, "saved_at": now()}
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as db:
        db.execute("CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, kind TEXT, payload TEXT)")
        db.execute("INSERT INTO snapshots VALUES (?, ?, ?)", (record["id"], kind, json.dumps(record, allow_nan=False)))
    return record


def read(identifier, kind="catalog"):
    if not DB.exists():
        raise HTTPException(404, "Сохранённый каталог не найден")
    with sqlite3.connect(DB) as db:
        row = db.execute("SELECT payload FROM snapshots WHERE id=? AND kind=?", (identifier, kind)).fetchone()
    if not row:
        raise HTTPException(404, "Сохранённый каталог не найден")
    return json.loads(row[0])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Search(Input):
    scene_id: str
    source: str = Field(default="demo", pattern="^(demo|skyfi)$")
    market: str = Field(default="commercial", pattern="^(commercial|open|all)$")
    from_date: date | None = None
    to_date: date | None = None

    @model_validator(mode="after")
    def dates(self):
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("Начало периода позже конца")
        return self


class Plan(Input):
    catalog_id: str = Field(min_length=1, max_length=64)
    budget: float = Field(default=500, ge=0, le=1e9)
    deadline_hours: float = Field(default=12, gt=0, le=8760)
    resolution_m: float = Field(default=1, gt=0, le=100)
    mandatory: list[str] = Field(default_factory=list, max_length=15)
    excluded: list[str] = Field(default_factory=list, max_length=100)
    objective: str = Field(default="coverage", pattern="^(coverage|cheapest)$")


class Review(Input):
    offer_id: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=12, max_length=1000)


class Variant(Input):
    offer_id: str = Field(min_length=1, max_length=200)
    asset_ids: list[str] = Field(min_length=1, max_length=15)


def skyfi_post(path, payload):
    # Fixed allowlist: never accept an arbitrary URL or invoke a purchase endpoint.
    if path not in {"/archives", "/order-archive/validate"}:
        raise ValueError("Unsupported provider operation")
    key = os.environ.get("SKYFI_API_KEY")
    if not key:
        raise HTTPException(503, "SKYFI_API_KEY не настроен на сервере. Доступен учебный каталог.")
    request = Request(SKYFI + path, data=json.dumps(payload).encode(), method="POST",
                      headers={"Content-Type": "application/json", "X-Skyfi-Api-Key": key})
    try:
        with urlopen(request, timeout=25) as response:
            return json.load(response)
    except HTTPError as error:
        # Do not echo upstream bodies/headers: they may contain account details.
        if error.code == 429:
            raise HTTPException(429, "SkyFi временно ограничил частоту запросов. Подождите минуту и повторите поиск.") from None
        raise HTTPException(502, f"SkyFi отклонил запрос (HTTP {error.code}). Проверьте доступ и условия заказа.") from None
    except (URLError, TimeoutError, ValueError):
        raise HTTPException(502, "SkyFi не ответил корректно. Сохранённые каталоги остаются доступны.") from None


def asset_snapshot(info):
    return [{**a, "expected_loss_rub": round((a.get("p_flood") or 0) * a["value_rub"] * a["vulnerability"], 2)}
            for a in info["assets"] if a.get("p_flood") is not None]


def demo_offers(info):
    bounds = info["bbox"]
    event = date.fromisoformat(info["observed_at"][:10])
    offers = []

    def add(identifier, title, geometry, rate=12, minimum=5, delivery=6, resolution=.5, provider="Учебная оптика", **extra):
        sqkm = area(geometry)
        offers.append({"id": identifier, "archive_id": None, "title": title, "provider": provider,
            "geometry": mapping(geometry), "footprint": mapping(geometry), "area_km2": round(sqkm, 3),
            "billable_km2": round(max(sqkm, minimum), 3), "minimum_billable_km2": minimum,
            "price": round(max(sqkm, minimum) * rate, 2), "currency": "USD", "resolution_m": resolution,
            "unit_price": rate, "technical_min_km2": 1, "technical_max_km2": None,
            "delivery_hours": delivery, "capture_date": str(event), "quality": "scenario",
            "price_status": "scenario", "price_source": "Учебные условия FloodValue; не предложение поставщика",
            "quote_at": "2026-09-26T00:00:00+00:00", "quote_validated": True,
            "full_scene_only": False, "order_issue": None, "preview_url": None, **extra})

    for a in info["assets"]:
        add("local-" + a["id"], "Отдельная зона · " + a["name"], box(*square_around(a, bounds)))
    # Grouped alternatives compete honestly with separate orders; no forced winner.
    w, s, e, n = bounds
    add("west", "Объединённый заказ · запад", box(w, s, w + (e-w)*.62, n))
    add("east", "Объединённый заказ · восток", box(w + (e-w)*.38, s, e, n))
    add("wide", "Вся область · один заказ", box(*bounds))
    add("radar", "Вся область · резервный радар", box(*bounds), rate=0, minimum=0,
        delivery=3, resolution=1, provider="Учебный радар", price=420, billable_km2=None,
        full_scene_only=True, price_source="Учебная фиксированная цена кадра: 420 USD")
    add("free", "Бесплатная обзорная сцена", box(*bounds), rate=0, minimum=0,
        resolution=10, delivery=2, provider="Учебные открытые данные")
    add("old", "Архив до события", box(*bounds), rate=1, minimum=0,
        capture_date=str(event - timedelta(days=10)))
    add("late", "Детальная сцена · долгая доставка", box(*bounds), rate=2, minimum=0, delivery=48)
    add("cloud", "Облачность у объектов не проверена", box(*bounds), rate=1, minimum=0, quality="unknown")
    return offers


def normalize_archive(item, aoi):
    footprint = from_wkt(item["footprint"])
    if not footprint.is_valid or footprint.is_empty or footprint.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Invalid footprint")
    full = bool(item.get("fullSceneOnly"))
    geometry = footprint if full else footprint.intersection(aoi)
    sqkm = area(geometry)
    if geometry.is_empty or sqkm == 0:
        return None
    issue = None
    if geometry.geom_type != "Polygon" or geometry.interiors or not geometry.equals(geometry.convex_hull):
        issue = "Контур требует отдельного согласования: API принимает выпуклый Polygon"
    elif len(geometry.exterior.coords) > 500:
        issue = "Контур превышает 500 вершин"
    elif not full and (sqkm < max(1, item.get("minSqKm", 0)) or sqkm > item.get("maxSqKm", math.inf)):
        issue = "Площадь контура не соответствует ограничениям заказа"
    price = item["priceFullScene"] if full else sqkm * item["priceForOneSquareKm"]
    delivery = item.get("deliveryTimeHours")
    if (not math.isfinite(price) or price < 0 or not math.isfinite(item["gsd"]) or item["gsd"] <= 0
            or (delivery is not None and (not math.isfinite(delivery) or delivery < 0))):
        raise ValueError("Invalid price or resolution")
    previews = item.get("thumbnailUrls") or {}
    preview = next((u for u in previews.values() if isinstance(u, str) and u.startswith("https://")), None)
    return {"id": str(item["archiveId"]), "archive_id": item["archiveId"],
        "title": item.get("constellation") or item["provider"], "provider": item["provider"],
        "geometry": mapping(geometry), "footprint": mapping(footprint), "area_km2": round(sqkm, 3),
        "billable_km2": None, "minimum_billable_km2": None,
        "technical_min_km2": item.get("minSqKm"), "price": round(price, 2), "currency": "USD",
        "technical_max_km2": item.get("maxSqKm"), "unit_price": item["priceForOneSquareKm"],
        "resolution_m": item["gsd"] / 100, "source_gsd_cm": item["gsd"], "delivery_hours": item.get("deliveryTimeHours"),
        "capture_date": item["captureTimestamp"][:10], "quality": "unknown",
        "price_status": "catalog_estimate", "price_source": SKYFI + "/archives",
        "quote_at": now(), "quote_validated": False, "full_scene_only": full,
        "order_issue": issue, "preview_url": preview, "cloud_percent": item.get("cloudCoveragePercent")}


@router.get("/status")
def status():
    return {"skyfi_configured": bool(os.environ.get("SKYFI_API_KEY")), "purchase_enabled": False}


@router.post("/catalogs")
def search(request: Search):
    if request.scene_id not in known_scene_ids():
        raise HTTPException(404, "Неизвестная сцена")
    info = metadata(request.scene_id)
    event = date.fromisoformat(info["observed_at"][:10])
    start, end = request.from_date or event, request.to_date or event + timedelta(days=3)
    if start > end or end > date.today():
        raise HTTPException(422, "Укажите корректный исторический период съёмки")
    truncated = False
    skipped = 0
    if request.source == "demo":
        offers = demo_offers(info)
    else:
        raw = skyfi_post("/archives", {"aoi": box(*info["bbox"]).wkt,
            "fromDate": start.isoformat() + "T00:00:00Z", "toDate": end.isoformat() + "T23:59:59Z",
            "pageSize": 50, "minOverlapRatio": .01,
            **({"openData": request.market == "open"} if request.market != "all" else {})})
        truncated = bool(raw.get("nextPage"))
        offers = []
        for item in raw.get("archives", []):
            try:
                offer = normalize_archive(item, box(*info["bbox"]))
                if offer and offer["id"] not in {o["id"] for o in offers}:
                    offers.append(offer)
            except (ValueError, KeyError, TypeError, GEOSException):
                skipped += 1
    return persist("catalog", {"scene_id": request.scene_id, "source": request.source,
        "captured_at": now(), "normalization_version": 1, "market": request.market, "mode": "retrospective", "currency": "USD", "bbox": info["bbox"],
        "event_date": str(event), "from_date": str(start), "to_date": str(end),
        "assets": asset_snapshot(info), "offers": offers, "truncated": truncated, "skipped": skipped,
        "disclaimer": "Учебные предложения и цены. Не предложения поставщиков." if request.source == "demo"
        else "Покупка исторического архива сегодня. Цены предварительные; качество требует проверки."})


@router.get("/catalogs/{catalog_id}")
def get_catalog(catalog_id: str):
    return read(catalog_id)


def find_offer(catalog, offer_id):
    offer = next((o for o in catalog["offers"] if o["id"] == offer_id), None)
    if offer is None:
        raise HTTPException(404, "Предложение не найдено")
    return offer


@router.post("/catalogs/{catalog_id}/variants")
def create_variant(catalog_id: str, request: Variant):
    catalog = read(catalog_id)
    original = find_offer(catalog, request.offer_id)
    if original["full_scene_only"]:
        raise HTTPException(422, "Этот продукт оплачивается целым кадром; уменьшение контура не снижает цену")
    if set(request.asset_ids) - {a["id"] for a in catalog["assets"]}:
        raise HTTPException(422, "Неизвестный объект")
    if len(catalog["offers"]) >= 100:
        raise HTTPException(422, "Достигнут лимит 100 вариантов заказа в каталоге")
    squares = [box(*square_around(a, catalog["bbox"])) for a in catalog["assets"] if a["id"] in request.asset_ids]
    geometry = box(min(g.bounds[0] for g in squares), min(g.bounds[1] for g in squares),
                   max(g.bounds[2] for g in squares), max(g.bounds[3] for g in squares))
    sqkm = area(geometry)
    if not shape(original["footprint"]).covers(geometry):
        raise HTTPException(422, "Сцена не покрывает весь новый контур вокруг выбранных объектов")
    if (sqkm < max(1, original.get("technical_min_km2") or 0)
            or (original.get("technical_max_km2") is not None and sqkm > original["technical_max_km2"])):
        raise HTTPException(422, "Новый контур не соответствует минимальной или максимальной площади продукта")
    variant = copy.deepcopy(original)
    identifier = hashlib.sha256((str(original.get("archive_id") or original["id"]) + geometry.wkt).encode()).hexdigest()[:16]
    billable = max(sqkm, original.get("minimum_billable_km2") or 0)
    variant.update(id="zone-" + identifier, geometry=mapping(geometry), area_km2=round(sqkm, 3),
                   title="Заказ на выбранные объекты · " + original["provider"],
                   billable_km2=round(billable, 3) if catalog["source"] == "demo" else None,
                   price=round(billable * original["unit_price"], 2), quote_at=now(),
                   quote_validated=catalog["source"] == "demo", order_issue=None,
                   price_status="scenario" if catalog["source"] == "demo" else "catalog_estimate")
    # A quote is bound to its geometry; never inherit a validated price after resizing.
    variant.pop("quote_response", None)
    if catalog["source"] == "skyfi":
        variant.update(price_source=SKYFI + "/archives", quality="unknown")
        variant.pop("quality_note", None)
        variant.pop("reviewed_at", None)
    if variant["id"] not in {o["id"] for o in catalog["offers"]}:
        catalog["offers"].append(variant)
    return persist("catalog", {**catalog, "parent_id": catalog_id})


@router.post("/catalogs/{catalog_id}/review")
def review(catalog_id: str, request: Review):
    catalog = read(catalog_id)
    offer = find_offer(catalog, request.offer_id)
    if not request.note.strip() or len(request.note.strip()) < 12:
        raise HTTPException(422, "Укажите основание проверки пригодности")
    offer.update(quality="operator_review", quality_note=request.note.strip(), reviewed_at=now())
    return persist("catalog", {**catalog, "parent_id": catalog_id})


@router.post("/catalogs/{catalog_id}/quotes/{offer_id}")
def validate_quote(catalog_id: str, offer_id: str):
    catalog = read(catalog_id)
    offer = find_offer(catalog, offer_id)
    if catalog["source"] != "skyfi" or offer.get("order_issue"):
        raise HTTPException(422, offer.get("order_issue") or "Учебная цена не подтверждается поставщиком")
    quote = skyfi_post("/order-archive/validate", {"aoi": shape(offer["geometry"]).wkt,
                                                  "archiveId": offer["archive_id"]})
    cents = quote.get("estimatedCostCents")
    if type(cents) is not int or cents < 0:
        raise HTTPException(502, "Поставщик вернул некорректную цену")
    offer.update(price=cents / 100, price_status="provider_estimate", quote_validated=True,
                 quote_at=now(), quote_response=quote, price_source=SKYFI + "/order-archive/validate")
    return persist("catalog", {**catalog, "parent_id": catalog_id})


def reason(offer, catalog, request):
    if offer["id"] in request.excluded:
        return "Исключено пользователем"
    if offer.get("order_issue"):
        return offer["order_issue"]
    if not (max(catalog["event_date"], catalog["from_date"]) <= offer["capture_date"] <= catalog["to_date"]):
        return "Снято вне периода события"
    if offer["resolution_m"] > request.resolution_m:
        return "Недостаточная детализация"
    if offer.get("delivery_hours") is None:
        return "Срок доставки неизвестен"
    if offer["delivery_hours"] > request.deadline_hours:
        return "Доставка позже срока"
    if offer["quality"] not in {"scenario", "operator_review", "verified"}:
        return "Пригодность у объектов не проверена"
    if not offer["quote_validated"]:
        return "Нужна проверка цены заказа"
    return None


def calculate(catalog, request):
    if catalog["source"] == "skyfi" and catalog.get("normalization_version") != 1:
        raise HTTPException(409, "Этот каталог сохранён до исправления единиц детализации. Нажмите «Найти предложения» заново.")
    assets = catalog["assets"]
    if len(assets) > 15:
        raise HTTPException(422, "Точный подбор поддерживает до 15 объектов")
    ids = [a["id"] for a in assets]
    if set(request.mandatory) - set(ids) or set(request.excluded) - {o["id"] for o in catalog["offers"]}:
        raise HTTPException(422, "Неизвестный объект или предложение")
    required = sum(1 << i for i, a in enumerate(assets) if a["id"] in request.mandatory)
    offers = []
    for original in catalog["offers"]:
        o = copy.deepcopy(original)
        polygon = shape(o["geometry"])
        mask = sum(1 << i for i, a in enumerate(assets) if polygon.covers(Point(a["longitude"], a["latitude"])))
        o.update(asset_ids=[ids[i] for i in range(len(ids)) if mask & (1 << i)], reason=reason(o, catalog, request), mask=mask)
        offers.append(o)
    blockers = dict(Counter(o["reason"] for o in offers if o["reason"]))
    eligible_count = sum(o["reason"] is None and bool(o["mask"]) for o in offers)
    # Exact set-cover DP over unique assets. Quotes are independent frozen line prices.
    dp = {0: (0, ())}
    for i, o in enumerate(offers):
        if o["reason"] or not o["mask"]:
            continue
        cents = round(o["price"] * 100)
        for covered, (cost, chosen) in list(dp.items()):
            union = covered | o["mask"]
            candidate = (cost + cents, chosen + (i,))
            old = dp.get(union)
            if old is None or (candidate[0], len(candidate[1])) < (old[0], len(old[1])):
                dp[union] = candidate
    value = {mask: sum(a["expected_loss_rub"] for i, a in enumerate(assets) if mask & (1 << i)) for mask in dp}
    feasible = [mask for mask in dp if mask & required == required]
    cheapest = min(feasible, key=lambda m: (dp[m][0], len(dp[m][1])), default=None)
    within = [m for m in feasible if dp[m][0] <= round(request.budget * 100)]
    if request.objective == "cheapest":
        selected = min(within, key=lambda m: (dp[m][0], len(dp[m][1])), default=None)
    else:
        selected = max(within, key=lambda m: (value[m], m.bit_count(), -dp[m][0], -len(dp[m][1])), default=None)
    # If mandatory requirements are impossible, show a clearly labelled partial fallback.
    satisfied = selected is not None
    if selected is None:
        available = [m for m in dp if dp[m][0] <= round(request.budget * 100)]
        selected = max(available, key=lambda m: ((m & required).bit_count(), value[m], -dp[m][0]))
    cost, chosen = dp[selected]
    selected_ids = [offers[i]["id"] for i in chosen]
    covered_ids = [ids[i] for i in range(len(ids)) if selected & (1 << i)]
    for o in offers:
        o["selected"] = o["id"] in selected_ids
        o["reason"] = o["reason"] or ("Выбрано в корзину" if o["selected"] else
                         "Дороже бюджета" if o["price"] > request.budget else "Альтернатива: не улучшает выбранную корзину")
        o.pop("mask")
    # Compare with cheapest separate order per covered target; deduplicate identical order IDs.
    independent = set()
    for asset_id in covered_ids:
        eligible = [(i, o) for i, o in enumerate(catalog["offers"]) if not reason(o, catalog, request)
                    and shape(o["geometry"]).covers(Point(assets[ids.index(asset_id)]["longitude"], assets[ids.index(asset_id)]["latitude"]))]
        independent.add(min(eligible, key=lambda pair: (pair[1]["price"], pair[0]))[0])
    naive_cost = sum(round(catalog["offers"][i]["price"] * 100) for i in independent)
    total_el = sum(a["expected_loss_rub"] for a in assets)
    selected_offers = [offers[i] for i in chosen]
    minimum_cost = dp[cheapest][0] / 100 if cheapest is not None else None
    increments = {}
    for i, asset in enumerate(assets):
        target = selected | (1 << i)
        options = [m for m in dp if m & target == target]
        least = min((dp[m][0] for m in options), default=None)
        increments[asset["id"]] = max(0, least - cost) / 100 if least is not None else None
    free_masks = [m for m in dp if dp[m][0] == 0]
    free_mask = max(free_masks, key=lambda m: (value[m], m.bit_count()))
    broad_offers = [o for o in catalog["offers"] if not reason(o, catalog, request)
                    and shape(o["geometry"]).covers(box(*catalog["bbox"]))]
    broad = min(broad_offers, key=lambda o: o["price"], default=None)
    return {"catalog_id": catalog["id"], "source": catalog["source"], "currency": catalog["currency"],
        "calculated_at": now(), "request": request.model_dump(), "offers": offers,
        "blockers": blockers, "eligible_count": eligible_count,
        "selected_ids": selected_ids, "covered_ids": covered_ids, "cost": cost / 100,
        "mandatory_satisfied": satisfied, "missing_ids": sorted(set(request.mandatory) - set(covered_ids)),
        "minimum_cost": minimum_cost, "budget_gap": max(0, minimum_cost - request.budget) if minimum_cost is not None else None,
        "incremental_cost": increments,
        "free_coverage_count": free_mask.bit_count(),
        "broad_cost": broad["price"] if broad else None,
        "broad_offer_id": broad["id"] if broad else None,
        "covered_el_rub": value[selected], "coverage_share": value[selected] / total_el if total_el else None,
        "delivery_hours": max((o["delivery_hours"] for o in selected_offers), default=0),
        "ordered_km2": round(sum(o["area_km2"] for o in selected_offers), 3),
        "billable_km2": round(sum(o["billable_km2"] for o in selected_offers), 3)
            if all(o["billable_km2"] is not None for o in selected_offers) else None,
        "independent_cost": naive_cost / 100, "saving_vs_independent": max(0, naive_cost - cost) / 100,
        "comparison_note": "Отдельный подбор самого дешёвого заказа для каждого охваченного объекта, без повторной оплаты одной позиции. Требования одинаковы; дополнительные объекты могут отличаться.",
        "optimality": "Точный минимум по наборам объектов среди пригодных предложений сохранённого каталога. Цены позиций суммируются; общая скидка поставщика не предполагается.",
        "limitations": ["Охват EL не является предотвращённым ущербом.", "Покупка не означает устранение неопределённости.",
                        "Сроки доставки оценочные. Налоги и дополнительные сборы требуют подтверждения."]}


@router.post("/plan")
def plan(request: Plan):
    return calculate(read(request.catalog_id), request)


@router.post("/decisions")
def save_decision(request: Plan):
    catalog = read(request.catalog_id)
    return persist("decision", {"catalog": catalog, "plan": calculate(catalog, request)})


@router.get("/decisions")
def decisions(scene_id: str):
    if not DB.exists():
        return {"decisions": []}
    with sqlite3.connect(DB) as db:
        rows = db.execute("SELECT payload FROM snapshots WHERE kind='decision' ORDER BY rowid DESC LIMIT 100").fetchall()
    items = [json.loads(r[0]) for r in rows]
    return {"decisions": [{"id": d["id"], "saved_at": d["saved_at"], "cost": d["plan"]["cost"],
                           "covered": len(d["plan"]["covered_ids"]), "catalog_id": d["catalog"]["id"]}
                          for d in items if d["catalog"]["scene_id"] == scene_id]}


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: str):
    return read(decision_id, "decision")


@router.get("/decisions/{decision_id}/export")
def export(decision_id: str):
    decision = read(decision_id, "decision")
    p, catalog = decision["plan"], decision["catalog"]
    selected = [o for o in p["offers"] if o["selected"]]
    report = ["# Заявка на спутниковые данные", "", catalog["disclaimer"],
        "Ретроспективный разбор; покупка архива сегодня.", f"Каталог: {catalog['captured_at']}",
        f"Сцена: {catalog['scene_id']}; период {catalog['from_date']} — {catalog['to_date']}",
        f"Стоимость: {p['cost']:.2f} USD", f"Обязательные условия выполнены: {p['mandatory_satisfied']}",
        f"Не покрыты: {', '.join(p['missing_ids']) or 'нет'}", "", "## Состав заказа"]
    for o in selected:
        report += [f"- {o['title']}: {o['price']:.2f} USD; {o['price_status']}; источник {o['price_source']}; расчёт {o['quote_at']}"]
    report += ["", "## Ограничения", *p["limitations"], p["optimality"]]
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("request.md", "\n".join(report))
        archive.writestr("decision.json", json.dumps(decision, ensure_ascii=False, indent=2))
        archive.writestr("orders.geojson", json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": o["geometry"], "properties": {k: v for k, v in o.items() if k not in {"geometry", "footprint"}}}
            for o in selected]}, ensure_ascii=False))
    data.seek(0)
    return StreamingResponse(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="procurement-{decision_id}.zip"'})
