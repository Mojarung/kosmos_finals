"""Exercise the real frozen package, dates/fallback and CPU GeoTIFF contract."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import date, timedelta

import numpy as np
import rasterio

from audit_models import EVIDENCE, OUT, ROOT, read, sha, verified_protocol, write
from floodvalue import paths
from floodvalue.chips import Chip, layer_path, read_s1, s1_valid
from floodvalue.fusion_infer import predict_fusion_recipe
from floodvalue.optical import event_dates, optical_date_allowed, read_optical


def main():
    protocol, _ = verified_protocol()
    package = ROOT / "artifacts/next_optical/recipe.json"
    dates = event_dates(paths.SEN1FLOODS11_DIR)
    names = protocol["parts"]["test_core"]
    first = names[0]
    eligible = next(n for n in names if optical_date_allowed(**dates[Chip.from_name(n).event]))
    chip = Chip.from_name(eligible)
    s1 = read_s1(paths.SEN1FLOODS11_DIR, chip)
    s2 = read_optical(paths.SEN1FLOODS11_DIR, chip)
    acquisition = dates[chip.event]
    future = {**acquisition, "s2_date": (date.fromisoformat(acquisition["s1_date"].replace("/", "-")) + timedelta(days=1)).isoformat()}
    broken = s2.copy()
    broken[:, :64, :64] = np.nan
    _, p, spread, usage = predict_fusion_recipe(package, [s1]*4, [s2, None, s2, broken],
                                               [acquisition, acquisition, future, acquisition])
    assert usage[0]["optical_used_pixels"] > 0
    assert usage[1]["optical_used_pixels"] == usage[2]["optical_used_pixels"] == 0
    np.testing.assert_array_equal(p[1], p[2])
    np.testing.assert_array_equal(p[1, :64, :64], p[3, :64, :64])
    valid = s1_valid(s1)
    assert np.isfinite(p[:, valid]).all() and np.isfinite(spread[:, valid]).all()
    assert ((p[:, valid] >= 0) & (p[:, valid] <= 1)).all()
    with np.load(OUT / f"{eligible}.npz") as saved:
        np.testing.assert_allclose(p[0], saved["latest_s1s2"], atol=1e-4, rtol=0)
        np.testing.assert_allclose(p[1], saved["latest_no_s2"], atol=1e-4, rtol=0)
        parity = float(np.abs(p[0]-saved["latest_s1s2"]).mean())

    cpu_dir = OUT / "cpu_geotiff"
    before = time.monotonic()
    # Exercise the packaged CLI, not the evaluation writer.
    result = subprocess.run([sys.executable, "-m", "floodvalue.cli", "infer", "--chip", first,
                             "--recipe", str(package), "--out", str(cpu_dir)], cwd=ROOT,
                            env={**os.environ, "FLOODVALUE_DEVICE": "cpu"}, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    cpu_seconds = time.monotonic()-before
    cpu_chip = Chip.from_name(first)
    with rasterio.open(layer_path(paths.SEN1FLOODS11_DIR, cpu_chip, "S1Hand")) as src:
        valid = s1_valid(src.read())
        grid = (src.crs, src.transform, src.shape)
    arrays = {}
    for filename, dtype, nodata in [("flood_probability.tif", "float32", -9999),
                                     ("flood_mask.tif", "uint8", 255),
                                     ("flood_uncertainty.tif", "float32", -9999)]:
        with rasterio.open(cpu_dir/filename) as src:
            assert (src.crs, src.transform, src.shape) == grid
            assert src.dtypes == (dtype,) and src.nodata == nodata
            values = src.read(1)
            assert (values[~valid] == nodata).all() and np.isfinite(values[valid]).all()
            arrays[filename] = values
    probability = arrays["flood_probability.tif"]
    assert ((probability[valid] >= 0) & (probability[valid] <= 1)).all()
    assert np.array_equal(arrays["flood_mask.tif"][valid], probability[valid] >= read(package)["threshold"])
    with np.load(OUT / f"{first}.npz") as saved:
        error = np.abs(probability[valid]-saved["latest_s1s2"][valid])
        disagree = float(np.mean((probability[valid] >= read(package)["threshold"]) !=
                                (saved["latest_s1s2"][valid] >= read(package)["threshold"])))
    assert error.mean() < 1e-4 and disagree < .001
    manifest = paths.SEN1FLOODS11_DIR/"audit_download_manifest.json"
    # Check the declared training lists of each shipped optical member.
    test_names = {n for part in protocol["parts"].values() for n in part}
    provenance = {}
    for summary in (package.parent/"optical").glob("*/train_summary.json"):
        metadata = read(summary)
        used = set(metadata["train_chips"]) | set(metadata["valid_chips"])
        assert not test_names.intersection(used)
        provenance[summary.parent.name] = {"train_chips": len(metadata["train_chips"]),
                                          "valid_chips": len(metadata["valid_chips"]), "test_overlap": 0}
    evidence = {"status": "passed", "recipe_sha256": sha(package),
                "input_manifest_sha256": sha(manifest), "input_files": len(read(manifest)["objects"]),
                "fallback_check_chip": eligible, "usage": usage,
                "future_optical_equals_missing": True, "partial_missing_equals_radar_at_missing_pixels": True,
                "batch_audit_vs_packaged_predictor_mean_abs_error": parity,
                "cpu_cli_chip": first, "cpu_cli_seconds_including_imports_and_loading": cpu_seconds,
                "cpu_vs_mps_mean_absolute_probability_error": float(error.mean()),
                "cpu_vs_mps_max_absolute_probability_error": float(error.max()),
                "cpu_vs_mps_mask_disagreement_fraction": disagree,
                "geotiff_grid_dtype_nodata_range_threshold": "passed", "optical_training_lists": provenance}
    write(EVIDENCE/"contracts.json", evidence)
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
