"""Live imagery catalogs (Roscosmos Geoportal, SkyFi): archive exploration, geographic comparison, estimates.

No endpoint places an order. Catalog responses retain their source and capture time.
"""
from __future__ import annotations

import copy
import html
import io
import json
import math
import os
import sqlite3
import uuid
import zipfile
from datetime import date, datetime, timezone
from itertools import combinations
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely import from_wkt
from shapely.errors import GEOSException
from shapely.geometry import box, mapping, shape
from shapely.ops import transform, unary_union

from .catalog import ROOT, known_scene_ids, metadata
from .planning import TO_EQUAL_AREA, today

router = APIRouter(prefix='/api/procurement', tags=['procurement'])
DB = Path(os.environ.get('FLOODVALUE_PROCUREMENT_DB', ROOT / 'data/runtime/procurement.sqlite3'))
SKYFI = 'https://app.skyfi.com/platform-api'
# Live catalogs: Roscosmos Geoportal and SCANEX (roubles, PP 840 estimate), free Copernicus data, SkyFi (dollars).
SOURCES = ('roscosmos', 'scanex', 'free', 'skyfi')


def now():
    return datetime.now(timezone.utc).isoformat()


def area(geometry):
    return transform(TO_EQUAL_AREA.transform, geometry).area / 1_000_000


def persist(kind, payload):
    record = {**payload, 'id': uuid.uuid4().hex, 'saved_at': now()}
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as db:
        db.execute('CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, kind TEXT, payload TEXT)')
        db.execute('INSERT INTO snapshots VALUES (?, ?, ?)', (record['id'], kind, json.dumps(record, allow_nan=False)))
    return record


def read(identifier, kind='catalog'):
    if DB.exists():
        with sqlite3.connect(DB) as db:
            row = db.execute('SELECT payload FROM snapshots WHERE id=? AND kind=?', (identifier, kind)).fetchone()
        if row:
            record = json.loads(row[0])
            catalog = record if kind == 'catalog' else record['catalog']
            if catalog.get('source') not in SOURCES or catalog.get('normalization_version') != 2:
                raise HTTPException(409, 'Обновите каталог: эта версия расчёта больше не используется.')
            return record
    raise HTTPException(404, 'Сохранённые данные не найдены')


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Search(Input):
    scene_id: str
    source: str = Field(default='roscosmos', pattern='^(roscosmos|scanex|free|skyfi)$')
    market: str = Field(default='commercial', pattern='^(commercial|open)$')
    from_date: date | None = None
    to_date: date | None = None
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)

    @model_validator(mode='after')
    def valid(self):
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError('Начало периода позже конца')
        if self.bbox:
            w, s, e, n = self.bbox
            if not (-180 <= w < e <= 180 and -85 <= s < n <= 85):
                raise ValueError('Некорректные границы территории')
            if not .01 <= area(box(*self.bbox)) <= 10000:
                raise ValueError('Площадь территории должна быть от 0,01 до 10 000 км²')
        return self


class Plan(Input):
    catalog_id: str = Field(min_length=1, max_length=64)
    budget: float = Field(default=500, ge=0, le=1e9)
    resolution_m: float = Field(default=1, gt=0, le=100)
    max_cloud_percent: float = Field(default=30, ge=0, le=100)
    target_coverage: float = Field(default=95, gt=0, le=100)
    selected_ids: list[str] = Field(default_factory=list, max_length=4)


def provider(path, payload=None):
    if path not in {'/archives', '/order-archive/validate'} and not path.startswith('/archives?page='):
        raise ValueError('Unsupported provider operation')
    key = os.environ.get('SKYFI_API_KEY')
    if not key:
        raise HTTPException(503, 'Для доступа к реальному каталогу настройте SKYFI_API_KEY на сервере.')
    request = Request(SKYFI + path, data=json.dumps(payload).encode() if payload is not None else None,
                      method='POST' if payload is not None else 'GET',
                      headers={'Content-Type': 'application/json', 'X-Skyfi-Api-Key': key})
    try:
        with urlopen(request, timeout=25) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 429:
            raise HTTPException(429, 'SkyFi ограничил частоту запросов. Сохранённый каталог доступен; обновите его позже.') from None
        if error.code == 403:
            raise HTTPException(502, 'SkyFi не разрешил эту операцию для аккаунта (403). Сравнение по ценам каталога доступно; финальная цена не подтверждена.') from None
        raise HTTPException(502, f'SkyFi отклонил запрос (HTTP {error.code}).') from None
    except (URLError, TimeoutError, ValueError):
        raise HTTPException(502, 'SkyFi временно не отвечает. Повторите обновление каталога.') from None


def normalize_archive(item, aoi, event_date=None):
    footprint = from_wkt(item['footprint'])
    if not footprint.is_valid or footprint.is_empty or footprint.geom_type not in {'Polygon', 'MultiPolygon'}:
        raise ValueError('Invalid footprint')
    overlap = footprint.intersection(aoi)
    if overlap.is_empty or area(overlap) <= 0:
        return None
    full = bool(item.get('fullSceneOnly'))
    geometry = footprint if full else overlap
    sqkm = area(geometry)
    issue = None
    if not full:
        if geometry.geom_type != 'Polygon' or geometry.interiors or not geometry.equals(geometry.convex_hull):
            issue = 'Контур обрезки требует согласования с поставщиком'
        elif len(geometry.exterior.coords) > 500:
            issue = 'Контур превышает 500 вершин'
        elif sqkm < max(1, item.get('minSqKm') or 0) or sqkm > (item.get('maxSqKm') or math.inf):
            issue = 'Площадь меньше минимума или больше максимума заказа'
    price = item['priceFullScene'] if full else sqkm * item['priceForOneSquareKm']
    gsd = item['gsd'] / 100
    if not math.isfinite(price) or price < 0 or not math.isfinite(gsd) or gsd <= 0:
        raise ValueError('Invalid price or resolution')
    thumbs = item.get('thumbnailUrls') or {}
    urls = [(k, v) for k, v in thumbs.items() if isinstance(v, str) and v.startswith('https://')]
    def pixels(pair):
        try:
            return int(pair[0].split('x')[0])
        except ValueError:
            return 0
    preview = max(urls, key=pixels)[1] if urls else None
    captured = item['captureTimestamp'][:10]
    return {'id': str(item['archiveId']), 'archive_id': item['archiveId'], 'provider': item['provider'],
        'title': item.get('constellation') or item['provider'], 'product_type': item.get('productType'),
        'geometry': mapping(geometry), 'footprint': mapping(footprint), 'coverage_geometry': mapping(overlap),
        'area_km2': round(sqkm, 4), 'coverage_km2': round(area(overlap), 4),
        'coverage_percent': round(min(100, area(overlap) / area(aoi) * 100), 2),
        'technical_min_km2': item.get('minSqKm'), 'technical_max_km2': item.get('maxSqKm'),
        'price': round(price, 2), 'unit_price': item['priceForOneSquareKm'], 'currency': 'USD',
        'resolution_m': gsd, 'source_gsd_cm': item['gsd'], 'delivery_hours': item.get('deliveryTimeHours'),
        'capture_date': captured, 'capture_timestamp': item['captureTimestamp'],
        'days_from_event': (date.fromisoformat(captured) - date.fromisoformat(event_date)).days if event_date else None,
        'price_status': 'catalog_estimate', 'price_source': SKYFI + '/archives', 'quote_at': now(),
        'quote_validated': False, 'quality': 'unknown', 'full_scene_only': full,
        'open_data': bool(item.get('openData')), 'order_issue': issue, 'preview_url': preview,
        'cloud_percent': item.get('cloudCoveragePercent'), 'off_nadir_angle': item.get('offNadirAngle')}


@router.get('/status')
def status():
    from . import roscosmos
    return {'skyfi_configured': bool(os.environ.get('SKYFI_API_KEY')), 'roscosmos_configured': roscosmos.configured(),
            'scanex_configured': True, 'free_configured': True, 'sources': list(SOURCES), 'purchase_enabled': False}


@router.get('/catalogs/latest')
def latest(scene_id: str, source: str | None = None):
    """The newest saved catalog of the scene; ``source`` narrows it to one provider."""
    if scene_id not in known_scene_ids():
        raise HTTPException(404, 'Неизвестная сцена')
    bounds = metadata(scene_id)['bbox']
    if DB.exists():
        with sqlite3.connect(DB) as db:
            rows = db.execute("SELECT payload FROM snapshots WHERE kind='catalog' ORDER BY rowid DESC LIMIT 200").fetchall()
        for row in rows:
            c = json.loads(row[0])
            if c.get('scene_id') == scene_id and c.get('bbox') == bounds and c.get('source') in ((source,) if source else SOURCES) and c.get('normalization_version') == 2:
                return c
    raise HTTPException(404, 'Для этой территории ещё нет сохранённого ответа каталога')


@router.post('/catalogs')
def search(request: Search):
    if request.scene_id not in known_scene_ids():
        raise HTTPException(404, 'Неизвестная сцена')
    info = metadata(request.scene_id)
    event = date.fromisoformat(info['observed_at'][:10])
    start, end = request.from_date or event, request.to_date or today()
    if start > end or end > today():
        raise HTTPException(422, 'Укажите корректный исторический период съёмки')
    bounds = request.bbox or info['bbox']
    aoi = box(*bounds)
    if request.source != 'skyfi':
        return search_live(request, aoi, bounds, event, start, end)
    raw = provider('/archives', {'aoi': aoi.wkt, 'fromDate': start.isoformat() + 'T00:00:00Z',
        'toDate': end.isoformat() + 'T23:59:59Z', 'pageSize': 100, 'minOverlapRatio': .01,
        'openData': request.market == 'open'})
    records = raw.get('archives', [])
    # Bounded paging: keep source completeness visible without issuing unbounded requests.
    next_page = raw.get('nextPage')
    pages = 1
    while next_page and pages < 3:
        raw = provider('/archives?' + urlencode({'page': next_page}))
        records += raw.get('archives', [])
        next_page = raw.get('nextPage')
        pages += 1
    offers, skipped, seen = [], 0, set()
    for item in records:
        try:
            offer = normalize_archive(item, aoi, str(event))
            if offer and offer['id'] not in seen:
                offers.append(offer)
                seen.add(offer['id'])
        except (ValueError, KeyError, TypeError, GEOSException):
            skipped += 1
    return persist('catalog', {'scene_id': request.scene_id, 'source': 'skyfi', 'normalization_version': 2,
        'captured_at': now(), 'market': request.market, 'currency': 'USD', 'bbox': bounds,
        'area_km2': round(area(aoi), 4), 'event_date': str(event), 'from_date': str(start), 'to_date': str(end),
        'offers': offers, 'truncated': bool(next_page), 'skipped': skipped, 'pages': pages,
        'source_url': SKYFI + '/archives',
        'disclaimer': 'Предварительные цены реального каталога SkyFi. Цена заказа и пригодность для анализа требуют проверки.'})


def live_sources():
    """Search and normalize functions of the catalogs that return roubles or free data, and their notes."""
    from . import copernicus, roscosmos, scanex
    return {
        'roscosmos': ([(roscosmos.search_optical, roscosmos.normalize_optical), (roscosmos.search_sar, roscosmos.normalize_sar)],
                      roscosmos.OPTICAL_SEARCH,
                      'Каталог Геопортала Роскосмоса. Цена — оценка по формуле ПП РФ № 840 со сценарной ставкой кейса; '
                      'цену поставщика и ссылку на оплату возвращает Order API при заказе.'),
        'scanex': ([(scanex.search, scanex.normalize)], scanex.SEARCH,
                   'Каталог СКАНЭКС «Космоснимки — Поиск» без учётной записи (интерфейс публичного сайта). '
                   'Цена — оценка по формуле ПП РФ № 840 со сценарной ставкой кейса, цену поставщика даёт СКАНЭКС '
                   'по заявке; разрешение номинальное по спутнику.'),
        'free': ([(copernicus.search, copernicus.normalize)], copernicus.SEARCH,
                 'Бесплатные Sentinel-1 и Sentinel-2 из Copernicus Data Space: поиск без ключа, скачивание после '
                 'бесплатной регистрации.'),
    }


def search_live(request, aoi, bounds, event, start, end):
    """Russian and free catalogs in the common offer shape, prices in roubles."""
    searchers, source_url, disclaimer = live_sources()[request.source]
    on = today()
    offers, skipped, seen = [], 0, set()
    for search_fn, normalize in searchers:
        for item in search_fn(aoi, start, end):
            try:
                offer = normalize(item, aoi, str(event), on)
                if offer and offer['id'] not in seen:
                    offers.append(offer)
                    seen.add(offer['id'])
            except (ValueError, KeyError, TypeError, GEOSException):
                skipped += 1
    return persist('catalog', {'scene_id': request.scene_id, 'source': request.source, 'normalization_version': 2,
        'captured_at': now(), 'market': request.market, 'currency': 'RUB', 'bbox': bounds,
        'area_km2': round(area(aoi), 4), 'event_date': str(event), 'from_date': str(start), 'to_date': str(end),
        'offers': offers, 'truncated': False, 'skipped': skipped, 'pages': 1, 'source_url': source_url,
        'disclaimer': disclaimer})


@router.get('/catalogs/{catalog_id}')
def get_catalog(catalog_id: str):
    return read(catalog_id)


def calculate(catalog, request):
    offers = copy.deepcopy(catalog['offers'])
    by_id = {o['id']: o for o in offers}
    if len(set(request.selected_ids)) != len(request.selected_ids) or set(request.selected_ids) - by_id.keys():
        raise HTTPException(422, 'Неизвестный или повторный снимок')
    target = box(*catalog['bbox'])
    total_area = area(target)
    for o in offers:
        reasons = []
        if (o['resolution_m'] or math.inf) > request.resolution_m:
            reasons.append('Недостаточная детализация')
        cloud = o.get('cloud_percent')
        if o.get('product_type') != 'SAR' and request.max_cloud_percent < 100:
            if cloud is None:
                reasons.append('Облачность неизвестна')
            elif cloud > request.max_cloud_percent:
                reasons.append('Облачность выше порога')
        if o.get('order_issue'):
            reasons.append(o['order_issue'])
        o['filter_reasons'] = reasons
        o['within_budget'] = o['price'] <= request.budget
        o['eligible'] = not reasons
    eligible = [o for o in offers if o['eligible']]
    # Stable ordering makes equal-cost recommendations reproducible.
    candidates = sorted(eligible, key=lambda o: (o['price'], -o['coverage_percent']))
    geometries = {o['id']: shape(o['coverage_geometry']) for o in candidates}
    choices = []
    for o in candidates:
        choices.append({'ids': [o['id']], 'cost': o['price'], 'coverage_percent': o['coverage_percent']})
    # Exact comparisons among one- and two-image portfolios in this catalog.
    for left, right in combinations(candidates, 2):
        if left['coverage_percent'] >= 99.999 or right['coverage_percent'] >= 99.999:
            continue
        covered = min(100, area(geometries[left['id']].union(geometries[right['id']])) / total_area * 100)
        choices.append({'ids': [left['id'], right['id']], 'cost': round(left['price'] + right['price'], 2), 'coverage_percent': round(covered, 2)})
    complete = [c for c in choices if c['coverage_percent'] + .001 >= request.target_coverage]
    minimum_full = min((c['cost'] for c in complete), default=None)
    affordable = [c for c in choices if c['cost'] <= request.budget]
    full_affordable = [c for c in affordable if c['coverage_percent'] + .001 >= request.target_coverage]
    recommendation = min(full_affordable, key=lambda c: (c['cost'], len(c['ids']), -c['coverage_percent']), default=None)
    if recommendation is None:
        recommendation = max(affordable, key=lambda c: (c['coverage_percent'], -c['cost'], -len(c['ids'])), default=None)
    picked_ids = request.selected_ids or (recommendation['ids'] if recommendation else [])
    picked = [by_id[i] for i in picked_ids]
    union = unary_union([shape(o['coverage_geometry']) for o in picked]) if picked else None
    covered_area = area(union) if union is not None else 0
    cost = round(sum(o['price'] for o in picked), 2) if picked else None
    frontier, best = [], -1
    for c in sorted(choices, key=lambda x: (x['cost'], -x['coverage_percent'])):
        if c['coverage_percent'] > best + .001:
            frontier.append(c)
            best = c['coverage_percent']
    return {'catalog_id': catalog['id'], 'request': request.model_dump(), 'offers': offers,
        'recommendation': recommendation, 'frontier': frontier, 'eligible_count': len(eligible),
        'minimum_full_cost': minimum_full, 'budget_gap': max(0, minimum_full - request.budget) if minimum_full is not None else None,
        'selected_ids': picked_ids, 'cost': cost, 'coverage_km2': round(covered_area, 3),
        'coverage_percent': round(min(100, covered_area / total_area * 100), 2),
        'uncovered_km2': round(max(0, total_area - covered_area), 3),
        'overlap_km2': round(max(0, sum(o['coverage_km2'] for o in picked) - covered_area), 3),
        'geometry': mapping(union) if union is not None else None,
        'price_verified': bool(picked) and all(o['quote_validated'] for o in picked),
        'status': 'empty' if not picked else 'estimate', 'calculated_at': now(),
        'method': 'Сравнение одиночных снимков и пар по геометрическому покрытию территории. Облачность указана для всего кадра; полезность в каждой точке не проверена.'}


@router.post('/plan')
def plan(request: Plan):
    return calculate(read(request.catalog_id), request)


@router.post('/catalogs/{catalog_id}/quotes/{offer_id}')
def validate_quote(catalog_id: str, offer_id: str):
    catalog = read(catalog_id)
    offer = next((o for o in catalog['offers'] if o['id'] == offer_id), None)
    if not offer:
        raise HTTPException(404, 'Снимок не найден')
    if offer.get('order_issue'):
        raise HTTPException(422, offer['order_issue'])
    if catalog.get('source') == 'roscosmos':
        raise HTTPException(409, 'Цену Роскосмоса подтверждает Order API Геопортала при создании заказа: он выставляет '
                                 'сумму по ПП № 840 и ссылку на оплату. Сервис заказов не создаёт.')
    if catalog.get('source') != 'skyfi':
        raise HTTPException(409, 'Цену этого источника подтверждает поставщик по заявке; бесплатные данные стоят 0.')
    quote = provider('/order-archive/validate', {'aoi': shape(offer['geometry']).wkt, 'archiveId': offer['archive_id']})
    cents = quote.get('estimatedCostCents')
    if type(cents) is not int or cents < 0:
        raise HTTPException(502, 'SkyFi не вернул подтверждённую оценку цены')
    offer.update(price=cents / 100, price_status='provider_estimate', quote_validated=True,
                 quote_at=now(), price_source=SKYFI + '/order-archive/validate')
    return persist('catalog', {**catalog, 'parent_id': catalog_id})


@router.post('/decisions')
def save_decision(request: Plan):
    catalog = read(request.catalog_id)
    result = calculate(catalog, request)
    if not result['selected_ids']:
        raise HTTPException(422, 'Выберите снимки для сравнения')
    return persist('decision', {'catalog': catalog, 'plan': result})


@router.get('/decisions/{decision_id}/export')
def export(decision_id: str):
    decision = read(decision_id, 'decision')
    p, catalog = decision['plan'], decision['catalog']
    selected = [o for o in p['offers'] if o['id'] in p['selected_ids']]
    esc = html.escape
    rub = catalog.get('currency') == 'RUB'
    money = (lambda v: f'{v:,.2f} ₽'.replace(',', ' ')) if rub else (lambda v: f'${v:.2f}')
    source_name = {'roscosmos': 'Геопортал Роскосмоса, API ЦБГД', 'scanex': 'СКАНЭКС «Космоснимки — Поиск»',
                   'free': 'Copernicus Data Space STAC'}.get(catalog.get('source'), 'SkyFi API')
    currency_note = (catalog['disclaimer'] if rub else
                     'Цены в долларах США из коммерческого каталога SkyFi. Это отдельный подбор снимков, а не расчёт по ПП № 840.')
    cards = ''.join('<article>' + (f'<img src="{esc(o["preview_url"], quote=True)}" alt="Превью поставщика">' if o['preview_url'] else '') +
        f'<h2>{esc(o["title"])} · {esc(o["capture_date"])}</h2><p>{esc(o["provider"])} · {(format(o["resolution_m"], ".2f") + " м") if o["resolution_m"] else "разрешение н/д"} · {o["coverage_percent"]:.1f}% территории</p>' +
        f'<b>{money(o["price"])}</b><p>Archive ID: {esc(o["id"])}</p></article>' for o in selected)
    report = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><title>FloodValue · сравнение снимков</title>
<style>body{{font:16px system-ui;max-width:1080px;margin:40px auto;color:#172d34;padding:24px}}.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:24px}}img{{width:100%;height:260px;object-fit:contain;background:#15262d}}article{{border-top:2px solid #317b7c;padding:20px 0}}h1{{font-size:36px}}b{{font-size:26px}}</style>
<h1>Снимки для территории {esc(catalog['scene_id'])}</h1><p>Источник: {source_name} · каталог получен {esc(catalog['captured_at'])}</p>
<p>Стоимость по каталогу: <b>{money(p['cost'])}</b> · покрытие {p['coverage_percent']:.1f}% · {p['coverage_km2']:.2f} км²</p>
<p>{currency_note}</p>
<p>{esc(catalog['disclaimer'])}</p><p>Исходная сцена паводка: {esc(catalog['event_date'])}. Снимки другой даты не подтверждают состояние территории в этот день.</p><div class="grid">{cards}</div><p>{esc(p['method'])}</p></html>'''
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('comparison.html', report)
        archive.writestr('decision.json', json.dumps(decision, ensure_ascii=False, indent=2))
        archive.writestr('coverage.geojson', json.dumps({'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': o['coverage_geometry'], 'properties': {'id': o['id'], 'price': o['price'], 'currency': o.get('currency', 'USD'), 'date': o['capture_date']}}
            for o in selected]}, ensure_ascii=False))
    data.seek(0)
    return StreamingResponse(data, media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="imagery-{decision_id}.zip"'})
