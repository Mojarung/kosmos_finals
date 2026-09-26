"""Audit development/test geometry and declared model training provenance."""
import numpy as np
import rasterio

from audit_models import EVIDENCE, ROOT, PARTS, read, sha, verified_protocol, write
from floodvalue import paths
from floodvalue.chips import Chip, layer_path
from floodvalue.split import touching_neighbours


def main():
    protocol, _ = verified_protocol()
    split = read(ROOT/"artifacts/split.json")["parts"]
    historical = ROOT/"artifacts/data_check.json"
    bounds = {c["chip"]: c["bounds"] for c in read(historical)["chips"]}
    grids = []
    for part in PARTS:
        for name in split[part]:
            chip = Chip.from_name(name)
            with rasterio.open(layer_path(paths.SEN1FLOODS11_DIR, chip, "S1Hand")) as s1:
                np.testing.assert_allclose(s1.bounds, bounds[name], atol=1e-12, rtol=0)
                assert s1.count == 2 and s1.dtypes == ("float32", "float32")
                for layer, count in (("LabelHand", 1), ("S2Hand", 13)):
                    with rasterio.open(layer_path(paths.SEN1FLOODS11_DIR, chip, layer)) as src:
                        assert (src.crs, src.shape, src.count) == (s1.crs, s1.shape, count)
                        # Label GeoTIFF coefficients differ at ~1e-18 degrees due to
                        # serialization; this tolerance is far below a pixel.
                        np.testing.assert_allclose(tuple(src.transform), tuple(s1.transform), atol=1e-12, rtol=0)
            grids.append(name)
    dev = split["train"] + split["valid"]
    overlap = {part: {reference: touching_neighbours(split[part], split[reference], bounds)
                      for reference in ("train", "valid")} for part in PARTS}
    declared = {}
    test = set(n for part in PARTS for n in split[part])
    for package in ("model", "next_optical"):
        for file in (ROOT/"artifacts"/package).rglob("train_summary.json"):
            metadata = read(file)
            used = set(metadata.get("train_chips", [])) | set(metadata.get("valid_chips", []))
            if used:
                assert not used.intersection(test), file
                declared[str(file.relative_to(ROOT))] = {"development_chips": len(used), "test_id_overlap": 0,
                                                       "sha256": sha(file)}
    evidence = {"historical_development_bounds_source": "artifacts/data_check.json",
                "historical_bounds_sha256": sha(historical), "fresh_checked_test_grids": len(grids),
                "test_bounds_match_historical": True, "adjacency": overlap,
                "counts": {part: {key: len(value) for key, value in data.items()} for part, data in overlap.items()},
                "test_core_without_train_or_validation_neighbours": [n for n in split["test_core"]
                    if n not in overlap["test_core"]["train"] and n not in overlap["test_core"]["valid"]],
                "declared_training_provenance": declared,
                "limits": "Development bounds and training lists are stored provenance, not independently recreated training; test grids checked on freshly downloaded rasters. Neighbour-free is not event-independent or blind."}
    write(EVIDENCE/"spatial_provenance.json", evidence)
    print(evidence["counts"])
    print("test_core without train/validation neighbours:", len(evidence["test_core_without_train_or_validation_neighbours"]))


if __name__ == "__main__":
    main()
