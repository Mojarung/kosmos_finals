# FloodValue: финальный рывок до сдачи 26.09 23:00

> **Для агента-исполнителя:** обязательный под-скилл — superpowers:executing-plans (нативно) или superpowers:subagent-driven-development. Шаги с чекбоксами `- [ ]`.

**Цель:** закрыть оставшиеся пункты ТЗ и критериев (живой inference любого официального чипа из интерфейса, строгий p в точке объекта, прокси-проверка по ТЗ, разбор ошибок, план пилота, воспроизводимость) и привести дизайн Vue-интерфейса к уровню защиты.

**Архитектура:** ядро построения сцены выносится из `scripts/prepare_demo.py` в `backend/app/scene_builder.py` и используется и скриптом, и API. Новый модуль `backend/app/ingest.py` ставит задание «скачать S1 → запустить замороженный ансамбль отдельным процессом → собрать сцену» и складывает результат в `data/runtime/`, каталог сцен объединяет демо и живые сцены. Фронтенд остаётся на Vue 3 + Vite (решение пользователя 25.09): правки в `App.vue`, `GlobeMap.vue`, `style.css`, новый компонент `ChipIngest.vue`.

**Стек:** FastAPI, numpy/rasterio/Pillow/pyproj (extra `demo`), uv; ML-пакет `ml/` (torch, segmentation-models-pytorch); Vue 3, MapLibre GL 6, @lucide/vue; Docker Compose.

**Основание:** `docs/source/FloodValue_Постановка_КосмоХакатон.pdf`, `docs/source/FloodValue_Критерии_КосмоХакатон.pdf`, аудит текущего состояния в `docs/08-status.md`, разговор с пользователем 25.09 (Vue, план-максимум, GitVerse в последние 2–3 часа).

## Глобальные ограничения

- Фронтенд: только Vue 3 + Vite, без перехода на React и без новых UI-зависимостей.
- Зависимости Python: только через `uv add` / `uv sync`; `uv.lock` коммитится.
- Эталонные демо-растры `data/demo/*/*.tif|*.f32|*.png` не меняются: пересборка метаданных идёт из уже закоммиченных растров (иначе поплывут SHA-256 манифеста на другой платформе).
- Порог модели 0,41, порог baseline VH < −23,6 dB, модель и калибровка заморожены: никакой подгонки на новых чипах.
- EL = p × V × q; p берётся из `flood_probability.tif` **в пикселе объекта**; объект не исключается и EL не обнуляется из-за маски.
- Метки `base_rate_status=scenario`, «охват EL ≠ предотвращённый ущерб», «вода, а не временное затопление» сохраняются везде, где есть числа.
- Тексты интерфейса на русском, без длинного тире `—` и `–` как разделителя (стоп-лист пользователя); для пустых значений `н/д`.
- Локальный запуск: API на 18000, Vite на 5180 (порт 8000 занят другим проектом).
- Коммиты — Conventional Commits, ветка `feat/floodvalue-final`, в `main` напрямую не пушить; пуш и PR — только с разрешения пользователя.

## Фокус ревью (что тесты задач могут пропустить)

1. Официальный чип без ручной метки или с меткой, полностью `-1`: сцена должна собраться, качество «нет метки», прокси-проверка «не проверена», без падения. Тест — в задаче 3.
2. Повторный запуск того же чипа, пока идёт первое задание, и запуск уже готового чипа: не два процесса inference, а ссылка на существующее задание или готовую сцену. Тест — в задаче 4.
3. Нет сети или бакет GCS недоступен: задание завершается статусом `error` с понятным текстом, API не зависает, интерфейс показывает причину. Тест — в задаче 4.
4. Чип из train/valid: интерфейс и метаданные помечают, что метрики на нём не независимы. Тест — в задаче 3.
5. Объект на границе зоны: `polygon.covers(point)` включает границу — покрытие не теряется. Тест — в задаче 7.

---

## Структура файлов

| Файл | Ответственность |
|---|---|
| `backend/app/scene_builder.py` (новый) | Из S1 + растров inference + метки собрать папку сцены: слои PNG, `.bin/.f32`, портфель 10 объектов, метаданные |
| `backend/app/ingest.py` (новый) | Задания живого inference: скачивание, запуск модели, сборка сцены, статусы |
| `backend/app/chips.py` (новый) | Каталог 446 официальных размеченных чипов с событием, датой и частью разбиения |
| `backend/app/catalog.py` | Объединение демо- и живых сцен, `scene_dir()`, сброс кэшей |
| `backend/app/analysis.py` | Строки каталога зон с ценой, сроком, покрытием и причиной выбора/отказа |
| `backend/app/evidence.py` | Прокси-проверка по ТЗ: метка y∈{0;1} в пикселе объекта + сводка test_core |
| `backend/app/exporter.py` | `probability_rule`, новые колонки, кроссплатформенные команды, source_manifest с датами |
| `backend/app/main.py` | `GET /api/chips`, `POST /api/scenes`, `GET /api/jobs/{id}`, `GET /api/evidence/{figure}` |
| `scripts/prepare_demo.py` | Тонкая обёртка над `scene_builder` для трёх демо-сцен |
| `scripts/build_chip_catalog.py` (новый) | Генерация `data/catalog/sen1floods11_handlabeled.json` |
| `scripts/check_reproducibility.py` (новый) | Повторный inference официального чипа и сравнение с эталоном (маски точно, |Δp| ≤ 1e-6) |
| `ml/src/floodvalue/error_analysis.py` (новый) + CLI | Разбор ошибок по событиям, яркости VH, JRC, краю воды; прокси-проверка EL на 1000 точках test_core |
| `frontend/src/ChipIngest.vue` (новый) | Выбор официального чипа и прогресс живого inference |
| `frontend/src/App.vue`, `GlobeMap.vue`, `style.css` | Дизайн-правки, таблица зон, шаги, слой-переключатель на карте |
| `backend/Dockerfile`, `compose.yaml`, `.dockerignore` | CPU-inference в образе, том `data/runtime` |
| `docs/09-pilot-plan.md` (новый), `docs/08-status.md`, `README.md`, `ml/REPORT.md` | План пилота, статус, инструкции, разбор ошибок |

---

### Task 1: Ветка и базовая проверка

**Файлы:** нет изменений.

- [ ] **Шаг 1:** `git switch -c feat/floodvalue-final` от актуального `main` (после `git pull --ff-only`).
- [ ] **Шаг 2:** Базовые проверки, результат записать в журнал задачи:

```powershell
uv run --project backend pytest backend/tests -q        # ожидается 5 passed
uv run --project ml pytest ml/tests -q                  # ожидается 21 passed
Set-Location frontend; npm run build; Set-Location ..   # ожидается успешная сборка
```

---

### Task 2: Ядро сцены `scene_builder` + строгий p в пикселе

**Файлы:**
- Создать: `backend/app/scene_builder.py`
- Изменить: `scripts/prepare_demo.py` (обёртка), `data/demo/*/metadata.json` (пересборка), `backend/tests/test_scene_builder.py` (новый)

**Интерфейсы:**
- Производит: `SceneSpec`, `build_scene(spec: SceneSpec, out_dir: Path) -> dict` (возвращает строку каталога `{chip_id, event, observed_at, bbox, model_water_area_km2, reference_water_area_km2, model_iou, split_part, origin}`), константы `ASSET_TYPES`, `MODEL_THRESHOLD = 0.41`, `INFERENCE_FILES`.
- Каждый объект в `metadata["assets"]`: `p_flood` = значение пикселя, `uncertainty` = значение пикселя, `p_window3`/`uncertainty_window3` = среднее валидных 3×3, `row`, `col`, `status` (`ok`/`no_data`).

- [ ] **Шаг 1: Тест на синтетике** (`backend/tests/test_scene_builder.py`):

```python
import json
import numpy as np
import rasterio
from rasterio.transform import from_origin
from app.scene_builder import SceneSpec, build_scene, ASSET_TYPES

def _raster(path, array, dtype, nodata, count=1):
    profile = {"driver": "GTiff", "width": 512, "height": 512, "count": count, "dtype": dtype,
               "crs": "EPSG:4326", "transform": from_origin(5.0, 9.1, 0.0000898, 0.0000898), "nodata": nodata}
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array if count > 1 else array[None])

def _synthetic_chip(tmp_path, with_label=True):
    rng = np.random.default_rng(0)
    vv = rng.normal(-12, 3, (512, 512)).astype("float32")
    vh = rng.normal(-20, 3, (512, 512)).astype("float32")
    vv[:, :40] = np.nan; vh[:, :40] = np.nan                     # полоса nodata
    prob = np.clip(rng.random((512, 512)), 0, 1).astype("float32")
    prob[:, :40] = -9999
    mask = np.where(prob >= 0.41, 1, 0).astype("uint8"); mask[:, :40] = 255
    unc = (rng.random((512, 512)) * 0.2).astype("float32"); unc[:, :40] = -9999
    base = (vh < -23.6).astype("uint8"); base[:, :40] = 255
    inf = tmp_path / "inf"; inf.mkdir()
    _raster(tmp_path / "X_1_S1Hand.tif", np.stack([vv, vh]), "float32", None, count=2)
    _raster(inf / "flood_probability.tif", prob, "float32", -9999)
    _raster(inf / "flood_mask.tif", mask, "uint8", 255)
    _raster(inf / "flood_uncertainty.tif", unc, "float32", -9999)
    _raster(inf / "baseline_mask.tif", base, "uint8", 255)
    label = None
    if with_label:
        label = tmp_path / "X_1_LabelHand.tif"
        _raster(label, np.where(prob > 0.5, 1, 0).astype("int16"), "int16", -1)
    return SceneSpec(chip_id="X_1", event="Тест", observed_at="2018-09-21", seed=7,
                     s1_path=tmp_path / "X_1_S1Hand.tif", label_path=label,
                     inference_dir=inf, split_part="train"), prob

def test_assets_use_pixel_probability_and_valid_area(tmp_path):
    spec, prob = _synthetic_chip(tmp_path)
    row = build_scene(spec, tmp_path / "out")
    meta = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert [a["class"] for a in meta["assets"]] == [t[0] for t in ASSET_TYPES]
    for asset in meta["assets"]:
        assert asset["col"] >= 40                                   # не в nodata
        assert asset["p_flood"] == round(float(prob[asset["row"], asset["col"]]), 5)
        assert asset["status"] == "ok"
    assert row["split_part"] == "train" and meta["split_part"] == "train"
    assert meta["metrics_independent"] is False                     # train-чип помечен

def test_scene_without_label_builds(tmp_path):
    spec, _ = _synthetic_chip(tmp_path, with_label=False)
    build_scene(spec, tmp_path / "out")
    meta = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert meta["model_quality"] is None and meta["reference_status"] == "no_manual_label"
    assert (tmp_path / "out" / "water_mask.bin").stat().st_size == 512 * 512
```

- [ ] **Шаг 2:** `uv run --project backend pytest backend/tests/test_scene_builder.py -q` → FAIL (`ModuleNotFoundError: app.scene_builder`).
- [ ] **Шаг 3: Реализация `backend/app/scene_builder.py`.** Перенести из `scripts/prepare_demo.py` функции `checksum`, `quality_metrics`, `render_layer`, логику `prepare_scene` в `build_scene(spec, out_dir)` со следующими отличиями:
  - `label_path` может быть `None`: `truth = np.full((512, 512), -1, np.int16)`, `reference_status = "no_manual_label"`, `model_quality/baseline_quality = None`, если валидных меток 0.
  - Объект: `p_flood = prob[row, col]`, `uncertainty = unc[row, col]` (пиксель, в котором стоит точка), плюс `p_window3`, `uncertainty_window3` (прежнее правило), `row`, `col`, `status`.
  - `split_part` и `metrics_independent = split_part in {"test_core", "bolivia", None}`; убрать `assert chip in per_chip` (для демо-сцен проверка остаётся в обёртке).
  - `source_manifest`: для S1 и метки `{name, url, sha256, version: "v1.1", layer, gcs_generation, published_at}`; `gcs_generation/published_at` из `spec.source_meta` (словарь из `download_manifest.json`, если есть), иначе `None`.
  - `probability_rule = "flood_probability.tif value at the asset pixel; 3x3 valid mean kept as robustness check"`.
- [ ] **Шаг 4:** Тесты задачи зелёные.
- [ ] **Шаг 5: Обёртка `scripts/prepare_demo.py`.** Оставить `SCENES`, `main()`; `prepare_scene()` вызывает `build_scene(SceneSpec(..., split_part="test_core"), OUTPUT / chip)`, проверка «чип в test_core» остаётся здесь. Импорт: `sys.path.insert(0, str(ROOT / "backend")); from app.scene_builder import SceneSpec, build_scene`. `source_meta` читать из `ml/data/sen1floods11/download_manifest.json`, если файл есть.
- [ ] **Шаг 6: Пересборка метаданных из эталонных растров.** Скопировать закоммиченные `data/demo/<chip>/{flood_probability,flood_mask,flood_uncertainty,baseline_mask}.tif` в `data/inference/<chip>/`, запустить `uv run --project backend python scripts/prepare_demo.py`, проверить `git diff --stat data/demo`: меняются только `metadata.json`; бинарные файлы и PNG без изменений.
- [ ] **Шаг 7:** Обновить ожидания в `backend/tests/test_planning.py`, если они завязаны на старые p (прогнать, поправить числа по фактическому выводу с пояснением в коммите).
- [ ] **Шаг 8: Коммит** `refactor(scene): extract scene builder, read p at asset pixel`.

---

### Task 3: Прокси-проверка по ТЗ

**Файлы:** `backend/app/evidence.py`, `backend/tests/test_planning.py`.

**Интерфейсы:** `asset_proxy_check(scene_id) -> dict` с полями `control_point_rule`, `n_points`, `n_skipped_invalid_label`, `mean_absolute_error_rub`, `bias_rub` (сумма EL − сумма EL_ref), `rows: [{asset_id, p_flood, y, el_rub, el_ref_rub}]`, `status`.

- [ ] **Шаг 1: Тест**

```python
def test_proxy_check_uses_binary_label_at_asset_pixel():
    from app.evidence import asset_proxy_check
    from app.catalog import metadata, mask
    result = asset_proxy_check("Nigeria_812045")
    info = metadata("Nigeria_812045")
    water = mask("Nigeria_812045", "water")
    for row in result["rows"]:
        asset = next(a for a in info["assets"] if a["id"] == row["asset_id"])
        y = water[asset["row"] * info["width"] + asset["col"]]
        assert y in (0, 1) and row["y"] == y
        assert row["el_ref_rub"] == round(y * asset["value_rub"] * asset["vulnerability"], 2)
    assert result["n_points"] + result["n_skipped_invalid_label"] == 10
```

- [ ] **Шаг 2:** FAIL → **Шаг 3:** реализовать (y = метка в пикселе объекта; невалидная метка → пропуск с подсчётом; при отсутствии метки у сцены `status = "not_checked_no_manual_label"`). Сводку по test_core из задачи 9 читать из `quality.json["proxy_test_core"]`, если есть.
- [ ] **Шаг 4:** Тесты зелёные. **Шаг 5: Коммит** `fix(evidence): proxy EL check with binary label at control points`.

---

### Task 4: Каталог официальных чипов и живой inference (API)

**Файлы:**
- Создать: `scripts/build_chip_catalog.py`, `data/catalog/sen1floods11_handlabeled.json`, `backend/app/chips.py`, `backend/app/ingest.py`, `backend/tests/test_ingest.py`
- Изменить: `backend/app/catalog.py`, `backend/app/main.py`, `backend/app/analysis.py`, `backend/app/exporter.py`, `.gitignore` (`data/runtime/`)

**Интерфейсы:**
- `chips.official_chips() -> list[dict]` — `{chip_id, event_key, event, observed_at, split_part}` (446 шт.).
- `catalog.scene_dir(scene_id) -> Path`, `catalog.refresh() -> None`, `catalog.RUNTIME_DIR`.
- `ingest.start(chip_id) -> dict` (задание), `ingest.get(job_id) -> dict | None`; стадии `queued → download → inference → prepare → done | error`; поля `{id, chip_id, stage, message, started_at, finished_at, timings, scene_id}`.
- HTTP: `GET /api/chips`, `POST /api/scenes {"chip_id": "..."}` → 202 + задание (или 200 + `scene_id`, если сцена уже есть), `GET /api/jobs/{id}`.
- Команда модели: env `FLOODVALUE_INFER_CMD` (строка, разбивается `shlex.split(posix=False)` на Windows); по умолчанию `ml/.venv/Scripts/floodvalue.exe infer` или `ml/.venv/bin/floodvalue infer`, если файл есть, иначе `uv run --project ml floodvalue infer`.
- Источник S1: сначала `FLOODVALUE_S1_CACHE` (по умолчанию `ml/data/sen1floods11/data/flood_events/HandLabeled`), иначе `https://storage.googleapis.com/sen1floods11/v1.1/data/flood_events/HandLabeled/{S1Hand|LabelHand}/<chip>_<layer>.tif`.
- Seed портфеля живой сцены: `2026` для любого чипа (правило записано в метаданных).

- [ ] **Шаг 1: Каталог.** `scripts/build_chip_catalog.py` читает `ml/artifacts/split.json` (части) и `ml/data/sen1floods11/Sen1Floods11_Metadata.geojson` (даты `s1_date`, `location`; Mekong ↔ Cambodia), пишет JSON. Названия событий по-русски: Bolivia→Боливия, Ghana→Гана, India→Индия, Mekong→Меконг · Камбоджа, Nigeria→Нигерия, Pakistan→Пакистан, Paraguay→Парагвай, Somalia→Сомали, Spain→Испания, Sri-Lanka→Шри-Ланка, USA→США. Проверка: 446 записей, все чипы из `split.json`.
- [ ] **Шаг 2: Тесты API и заданий** (`backend/tests/test_ingest.py`): monkeypatch `ingest._fetch` (копирует синтетические файлы из фикстуры задачи 2) и `ingest._run_model` (пишет растры); проверить: задание доходит до `done`; `GET /api/scenes` содержит новую сцену с `origin: "live"`; `GET /api/analysis?scene_id=X_1` отвечает 200; повторный `POST` во время работы возвращает то же задание; `POST` для готовой сцены — 200 и `scene_id`; `_fetch`, бросающий `OSError`, даёт `stage == "error"` и текст «Не удалось скачать S1: ...»; неизвестный чип — 404.
- [ ] **Шаг 3:** FAIL → **Шаг 4:** реализация: `ThreadPoolExecutor(max_workers=1)`, словарь заданий под `threading.Lock`, таймаут модели 600 с, запись во временную папку и атомарный `replace` в `data/runtime/<chip>`, дописывание `data/runtime/scenes.json`, `catalog.refresh()`.
- [ ] **Шаг 5:** Заменить все `DATA_DIR / scene_id` на `scene_dir(scene_id)` (`main.demo_asset`, `exporter.package`); `metadata()` читает из `scene_dir`.
- [ ] **Шаг 6:** Тесты зелёные, плюс ручной прогон на реальном чипе: `POST /api/scenes {"chip_id":"Bolivia_103757"}` → `done` за < 90 с, анализ и экспорт открываются, `validate_package.py` проходит.
- [ ] **Шаг 7: Коммит** `feat(ingest): run frozen ensemble on any official Sen1Floods11 chip`.

---

### Task 5: Интерфейс живого inference (`ChipIngest.vue`)

**Файлы:** создать `frontend/src/ChipIngest.vue`; изменить `App.vue`, `style.css`.

**Поведение:** в секции «События» кнопка «Добавить чип Sen1Floods11» раскрывает панель: поле поиска (фильтр по `chip_id` и событию), список до 8 совпадений с плашкой части разбиения (`test` зелёная, `train`/`valid` янтарная с подсказкой «метрики не независимы»), кнопка «Рассчитать». Прогресс — четыре шага «Скачивание S1 → Ансамбль U-Net → Объекты и зоны → Готово» с таймингами из задания, опрос `GET /api/jobs/{id}` раз в 800 мс; ошибка — текстом внутри панели с кнопкой «Повторить». По `done`: обновить список сцен, выбрать новую сцену, карта летит к чипу. У живой сцены в списке событий пометка «живой расчёт».

- [ ] **Шаг 1:** компонент с `props: { scenes }`, `emits: ['ready']`, без внешних библиотек.
- [ ] **Шаг 2:** подключить в `App.vue` (после списка сцен), `@ready="id => chooseScene(id)"` + `fetchAnalysis()`.
- [ ] **Шаг 3:** проверка в браузере: запуск реального чипа, прогресс, выбор сцены, ошибка при выключенной сети (роут `**/api/scenes` → 503 через playwright).
- [ ] **Шаг 4: Коммит** `feat(atlas): add official chip picker with live inference progress`.

---

### Task 6: Карта без наложений

**Файлы:** `frontend/src/GlobeMap.vue`, `frontend/src/style.css`.

- [ ] **Шаг 1: Порядок слоёв.** После `style.load` вставлять растры перед первым слоем-подписью (`type === 'symbol'` и `layout['text-field']`), а все линейные/площадные слои подложки, стоящие выше этой точки, переносить под `radar-layer` через `map.moveLayer(id, 'radar-layer')`; символы-иконки POI (`symbol` без `text-field`) скрывать внутри экстента чипа не нужно — просто прятать `visibility: none`.
- [ ] **Шаг 2: Атрибуция** — компактная и свёрнутая при загрузке (снять класс `maplibregl-compact-show`), в левом нижнем углу области карты; `NavigationControl` убрать, вместо него собственная вертикальная панель инструментов рядом с «3D-обзор»: `+`, `−`, «Север», «Земля» (вызовы `map.zoomIn/zoomOut/resetNorthPitch/showGlobe`), единый стиль с остальными кнопками.
- [ ] **Шаг 3:** Скриншоты 1440×900, 1366×768, 1100×800: ни атрибуция, ни кнопки не лежат на чипе; на чипе нет линий дорог/ЖД подложки.
- [ ] **Шаг 4: Коммит** `fix(atlas): keep map chrome and basemap lines off the chip`.

---

### Task 7: Каталог зон с ценой, сроком и причиной

**Файлы:** `backend/app/analysis.py`, `backend/tests/test_planning.py`, `frontend/src/App.vue`, `frontend/src/style.css`.

**Интерфейсы:** в ответе анализа `zone_table: [{id, name, sensor_label, area_km2, delivery_hours, timely, covered_asset_ids, covered_expected_loss_rub, standalone_cost_rub, basket_cost_rub, in_b, in_c, status, reason}]`, `status ∈ {"selected_c", "broad_b", "not_selected"}`; причины: «позже срока решения», «нет объектов в области», «объекты уже покрыты выбранными зонами», «не помещается в бюджет с пересчётом скидки».

- [ ] **Шаг 1: Тест**

```python
def test_zone_table_explains_every_zone():
    from app.analysis import analyze
    result = analyze(None, 6500, "risk", 8, None, "Nigeria_812045")
    rows = {row["id"]: row for row in result["zone_table"]}
    assert set(rows) == {zone["id"] for zone in result["zones"]}
    for zone_id in result["strategies"]["C"]["zone_ids"]:
        assert rows[zone_id]["status"] == "selected_c" and rows[zone_id]["basket_cost_rub"] > 0
    for row in rows.values():
        assert row["status"] == "selected_c" or row["reason"]
    late = analyze(None, 6500, "risk", 4, None, "Nigeria_812045")
    assert all(r["reason"] == "позже срока решения" for r in late["zone_table"] if r["status"] == "not_selected")

def test_zone_covers_asset_on_boundary():
    from app.planning import contains
    asset = {"longitude": 5.8, "latitude": 9.1}
    assert contains([5.8, 9.0, 5.9, 9.1], asset)      # точка на углу контура
```

- [ ] **Шаг 2:** FAIL → **Шаг 3:** реализовать `zone_table(...)` в `analysis.py` (standalone — `quote([zone])`, в корзине — строка из `strategies["C"]["quote"]["lines"]`; причина «не помещается» проверяется пересчётом `quote(C + [zone]) > budget`).
- [ ] **Шаг 4:** UI во вкладке «Съёмка»: таблица зон вместо «Зоны на карте» — строка: `Z10 · водозабор`, «оптика 1 м · новая L2», «через 6 ч», цена (в корзине C или отдельно), чипы покрытых объектов, EL в охвате, статус-плашка и причина; наведение/клик подсвечивает зону на карте (проп `focusZone`).
- [ ] **Шаг 5:** Тесты зелёные, скриншот вкладки. **Шаг 6: Коммит** `feat(plan): explain price, deadline and reason for every candidate zone`.

---

### Task 8: Навигация, слой-переключатель, читаемость

**Файлы:** `frontend/src/App.vue`, `frontend/src/GlobeMap.vue`, `frontend/src/style.css`, `frontend/index.html`, `frontend/public/favicon.svg`.

- [ ] **Шаг 1: Шаги в шапке** — 4 кнопки `01 Вода · 02 Объекты · 03 Съёмка · 04 Качество` с `aria-current="step"` для активной; клик переключает правую панель (`rightMode`), «Вода» фокусирует чип. Вкладки внутри правой панели убрать (дублируют шаги); на ширине < 1180 навигация остаётся в нижнем доке. Номера из заголовков панелей убрать («Приоритеты», «Заказ ДЗЗ», «Проверка»).
- [ ] **Шаг 2: Переключатель слоя на карте** — плавающая группа в верхнем левом углу области карты (под плашкой чипа): Маска / p / Разброс / Порог / Метка / Без слоя, плюс переключатели «Радар» и «Зоны»; из левой панели секцию «Что видно на карте» убрать. Легенда внизу показывает шкалу для p и разброса (градиент 0→1).
- [ ] **Шаг 3: Читаемость для проектора** — минимальный кегль 11 px (подписи), 12 px (вторичный текст), 13 px (основной); ширины панелей 318 / 360 px; `font-variant-numeric: tabular-nums` для всех чисел; проверить, что при 1366×768 видны «Карта воды» и первые 6 объектов без прокрутки не хуже текущего.
- [ ] **Шаг 4: Строки объектов** — `p 98% · ±2 п.п.`, статус `нет данных` при `p_flood == null`, метка «в плане C»; в карточке объекта — «p в пикселе» и «среднее 3×3».
- [ ] **Шаг 5:** favicon (SVG волны в цвете `--blue-deep`), `<meta name="description">`.
- [ ] **Шаг 6:** Замена `—` в интерфейсных строках: пустые значения `н/д`, в предложениях — точка/двоеточие.
- [ ] **Шаг 7:** Скриншоты 1440×900 / 1366×768 / 1100×800 / 390×844 в светлой и тёмной теме; `accessibility-scan` по живой странице; `web-design-guidelines` по файлам. **Коммит** `feat(atlas): step navigation, map layer switcher and projector-ready type`.

---

### Task 9: Разбор ошибок и прокси-проверка на test_core (ML, CPU)

**Файлы:** создать `ml/src/floodvalue/error_analysis.py`, подкоманда `floodvalue errors` в `cli.py`, тест `ml/tests/test_error_analysis.py`; артефакты `ml/artifacts/evaluation/error_analysis.json`, `error_analysis.md`; раздел в `ml/REPORT.md`; `data/demo/quality.json["errors"]`, `["proxy_test_core"]` через `prepare_demo.py`.

**Содержание:** один проход ансамбля по `test_core`, `test_adjacent`, `bolivia` (CPU, модель грузится один раз). Для каждой части: FP/FN по событиям; по корзинам VH (dB) и VV; по JRC (постоянная/не постоянная вода, слой `JRCWaterHand`); по расстоянию до края воды (≤ 2 px / внутри); по децилям разброса. Для Pakistan и `Bolivia_242570` — распределения VH у пропущенной воды против найденной (проверка гипотезы «затопленная растительность/мелкая вода ярче в VH»). Прокси-проверка EL по ТЗ: 1000 контрольных точек, seed 2026, равномерно по валидным пикселям `test_core` с валидной меткой, тип объекта по кругу из таблицы кейса (V/q фиксированы), метрики MAE, смещение суммы, по типам.

- [ ] **Шаг 1:** тест на синтетике: функция `stratified_errors(pred, truth, valid, strata)` считает FP/FN по корзинам; `proxy_points(...)` детерминирована при seed.
- [ ] **Шаг 2–4:** реализация, прогон `uv run --project ml floodvalue errors`, раздел REPORT «Где и почему ошибается модель» с таблицами и выводами.
- [ ] **Шаг 5: Коммит** `feat(ml): stratified error analysis and test_core proxy EL check`.

---

### Task 10: Воспроизводимость одной командой и кроссплатформенные инструкции

**Файлы:** создать `scripts/check_reproducibility.py`; изменить `README.md`, `backend/app/exporter.py` (`commands`), `docs/06-deliverables.md`.

- [ ] **Шаг 1:** скрипт: `--chip` (по умолчанию три демо-чипа), берёт S1 из `data/source` или скачивает, запускает inference в temp, сравнивает с `data/demo/<chip>`: маски побайтно, `max|Δp|`, `max|Δu|`, число переключений маски на пороге; допуск 1e-6; печатает таблицу, код возврата 1 при превышении.
- [ ] **Шаг 2:** прогон на Windows CPU: ожидается `max|Δp| ≈ 4.2e-07`, 0 переключений (замер 25.09).
- [ ] **Шаг 3:** README и `run_metadata.commands` — команды через `uv run --project ...` (работают на Windows, Linux, macOS), раздел «Допустимая численная погрешность», запуск живого inference в интерфейсе.
- [ ] **Шаг 4: Коммит** `feat(repro): one-command reproducibility check with numeric tolerance`.

---

### Task 11: Docker с CPU-inference

**Файлы:** `backend/Dockerfile`, `compose.yaml`, `.dockerignore`.

- [ ] **Шаг 1:** в образ backend: `uv sync --project backend --frozen --no-dev --extra demo`; отдельное окружение `/opt/ml`: `uv export --project ml --frozen --no-dev --no-hashes` → заменить `+cu132` на `+cpu`, установить с `--index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple`, затем `uv pip install --no-deps ./ml`; скопировать `ml/artifacts/model`, `ml/artifacts/evaluation/calibration.json`, `ml/artifacts/baseline/baseline.json`, `data/catalog`; `ENV FLOODVALUE_INFER_CMD="/opt/ml/bin/floodvalue infer"`.
- [ ] **Шаг 2:** `compose.yaml`: именованный том `runtime:/app/data/runtime`; `.dockerignore`: `ml/data`, `ml/.venv`, `ml/runs`, `data/runtime`, `data/inference`, `.playwright-cli`.
- [ ] **Шаг 3:** `docker compose up --build`, живой inference Bolivia_103757 внутри контейнера, экспорт + валидатор.
- [ ] **Шаг 4: Коммит** `build(docker): ship CPU inference for live chips`.

---

### Task 12: Документы для жюри

**Файлы:** `docs/09-pilot-plan.md` (новый), `docs/08-status.md`, `README.md`, `docs/README.md`, `docs/product.md`.

- [ ] **Шаг 1:** `09-pilot-plan.md` (критерий 20): пользователь и решение; этапы 0–3 (ретроспектива на событиях вне обучения по Copernicus EMS, теневой режим на реальном реестре одного оператора, платный заказ по реальной котировке, сверка с обследованием); метрики пилота из ТЗ (время до решения, пропуски значимых объектов, доля ручной проверки, цена полезного наблюдения); данные и лицензии; критерии остановки; сроки.
- [ ] **Шаг 2:** `08-status.md`: новая дата, доказательства (живой inference, строгий p, прокси по ТЗ, разбор ошибок, Windows-воспроизводимость), пересчитанная таблица критериев, оставшиеся внешние зависимости (GitVerse, котировка).
- [ ] **Шаг 3: Коммит** `docs: pilot plan, status and jury instructions`.

---

### Task 13: Финальная проверка и GitVerse

- [ ] Полный прогон: backend + ml тесты, сборка фронта, `docker compose up --build`, браузерный сценарий защиты (3D → пиксель → объект → план → бюджет 6500→5000 → экспорт → валидатор → живой чип).
- [ ] `superpowers:verification-before-completion` и ревью ветки.
- [ ] 26.09 ~20:00: пуш в GitVerse по ссылке пользователя (код + LFS-веса), чистый клон, inference на официальном чипе, ссылка в README/`run_metadata`.

---

## Самопроверка плана

- Покрытие ТЗ: живой запуск другого чипа (защита, крит. 11, 19, 20) — задачи 4, 5, 11; p в точке (крит. 13) — задача 2; прокси y∈{0;1} (крит. 14) — задачи 3, 9; цены и сроки зон, причины (крит. 15, 19) — задача 7; разбор ошибок (крит. 4) — задача 9; погрешность и повторный запуск (крит. 11) — задача 10; план пилота (крит. 20) — задача 12; GitVerse (крит. 11, 12) — задача 13.
- Вне плана (нужен GPU у коллег): leave-one-event-out переобучение для крит. 6; изотоническая калибровка.
- Имена интерфейсов сверены: `scene_dir`, `refresh`, `build_scene`, `SceneSpec`, `zone_table`, `asset_proxy_check`.
