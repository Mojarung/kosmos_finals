# Повторный аудит замороженных моделей

Протокол: [../INDEPENDENT_AUDIT_PROTOCOL.md](../INDEPENDENT_AUDIT_PROTOCOL.md). Итог для пользователя: [../../../docs/13-model-audit.md](../../../docs/13-model-audit.md).

Это свежий inference из поставляемых весов на прежних отложенных данных. **Не новый слепой test**: результаты этих событий уже известны, development CV использовалась для выбора архитектуры/смеси. Веса и пороги во время аудита не менялись.

## Воспроизведение

Исходники ML зафиксированы на `76f6654`. Для точного воспроизведения взять коммит, добавивший этот аудит: `git log --diff-filter=A --format=%H -- ml/scripts/audit_models.py`. Последующие изменения CLI в main не меняют проверенные веса, но строгая SHA-проверка намеренно отклоняет такой изменённый checkout. Использовать отдельную рабочую копию коммита аудита, не откатывать рабочий main.

Из корня репозитория:

```bash
uv sync --project ml --frozen --group research --group dev
# На Mac, для установленной вместе с torch библиотеки OpenMP:
export DYLD_LIBRARY_PATH="$PWD/ml/.venv/lib/python3.13/site-packages/torch/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
export OMP_NUM_THREADS=4
export FLOODVALUE_DEVICE=mps # на машине без Metal выбрать cpu или cuda
export FLOODVALUE_EVAL_BATCH=4
ml/.venv/bin/python ml/scripts/audit_download.py
ml/.venv/bin/python ml/scripts/audit_models.py infer
ml/.venv/bin/python ml/scripts/audit_spatial.py
ml/.venv/bin/python ml/scripts/audit_contracts.py
ml/.venv/bin/python ml/scripts/audit_report.py
ml/.venv/bin/python ml/scripts/audit_diagnostics.py
```

`freeze` уже выполнен до первого прогона; сохранённый `protocol.json` проверяет хеши кода/рецептов/весов. При изменении любого из них нужен отдельный протокол/каталог, а не перезапись этого эксперимента. Прогнозы в `ml/outputs/audit_20260926` игнорируются Git; для полностью свежего inference на той же машине сначала переместить этот каталог в архив. Сохранённые прогнозы разрешено возобновлять только на тех же входах, проверенных по `input_manifest.json`.

## Артефакты

- `protocol.json`: выбор кандидата, исходный commit, 128 SHA256, версии библиотек, списки чипов, пороги.
- `input_manifest.json`: официальные объекты, поколения, размеры и MD5; данные не загружаются в Git.
- `metrics.json`, `per_chip_metrics.csv`: pooled/событийные/покадровые метрики; парные разности и описательные интервалы.
- `contracts.json`: реальный inference с пропусками/датами, пакетный CLI на CPU, контракт GeoTIFF и сравнение CPU/MPS.
- `spatial_provenance.json`: соседство train/validation/test и проверка сохранённых списков обучения.
- `diagnostics.json`: дополнительные срезы и прокси EL десяти фиксированных точек на чип; для анализа ошибок, не подбора параметров.
- `inference_inputs.json`: даты, доступность S2 и время частей вычисления. Время снято при совместной работе процессов, не является изолированным p95-бенчмарком.
- `comparison.png`, `comparison.pdf`: сравнение IoU и калибровки.

29 целевых тестов пройдены командой:

```bash
ml/.venv/bin/python -m pytest ml/tests/test_metrics_split_baseline.py ml/tests/test_fusion_infer.py ml/tests/test_next_evaluation.py ml/tests/test_optical.py ml/tests/test_calibration_uncertainty.py -q
```

Дополнительные срезы по соседству validation и прокси EL добавлены после чтения кода разбиения. Они не меняют заранее выбранные основные сравнения и не используются для настройки кандидата.

Первичный `audit_models.py report` остановился на JSON-защите от NaN: у Ghana_83483 и Paraguay_34417 нет валидных контрольных пикселей. Исправленный `audit_report.py` сохраняет для них пустые метрики; исходный замороженный runner оставлен неизменным. Предсказания, маски оценки и параметры моделей не пересчитывались и не менялись. Для итогового отчёта используется команда выше.
