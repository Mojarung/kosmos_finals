"""Historical source timing must not silently become an operational claim."""

from app.temporal import scene_timing


def test_2018_demo_discloses_post_event_reference():
    timing = scene_timing({"observed_at": "2018-09-21T00:00:00Z"})
    assert timing["analysis_mode"] == "retrospective_dataset_demo"
    assert timing["water_reference_status"] == "post_event_observations_possible"
    assert "after" in timing["water_reference_note"]


def test_recent_rtc_reference_predates_image_but_quality_is_unverified():
    timing = scene_timing({"observed_at": "2026-09-21T00:00:00Z", "source_kind": "live_sentinel1_rtc"})
    assert timing["water_reference_status"] == "pre_event_historical_reference"
    assert "not been measured" in timing["availability_note"]
