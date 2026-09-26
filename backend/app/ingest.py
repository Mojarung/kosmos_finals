"""Live inference of any official Sen1Floods11 hand-labeled chip.

One job = download S1 (and the manual label) from the official bucket or a local
cache, run the frozen ensemble as a separate process, build the scene with the
same code as the demo scenes, and register it in data/runtime. Jobs run one at a
time; a repeated request for the same chip returns the running job.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import catalog
from .chips import chip_record
from .scene_builder import SceneSpec, build_scene, load_source_meta

ROOT = Path(__file__).resolve().parents[2]
BUCKET_URL = "https://storage.googleapis.com/sen1floods11/v1.1/data/flood_events/HandLabeled"
DOWNLOAD_MANIFEST = ROOT / "ml" / "data" / "sen1floods11" / "download_manifest.json"
LIVE_SEED = 2026
MODEL_TIMEOUT_S = 600
DOWNLOAD_TIMEOUT_S = 60
STAGE_MESSAGES = {
    "queued": "В очереди",
    "download": "Скачиваем S1, ручную метку и постоянную воду JRC из Sen1Floods11 v1.1",
    "inference": "Ансамбль U-Net считает вероятность, маску и разброс",
    "prepare": "Размещаем 10 объектов, строим зоны и слои карты",
}
FINAL_STAGES = {"done", "error"}

_jobs: dict[str, dict] = {}
_by_chip: dict[str, str] = {}
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cache_dirs() -> list[Path]:
    configured = os.environ.get("FLOODVALUE_S1_CACHE")
    if configured:
        return [Path(item) for item in configured.split(os.pathsep) if item]
    return [ROOT / "ml" / "data" / "sen1floods11" / "data" / "flood_events" / "HandLabeled"]


def infer_command() -> list[str]:
    configured = os.environ.get("FLOODVALUE_INFER_CMD")
    if configured:
        return shlex.split(configured, posix=os.name != "nt")
    for candidate in (ROOT / "ml" / ".venv" / "Scripts" / "floodvalue.exe", ROOT / "ml" / ".venv" / "bin" / "floodvalue"):
        if candidate.exists():
            return [str(candidate), "infer"]
    if (ROOT / "ml" / "src" / "floodvalue" / "cli.py").exists():
        return [sys.executable, "-m", "floodvalue.cli", "infer"]
    return ["uv", "run", "--project", str(ROOT / "ml"), "floodvalue", "infer"]


def _fetch(chip_id: str, layer: str, target: Path) -> Path:
    """Copy <chip>_<layer>.tif from a local cache or download it from the official bucket."""
    name = f"{chip_id}_{layer}.tif"
    destination = target / name
    for base in cache_dirs():
        local = base / layer / name
        if local.exists():
            shutil.copyfile(local, destination)
            return destination
    with urllib.request.urlopen(f"{BUCKET_URL}/{layer}/{name}", timeout=DOWNLOAD_TIMEOUT_S) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)
        sidecar = {"generation": response.headers.get("x-goog-generation"),
                   "updated": response.headers.get("Last-Modified")}
    (target / f"{name}.meta.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return destination


def _run_model(s1_path: Path, out_dir: Path) -> None:
    command = [*infer_command(), "--s1", str(s1_path), "--out", str(out_dir)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=MODEL_TIMEOUT_S, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                            creationflags=flags)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise RuntimeError(" ".join(tail) or f"exit code {result.returncode}")


def _source_meta(folder: Path) -> dict:
    meta = load_source_meta(DOWNLOAD_MANIFEST)
    for sidecar in folder.glob("*.meta.json"):
        meta[sidecar.name.removesuffix(".meta.json")] = json.loads(sidecar.read_text(encoding="utf-8"))
    return meta


def _snapshot(job: dict) -> dict:
    return {**job, "timings": dict(job["timings"])}


def _update(job_id: str, **changes) -> None:
    with _lock:
        _jobs[job_id].update(changes)


def _stage(job_id: str, stage: str, started: float) -> float:
    now = time.perf_counter()
    with _lock:
        job = _jobs[job_id]
        if job["stage"] in STAGE_MESSAGES and job["stage"] != "queued":
            job["timings"][job["stage"]] = round(now - started, 2)
        job.update(stage=stage, message=STAGE_MESSAGES[stage])
    return now


def _work(job_id: str, record: dict) -> None:
    chip_id = record["chip_id"]
    begin = time.perf_counter()
    _update(job_id, started_at=_now())
    try:
        with tempfile.TemporaryDirectory(prefix=f"floodvalue-{chip_id}-") as scratch:
            scratch = Path(scratch)
            mark = _stage(job_id, "download", begin)
            try:
                s1_path = _fetch(chip_id, "S1Hand", scratch)
            except (OSError, urllib.error.URLError) as error:
                raise _JobError(f"Не удалось скачать S1: {error}") from error
            try:
                label_path: Path | None = _fetch(chip_id, "LabelHand", scratch)
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise _JobError(f"Не удалось скачать ручную метку: {error}") from error
                label_path = None
            except OSError as error:
                raise _JobError(f"Не удалось скачать ручную метку: {error}") from error
            # Without JRC the scene still builds; the map then shows all water, and says so.
            try:
                jrc_path: Path | None = _fetch(chip_id, "JRCWaterHand", scratch)
            except (OSError, urllib.error.URLError):
                jrc_path = None
            mark = _stage(job_id, "inference", mark)
            inference_dir = scratch / "inference"
            try:
                _run_model(s1_path, inference_dir)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                raise _JobError(f"Модель не отработала: {error}") from error
            mark = _stage(job_id, "prepare", mark)
            staging = catalog.RUNTIME_DIR / f".{chip_id}.partial"
            final = catalog.RUNTIME_DIR / chip_id
            shutil.rmtree(staging, ignore_errors=True)
            spec = SceneSpec(chip_id=chip_id, event=record["event"], observed_at=record["observed_at"],
                             seed=LIVE_SEED, s1_path=s1_path, label_path=label_path,
                             inference_dir=inference_dir, split_part=record["split_part"], origin="live",
                             source_meta=_source_meta(scratch), jrc_path=jrc_path)
            try:
                row = build_scene(spec, staging)
            except (OSError, ValueError) as error:
                raise _JobError(f"Не удалось собрать сцену: {error}") from error
            shutil.rmtree(final, ignore_errors=True)
            os.replace(staging, final)
            catalog.register_runtime_scene(row)
        with _lock:
            job = _jobs[job_id]
            job["timings"]["prepare"] = round(time.perf_counter() - mark, 2)
            total = round(time.perf_counter() - begin, 1)
            job.update(stage="done", scene_id=chip_id, finished_at=_now(),
                       message=f"Готово за {total:.1f} с".replace(".", ","))
    except _JobError as error:
        _update(job_id, stage="error", message=str(error), finished_at=_now())
    except Exception as error:  # noqa: BLE001 - the job must end in a readable state
        _update(job_id, stage="error", message=f"Сбой расчёта: {error}", finished_at=_now())


class _JobError(Exception):
    """Failure with an operator-facing message."""


def start(chip_id: str) -> dict:
    """Queue a live inference job; KeyError if the chip is not an official hand-labeled chip."""
    record = chip_record(chip_id)
    with _lock:
        if chip_id in catalog.known_scene_ids():
            return {"id": None, "chip_id": chip_id, "stage": "done", "scene_id": chip_id,
                    "message": "Сцена уже рассчитана", "timings": {}}
        running = _by_chip.get(chip_id)
        if running and _jobs[running]["stage"] not in FINAL_STAGES:
            return _snapshot(_jobs[running])
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"id": job_id, "chip_id": chip_id, "event": record["event"],
                         "split_part": record["split_part"], "stage": "queued",
                         "message": STAGE_MESSAGES["queued"], "created_at": _now(), "started_at": None,
                         "finished_at": None, "timings": {}, "scene_id": None}
        _by_chip[chip_id] = job_id
        snapshot = _snapshot(_jobs[job_id])
    _executor.submit(_work, job_id, record)
    return snapshot


def get(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return _snapshot(job) if job else None
