"""Short-lived progress registry for long RTC downloads and tiled inference."""

from __future__ import annotations

import threading
import uuid

from .live import LiveError, prepare

_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def start(bounds: list[float], item_id: str) -> dict:
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "stage": "queued", "progress": 0.0,
           "message": "Готовим запрос к каталогу", "scene_id": None, "error": None}
    with _lock:
        _jobs[job_id] = job

    def update(stage: str, progress: float, message: str) -> None:
        with _lock:
            job.update(stage=stage, progress=round(progress, 3), message=message)

    def run() -> None:
        try:
            update("catalog", 0, "Проверяем дату и покрытие снимка")
            result = prepare(bounds, item_id, update)
            with _lock:
                job.update(stage="done", progress=1.0, message="Снимок готов",
                           scene_id=result["chip_id"], valid_pixels=result["valid_pixels"],
                           total_pixels=result["width"] * result["height"],
                           pixel_size_m_approx=result["pixel_size_m_approx"])
        except Exception as error:
            with _lock:
                job.update(stage="error", error=str(error) if isinstance(error, LiveError) else "Ошибка обработки снимка",
                           message="Не удалось подготовить снимок")

    threading.Thread(target=run, name=f"live-{job_id}", daemon=True).start()
    return dict(job)


def get(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
