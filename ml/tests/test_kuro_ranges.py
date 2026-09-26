import importlib.util
import io
import json
from pathlib import Path
import tarfile

import numpy as np
import pytest


def test_partial_tar_ranges_only_yield_complete_samples():
    pytest.importorskip("shapely")
    location = Path(__file__).parents[1] / "scripts/prepare_kuro_pilot.py"
    spec = importlib.util.spec_from_file_location("prepare_kuro_pilot", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for sample in ("first", "second"):
            for field in ("flood_vv", "flood_vh", "sec1_vv", "sec1_vh", "sec2_vv", "sec2_vh",
                          "dem", "mask", "valid_mask", "info"):
                payload = io.BytesIO()
                if field == "info":
                    payload.write(json.dumps({"grid_id": sample}).encode())
                    suffix = "info.json"
                else:
                    np.save(payload, np.ones((1, 2, 2), dtype=np.uint8))
                    suffix = field + ".npy"
                entry = tarfile.TarInfo(sample + "." + suffix)
                entry.size = payload.tell()
                payload.seek(0)
                archive.addfile(entry, payload)
    data = stream.getvalue()
    assert [key for key, _ in module.read_samples(data)] == ["first", "second"]
    # The range starts in the first array's payload: the first sample is incomplete.
    assert [key for key, _ in module.read_samples(data[512:])] == ["second"]
    # Cutting off the second sample must not create a partly assembled training row.
    assert [key for key, _ in module.read_samples(data[:15*1024])] == ["first"]
