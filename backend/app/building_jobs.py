"""Resumable source loading, independent of the HTTP connection or rate edits."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import uuid
import logging
import time

from fastapi import HTTPException
from shapely.geometry import box

from . import building_sources as sources, catalog

_POOL = ThreadPoolExecutor(max_workers=2)
_LOCK = Lock()
_JOBS = {}


def status(job_id):
    with _LOCK:
        if job_id not in _JOBS:
            raise HTTPException(404, "Загрузка не найдена. Нажмите «Повторить загрузку».")
        return dict(_JOBS[job_id])


def start(scene_id, source):
    info = catalog.metadata(scene_id)
    if source not in {"auto", "osm", "paiporta"}:
        raise ValueError("Неизвестный источник контуров")
    if source != 'paiporta':
        sources.validate_area(info['bbox'])
    with _LOCK:
        for job in _JOBS.values():
            if job['scene_id'] == scene_id and job['source'] == source and job['status'] != 'error':
                return dict(job)
        if sum(j['status'] in {'queued', 'loading'} for j in _JOBS.values()) >= 4:
            raise HTTPException(429, "Загружаются другие области. Повторите через минуту.")
        for key in list(_JOBS):
            if len(_JOBS) < 32:
                break
            if _JOBS[key]['status'] in {'ready', 'error'}:
                del _JOBS[key]
        job_id = uuid.uuid4().hex[:16]
        _JOBS[job_id] = dict(job_id=job_id, scene_id=scene_id, source=source, status='queued', progress=0)
    _POOL.submit(_work, job_id, info)
    return status(job_id)


def _work(job_id, info):
    from .buildings import load_source
    area = sources.area_m2(box(*info['bbox']))
    covered = 0.0
    def progress(bounds, count):
        nonlocal covered
        with _LOCK:
            covered += sources.area_m2(box(*bounds))
            _JOBS[job_id]['progress'] = max(_JOBS[job_id]['progress'], min(99, round(covered / area * 100)))
    with _LOCK:
        _JOBS[job_id]['status'] = 'loading'
        job = dict(_JOBS[job_id])
    try:
        deadline = time.monotonic() + sources.OSM_LOAD_SECONDS
        for attempt in range(1, 4):
            covered = 0.0
            with _LOCK:
                _JOBS[job_id]['attempt'] = attempt
            try:
                data = load_source(job['scene_id'], job['source'], progress=progress, deadline=deadline)
                break
            except sources.OSMUnavailable:
                if attempt == 3 or time.monotonic() >= deadline:
                    raise
                logging.getLogger(__name__).warning("Building job %s: retrying unfinished tiles, attempt %s", job_id, attempt + 1)
                time.sleep(attempt * 2)
        identifier = sources.save(data)
        with _LOCK:
            _JOBS[job_id].update(status='ready', progress=100, catalog_id=identifier, count=len(data['features']))
    except Exception as error:
        logging.getLogger(__name__).exception("Building job %s for scene %s failed", job_id, job["scene_id"])
        with _LOCK:
            _JOBS[job_id].update(status='error', error=str(error) if isinstance(error, ValueError) else 'Не удалось загрузить здания. Повторите загрузку — полученные части сохранены.')
