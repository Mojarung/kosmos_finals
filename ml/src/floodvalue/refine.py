"""Improve the shipped ensemble on its original split; select on validation only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
from scipy.special import expit, logit

from floodvalue import paths
from floodvalue.calibration import Platt, best_prob_threshold
from floodvalue.cv import score_arrays
from floodvalue.cv_calibration import Isotonic, fit_calibrator
from floodvalue.chips import s1_valid
from floodvalue.dataset import load_raw
from floodvalue.ensemble import load_member
from floodvalue.features import normalize
from floodvalue.split import load_split
from floodvalue.train import predict_logits


SPATIAL_MODES = ("full", "scale050", "scale075", "scale125", "tiles256", "tiles384")


def predict_spatial(model, x, spatial="full"):
    """Geometry-only inference variants, with no access to labels or chip identity."""
    if spatial == "full":
        return predict_logits(model, x).cpu().numpy()
    import torch
    import torch.nn.functional as F
    if spatial.startswith("scale") and spatial in SPATIAL_MODES:
        scale = int(spatial[5:])/100
        shape = tuple(max(32, round(d*scale/32)*32) for d in x.shape[-2:])
        resized = F.interpolate(torch.from_numpy(x), size=shape, mode="bilinear", align_corners=False).numpy()
        z = predict_logits(model, resized)
        return F.interpolate(z[:, None], size=x.shape[-2:], mode="bilinear", align_corners=False)[:, 0].cpu().numpy()
    if spatial not in {"tiles256", "tiles384"}:
        raise ValueError(spatial)
    tile = int(spatial[5:])
    stride, border = tile//2, tile//4
    padded = np.pad(x, ((0, 0), (0, 0), (border, border), (border, border)), mode="reflect")
    h, w = padded.shape[-2:]
    if min(h, w) < tile:
        raise ValueError("input is too small for the selected tiling")
    ys = sorted(set(range(0, h-tile+1, stride)) | {h-tile})
    xs = sorted(set(range(0, w-tile+1, stride)) | {w-tile})
    window = np.maximum(np.outer(np.hanning(tile), np.hanning(tile)), .01).astype(np.float32)
    total, weight = np.zeros((len(x), h, w), np.float32), np.zeros((h, w), np.float32)
    for y in ys:
        for xx in xs:
            patch = np.ascontiguousarray(padded[:, :, y:y+tile, xx:xx+tile])
            total[:, y:y+tile, xx:xx+tile] += predict_logits(model, patch).cpu().numpy()*window
            weight[y:y+tile, xx:xx+tile] += window
    return (total/weight)[:, border:border+x.shape[-2], border:border+x.shape[-1]]


def predict_transforms(member, s1_chips, tta="none", spatial="full"):
    """Each member's spatial transforms are inverted before mean-logit aggregation."""
    x = np.stack([normalize(s1, member.stats)[0] for s1 in s1_chips])
    rotations = [0] if tta == "none" else range(4)
    flips = [False, True] if tta == "d4" else [False]
    if tta not in {"none", "rot4", "d4"}:
        raise ValueError(tta)
    total = np.zeros((len(x), *x.shape[-2:]), np.float32)
    for flip in flips:
        for k in rotations:
            transformed = np.flip(x, -1) if flip else x
            transformed = np.ascontiguousarray(np.rot90(transformed, k, axes=(-2, -1)))
            z = predict_spatial(member.model, transformed, spatial)
            z = np.rot90(z, -k, axes=(-2, -1))
            total += np.flip(z, -1) if flip else z
    return total / (len(rotations) * len(flips))


def cache_predictions(member_dir, part, tta, out, spatial="full"):
    split = load_split(paths.ARTIFACTS_DIR / "split.json")
    fingerprint = hashlib.sha256((member_dir/"model.safetensors").read_bytes()).hexdigest()
    meta = {"member": str(member_dir), "weights_sha256": fingerprint, "part": part,
            "tta": tta, "names": split[part]}
    if spatial != "full":
        meta["spatial"] = spatial
    metadata_path = out.with_suffix(".json")
    if out.exists() and metadata_path.exists():
        if json.loads(metadata_path.read_text()) != meta:
            raise ValueError(f"cache provenance mismatch: {out}")
        return
    member = load_member(member_dir)
    raw = load_raw(paths.SEN1FLOODS11_DIR, split[part])
    logits = predict_transforms(member, raw.s1, tta, spatial)
    labels = np.stack(raw.labels)
    valid = np.stack([s1_valid(s1) for s1 in raw.s1]) & (labels != -1)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, names=raw.names, logits=logits, target=labels == 1, valid=valid)
    metadata_path.write_text(json.dumps(meta, indent=2))
    print(json.dumps({"cache": str(out), "member": str(member_dir), "part": part, "tta": tta}), flush=True)


def combine(archives, weights, space="logits"):
    from floodvalue.cv_blend import blend_arrays
    loaded = []
    for path in archives:
        with np.load(path) as a:
            loaded.append({k: a[k] for k in a.files})
    result = {k: loaded[0][k] for k in ("names", "valid", "target")}
    result["logits"] = blend_arrays(loaded, weights, space)
    return result


def select_recipe(archives, weights, calibration, space="logits"):
    split = load_split(paths.ARTIFACTS_DIR / "split.json")
    metadata = [json.loads(p.with_suffix(".json").read_text()) for p in archives]
    if any(m["part"] != "valid" or m["names"] != split["valid"] for m in metadata):
        raise ValueError("selection requires the original validation part, never test")
    a = combine(archives, weights, space)
    mask = a["valid"]
    cal = fit_calibrator(a["logits"][mask], a["target"][mask], calibration)
    threshold, _ = best_prob_threshold(cal.apply(a["logits"][mask]), a["target"][mask])
    score = score_arrays(a["names"].tolist(), a["logits"], a["target"], mask, cal, threshold)
    return {"members": metadata, "weights": weights, "space": space, "calibration_method": calibration,
            "calibration": cal.as_dict(), "threshold": threshold, "validation": score,
            "rule": "original validation only; frozen before test_core evaluation"}


def load_recipe_calibration(recipe):
    data = recipe["calibration"]
    return (Isotonic(np.asarray(data["x"]), np.asarray(data["y"]))
            if recipe["calibration_method"] == "isotonic" else Platt(**data))


def member_path(metadata, recipe, recipe_path):
    path = Path(metadata["member"])
    if path.is_absolute():
        return path
    return (recipe_path.parent if recipe.get("member_root") == "recipe" else paths.PIPELINE_ROOT) / path


def package_recipe(recipe_path, destination):
    """Preserve exact checkpoint bytes; package independently of the shipped v1."""
    recipe = json.loads(recipe_path.read_text())
    destination.mkdir(parents=True, exist_ok=True)
    members = []
    for i, metadata in enumerate(recipe["members"]):
        src = member_path(metadata, recipe, recipe_path)
        target = destination/"members"/f"m{i}"
        if hashlib.sha256((src/"model.safetensors").read_bytes()).hexdigest() != metadata["weights_sha256"]:
            raise ValueError("source checkpoint changed since selection")
        target.mkdir(parents=True, exist_ok=True)
        for name in ["model.safetensors", "config.json", "norm_stats.json", "train_summary.json"]:
            shutil.copy2(src/name, target/name)
        members.append({"member": f"members/m{i}", "tta": metadata["tta"],
                        "weights_sha256": metadata["weights_sha256"], "source": str(src)})
        if metadata.get("spatial", "full") != "full":
            members[-1]["spatial"] = metadata["spatial"]
    recipe = {**recipe, "members": members, "member_root": "recipe"}
    path = destination/"recipe.json"
    path.write_text(json.dumps(recipe, indent=2, allow_nan=False))
    return path


def predict_recipe(recipe_path, s1_chips, calibrated=True):
    import gc
    recipe = json.loads(recipe_path.read_text())
    weights = np.asarray(recipe["weights"], np.float32)
    if (weights.shape != (len(recipe["members"]),) or not np.isfinite(weights).all()
            or (weights < 0).any() or weights.sum() <= 0):
        raise ValueError("invalid recipe weights")
    weights /= weights.sum()
    active = np.flatnonzero(weights > 0)
    weights = weights[active]
    logits = []
    for index in active:
        metadata = recipe["members"][index]
        path = member_path(metadata, recipe, recipe_path)
        if hashlib.sha256((path/"model.safetensors").read_bytes()).hexdigest() != metadata["weights_sha256"]:
            raise ValueError("recipe checkpoint checksum mismatch")
        member = load_member(path)
        logits.append(predict_transforms(member, s1_chips, metadata["tta"], metadata.get("spatial", "full")))
        del member
        gc.collect()
    logits = np.stack(logits)
    member_prob = expit(logits)
    if recipe["space"] == "logits":
        z = np.sum(logits * weights[:, None, None, None], axis=0)
    elif recipe["space"] == "probabilities":
        z = logit(np.clip(np.sum(member_prob * weights[:, None, None, None], axis=0), 1e-6, 1-1e-6))
    else:
        raise ValueError("unknown aggregation space")
    prob = load_recipe_calibration(recipe).apply(z) if calibrated else expit(z)
    mean_p = np.sum(member_prob * weights[:, None, None, None], axis=0)
    spread = np.sqrt(np.sum(weights[:, None, None, None]*(member_prob-mean_p)**2, axis=0))
    return recipe, prob, spread


def infer_recipe(s1_path, recipe_path, out_dir, baseline=None):
    import rasterio
    from floodvalue.infer import PROB_NODATA, MASK_NODATA, _write, sha256
    with rasterio.open(s1_path) as src:
        s1, profile = src.read().astype(np.float32), dict(src.profile)
    valid = s1_valid(s1)
    recipe, prob, spread = predict_recipe(recipe_path, [s1])
    threshold = recipe["threshold"]
    files = {
        "flood_probability.tif": (np.where(valid, prob[0], PROB_NODATA), "float32", PROB_NODATA),
        "flood_mask.tif": (np.where(valid, prob[0] >= threshold, MASK_NODATA), "uint8", MASK_NODATA),
        "flood_uncertainty.tif": (np.where(valid, spread[0], PROB_NODATA), "float32", PROB_NODATA),
    }
    if baseline is not None:
        water, _ = baseline.predict(s1)
        files["baseline_mask.tif"] = (np.where(valid, water, MASK_NODATA), "uint8", MASK_NODATA)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, (array, dtype, nodata) in files.items():
        _write(out_dir/name, profile, array, dtype, nodata)
    result = {"s1": str(s1_path), "recipe": str(recipe_path), "recipe_sha256": sha256(recipe_path),
              "threshold": threshold, "valid_pixels": int(valid.sum()),
              "calibration_method": recipe["calibration_method"],
              "uncertainty": "weighted std of uncalibrated model probabilities, after TTA per model; not a confidence interval",
              "files": {name: sha256(out_dir/name) for name in files}}
    (out_dir/"inference.json").write_text(json.dumps(result, indent=2))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    predict = commands.add_parser("predict")
    predict.add_argument("--member", type=Path, required=True)
    predict.add_argument("--part", choices=["valid", "test_core", "test_adjacent", "bolivia"], default="valid")
    predict.add_argument("--tta", choices=["none", "rot4", "d4"], default="none")
    predict.add_argument("--spatial", choices=SPATIAL_MODES, default="full")
    predict.add_argument("--out", type=Path, required=True)
    select = commands.add_parser("select")
    select.add_argument("--archives", type=Path, nargs="+", required=True)
    select.add_argument("--weights", type=float, nargs="+")
    select.add_argument("--calibration", choices=["platt", "isotonic", "identity"], default="isotonic")
    select.add_argument("--space", choices=["logits", "probabilities"], default="logits")
    select.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if args.command == "predict":
        cache_predictions(args.member, args.part, args.tta, args.out, args.spatial)
    else:
        result = select_recipe(args.archives, args.weights or [1.] * len(args.archives), args.calibration, args.space)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, allow_nan=False))
        print(json.dumps({"out": str(args.out), "validation_iou": result["validation"]["overall"]["micro"]["iou"],
                          "brier": result["validation"]["probability"]["brier"]}), flush=True)


if __name__ == "__main__":
    main()
