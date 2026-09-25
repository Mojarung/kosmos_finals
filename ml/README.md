# FloodValue pipeline: карта воды по Sentinel-1

Пайплайн находит воду на S1-чипах официального [Sen1Floods11](https://github.com/cloudtostreet/Sen1Floods11) двумя методами и пишет растры в формате кейса. Первый метод - пороговый baseline, второй - ансамбль U-Net с откалиброванными вероятностями и неопределённостью. Экономика (объекты, EL, зоны, A/B/C) будет отдельным модулем, этот пакет её не касается.

Всё запускается через `uv` из этой папки. Обучение шло на RTX 5070 12 ГБ; inference и оценка работают и на CPU, только медленнее.

## Установка

```bash
cd ml
uv sync
```

Версии зафиксированы в `uv.lock`: Python 3.13, torch 2.14.0+cu132, torchvision 0.29.0+cu132, segmentation-models-pytorch 0.5.0, rasterio 1.5.1, numpy 2.5.3, scipy 1.18.1, scikit-learn 1.9.1. Колёса torch берутся из индекса `https://download.pytorch.org/whl/cu132` (см. `pyproject.toml`), они работают и без GPU.

Веса лежат в Git LFS: после клонирования выполнить `git lfs install --local && git lfs pull`.

## Полный прогон

```bash
uv run floodvalue download          # S1Hand, LabelHand, JRCWaterHand, splits v1.1, ~700 МБ, md5 сверяется
uv run floodvalue check             # каналы, CRS, сетки, значения меток -> artifacts/data_check.json
uv run floodvalue split             # авторский split + проверка соседства -> artifacts/split.json
uv run floodvalue baseline          # порог на train, канал по valid -> artifacts/baseline/baseline.json
sh scripts/train_experiments.sh     # контролируемый эксперимент: 3 конфигурации x 2 seed
uv run floodvalue experiments unet_r34_vvvh unet_r34_vvvh_diff unet_r34_vvvh_scratch
sh scripts/train_final.sh           # seed 2-4 выбранной конфигурации
uv run floodvalue package runs/unet_r34_vvvh/seed0 runs/unet_r34_vvvh/seed1 runs/unet_r34_vvvh/seed2 \
  runs/unet_r34_vvvh/seed3 runs/unet_r34_vvvh/seed4   # ансамбль -> artifacts/model, веса float16
uv run floodvalue evaluate          # калибровка и порог по valid, метрики на test и Bolivia, графики, summary.md
```

Одна модель обучается на RTX 5070 около 4 минут, все 9 моделей (6 в эксперименте и 3 финальных seed) около 40 минут. Без переобучения можно сразу запускать `evaluate` и `infer`: веса ансамбля уже лежат в `artifacts/model`.

Техническая проверка проводки (модель должна выучить несколько train-чипов целиком, без аугментаций):

```bash
uv run floodvalue train --config configs/unet_r34_vvvh.json --overfit 4 --epochs 300
```

## Inference на любом официальном S1-чипе

```bash
uv run floodvalue infer --chip Bolivia_103757 --out outputs/Bolivia_103757
uv run floodvalue infer --s1 path/to/EVENT_ID_S1Hand.tif --out outputs/my_chip
```

В папке `--out` появятся:

| Файл | Формат | Содержимое |
|---|---|---|
| `flood_probability.tif` | Float32, nodata -9999 | откалиброванная вероятность воды p |
| `flood_mask.tif` | UInt8, nodata 255 | 1 вода, 0 не вода; `mask == 1` ровно там, где `p >= threshold` |
| `flood_uncertainty.tif` | Float32, nodata -9999 | стандартное отклонение вероятностей членов ансамбля |
| `baseline_mask.tif` | UInt8, nodata 255 | маска порогового baseline |
| `inference.json` | JSON | порог, параметры калибровки, доли, sha256 файлов |

CRS, transform и размер копируются из входного S1. Модель работает только по S1, никакие дополнительные данные не нужны. Повторная подгонка на новом чипе не выполняется.

## Протокол проверки

- Split: авторские списки v1.1 (train 252, valid 89, test 90, Bolivia 15). Авторы делили чипы случайно внутри событий, поэтому test-чипы, чей контур касается train-чипа того же события, вынесены в `test_adjacent` (35 чипов). Независимым по пространству остаётся `test_core` (55 чипов), независимым по событию - `bolivia`.
- Валидные пиксели: `finite(VV) & finite(VH) & label != -1`. Оба метода оцениваются на одном и том же наборе.
- На validation выбираются: канал baseline, checkpoint каждой модели, конфигурация, параметры калибровки Platt и порог бинаризации. Test и Bolivia ни в каком выборе не участвуют.
- Нормализация: клип по перцентилям 0,1-99,9 и среднее/σ, посчитанные только по train.

## Результаты

IoU по всем валидным пикселям части; оба метода на одном наборе пикселей. Полная таблица: `artifacts/evaluation/summary.md`, разбор: [REPORT.md](REPORT.md).

| Часть | Чипов | Baseline (VH < -23,6 dB) | Ансамбль U-Net |
|---|---|---|---|
| test_core | 55 | 0,519 | 0,697 |
| test_adjacent | 35 | 0,554 | 0,648 |
| Bolivia, новое событие | 15 | 0,535 | 0,655 |

Порог основного метода 0,41 по откалиброванной p. Калибровка снижает ECE на test с 0,0247 до 0,0136. Разброс ансамбля отделяет ошибочные пиксели с AUROC 0,896 на test и 0,904 на Bolivia.

| Что | Где |
|---|---|
| Веса ансамбля (5 × 49 МБ, float16, LFS) и sha256 | `artifacts/model/` |
| Порог и калибровка | `artifacts/evaluation/calibration.json` |
| Метрики по частям, событиям и чипам | `artifacts/evaluation/metrics.csv`, `evaluation.json` |
| Графики | `artifacts/evaluation/figures/` |
| Split и проверка данных | `artifacts/split.json`, `artifacts/data_check.json` |
| Эксперимент | `artifacts/experiments.json` |
