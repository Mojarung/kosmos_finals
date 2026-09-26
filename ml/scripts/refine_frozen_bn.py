"""Additional validation-only probe motivated by the warm-start validation drop."""
import hashlib
import json
from pathlib import Path
import time

from refine_v1 import ROOT, tune, predict, select


def main():
    while not (ROOT/"selected_recipe.json").exists():
        time.sleep(10)
    previous = json.loads((ROOT/"selected_recipe.json").read_text())
    ranking = previous["selection"]["validation_ranking"]
    name = "frozen_lovasz"
    for seed in [0, 1]:
        tune(name, seed)
    _, pair = select(name+"_pair", [predict(name, seed, "none") for seed in [0, 1]])
    _, base = select("v1_pair", [predict("v1", seed, "none") for seed in [0, 1]])
    screening = {"candidate_iou": pair["validation"]["overall"]["micro"]["iou"],
                 "original_pair_iou": base["validation"]["overall"]["micro"]["iou"]}
    (ROOT/"frozen_bn_screen.json").write_text(json.dumps(screening, indent=2))
    if screening["candidate_iou"] > screening["original_pair_iou"]:
        for seed in [2, 3, 4]:
            tune(name, seed)
        for tta in ["none", "rot4", "d4"]:
            archives = [predict(name, seed, tta) for seed in range(5)]
            for label, items in [(name+"_five_"+tta, archives),
                                  ("v1_"+name+"_blend_"+tta,
                                   [predict("v1", seed, tta) for seed in range(5)]+archives)]:
                path, r = select(label, items)
                ranking.append({"path": str(path), "iou": r["validation"]["overall"]["micro"]["iou"],
                                "brier": r["validation"]["probability"]["brier"]})
    ranking.sort(key=lambda r: (-r["iou"], r["brier"]))
    chosen = json.loads(Path(ranking[0]["path"]).read_text())
    chosen["selection"] = {"validation_ranking": ranking, "frozen_at_unix": time.time(),
                           "test_seen_for_selection": False, "additional_probe": screening}
    payload = json.dumps(chosen, indent=2, allow_nan=False)
    (ROOT/"final_recipe.json").write_text(payload)
    (ROOT/"final_audit.json").write_text(json.dumps({"recipe_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                                                  "validation_ranking": ranking}, indent=2))
    print(json.dumps({"final_selection": ranking[0]}), flush=True)


if __name__ == "__main__":
    main()
