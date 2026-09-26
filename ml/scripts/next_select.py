"""Aggregate identical development folds and optionally freeze the selected recipe."""
import argparse
import json
from pathlib import Path
import time

import numpy as np

from floodvalue.fusion_infer import member_spec, sha
from floodvalue.next_boost import CONFIGS
from floodvalue.optical import DATE_POLICY
from next_fusion import SPECS
from next_branchcal import PARENTS
from next_radar_cv import SPECS as RADAR_SPECS
from next_radar_weights import WEIGHTS as RADAR_WEIGHTS

ROOT = Path("runs/next")
CONTROL_VALID = .8547464612151694


def score_file(path):
    r = json.loads(path.read_text())
    return r, r.get("score", r.get("validation"))


def candidates():
    yield "control", Path("runs/goal072/optical/recipe_alpha0.75.json"), [
        Path(f"runs/goal072/optical_cv/fold{i}/result_alpha0.75.json") for i in range(3)], ["old"], .75
    for member in [*CONFIGS, "cnn"]:
        base = ROOT/"cnn" if member == "cnn" else ROOT/"boost"/member
        for alpha in [.5, .75, 1.]:
            yield f"{member}_{alpha:.2f}", base/"full"/f"result_alpha{alpha:.2f}.json", [
                base/f"fold{i}"/f"result_alpha{alpha:.2f}.json" for i in range(3)], [member], alpha
    for name, spec in SPECS.items():
        base = ROOT/"fusion"/name
        yield name, base/"full/result.json", [base/f"fold{i}/result.json" for i in range(3)], spec["members"], .75
    for name, parent in PARENTS.items():
        base = ROOT/"fusion"/name
        members = ["old"] if parent == "control" else SPECS[parent]["members"]
        yield name, base/"full/result.json", [base/f"fold{i}/result.json" for i in range(3)], members, .75
    decision = ROOT/"radar_cv/decision.json"
    if decision.exists() and json.loads(decision.read_text())["status"] != "rejected":
        for name, members in RADAR_SPECS.items():
            base = ROOT/"radar_cv"/name
            yield name, base/"full/result.json", [base/f"fold{i}/result.json" for i in range(3)], members, .75
            for tag in RADAR_WEIGHTS:
                variant = name+"_"+tag
                base = ROOT/"radar_cv"/variant
                yield variant, base/"full/result.json", [base/f"fold{i}/result.json" for i in range(3)], members, .75


def aggregate(paths):
    events, chips = {}, {}
    totals = np.zeros(4, np.int64)
    brier_sum = 0.
    for path in paths:
        _, score = score_file(path)
        for event, r in score["events"].items():
            if event in events:
                raise ValueError("duplicate outer event")
            events[event] = r["micro"]["iou"]
        for name, r in score["per_chip"].items():
            if name in chips:
                raise ValueError("duplicate outer chip")
            chips[name] = {k: r[k] for k in ["n_pixels", "n_water"]}
        m = score["overall"]["micro"]
        totals += [m[k] for k in ["tp", "fp", "fn", "tn"]]
        brier_sum += score["probability"]["brier"]*score["probability"]["n_pixels"]
    return {"cv_event_iou": float(np.mean(list(events.values()))),
            "cv_pooled_iou": float(totals[0]/sum(totals[:3])), "cv_brier": float(brier_sum/sum(totals)),
            "events": events, "chips": chips, "confusion": dict(zip(["tp", "fp", "fn", "tn"], totals.tolist()))}


def summary():
    rows, pending = [], []
    for name, full, folds, members, alpha in candidates():
        if not all(p.exists() for p in [full, *folds]):
            pending.append(name)
            continue
        full_r, valid = score_file(full)
        cv = aggregate(folds)
        row = {"name": name, "valid_iou": valid["overall"]["micro"]["iou"],
               "valid_brier": valid["probability"]["brier"], "full_result": str(full),
               "fold_results": [str(p) for p in folds], "members": members, "alpha": alpha, **cv}
        rows.append(row)
    control = next(r for r in rows if r["name"] == "control")
    reference_chips = control["chips"]
    for row in rows:
        if row.pop("chips") != reference_chips:
            raise ValueError("CV scoring pixels differ from frozen control")
    return rows, pending


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    rows, pending = summary()
    control = next(r for r in rows if r["name"] == "control")
    for row in rows:
        keys = sorted(control["events"])
        delta = np.array([row["events"][k]-control["events"][k] for k in keys])
        draws = np.random.default_rng(78).choice(delta, (10000, len(delta)), replace=True).mean(axis=1)
        row["cv_event_delta"] = float(delta.mean())
        row["paired_event_bootstrap_95"] = np.quantile(draws, [.025, .975]).tolist()
        row["eligible"] = row["valid_iou"] >= CONTROL_VALID-.002
    rows.sort(key=lambda r: (-r["cv_event_iou"], r["cv_brier"]))
    eligible = [r for r in rows if r["eligible"]]
    winner = eligible[0]
    report = {"rule": "max mean event-CV IoU among validation >= control-.002; tie lower CV Brier; control eligible",
              "winner": winner["name"], "pending": pending, "candidates": rows,
              "note": "CV is model-selection data; bootstrap over ten events is descriptive, not a new blind test"}
    (ROOT/"selection_summary.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({"winner": winner["name"], "event_cv": winner["cv_event_iou"],
                      "valid": winner["valid_iou"], "completed": len(rows), "pending": pending}), flush=True)
    if not args.freeze:
        return
    if pending or len(list((ROOT/"radar_screen").glob("*.json"))) != 24:
        raise ValueError("all five research directions must finish before freeze")
    if not (ROOT/"cnn/full/rot4/ranking.json").exists():
        raise ValueError("CNN TTA screen is incomplete")
    decision = ROOT/"radar_cv/decision.json"
    if not decision.exists() or json.loads(decision.read_text())["status"] not in ["complete", "rejected"]:
        raise ValueError("radar follow-up is incomplete")
    destination = ROOT/"release_recipe.json"
    audit_path = ROOT/"release_audit.json"
    if destination.exists():
        audit = json.loads(audit_path.read_text())
        if sha(destination) != audit["recipe_sha256"]:
            raise ValueError("frozen recipe changed")
        print("already frozen", str(destination))
        return
    result, _ = score_file(Path(winner["full_result"]))
    learned = result.get("learned", {"weights": [1/len(winner["members"])]*len(winner["members"]), "gate": None})
    # SLSQP can leave numerical zeros around 1e-17. Do not load an entire model
    # for a contribution far below float32 resolution.
    optical_weights = np.asarray(learned["weights"], np.float64)
    optical_weights[optical_weights < 1e-12] = 0
    optical_weights /= optical_weights.sum()
    base = Path(result.get("base_recipe", "runs/goal072/recipes/v1_weighted_probability_mean.json"))
    recipe = {"mode": "S1+S2 with S1-only fallback", "name": winner["name"],
              "fusion_members": [member_spec(n, w) for n,w in zip(winner["members"], optical_weights)],
              "numerical_zero_weight_cutoff": 1e-12,
              "gate": learned["gate"], "optical_weight": winner["alpha"],
              "base_recipe": str(base), "base_recipe_sha256": sha(base),
              "calibration_method": result.get("calibration_method", "isotonic"), "calibration": result["calibration"],
              "threshold": result["threshold"], "date_policy": DATE_POLICY,
              "uncertainty": "sqrt(.25*(optical_mean-radar)^2+.5*optical_member_variance); radar std on fallback; not a confidence interval",
              "selection": {**report, "frozen_at_unix": time.time(), "new_final_test_predictions_seen": False}}
    destination.write_text(json.dumps(recipe, indent=2, allow_nan=False))
    sources = [*Path("src/floodvalue").glob("*.py"), *Path("scripts").glob("next_*.py"),
               Path("research/NEXT_PROTOCOL.md"), Path("research/cv_plan.json"), Path("artifacts/split.json"),
               Path("pyproject.toml"), Path("uv.lock")]
    audit_path.write_text(json.dumps({"recipe_sha256": sha(destination),
        "sources_sha256": {str(p): sha(p) for p in sources},
        "results_sha256": {str(p): sha(p) for r in rows for p in map(Path, [r["full_result"], *r["fold_results"]])}}, indent=2))
    print("frozen", str(destination), flush=True)


if __name__ == "__main__":
    main()
