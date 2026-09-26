"""Explain which inputs could have been available at the observation time."""

from __future__ import annotations

from datetime import datetime


def scene_timing(info: dict) -> dict:
    """Return a disclosure, not a claim that historical inputs were operational."""
    observed = str(info.get("observed_at") or "")
    try:
        year = datetime.fromisoformat(observed.replace("Z", "+00:00")).year
    except ValueError:
        year = None
    uploaded = info.get("source_kind") == "uploaded_sentinel1_geotiff"
    if uploaded:
        return {"analysis_mode": "uploaded_observation", "water_reference_status": "not_separated",
                "water_reference_note": "К этому GeoTIFF не приложен слой постоянной воды.",
                "availability_note": ("Дату наблюдения ввёл тот, кто загрузил файл. Происхождение снимка и его "
                                      "доступность на дату события независимо не проверены.")}
    live = info.get("source_kind") == "live_sentinel1_rtc"
    if live:
        if year is not None and year > 2021:
            water = ("JRC GSW построен по наблюдениям до 2021 года, то есть раньше этого снимка Sentinel-1 RTC. "
                     "Более поздние изменения рек и водохранилищ в нём могут не отражаться.")
            water_status = "pre_event_historical_reference"
        else:
            water = ("Не доказано, что слой JRC GSW 1984–2021 старше этого снимка. "
                     "Отделённую маску паводка нельзя считать оперативной оценкой.")
            water_status = "not_proven_pre_event"
        return {"analysis_mode": "rtc_observation", "water_reference_status": water_status,
                "water_reference_note": water,
                "availability_note": ("Дата снимка показана отдельно от времени расчёта. "
                                      "Качество карты воды в этом регионе на месте не измерялось.")}
    if year is not None and year <= 2018:
        water = ("JRCWaterHand из Sen1Floods11 построен по годовым наблюдениям до 2018 года включительно: "
                 "для паводка 2018 года туда попали месяцы после события, для паводков 2016–2017 годов "
                 "более поздние годы. Постоянная вода отделена ретроспективно.")
        water_status = "post_event_observations_possible"
    else:
        water = ("JRCWaterHand из Sen1Floods11 построен по наблюдениям до 2018 года. Прежде чем судить, "
                 "был ли слой доступен в прошлом, сверьте дату сцены и дату публикации источника.")
        water_status = "historical_reference"
    return {"analysis_mode": "retrospective_dataset_demo", "water_reference_status": water_status,
            "water_reference_note": water,
            "availability_note": ("Sen1Floods11 опубликован позже как исследовательский набор данных. Его разметка и слой JRC "
                                  "нужны для ретроспективной демонстрации; мы не утверждаем, что они были "
                                  "доступны в первые 6–12 часов исторического паводка.")}
