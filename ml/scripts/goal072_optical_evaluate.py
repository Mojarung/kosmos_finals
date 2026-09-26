"""Freeze optical selection from valid/event CV, then evaluate the original final splits."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import joblib
import numpy as np
from scipy.special import expit, logit
from sklearn.metrics import roc_auc_score

from floodvalue import paths
from floodvalue.calibration import Platt
from floodvalue.chips import Chip, read_s1
from floodvalue.cv import score_arrays
from floodvalue.optical import event_dates, optical_date_allowed, read_optical, predict_optical, package_optical_recipe
from floodvalue.refine import cache_predictions, combine, load_recipe_calibration, member_path
from floodvalue.split import load_split
from export_refinement_results import bootstrap_delta

ROOT = Path("runs/goal072")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def radar_caches(recipe, recipe_path, part, tta_override=None):
    archives = []
    for metadata in recipe["members"]:
        member = member_path(metadata, recipe, recipe_path).resolve()
        if sha(member/"model.safetensors") != metadata["weights_sha256"]:
            raise ValueError("radar weights changed after selection")
        tta = tta_override or metadata["tta"]
        key = hashlib.sha256(str(member).encode()).hexdigest()[:12]
        cache = Path("runs/refine/test_predictions")/f"{key}_{tta}_{part}.npz"
        cache_predictions(member, part, tta, cache)
        archives.append(cache)
    return archives


def freeze():
    selected = ROOT/"optical_release_recipe.json"
    audit_path = ROOT/"optical_release_audit.json"
    if selected.exists() and audit_path.exists():
        if sha(selected) != json.loads(audit_path.read_text())["recipe_sha256"]:
            raise ValueError("frozen optical recipe changed")
        return selected
    ranking = json.loads((ROOT/"optical/validation_ranking.json").read_text())
    summary = []
    for row in ranking:
        events, counts, brier_sum, pixels = {}, np.zeros(4), 0., 0
        for fold in range(3):
            path = ROOT/"optical_cv"/f"fold{fold}"/f"result_alpha{row['alpha']:.2f}.json"
            result = json.loads(path.read_text())
            for event, metrics in result["score"]["events"].items():
                if event in events:
                    raise ValueError("event appears in multiple outer folds")
                events[event] = metrics["micro"]["iou"]
            m = result["score"]["overall"]["micro"]
            counts += [m[k] for k in ["tp", "fp", "fn", "tn"]]
            n = result["score"]["probability"]["n_pixels"]
            brier_sum += n*result["score"]["probability"]["brier"]
            pixels += n
        summary.append({**row, "cv_event_mean_iou": float(np.mean(list(events.values()))),
                        "cv_pooled_iou": float(counts[0]/counts[:3].sum()),
                        "cv_brier": brier_sum/pixels, "cv_events": events})
    # Prefer event transfer among the practically tied original-validation finalists.
    valid_best = max(r["iou"] for r in summary)
    eligible = [r for r in summary if r["iou"] >= valid_best-.002]
    winner = max(eligible, key=lambda r: (r["cv_event_mean_iou"], -r["cv_brier"]))
    recipe = json.loads(Path(winner["recipe"]).read_text())
    base_path = paths.PIPELINE_ROOT/recipe["base_recipe"]
    recipe["base_recipe_sha256"] = sha(base_path)
    recipe["selection"] = {"rule": "maximum event-CV mean IoU among original-validation variants within .002 of best",
                           "ranking": summary, "selected_alpha": winner["alpha"],
                           "frozen_at_unix": time.time(), "new_final_test_predictions_seen": False}
    selected.write_text(json.dumps(recipe, indent=2, allow_nan=False))
    audit = {"recipe_sha256": sha(selected), "selection": recipe["selection"],
             "model_sha256": recipe["weights_sha256"], "base_recipe_sha256": sha(base_path),
             "source_sha256": {str(p): sha(p) for p in [Path(__file__), Path("src/floodvalue/optical.py"),
                              Path("src/floodvalue/refine.py"), Path("scripts/goal072_optical_cv.py")]}}
    audit_path.write_text(json.dumps(audit, indent=2))
    (ROOT/"optical_cv_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"frozen": str(selected), "alpha": winner["alpha"],
                      "valid_iou": winner["iou"], "cv_event_iou": winner["cv_event_mean_iou"]}), flush=True)
    return selected


def main():
    while not (ROOT/"optical_cv/fold2/result_alpha1.00.json").exists():
        time.sleep(10)
    selected = freeze()
    recipe = json.loads(selected.read_text())
    base_path = paths.PIPELINE_ROOT/recipe["base_recipe"]
    if sha(base_path) != recipe["base_recipe_sha256"]:
        raise ValueError("radar recipe changed after validation selection")
    base_recipe = json.loads(base_path.read_text())
    weights = np.asarray(base_recipe["weights"], np.float32)
    weights /= weights.sum()
    alpha = recipe["optical_weight"]
    checkpoint = paths.PIPELINE_ROOT/recipe["optical_member"]/"model.joblib"
    if sha(checkpoint) != recipe["weights_sha256"]:
        raise ValueError("optical weights changed")
    model = joblib.load(checkpoint)
    subprocess.run([sys.executable, "scripts/goal072_download_s2.py", "--parts", "test_core", "test_adjacent", "bolivia"], check=True)
    original_calibration = json.loads((paths.ARTIFACTS_DIR/"evaluation/calibration.json").read_text())
    split = load_split(paths.ARTIFACTS_DIR/"split.json")
    dates = event_dates(paths.SEN1FLOODS11_DIR)
    out_path = ROOT/"optical_test_comparison.json"
    result = {"recipe_sha256": sha(selected), "selection": recipe["selection"], "parts": {},
              "rule": "frozen original validation/event CV selection; identical S1 valid pixels on original test splits",
              "started_at_unix": time.time()}
    if out_path.exists():
        result = json.loads(out_path.read_text())
        if result["recipe_sha256"] != sha(selected):
            raise ValueError("previous evaluation belongs to another frozen recipe")
    for part in ["test_core", "test_adjacent", "bolivia"]:
        if part in result["parts"]:
            continue
        archives = radar_caches(base_recipe, base_path, part)
        base = combine(archives, weights, base_recipe["space"])
        base_p = expit(base["logits"])
        second_moment = np.zeros_like(base_p)
        for weight, path in zip(weights, archives):
            with np.load(path) as a:
                second_moment += weight*expit(a["logits"])**2
        spread = np.sqrt(np.maximum(second_moment-base_p**2, 0))
        hybrid = base_p.copy()
        usage = {}
        for i, name in enumerate(split[part]):
            chip = Chip.from_name(name)
            if optical_date_allowed(**dates[chip.event]):
                s1 = read_s1(paths.SEN1FLOODS11_DIR, chip)
                p, available = predict_optical(model, s1, read_optical(paths.SEN1FLOODS11_DIR, chip))
                hybrid[i] = np.where(available, (1-alpha)*base_p[i]+alpha*p, base_p[i])
                spread[i] = np.where(available, .5*np.abs(p-base_p[i]), spread[i])
                usage[name] = int((available & base["valid"][i]).sum())
            else:
                usage[name] = 0
        z = logit(np.clip(hybrid, 1e-6, 1-1e-6)).astype(np.float32)
        cal = load_recipe_calibration(recipe)
        score = score_arrays(base["names"].tolist(), z, base["target"], base["valid"], cal, recipe["threshold"])
        control = score_arrays(base["names"].tolist(), base["logits"], base["target"], base["valid"],
                               load_recipe_calibration(base_recipe), base_recipe["threshold"])
        fallback = score_arrays(base["names"].tolist(), base["logits"], base["target"], base["valid"],
                                cal, recipe["threshold"])
        original_arrays = combine(radar_caches(base_recipe, base_path, part, "none"), [1.]*len(weights), "logits")
        original = score_arrays(original_arrays["names"].tolist(), original_arrays["logits"],
                                original_arrays["target"], original_arrays["valid"],
                                Platt(**original_calibration["platt"]), original_calibration["threshold"])
        del original_arrays
        if set(original["per_chip"]) != set(score["per_chip"]):
            raise ValueError("comparison chip sets differ")
        for name in score["per_chip"]:
            if any(original["per_chip"][name][k] != score["per_chip"][name][k] for k in ["n_pixels", "n_water"]):
                raise ValueError("final test scoring pixel counts changed")
        mask = base["valid"]
        predicted = cal.apply(z) >= recipe["threshold"]
        errors = (predicted[mask] != base["target"][mask])
        uncertainty = spread[mask]
        indices = np.random.default_rng(72).choice(len(errors), min(1_000_000, len(errors)), replace=False)
        auc = float(roc_auc_score(errors[indices], uncertainty[indices]))
        result["parts"][part] = {"original": original, "radar_control": control, "s1_s2": score,
                                  "s1_fallback": fallback,
                                  "optical_used_scored_pixels": usage,
                                  "uncertainty_error_auc": {"auroc": auc, "sampled_pixels": len(indices), "seed": 72},
                                  "paired_bootstrap": bootstrap_delta(original, score)}
        np.savez_compressed(ROOT/f"optical_{part}_predictions.npz", names=base["names"], logits=z,
                            target=base["target"], valid=base["valid"], uncertainty=spread)
        out_path.write_text(json.dumps(result, indent=2, allow_nan=False))
        print(json.dumps({"part": part, "original_iou": original["overall"]["micro"]["iou"],
                          "radar_control_iou": control["overall"]["micro"]["iou"],
                          "s1_s2_iou": score["overall"]["micro"]["iou"],
                          "s1_s2_brier": score["probability"]["brier"], "uncertainty_error_auroc": auc}), flush=True)
    result["completed_at_unix"] = time.time()
    out_path.write_text(json.dumps(result, indent=2, allow_nan=False))
    package = package_optical_recipe(selected, paths.ARTIFACTS_DIR/"goal072_optical")
    print("packaged", str(package), flush=True)


if __name__ == "__main__":
    main()
