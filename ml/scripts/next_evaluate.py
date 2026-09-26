"""Evaluate exactly one frozen next-series candidate; never tune on these outputs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
from scipy.special import expit, logit

from floodvalue import paths
from floodvalue.chips import Chip, read_s1
from floodvalue.cv import score_arrays
from floodvalue.fusion import fuse, image_quality
from floodvalue.fusion_calibration import fusion_calibration
from floodvalue.fusion_infer import optical_ensemble, package_fusion, sha
from floodvalue.optical import event_dates, optical_date_allowed, read_optical
from floodvalue.refine import combine, load_recipe_calibration
from floodvalue.split import load_split
from export_refinement_results import bootstrap_delta
from goal072_optical_evaluate import radar_caches

ROOT = Path("runs/next")


def optical_worker(package, part, worker, workers):
    recipe = json.loads(package.read_text())
    audit = json.loads((ROOT/"release_audit.json").read_text())
    if recipe["source_recipe_sha256"] != audit["recipe_sha256"]:
        raise ValueError("worker recipe was not frozen")
    fingerprint = sha(package)
    dates = event_dates(paths.SEN1FLOODS11_DIR)
    directory = ROOT/"optical_test_cache"/part
    directory.mkdir(parents=True, exist_ok=True)
    names = load_split(paths.ARTIFACTS_DIR/"split.json")[part]
    for i, name in enumerate(names):
        if i % workers != worker:
            continue
        destination = directory/(name+".npz")
        if destination.exists():
            with np.load(destination) as a:
                if str(a["recipe_sha256"]) != fingerprint:
                    raise ValueError("optical cache belongs to another frozen recipe")
            continue
        chip = Chip.from_name(name)
        s1 = read_s1(paths.SEN1FLOODS11_DIR, chip)
        if optical_date_allowed(**dates[chip.event]):
            p, available, variance = optical_ensemble(package, recipe, s1, read_optical(paths.SEN1FLOODS11_DIR, chip))
        else:
            p, available, variance = np.zeros(s1.shape[1:], np.float32), np.zeros(s1.shape[1:], bool), np.zeros(s1.shape[1:], np.float32)
        temporary = destination.with_suffix(".tmp.npz")
        np.savez_compressed(temporary, probability=p, available=available, variance=variance, recipe_sha256=fingerprint)
        temporary.replace(destination)
        print("optical CPU", part, name, flush=True)


def cache_optical_parallel(package, part, workers):
    env = {**os.environ, "FLOODVALUE_DEVICE": "cpu", "OMP_NUM_THREADS": "4", "VECLIB_MAXIMUM_THREADS": "4"}
    processes, logs = [], []
    try:
        for i in range(workers):
            log = (ROOT/f"test_optical_{part}_{i}.log").open("a")
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable, "-u", __file__, "--worker", str(i),
                "--workers", str(workers), "--part", part, "--recipe", str(package)], env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT))
        for p in processes:
            if p.wait() != 0:
                raise RuntimeError("optical test worker failed; inspect runs/next/test_optical_*.log")
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
                p.wait()
        for log in logs:
            log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--worker", type=int)
    parser.add_argument("--part", choices=["test_core", "test_adjacent", "bolivia"])
    parser.add_argument("--recipe", type=Path)
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("workers must be positive")
    if args.worker is not None:
        if args.part is None or args.recipe is None or not 0 <= args.worker < args.workers:
            raise ValueError("worker requires recipe, part and a valid worker index")
        optical_worker(args.recipe, args.part, args.worker, args.workers)
        return
    source = ROOT/"release_recipe.json"
    audit = json.loads((ROOT/"release_audit.json").read_text())
    if sha(source) != audit["recipe_sha256"]:
        raise ValueError("release recipe changed after freeze")
    source_recipe = json.loads(source.read_text())
    result_path = ROOT/"test_comparison.json"
    if source_recipe["name"] == "control":
        result_path.write_text(json.dumps({"status": "frozen control retained", "new_test_evaluation": False,
            "recipe_sha256": sha(source), "existing_results": "runs/goal072/optical_test_comparison.json"}, indent=2))
        print("No new candidate beat the development selection; existing control retained.", flush=True)
        return
    package = package_fusion(source, paths.ARTIFACTS_DIR/"next_optical")
    recipe = json.loads(package.read_text())
    base_path = package.parent/recipe["base_recipe"]
    base_recipe = json.loads(base_path.read_text())
    dates = event_dates(paths.SEN1FLOODS11_DIR)
    old = json.loads(Path("runs/goal072/optical_test_comparison.json").read_text())
    result = {"name": recipe["name"], "source_recipe_sha256": sha(source), "package_sha256": sha(package),
              "parts": {}, "started_at_unix": time.time(),
              "optical_device": "CPU", "optical_workers": args.workers,
              "rule": "one candidate frozen from development; original final parts and S1 masks; previous test already known"}
    if result_path.exists():
        result = json.loads(result_path.read_text())
        if result["source_recipe_sha256"] != sha(source) or result["package_sha256"] != sha(package):
            raise ValueError("test evaluation belongs to another frozen recipe")
    for part in ["test_core", "test_adjacent", "bolivia"]:
        if part in result["parts"]:
            continue
        # Reuse existing original-path radar caches, while checking identical weight hashes.
        original_base = Path(source_recipe["base_recipe"])
        original_recipe = json.loads(original_base.read_text())
        if [m["weights_sha256"] for m in original_recipe["members"]] != [m["weights_sha256"] for m in base_recipe["members"]]:
            raise ValueError("packaged radar members differ")
        archives = radar_caches(original_recipe, original_base, part)
        a = combine(archives, base_recipe["weights"], base_recipe["space"])
        radar = expit(a["logits"])
        hybrid = radar.copy()
        all_available = np.zeros_like(radar, dtype=bool)
        weights = np.asarray(base_recipe["weights"], np.float32)
        weights /= weights.sum()
        second = np.zeros_like(radar)
        for w, path in zip(weights, archives):
            with np.load(path) as z:
                second += w*expit(z["logits"])**2
        uncertainty = np.sqrt(np.maximum(second-radar**2, 0))
        cache_optical_parallel(package, part, args.workers)
        usage = {}
        for i, name in enumerate(a["names"]):
            chip = Chip.from_name(str(name))
            usage[str(name)] = 0
            if optical_date_allowed(**dates[chip.event]):
                with np.load(ROOT/"optical_test_cache"/part/(str(name)+".npz")) as cached:
                    if str(cached["recipe_sha256"]) != sha(package):
                        raise ValueError("optical cache checksum differs")
                    optical, available, variance = cached["probability"], cached["available"], cached["variance"]
                all_available[i] = available
                q = image_quality(read_optical(paths.SEN1FLOODS11_DIR, chip)) if recipe.get("gate") else None
                hybrid[i] = fuse(radar[i], optical, available, q, recipe.get("gate"), recipe["optical_weight"])
                uncertainty[i] = np.where(available, np.sqrt(.25*(optical-radar[i])**2+.5*variance), uncertainty[i])
                usage[str(name)] = int((available & a["valid"][i]).sum())
            if i % 20 == 0:
                print("frozen test inference", part, i, flush=True)
        logits = logit(np.clip(hybrid, 1e-6, 1-1e-6)).astype(np.float32)
        cal = fusion_calibration(recipe, all_available)
        score = score_arrays(a["names"].tolist(), logits, a["target"], a["valid"], cal, recipe["threshold"])
        fallback = score_arrays(a["names"].tolist(), a["logits"], a["target"], a["valid"],
                                fusion_calibration(recipe, np.zeros_like(all_available)), recipe["threshold"])
        radar_only = score_arrays(a["names"].tolist(), a["logits"], a["target"], a["valid"],
                                  load_recipe_calibration(base_recipe), base_recipe["threshold"])
        control = old["parts"][part]["s1_s2"]
        if set(control["per_chip"]) != set(score["per_chip"]):
            raise ValueError("test chip set changed")
        for name, r in score["per_chip"].items():
            if any(r[k] != control["per_chip"][name][k] for k in ["n_pixels", "n_water"]):
                raise ValueError("test scoring mask changed")
        result["parts"][part] = {"control": control, "candidate": score, "s1_fallback": fallback,
                                  "radar_only_own_calibration": radar_only,
                                  "paired_bootstrap_vs_control": bootstrap_delta(control, score),
                                  "optical_used_scored_pixels": usage}
        np.savez_compressed(ROOT/f"{part}_predictions.npz", names=a["names"], logits=logits,
                            target=a["target"], valid=a["valid"], uncertainty=uncertainty)
        result_path.write_text(json.dumps(result, indent=2, allow_nan=False))
        print(json.dumps({"part": part, "control": control["overall"]["micro"]["iou"],
                          "candidate": score["overall"]["micro"]["iou"]}), flush=True)
    result["completed_at_unix"] = time.time()
    result_path.write_text(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
