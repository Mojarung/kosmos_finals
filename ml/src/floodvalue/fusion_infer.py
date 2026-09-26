"""Portable inference for frozen optical ensembles with optional quality gating."""
import hashlib
import json
from pathlib import Path
import shutil

import joblib
import numpy as np
from scipy.special import logit

from floodvalue import paths
from floodvalue.chips import s1_valid
from floodvalue.fusion import fuse, image_quality
from floodvalue.fusion_calibration import fusion_calibration
from floodvalue.optical import optical_date_allowed, predict_optical, _resolve
from floodvalue.refine import package_recipe, predict_recipe


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_member(directory, member):
    for name, expected in member["checksums"].items():
        if sha(directory/name) != expected:
            raise ValueError(f"fusion member checksum mismatch: {member['name']}/{name}")


def member_spec(name, weight):
    if name == "old":
        directory = Path("runs/goal072/optical")
    elif name == "cnn":
        directory = Path("runs/next/cnn/full")
    else:
        directory = Path("runs/next/boost")/name/"full"
    files = ["config.json", "model.safetensors", "optical_norm.json"] if name == "cnn" else ["config.json", "model.joblib"]
    return {"name": name, "path": str(directory), "weight": float(weight),
            "kind": "cnn" if name == "cnn" else "tree", "tta": "none",
            "checksums": {f: sha(directory/f) for f in files}}


def package_fusion(source, destination):
    recipe = json.loads(source.read_text())
    if destination.exists() and (destination/"recipe.json").exists():
        previous = json.loads((destination/"recipe.json").read_text())
        if previous.get("source_recipe_sha256") != sha(source):
            raise ValueError("refusing to replace a different frozen fusion package")
        return destination/"recipe.json"
    destination.mkdir(parents=True, exist_ok=True)
    for member in recipe["fusion_members"]:
        root = source.parent if recipe.get("member_root") == "recipe" else paths.PIPELINE_ROOT
        directory = root/Path(member["path"])
        verify_member(directory, member)
        target = destination/"optical"/member["name"]
        target.mkdir(parents=True, exist_ok=True)
        for filename in [*member["checksums"], "train_summary.json", "provenance.json"]:
            if (directory/filename).exists():
                shutil.copy2(directory/filename, target/filename)
        member["path"] = str(target.relative_to(destination))
    base = _resolve(source, recipe, "base_recipe")
    if sha(base) != recipe["base_recipe_sha256"]:
        raise ValueError("radar recipe changed after selection")
    packaged = package_recipe(base, destination/"radar")
    recipe.update(base_recipe="radar/recipe.json", base_recipe_sha256=sha(packaged),
                  member_root="recipe", source_recipe_sha256=sha(source))
    target = destination/"recipe.json"
    target.write_text(json.dumps(recipe, indent=2, allow_nan=False))
    shutil.copy2(source, destination/"source_recipe.json")
    audit = source.parent/"release_audit.json"
    if audit.exists():
        if json.loads(audit.read_text())["recipe_sha256"] != sha(source):
            raise ValueError("release audit belongs to another source recipe")
        shutil.copy2(audit, destination/"selection_audit.json")
    return target


def optical_ensemble(recipe_path, recipe, s1, s2):
    values, weights = [], []
    available = None
    root = recipe_path.parent if recipe.get("member_root") == "recipe" else paths.PIPELINE_ROOT
    for member in recipe["fusion_members"]:
        if member["weight"] == 0:
            continue
        directory = root/Path(member["path"])
        verify_member(directory, member)
        if member["kind"] == "cnn":
            from floodvalue.optical_cnn import load_cnn, predict_cnn
            model, stats = load_cnn(directory)
            p, mask = predict_cnn(model, stats, [s1], [s2], member["tta"])
            p, mask = p[0], mask[0]
        elif member["kind"] == "tree":
            # Only our checksum-verified local training artifacts are loaded.
            try:
                model = joblib.load(directory/"model.joblib")
            except ModuleNotFoundError as exc:
                raise RuntimeError("This recipe needs research model dependencies: uv sync --group research") from exc
            except OSError as exc:
                if "libomp" not in str(exc):
                    raise
                raise RuntimeError("LightGBM needs OpenMP. On this Mac, set DYLD_LIBRARY_PATH to "
                                   ".venv/lib/python3.13/site-packages/torch/lib before starting Python; "
                                   "see research/NEXT_RESULTS.md") from exc
            p, mask = predict_optical(model, s1, s2)
        else:
            raise ValueError("unknown optical member kind")
        if available is not None and not np.array_equal(mask, available):
            raise ValueError("optical member availability differs")
        available = mask
        values.append(p)
        weights.append(member["weight"])
        del model
    if not weights or min(weights) < 0 or not np.isclose(sum(weights), 1):
        raise ValueError("invalid convex optical weights")
    mean = sum(w*p for w,p in zip(weights, values))
    variance = sum(w*(p-mean)**2 for w,p in zip(weights, values))
    return mean, available, variance


def predict_fusion_recipe(recipe_path, s1_chips, s2_chips, dates):
    if not len(s1_chips) == len(s2_chips) == len(dates):
        raise ValueError("S1, S2 and dates must have equal lengths")
    recipe = json.loads(recipe_path.read_text())
    base_path = _resolve(recipe_path, recipe, "base_recipe")
    if sha(base_path) != recipe["base_recipe_sha256"]:
        raise ValueError("radar recipe checksum mismatch")
    _, base, spread = predict_recipe(base_path, s1_chips, calibrated=False)
    hybrid = base.copy()
    all_available = np.zeros_like(base, dtype=bool)
    usage = []
    for i, (s1, s2, acquisition) in enumerate(zip(s1_chips, s2_chips, dates)):
        enabled = s2 is not None and acquisition is not None and optical_date_allowed(**acquisition)
        if enabled:
            optical, available, variance = optical_ensemble(recipe_path, recipe, s1, s2)
            all_available[i] = available
            quality = image_quality(s2) if recipe.get("gate") else None
            hybrid[i] = fuse(base[i], optical, available, quality, recipe.get("gate"), recipe["optical_weight"])
            # Equal mass for radar and optical experts; includes optical ensemble
            # disagreement. This measures model disagreement, not a confidence interval.
            disagreement = np.sqrt(.25*(optical-base[i])**2+.5*variance)
            spread[i] = np.where(available, disagreement, spread[i])
            count = int((available & s1_valid(s1)).sum())
        else:
            count = 0
        usage.append({"optical_used_pixels": count, "dates": acquisition,
                      "mode": "S1+S2" if count else "S1 fallback"})
    z = logit(np.clip(hybrid, 1e-6, 1-1e-6)).astype(np.float32)
    return recipe, fusion_calibration(recipe, all_available).apply(z), spread, usage
