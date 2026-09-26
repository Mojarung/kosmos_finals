"""A project-linked SkyFi shortlist. It never places or implies a purchase."""
from __future__ import annotations

import csv
import io
import json
import uuid
import zipfile
from itertools import combinations

from fastapi.responses import StreamingResponse
from pydantic import Field
from shapely.geometry import Point, shape

from . import procurement, projects


class Input(procurement.Plan):
    deadline_hours: int = Field(default=24, ge=1, le=168)
    target_el_percent: float = Field(default=95, gt=0, le=100)


def _inventory(project_id: str) -> tuple[dict, list[dict]]:
    with projects._connect() as db:
        project = dict(projects._project(db, project_id))
        assets = [json.loads(row['payload']) for row in db.execute(
            'SELECT payload FROM assets WHERE project_id=? ORDER BY id', (project_id,))]
    if not assets:
        raise ValueError('Сначала импортируйте объекты проекта.')
    return project, assets


def preview(project_id: str, request: Input) -> dict:
    project, assets = _inventory(project_id)
    catalog = procurement.read(request.catalog_id)
    return preview_inventory(project, assets, catalog, request)


def preview_inventory(project: dict, assets: list[dict], catalog: dict, request: Input) -> dict:
    """Shared shortlist for project points or actual building footprints."""
    project_id = project['id']
    if catalog['scene_id'] != project['scene_id']:
        raise ValueError('Каталог относится к другому снимку проекта.')
    currency = catalog.get('currency')
    if currency not in {'USD', 'RUB'}:
        raise ValueError('Неподдерживаемая валюта каталога.')

    base = procurement.calculate(catalog, request)
    weights = [float(asset['expected_loss_rub'] or 0) for asset in assets]
    total_el = round(sum(weights), 2)
    masks: dict[str, int] = {}
    footprints = [shape(asset['geometry']) if asset.get('geometry') else Point(asset['longitude'], asset['latitude']) for asset in assets]
    for offer in base['offers']:
        geometry = shape(offer['coverage_geometry'])
        mask = sum(1 << index for index, asset in enumerate(assets)
                   if geometry.covers(footprints[index]))
        masks[offer['id']] = mask
        ids = [asset['id'] for index, asset in enumerate(assets) if mask & (1 << index)]
        covered = round(sum(weights[index] for index in range(len(assets)) if mask & (1 << index)), 2)
        offer['covered_asset_ids'] = ids
        offer['covered_expected_loss_rub'] = covered
        offer['covered_el_percent'] = round(covered / total_el * 100, 1) if total_el else None
        offer['timely'] = offer.get('delivery_hours') is not None and offer['delivery_hours'] <= request.deadline_hours
        offer['project_eligible'] = offer['eligible'] and offer['timely']

    viable = [offer for offer in base['offers'] if offer['project_eligible'] and masks[offer['id']]]
    cache: dict[int, tuple[float, int]] = {}

    def coverage(mask: int) -> tuple[float, int]:
        if mask not in cache:
            cache[mask] = (round(sum(weight for i, weight in enumerate(weights) if mask & (1 << i)), 2),
                           mask.bit_count())
        return cache[mask]

    choices = []
    for group in [*( (offer,) for offer in viable), *combinations(viable, 2)]:
        cost = round(sum(offer['price'] for offer in group), 2)
        if cost > request.budget:
            continue
        mask = 0
        for offer in group:
            mask |= masks[offer['id']]
        covered_el, covered_count = coverage(mask)
        if covered_count:
            choices.append({'ids': [offer['id'] for offer in group], 'cost': cost,
                            'covered_expected_loss_rub': covered_el, 'covered_asset_count': covered_count,
                            'covered_el_percent': round(covered_el / total_el * 100, 1) if total_el else None})
    target = [row for row in choices if total_el and row['covered_expected_loss_rub'] >=
              total_el * request.target_el_percent / 100]
    if target:
        recommendation = min(target, key=lambda row: (row['cost'], len(row['ids']), -row['covered_asset_count']))
    else:
        recommendation = min(choices, key=lambda row: (-row['covered_expected_loss_rub'],
            -row['covered_asset_count'], row['cost'], len(row['ids'])), default=None)

    picked_ids = request.selected_ids or (recommendation['ids'] if recommendation else [])
    if picked_ids:
        geographic = procurement.calculate(catalog, request.model_copy(update={'selected_ids': picked_ids}))
        for field in ('coverage_km2', 'coverage_percent', 'uncovered_km2', 'overlap_km2', 'geometry'):
            base[field] = geographic[field]
    else:
        base.update(coverage_km2=0, coverage_percent=0, uncovered_km2=catalog['area_km2'],
                    overlap_km2=0, geometry=None)
    by_id = {offer['id']: offer for offer in base['offers']}
    picked = [by_id[identifier] for identifier in picked_ids]
    mask = 0
    blockers = []
    for offer in picked:
        mask |= masks[offer['id']]
        if not offer['eligible']:
            blockers.append(f"{offer['id']}: снимок не проходит технические фильтры")
        if not offer['timely']:
            blockers.append(f"{offer['id']}: получение позже {request.deadline_hours} ч или срок неизвестен")
    cost = round(sum(offer['price'] for offer in picked), 2) if picked else None
    if cost is not None and cost > request.budget:
        blockers.append('Цена превышает бюджет в ' + currency)
    if picked and not all(offer['quote_validated'] for offer in picked):
        blockers.append('Цена заказа ещё не подтверждена поставщиком')
    if picked and any(abs(offer.get('days_from_event') or 0) > 1 for offer in picked):
        blockers.append('Дата архива отличается от даты исходного события: снимок не подтверждает воду в тот день')
    covered_el, covered_count = coverage(mask)
    return {**base, 'project_id': project_id, 'project_name': project['name'],
        'scene_id': project['scene_id'], 'asset_count': len(assets),
        'estimated_asset_count': sum(a['expected_loss_rub'] is not None for a in assets),
        'total_expected_loss_rub': total_el, 'recommendation': recommendation,
        'selected_ids': picked_ids, 'cost': cost,
        'covered_asset_ids': [asset['id'] for index, asset in enumerate(assets) if mask & (1 << index)],
        'covered_asset_count': covered_count, 'covered_expected_loss_rub': covered_el,
        'covered_el_percent': round(covered_el / total_el * 100, 1) if total_el else None,
        'deadline_hours': request.deadline_hours, 'target_el_percent': request.target_el_percent,
        'currency': currency, 'budget': request.budget, ('budget_usd' if currency == 'USD' else 'budget_rub'): request.budget,
        'blockers': blockers, 'price_verified': bool(picked) and all(o['quote_validated'] for o in picked),
        'status': 'empty' if not picked else 'draft_quote_checked' if all(o['quote_validated'] for o in picked)
                  else 'draft_catalog_estimate',
        'interpretation': ('Covered EL is a priority for visual inspection, not avoided loss. '
                           'Historical archive imagery only describes its own capture date; no purchase is submitted.')}


def save(project_id: str, request: Input) -> dict:
    result = preview(project_id, request)
    if not result['selected_ids']:
        raise ValueError('Выберите хотя бы один снимок для черновика заявки.')
    project, assets = _inventory(project_id)
    catalog = procurement.read(request.catalog_id)
    selected = [offer for offer in result['offers'] if offer['id'] in result['selected_ids']]
    payload = {key: value for key, value in result.items() if key not in {'offers', 'frontier'}}
    payload.update({'asset_snapshot': assets, 'selected_offers': selected,
        'catalog_captured_at': catalog['captured_at'], 'catalog_source_url': catalog['source_url'],
        'catalog_id': catalog['id'], 'currency': catalog['currency'], 'purchase_submitted': False,
        'quote_note': 'Catalog estimate or price validation only; no order is placed.'})
    record = {'id': uuid.uuid4().hex[:12], 'created_at': projects._now(), **payload}
    with projects._connect() as db:
        projects._project(db, project_id)
        db.execute('INSERT INTO imagery_requests VALUES (?,?,?,?)',
                   (record['id'], project_id, record['created_at'], json.dumps(payload, ensure_ascii=False, allow_nan=False)))
        db.execute('UPDATE projects SET updated_at=? WHERE id=?', (projects._now(), project_id))
    return _summary(record)


def _summary(record: dict) -> dict:
    return {key: record.get(key) for key in ('id', 'created_at', 'project_id', 'scene_id', 'status',
        'catalog_id', 'catalog_captured_at', 'currency', 'cost', 'price_verified', 'purchase_submitted',
        'deadline_hours', 'covered_expected_loss_rub', 'covered_asset_count', 'covered_el_percent',
        'selected_ids', 'blockers')}


def export(project_id: str, request_id: str) -> StreamingResponse:
    with projects._connect() as db:
        projects._project(db, project_id)
        row = db.execute('SELECT payload, created_at FROM imagery_requests WHERE project_id=? AND id=?',
                         (project_id, request_id)).fetchone()
    if row is None:
        raise KeyError('Заявка не найдена')
    record = {'id': request_id, 'created_at': row['created_at'], **json.loads(row['payload'])}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('request.json', json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False))
        table = io.StringIO()
        writer = csv.writer(table)
        writer.writerow(['asset_id', 'name', 'longitude', 'latitude', 'p_water', 'value_rub',
                         'vulnerability', 'expected_loss_rub', 'covered_by_selected', 'value_source'])
        selected = set(record['covered_asset_ids'])
        for asset in record['asset_snapshot']:
            writer.writerow([asset['id'], asset['name'], asset['longitude'], asset['latitude'],
                             asset['p_water'], asset['value_rub'], asset['vulnerability'],
                             asset['expected_loss_rub'], asset['id'] in selected, asset['value_source']])
        archive.writestr('project_assets.csv', table.getvalue())
        archive.writestr('selected_offers.geojson', json.dumps({'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': offer['coverage_geometry'], 'properties': {
                'archive_id': offer['archive_id'], 'provider': offer['provider'], 'price': offer['price'], 'currency': record['currency'], **({'price_usd': offer['price']} if record['currency'] == 'USD' else {'price_rub': offer['price']}),
                'price_status': offer['price_status'], 'capture_date': offer['capture_date'],
                'delivery_hours': offer['delivery_hours']}} for offer in record['selected_offers']]}, ensure_ascii=False))
        archive.writestr('README.txt', 'Черновик заявки: закупка не выполнена. Валюта цены каждого предложения указана в выгрузке; расчёт A/B/C в RUB — отдельный сценарий.\nОхваченный EL — приоритет проверки, не предотвращённый ущерб. Архив другого дня не подтверждает воду в дату события.\n')
    output.seek(0)
    return StreamingResponse(output, media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="project-imagery-{request_id}.zip"'})
