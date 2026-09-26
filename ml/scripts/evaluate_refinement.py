"""Score a validation-frozen recipe against the shipped model on identical test chips."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from floodvalue.cv import score_arrays
from floodvalue.refine import combine, load_recipe_calibration, member_path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--recipe", type=Path, default=Path("runs/refine/selected_recipe.json"))
    p.add_argument("--audit", type=Path, default=Path("runs/refine/selection_audit.json"))
    p.add_argument("--out", type=Path, default=Path("runs/refine/test_comparison.json"))
    args = p.parse_args()
    audit = json.loads(args.audit.read_text())
    if sha(args.recipe) != audit["recipe_sha256"]:
        raise ValueError("recipe changed after validation selection")
    candidate = json.loads(args.recipe.read_text())
    # Use the colleague's stored calibration and threshold, never refit them on test.
    baseline = json.loads(Path("runs/refine/recipes/v1_none_platt.json").read_text())
    original_calibration = json.loads(Path("artifacts/evaluation/calibration.json").read_text())
    baseline.update(calibration=original_calibration["platt"],
                    threshold=original_calibration["threshold"], calibration_method="platt")
    result = {"candidate_recipe_sha256": sha(args.recipe), "started_at_unix": time.time(),
              "rule": "frozen parameters, identical original test chips and valid-pixel masks; no test fitting",
              "baseline_calibration": original_calibration, "parts": {}}
    for part in ["test_core", "test_adjacent", "bolivia"]:
        result["parts"][part] = {}
        for label, recipe in [("original", baseline), ("candidate", candidate)]:
            archives = []
            for metadata in recipe["members"]:
                member = member_path(metadata, recipe, args.recipe).resolve()
                key = hashlib.sha256(str(member).encode()).hexdigest()[:12]
                cache = Path("runs/refine/test_predictions") / f"{key}_{metadata['tta']}_{part}.npz"
                if sha(member/"model.safetensors") != metadata["weights_sha256"]:
                    raise ValueError("checkpoint changed since validation selection")
                subprocess.run([sys.executable, "-m", "floodvalue.refine", "predict", "--member", str(member),
                                "--part", part, "--tta", metadata["tta"], "--out", str(cache)], check=True)
                archives.append(cache)
            arrays = combine(archives, recipe["weights"], recipe["space"])
            score = score_arrays(arrays["names"].tolist(), arrays["logits"], arrays["target"], arrays["valid"],
                                 load_recipe_calibration(recipe), recipe["threshold"])
            result["parts"][part][label] = score
            print(json.dumps({"part": part, "model": label, "iou": score["overall"]["micro"]["iou"],
                              "brier": score["probability"]["brier"]}), flush=True)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, allow_nan=False))
    result["completed_at_unix"] = time.time()
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
