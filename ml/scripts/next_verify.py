"""Verify packaged CPU GeoTIFFs against the frozen evaluation and artifact checksums."""
import json
from pathlib import Path

import numpy as np
import rasterio

from floodvalue import paths
from floodvalue.chips import Chip, layer_path, s1_valid
from floodvalue.fusion_calibration import fusion_calibration
from floodvalue.fusion_infer import sha, verify_member


def main():
    root = Path("runs/next")
    package = Path("artifacts/next_optical/recipe.json")
    recipe = json.loads(package.read_text())
    comparison = json.loads((root/"test_comparison.json").read_text())
    if sha(package) != comparison["package_sha256"]:
        raise ValueError("package differs from evaluated candidate")
    if sha(package.parent/"source_recipe.json") != recipe["source_recipe_sha256"]:
        raise ValueError("packaged source recipe checksum differs")
    audit = json.loads((package.parent/"selection_audit.json").read_text())
    if audit["recipe_sha256"] != recipe["source_recipe_sha256"]:
        raise ValueError("packaged audit differs")
    for section in ["sources_sha256", "results_sha256"]:
        for filename, expected in audit[section].items():
            if sha(Path(filename)) != expected:
                raise ValueError("a frozen source or development result changed: "+filename)
    for member in recipe["fusion_members"]:
        directory = package.parent/member["path"]
        if not directory.resolve().is_relative_to(package.parent.resolve()):
            raise ValueError("optical member depends on an external run directory")
        verify_member(directory, member)
    base = package.parent/recipe["base_recipe"]
    if sha(base) != recipe["base_recipe_sha256"]:
        raise ValueError("radar recipe checksum differs")
    for member in json.loads(base.read_text())["members"]:
        directory = base.parent/member["member"]
        if not directory.resolve().is_relative_to(package.parent.resolve()):
            raise ValueError("radar member depends on an external run directory")
        if sha(directory/"model.safetensors") != member["weights_sha256"]:
            raise ValueError("radar weight checksum differs")
    name = "Bolivia_103757"
    chip = Chip.from_name(name)
    out = Path("outputs/next_"+name)
    with rasterio.open(layer_path(paths.SEN1FLOODS11_DIR, chip, "S1Hand")) as src:
        valid = s1_valid(src.read())
        grid = (src.crs, src.transform, src.shape)
    values = {}
    for filename, nodata, dtype in [("flood_probability.tif", -9999, "float32"),
                                    ("flood_mask.tif", 255, "uint8"), ("flood_uncertainty.tif", -9999, "float32")]:
        with rasterio.open(out/filename) as src:
            if (src.crs, src.transform, src.shape) != grid or src.nodata != nodata or src.dtypes != (dtype,):
                raise ValueError("GeoTIFF contract changed")
            a = src.read(1)
            if not np.isfinite(a[valid]).all() or not (a[~valid] == nodata).all():
                raise ValueError("invalid output pixels or nodata")
            values[filename] = a
    actual = values["flood_probability.tif"]
    if not ((actual[valid] >= 0) & (actual[valid] <= 1)).all():
        raise ValueError("probabilities outside [0, 1]")
    if not np.array_equal(values["flood_mask.tif"][valid] == 1, actual[valid] >= recipe["threshold"]):
        raise ValueError("mask does not equal probability threshold")
    with np.load(root/"bolivia_predictions.npz") as a:
        i = a["names"].tolist().index(name)
        with np.load(root/"optical_test_cache/bolivia"/(name+".npz")) as cached:
            expected = fusion_calibration(recipe, cached["available"]).apply(a["logits"][i])
        error = np.abs(actual[valid]-expected[valid])
        disagreements = float(np.mean((actual[valid] >= recipe["threshold"]) != (expected[valid] >= recipe["threshold"])))
        uncertainty_error = np.abs(values["flood_uncertainty.tif"][valid]-a["uncertainty"][i][valid])
    if error.mean() >= 1e-4 or disagreements >= .001 or uncertainty_error.max() >= .001:
        raise ValueError("CPU/evaluation prediction discrepancy requires investigation")
    original = json.loads(Path("runs/goal072/recipes/v1_weighted_probability_mean.json").read_text())
    if any(sha(Path(m["member"])/"model.safetensors") != m["weights_sha256"] for m in original["members"]):
        raise ValueError("original radar checkpoints changed")
    result = {"chip": name, "portable_package": True, "recipe_sha256": sha(package),
              "frozen_sources_and_development_results": "unchanged since selection",
              "grid_nodata_dtype_threshold_checks": "passed", "original_radar_weights": "unchanged",
              "probability_mean_abs_error_cpu_vs_evaluation": float(error.mean()),
              "probability_max_abs_error_cpu_vs_evaluation": float(error.max()),
              "mask_disagreement_fraction_cpu_vs_evaluation": disagreements,
              "uncertainty_max_abs_error_cpu_vs_evaluation": float(uncertainty_error.max()),
              "inference": json.loads((out/"inference.json").read_text())}
    (root/"verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({k:v for k,v in result.items() if k != "inference"}, indent=2))


if __name__ == "__main__":
    main()
