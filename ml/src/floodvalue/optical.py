"""Optional S1+S2 spectral classifier; chronology and S1 scoring masks stay explicit."""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import shutil

import joblib
import numpy as np
import rasterio
from scipy.ndimage import uniform_filter
from scipy.special import expit, logit
from sklearn.ensemble import HistGradientBoostingClassifier

from floodvalue import paths
from floodvalue.calibration import best_prob_threshold
from floodvalue.chips import Chip, layer_path, read_label, read_s1, s1_valid
from floodvalue.cv import score_arrays
from floodvalue.cv_calibration import fit_calibrator
from floodvalue.refine import combine, load_recipe_calibration, package_recipe, predict_recipe
from floodvalue.split import load_split
from floodvalue.tabular import texture_features

DATE_POLICY = "S2 calendar date no later than S1 and at most two days earlier; time-of-day unavailable"


def optical_date_allowed(s1_date, s2_date):
    delta = (date.fromisoformat(s1_date.replace("/", "-"))-
             date.fromisoformat(s2_date.replace("/", "-"))).days
    return 0 <= delta <= 2


def event_dates(root):
    features = json.loads((root/"Sen1Floods11_Metadata.geojson").read_text())["features"]
    result = {}
    for item in features:
        p = item["properties"]
        event = "Mekong" if p["location"] == "Cambodia" else p["location"]
        result[event] = {k: p[k] for k in ["s1_date", "s2_date"]}
    return result


def read_optical(root, chip):
    with rasterio.open(layer_path(root, chip, "S1Hand")) as ref:
        with rasterio.open(layer_path(root, chip, "S2Hand")) as src:
            if src.crs != ref.crs or src.transform != ref.transform or src.shape != ref.shape:
                raise ValueError("S2 and S1 grids differ; reproject before prediction")
            if src.count != 13:
                raise ValueError("expected the 13 official S2Hand spectral bands")
            return src.read().astype(np.float32)/10000.


def spectral_features(s1, s2):
    available = np.isfinite(s2).all(axis=0) & (np.nan_to_num(s2).max(axis=0) > 0)
    x = np.clip(np.nan_to_num(s2), 0, 2).astype(np.float32)
    blue, green, red, nir, swir1, swir2 = x[[1, 2, 3, 7, 11, 12]]
    ndwi = (green-nir)/(green+nir+1e-6)
    mndwi = (green-swir1)/(green+swir1+1e-6)
    ndvi = (nir-red)/(nir+red+1e-6)
    awei = blue+2.5*green-1.5*(nir+swir1)-.25*swir2
    ndmi = (nir-swir1)/(nir+swir1+1e-6)
    indices = [ndwi, mndwi, ndvi, awei, ndmi]
    extra = list(x)+indices
    count = available.astype(np.float32)
    for size in [3, 9]:
        n = np.maximum(uniform_filter(count, size, mode="reflect"), 1e-6)
        for value in [ndwi, mndwi, ndvi]:
            extra.append(uniform_filter(value*count, size, mode="reflect")/n)
    optic = np.stack(extra, axis=-1)
    optic[~available] = np.nan
    return np.concatenate([texture_features(s1), optic], axis=-1), available


def predict_optical(model, s1, s2):
    f, available = spectral_features(s1, s2)
    flat = f.reshape(-1, f.shape[-1])
    p = np.concatenate([model.predict_proba(flat[i:i+65536])[:, 1]
                        for i in range(0, len(flat), 65536)])
    return p.reshape(f.shape[:2]).astype(np.float32), available


def _resolve(recipe_path, recipe, key):
    p = Path(recipe[key])
    return p if p.is_absolute() else (recipe_path.parent if recipe.get("member_root") == "recipe"
                                    else paths.PIPELINE_ROOT)/p


def package_optical_recipe(source, destination):
    recipe = json.loads(source.read_text())
    member = _resolve(source, recipe, "optical_member")
    if hashlib.sha256((member/"model.joblib").read_bytes()).hexdigest() != recipe["weights_sha256"]:
        raise ValueError("optical weights changed after selection")
    target = destination/"optical"
    target.mkdir(parents=True, exist_ok=True)
    for name in ["model.joblib", "config.json", "train_summary.json"]:
        shutil.copy2(member/name, target/name)
    base_path = package_recipe(_resolve(source, recipe, "base_recipe"), destination/"radar")
    recipe.update(optical_member="optical", base_recipe="radar/recipe.json", member_root="recipe",
                  base_recipe_sha256=hashlib.sha256(base_path.read_bytes()).hexdigest())
    destination.mkdir(parents=True, exist_ok=True)
    result = destination/"recipe.json"
    result.write_text(json.dumps(recipe, indent=2, allow_nan=False))
    return result


def predict_optical_recipe(recipe_path, s1_chips, s2_chips, dates):
    """Return calibrated water probabilities, cross-model spread and optical usage."""
    recipe = json.loads(recipe_path.read_text())
    if not len(s1_chips) == len(s2_chips) == len(dates):
        raise ValueError("S1, optional S2 and acquisition-date lists must have equal lengths")
    alpha = recipe["optical_weight"]
    if not 0 <= alpha <= 1:
        raise ValueError("invalid optical mixture weight")
    base_path = _resolve(recipe_path, recipe, "base_recipe")
    if recipe.get("base_recipe_sha256") and hashlib.sha256(base_path.read_bytes()).hexdigest() != recipe["base_recipe_sha256"]:
        raise ValueError("radar recipe changed after packaging")
    _, base, spread = predict_recipe(base_path, s1_chips, calibrated=False)
    checkpoint = _resolve(recipe_path, recipe, "optical_member")/"model.joblib"
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != recipe["weights_sha256"]:
        raise ValueError("optical checkpoint checksum mismatch")
    model = joblib.load(checkpoint)  # This is our locally trained, checksum-verified artifact.
    hybrid = base.copy()
    usage = []
    for i, (s1, s2, acquisition) in enumerate(zip(s1_chips, s2_chips, dates)):
        enabled = (s2 is not None and acquisition is not None and optical_date_allowed(**acquisition))
        if enabled and alpha > 0:
            optical, available = predict_optical(model, s1, s2)
            hybrid[i] = np.where(available, (1-alpha)*base[i]+alpha*optical, base[i])
            # Disagreement between the radar and optical classifiers remains meaningful
            # even when selection chooses optical weight 1. It is not a confidence interval.
            spread[i] = np.where(available, .5*np.abs(optical-base[i]), spread[i])
            count = int((available & s1_valid(s1)).sum())
        else:
            count = 0
        usage.append({"optical_used_pixels": count, "dates": acquisition,
                      "mode": "S1+S2" if count else "S1 fallback"})
    z = logit(np.clip(hybrid, 1e-6, 1-1e-6)).astype(np.float32)
    return recipe, load_recipe_calibration(recipe).apply(z), spread, usage


def infer_optical(s1_path, recipe_path, out_dir, s2_path=None, acquisition=None):
    from floodvalue.infer import PROB_NODATA, MASK_NODATA, _write, sha256
    with rasterio.open(s1_path) as src:
        s1, profile = src.read().astype(np.float32), dict(src.profile)
        s2 = None
        if s2_path is not None:
            with rasterio.open(s2_path) as optical:
                if (optical.crs != src.crs or optical.transform != src.transform or optical.shape != src.shape
                        or optical.count != 13):
                    raise ValueError("optical GeoTIFF must have 13 bands on exactly the S1 grid")
                s2 = optical.read().astype(np.float32)/10000.
    config = json.loads(recipe_path.read_text())
    if "fusion_members" in config:
        from floodvalue.fusion_infer import predict_fusion_recipe
        predictor = predict_fusion_recipe
    else:
        predictor = predict_optical_recipe
    recipe, probability, spread, usage = predictor(recipe_path, [s1], [s2], [acquisition])
    valid = s1_valid(s1)
    files = {
        "flood_probability.tif": (np.where(valid, probability[0], PROB_NODATA), "float32", PROB_NODATA),
        "flood_mask.tif": (np.where(valid, probability[0] >= recipe["threshold"], MASK_NODATA), "uint8", MASK_NODATA),
        "flood_uncertainty.tif": (np.where(valid, spread[0], PROB_NODATA), "float32", PROB_NODATA),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, (array, dtype, nodata) in files.items():
        _write(out_dir/name, profile, array, dtype, nodata)
    result = {"s1": str(s1_path), "s2": str(s2_path) if s2_path else None, "recipe": str(recipe_path),
              "recipe_sha256": sha256(recipe_path), "threshold": recipe["threshold"], **usage[0],
              "valid_pixels": int(valid.sum()), "date_policy": DATE_POLICY,
              "uncertainty": config.get("uncertainty", "half absolute S1/S2 probability disagreement when optical is used; radar ensemble std otherwise; not a confidence interval"),
              "files": {name: sha256(out_dir/name) for name in files}}
    (out_dir/"inference.json").write_text(json.dumps(result, indent=2))
    return result


def train_and_validate(out):
    root = paths.SEN1FLOODS11_DIR
    split = load_split(paths.ARTIFACTS_DIR/"split.json")
    dates = event_dates(root)
    eligible = {event: optical_date_allowed(**d) for event, d in dates.items()}
    train_names = [n for n in split["train"] if eligible[Chip.from_name(n).event]]
    config = {"kind": "s1_s2_hist_gradient_boosting", "seed": 72, "pixels_per_chip": 3500,
              "features": "S1 texture plus 13 reflectances, five spectral indices and local means",
              "max_iter": 500, "max_leaf_nodes": 31, "learning_rate": .06,
              "date_policy": DATE_POLICY, "dates": dates}
    out.mkdir(parents=True, exist_ok=True)
    checkpoint = out/"model.joblib"
    if checkpoint.exists():
        previous = json.loads((out/"config.json").read_text())
        if previous != config:
            raise ValueError("existing optical model has a different configuration")
        model = joblib.load(checkpoint)
    else:
        rng = np.random.default_rng(config["seed"])
        xs, ys = [], []
        for i, name in enumerate(train_names):
            chip = Chip.from_name(name)
            s1, label = read_s1(root, chip), read_label(root, chip)
            f, available = spectral_features(s1, read_optical(root, chip))
            valid = s1_valid(s1) & (label != -1) & available
            idx = np.flatnonzero(valid)
            idx = rng.choice(idx, size=min(config["pixels_per_chip"], len(idx)), replace=False)
            xs.append(f.reshape(-1, f.shape[-1])[idx])
            ys.append(label.ravel()[idx] == 1)
            if i % 40 == 0:
                print("training feature chips", i, flush=True)
        x, y = np.concatenate(xs), np.concatenate(ys)
        print("fit", x.shape, "water", float(y.mean()), flush=True)
        model = HistGradientBoostingClassifier(max_iter=config["max_iter"], learning_rate=config["learning_rate"],
                                               max_leaf_nodes=config["max_leaf_nodes"], min_samples_leaf=40,
                                               l2_regularization=5., random_state=config["seed"],
                                               early_stopping=True, validation_fraction=.1, n_iter_no_change=30)
        model.fit(x, y)
        joblib.dump(model, checkpoint, compress=3)
        (out/"config.json").write_text(json.dumps(config, indent=2))
        (out/"train_summary.json").write_text(json.dumps({"train_chips": train_names,
            "valid_chips": split["valid"], "n_iter": int(model.n_iter_), "sampled_training_pixels": len(y)}, indent=2))
        del x, y, xs, ys
    archive = out/"valid_predictions.npz"
    if not archive.exists():
        probabilities, availabilities = [], []
        for i, name in enumerate(split["valid"]):
            chip = Chip.from_name(name)
            s1 = read_s1(root, chip)
            if eligible[chip.event]:
                p, available = predict_optical(model, s1, read_optical(root, chip))
            else:
                p, available = np.zeros(s1.shape[1:], np.float32), np.zeros(s1.shape[1:], bool)
            probabilities.append(p)
            availabilities.append(available)
            if i % 10 == 0:
                print("validation chips", i, flush=True)
        np.savez_compressed(archive, names=split["valid"], probability=np.stack(probabilities),
                            optical_available=np.stack(availabilities))
    with np.load(archive) as data:
        if data["names"].tolist() != split["valid"]:
            raise ValueError("optical validation archive does not match split")
        p, available = data["probability"], data["optical_available"]
    base_recipe_path = Path("runs/goal072/recipes/v1_weighted_probability_mean.json")
    base_recipe = json.loads(base_recipe_path.read_text())
    base_paths = [Path(f"runs/refine/predictions/v1_m{i}_rot4_valid.npz") for i in range(5)]
    base = combine(base_paths, base_recipe["weights"], base_recipe["space"])
    base_p = expit(base["logits"])
    mask = base["valid"]  # Never remove cloudy, optical-invalid or optical-unavailable scoring pixels.
    rows = []
    for alpha in [0., .25, .5, .75, 1.]:
        hybrid = np.where(available, (1-alpha)*base_p+alpha*p, base_p)
        z = logit(np.clip(hybrid, 1e-6, 1-1e-6)).astype(np.float32)
        cal = fit_calibrator(z[mask], base["target"][mask], "isotonic")
        threshold, _ = best_prob_threshold(cal.apply(z[mask]), base["target"][mask])
        score = score_arrays(base["names"].tolist(), z, base["target"], mask, cal, threshold)
        recipe = {"mode": "S1+S2 with S1-only fallback", "optical_weight": alpha,
                  "optical_member": str(out), "weights_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                  "base_recipe": str(base_recipe_path), "date_policy": DATE_POLICY,
                  "calibration_method": "isotonic", "calibration": cal.as_dict(),
                  "threshold": threshold, "validation": score,
                  "optical_available_fraction": float(available[mask].mean()),
                  "rule": "original validation only; identical S1 valid pixels; no test reads"}
        destination = out/f"recipe_alpha{alpha:.2f}.json"
        destination.write_text(json.dumps(recipe, indent=2, allow_nan=False))
        row = {"recipe": str(destination), "alpha": alpha,
               "iou": score["overall"]["micro"]["iou"], "event_mean_iou": score["event_mean_iou"],
               "brier": score["probability"]["brier"]}
        rows.append(row)
        (out/"validation_ranking.json").write_text(json.dumps(sorted(rows, key=lambda x: -x["iou"]), indent=2))
        print(json.dumps(row), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("runs/goal072/optical"))
    args = parser.parse_args()
    train_and_validate(args.out)


if __name__ == "__main__":
    main()
