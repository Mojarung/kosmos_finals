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
    live = info.get("source_kind") == "live_sentinel1_rtc"
    if live:
        if year is not None and year > 2021:
            water = ("JRC GSW uses observations through 2021, before this Sentinel-1 RTC image. "
                     "It may miss later changes to rivers and reservoirs.")
            water_status = "pre_event_historical_reference"
        else:
            water = ("The JRC GSW 1984-2021 reference is not proven to predate this image. "
                     "Do not interpret the separated mask as an operational flood estimate.")
            water_status = "not_proven_pre_event"
        return {"analysis_mode": "rtc_observation", "water_reference_status": water_status,
                "water_reference_note": water,
                "availability_note": ("The image date is shown separately from processing time. "
                                      "Local segmentation quality has not been measured for this region.")}
    if year is not None and year <= 2018:
        water = ("Sen1Floods11 JRCWaterHand uses annual observations through 2018; this includes "
                 "months after a 2018 event and later years after a 2016-2017 event. "
                 "The permanent-water separation is retrospective.")
        water_status = "post_event_observations_possible"
    else:
        water = ("Sen1Floods11 JRCWaterHand uses observations through 2018. "
                 "Check the scene date and source publication before interpreting historical availability.")
        water_status = "historical_reference"
    return {"analysis_mode": "retrospective_dataset_demo", "water_reference_status": water_status,
            "water_reference_note": water,
            "availability_note": ("Sen1Floods11 is a later published research dataset. Its label and JRC layer "
                                  "are used for retrospective demonstration, not claimed available "
                                  "within 6-12 hours of the historical flood.")}
