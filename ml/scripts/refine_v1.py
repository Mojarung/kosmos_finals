"""Validation-only refinement of the five shipped members; does not score test."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path("runs/refine")


def call(args, log):
    with (ROOT/log).open("a") as stream:
        subprocess.run([sys.executable, *args], stdout=stream, stderr=subprocess.STDOUT, check=True)


def tune(name, seed):
    destination = ROOT/"members"/name/f"m{seed}"
    print(f"Fine-tune {name} m{seed}", flush=True)
    call(["-m", "floodvalue.finetune", "--source", f"artifacts/model/members/m{seed}",
          "--config", f"configs/refine/{name}.json", "--seed", str(seed), "--out", str(destination)],
         f"{name}_m{seed}.log")
    return destination


def predict(name, seed, tta):
    member = Path(f"artifacts/model/members/m{seed}") if name == "v1" else ROOT/"members"/name/f"m{seed}"
    out = ROOT/"predictions"/f"{name}_m{seed}_{tta}_valid.npz"
    call(["-m", "floodvalue.refine", "predict", "--member", str(member), "--tta", tta,
          "--out", str(out)], "predictions.log")
    return out


def select(name, archives):
    out = ROOT/"recipes"/(name+".json")
    call(["-m", "floodvalue.refine", "select", "--archives", *map(str, archives),
          "--calibration", "isotonic", "--out", str(out)], "selection.log")
    r = json.loads(out.read_text())
    print(name, r["validation"]["overall"]["micro"]["iou"], flush=True)
    return out, r


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--wait-baseline", action="store_true")
    args = p.parse_args()
    ROOT.mkdir(exist_ok=True)
    if args.wait_baseline:
        while not (ROOT/"recipes/v1_d4_isotonic.json").exists():
            time.sleep(10)
    candidates = []
    for name in ["continue_dice", "lovasz", "context_lovasz"]:
        archives = []
        for seed in [0, 1]:
            tune(name, seed)
            archives.append(predict(name, seed, "none"))
        path, r = select(name+"_pair", archives)
        candidates.append({"name": name, "validation_iou": r["validation"]["overall"]["micro"]["iou"],
                           "recipe": str(path)})
    winner = max(candidates, key=lambda c: c["validation_iou"])["name"]
    (ROOT/"training_selection.json").write_text(json.dumps({"candidates": candidates, "winner": winner,
        "rule": "best pooled IoU on original validation of the same first two source members"}, indent=2))
    print("Extend recipe to all five members:", winner, flush=True)
    for seed in [2, 3, 4]:
        tune(winner, seed)
    # All five original members remain eligible; TTA and identity/updated blends are selected on valid only.
    finalists = [ROOT/"recipes"/f"v1_{tta}_isotonic.json" for tta in ["none", "rot4", "d4"]]
    for tta in ["none", "rot4", "d4"]:
        archives = [predict(winner, seed, tta) for seed in range(5)]
        path, _ = select(winner+"_five_"+tta, archives)
        finalists.append(path)
        old = [predict("v1", seed, tta) for seed in range(5)]
        path, _ = select("v1_"+winner+"_blend_"+tta, old+archives)
        finalists.append(path)
    ranking = []
    for path in finalists:
        recipe = json.loads(path.read_text())
        ranking.append({"path": str(path), "iou": recipe["validation"]["overall"]["micro"]["iou"],
                        "brier": recipe["validation"]["probability"]["brier"]})
    ranking.sort(key=lambda x: (-x["iou"], x["brier"]))
    chosen = json.loads(Path(ranking[0]["path"]).read_text())
    chosen["selection"] = {"validation_ranking": ranking, "frozen_at_unix": time.time(),
                           "test_seen_for_selection": False}
    payload = json.dumps(chosen, indent=2, allow_nan=False)
    (ROOT/"selected_recipe.json").write_text(payload)
    (ROOT/"selection_audit.json").write_text(json.dumps({"recipe_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                                                      "validation_ranking": ranking}, indent=2))
    print(json.dumps({"selected": ranking[0], "frozen": "runs/refine/selected_recipe.json"}), flush=True)


if __name__ == "__main__":
    main()
