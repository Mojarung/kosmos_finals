# Воспроизведение L2A boosting и TerraMind tiny

Рабочая директория — `ml/`, исходная версия проекта `cae7833` плюс исходники этой
итерации. Требуются уже загруженный Sen1Floods11 и research/dev-зависимости проекта.
Рабочие данные и окружение находятся в игнорируемых `data/` и `runs/`. Копии обученных
checkpoints и метаданных опубликованы через Git LFS в `artifacts/service_research/`;
production-модели не заменены. Для восстановления весов без переобучения:

```bash
git lfs pull
uv run python scripts/package_service_research.py restore
```

## Данные и бустинг

```bash
uv sync --group research --group dev
uv run python -m floodvalue.l2a_data --workers 4
uv run python -m floodvalue.l2a_boost
```

На использованном Mac запускали через `.venv/bin/python`; для LightGBM:

```bash
export OMP_NUM_THREADS=4
export DYLD_LIBRARY_PATH="$PWD/.venv/lib/python3.13/site-packages/torch/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
```

Загрузчик возобновляется по готовым диапазонам/чипам. Сначала фиксирует STAC-план в
`data/l2a_boost/acquisition.json`, затем пишет `ready.json` только после завершения
всех загрузок. Для повторения **тех же** источников нужно сохранить этот исходный
план; `L2A_ACQUISITION_MANIFEST.json` содержит выбранные item IDs, даты, asset URLs,
сопоставление с чипами и SHA256 плана. Новый поиск каталога может вернуть иной набор.

Бустинг сохраняет inner-selection, список train/outer, модели и OOF каждого фолда.
Смена параметров или исходников при существующем `signature.json` останавливает
запуск: для новой гипотезы нужен отдельный каталог, старый опыт следует сохранить.

## Изолированное окружение TerraMind

`TERRAMIND_ENV.txt` — точный snapshot Python 3.13 / macOS arm64 использованного
окружения. На других платформах доступность этих wheels нужно проверить отдельно.
Основные версии: TerraTorch 1.2.13, torch 2.14.0. Production-зависимости не менялись.

```bash
uv venv runs/terramind_env --python 3.13
uv pip sync --python runs/terramind_env/bin/python research/TERRAMIND_ENV.txt
export PYTHONPATH="$PWD/src"
export MPLCONFIGDIR="$PWD/runs/mplconfig"
runs/terramind_env/bin/python - <<'PY'
from huggingface_hub import hf_hub_download
from pathlib import Path
import hashlib
p = hf_hub_download(
    repo_id="ibm-esa-geospatial/TerraMind-1.0-tiny",
    revision="2b5ac0a3ed7dd7e922ccfd595b56607f342df343",
    filename="TerraMind_v1_tiny.pt",
    local_dir="runs/terramind/pretrained",
)
assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == "e56ea9ebcd4451078b9ca4893d5cd8a89bbee376ae16829c3e7fbbbc76de0eba"
PY
export HF_HUB_OFFLINE=1
runs/terramind_env/bin/python -m floodvalue.terramind_cv
runs/terramind_env/bin/python scripts/benchmark_terramind.py
```

Тренировка использует MPS, если он доступен, иначе CPU. Фиксированы 25 эпох;
последний checkpoint, без выбора по outer-меткам. На CPU проверялся inference,
CPU-обучение по времени не измерялось. Resume сохраняется после каждой эпохи.

## Общая оценка после завершения 20 моделей

```bash
.venv/bin/python -m floodvalue.service_oof
.venv/bin/python scripts/service_experiment_manifest.py
.venv/bin/python -m pytest -q
git diff --check
```

Evaluator требует все 332 OOF-чипа каждого участника, проверяет имя, номер внешнего
фолда, размер и диапазон вероятностей. Выдаёт фиксированный порог 0,5 и отдельно
OOF-fitted Platt/порог; latter включает оптимизм выбора на development. Восемь
рецептов зафиксированы в `SERVICE_BLEND_PROTOCOL.md`, все публикуются. Ни одна из
этих команд не переоценивает test или не публикует новую сервисную модель.

## Kuro Siwo: подготовка и парный опыт

```bash
.venv/bin/python scripts/probe_external_data.py
.venv/bin/python scripts/prepare_kuro_pilot.py --ranges-per-shard 20
runs/terramind_env/bin/python -m floodvalue.terramind_transfer
.venv/bin/python -m floodvalue.service_oof --kuro-only
.venv/bin/python -m floodvalue.service_oof --routing
.venv/bin/python scripts/service_experiment_manifest.py --include-kuro
.venv/bin/python scripts/report_service_iteration.py
```

Загружаются 100 диапазонов по 8 MiB: 800 MiB, без скачивания пяти целых архивов.
Повторный запуск пропускает готовые диапазоны. Допуск: событие после 2019 года и
отсутствие пересечения с любым из 446 Sen1Floods11-чипов плюс буфер 0,02°. Это
консервативный фильтр поздней внешней обучающей выборки, не историческая доступность
данных для событий 2016–2019. Метки benchmark при фильтре не читаются.

`KURO_POOL_MANIFEST.json` перечисляет все 361 проверенный тайл, 160 допущенных из
12 событий, источник и географию каждого. При обучении нужно брать **только ID из
этого manifest**, а не все файлы `data/kuro_pilot/samples`: там также сохраняются
образцы прежнего маленького пилота. 95/160 тайлов принадлежат одному событию, поэтому
внешнее обучение использует равновероятный выбор события, затем тайла. Переносится
только backbone, decoder заново инициализируется как в контроле. Рецепт CV неизменен.
Подробности: `KURO_TRANSFER_PROTOCOL.md`; после окончания — `KURO_TRANSFER_RESULTS.json`.
Поле `trained: false` в acquisition manifest отражает момент подготовки данных;
факт обучения и его настройки фиксирует `SERVICE_EXPERIMENT_MANIFEST.json`.
