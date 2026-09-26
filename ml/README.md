# FloodValue pipeline: карта воды по Sentinel-1

Пайплайн находит воду на S1-чипах официального [Sen1Floods11](https://github.com/cloudtostreet/Sen1Floods11) двумя методами и пишет растры в формате кейса. Первый метод - пороговый baseline, второй - ансамбль U-Net с откалиброванными вероятностями и неопределённостью. Экономика (объекты, EL, зоны, A/B/C) будет отдельным модулем, этот пакет её не касается.

Всё запускается через `uv` из этой папки. Обучение шло на RTX 5070 12 ГБ; inference и оценка работают и на CPU, только медленнее.

Идеи из открытых Kaggle-решений собраны в [KAGGLE_RESEARCH.md](KAGGLE_RESEARCH.md). Новые ML-эксперименты используют три фолда с исключением целых событий: [результаты](research/CV_RESULTS.md), [протокол и команды CV](research/CV_PROTOCOL.md). Их веса и прогнозы сохраняются отдельно в `runs/cv`; test_core и Bolivia в подборе не участвуют. Для обучения на Apple GPU можно явно задать `FLOODVALUE_DEVICE=mps`.

Продолжение работы с готовыми весами коллеги на исходном split дало **test_core IoU 0,70438 против 0,69696** при повторной оценке исходника на том же устройстве. Победил взвешенный ансамбль исходных пяти сетей с четырьмя поворотами и изотонической калибровкой. Все параметры выбраны на validation и зафиксированы до теста. [Полное сравнение, ограничения и запуск](research/REFINEMENT_RESULTS.md), [протокол](research/REFINEMENT_PROTOCOL.md). Эти цифры относятся к исходному test_core, а не к отдельному event-held-out CV.

На Bolivia этот рецепт снизил IoU с 0,65928 до 0,65274. Он сохранён как отдельный экспериментальный вариант; исходный v1 остаётся вариантом по умолчанию. Устойчивое улучшение на новых событиях пока не подтверждено.

Отдельный режим **S1+S2 достиг test_core IoU 0,84869 и Bolivia 0,83684**. Это расширение входных данных оптикой, а не результат S1-only. S2 допускается только в дату S1 или на 1–2 дня раньше; иначе используется S1. Смесь, калибровка и порог выбраны на validation и event CV до финальной проверки. Все test-пиксели остались прежними. [Результаты, ограничения и воспроизведение](research/GOAL072_OPTICAL_RESULTS.md), [исследованные решения](research/GOAL072_RESEARCH.md).

Следующая серия проверила 35 конфигураций на той же event CV. Выбран ансамбль HGB/LightGBM/CatBoost с прежним радаром: **S1+S2 test_core 0,84938, test_adjacent 0,83813, Bolivia 0,83735**. Event-CV IoU вырос с 0,77421 до 0,78410. Прирост на test небольшой: парный bootstrap разности test_core включает ноль. Новый пакет — `artifacts/next_optical/`; прежний сохранён. [Все эксперименты, сравнение и проверка CPU](research/NEXT_RESULTS.md).

Чистый S1-контроль из этой серии получил **0,70617 на test_core и 0,66314 на Bolivia** за счёт усреднения вероятностей исходных пяти моделей. Он доступен отдельно; результаты S1 и S1+S2 приводятся раздельно.

Разметка Sen1Floods11 основана на классификации по S2 с ручными исправлениями. Эту зависимость учитываем при сравнении режимов; подробнее — в разделе о данных и ограничениях отчёта.

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
uv run floodvalue infer --chip Bolivia_103757 --recipe artifacts/refined_model/recipe.json --out outputs/refined_Bolivia_103757
uv run floodvalue infer --chip Bolivia_103757 --recipe artifacts/goal072_optical/radar/recipe.json --out outputs/radar_Bolivia_103757
```

`--recipe` включает новый вариант; без этого параметра запускается исходный v1. Новый рецепт делает 20 проходов сети вместо 5 и также работает на CPU. Веса находятся отдельно в `artifacts/refined_model/` и проверяются по SHA256 при загрузке.

Для дополнительного S1+S2 режима:

```bash
uv run python scripts/goal072_download_s2.py --parts train valid test_core test_adjacent bolivia
uv run floodvalue infer --chip Bolivia_103757 --recipe artifacts/goal072_optical/recipe.json --out outputs/optical_Bolivia_103757
uv run floodvalue infer --chip Bolivia_103757 --no-optical --recipe artifacts/goal072_optical/recipe.json --out outputs/fallback_Bolivia_103757
```

Загрузчик S2 проверяет MD5 и скачивает около 1,02 ГБ на все чипы. Отсутствующая или более поздняя S2 приводит к S1 fallback. Для своих файлов нужны `--s1`, `--s2`, `--s1-date`, `--s2-date`; оптика должна содержать 13 полос reflectance×10000 на сетке S1. `--no-optical` проверяет fallback расширенного рецепта с его общей калибровкой; отдельный чистый S1-контроль запускается через `radar/recipe.json`.

Для нового ансамбля бустингов нужны дополнительные зависимости:

```bash
uv sync --group research
# На Mac LightGBM может использовать OpenMP из уже установленного torch:
export DYLD_LIBRARY_PATH="$PWD/.venv/lib/python3.13/site-packages/torch/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
uv run floodvalue infer --chip Bolivia_103757 --recipe artifacts/next_optical/recipe.json --out outputs/next_Bolivia_103757
```

Команда работает на CPU; порог 0,46 и веса смеси уже зафиксированы. `--no-optical` поддерживается и этим рецептом.

В папке `--out` появятся:

| Файл | Формат | Содержимое |
|---|---|---|
| `flood_probability.tif` | Float32, nodata -9999 | откалиброванная вероятность воды p |
| `flood_mask.tif` | UInt8, nodata 255 | 1 вода, 0 не вода; `mask == 1` ровно там, где `p >= threshold` |
| `flood_uncertainty.tif` | Float32, nodata -9999 | разброс S1-ансамбля; с S2 — расхождение S1/S2, в новом рецепте также учитывается разброс оптических моделей |
| `baseline_mask.tif` | UInt8, nodata 255 | маска порогового baseline в S1-режиме |
| `inference.json` | JSON | порог, параметры калибровки, доли, sha256 файлов |

CRS, transform и размер копируются из входного S1. По умолчанию модель работает только по S1, дополнительные данные не нужны. S1+S2 выбирается явно через отдельный рецепт. Повторная подгонка на новом чипе не выполняется.

## Протокол проверки

- Split: авторские списки v1.1 (train 252, valid 89, test 90, Bolivia 15). Авторы делили чипы случайно внутри событий, поэтому test-чипы, чей контур касается train-чипа того же события, вынесены в `test_adjacent` (35 чипов). Независимым по пространству остаётся `test_core` (55 чипов), независимым по событию - `bolivia`.
- Валидные пиксели: `finite(VV) & finite(VH) & label != -1`. Оба метода оцениваются на одном и том же наборе.
- На validation выбираются: канал baseline, checkpoint каждой модели, конфигурация, параметры калибровки Platt и порог бинаризации. Test и Bolivia ни в каком выборе не участвуют.
- Нормализация: клип по перцентилям 0,1-99,9 и среднее/σ, посчитанные только по train.

## Результаты поставляемого ансамбля v1

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
