"""Freeze the context recipe on out-of-fold predictions, then score the test parts once.

Two tracks are frozen together:

* ``radar``: no optics at all (members without the ``s2`` group, or an optical member
  run with its optical channels zeroed, written ``name:nos2``);
* ``optical``: at least one member reads Sentinel-2 L2A where the date rule allows it
  and SCL says the pixel is clear; elsewhere its optical channels are zero.

``select`` picks for each track the equal-weight blend of up to three members with the
best pooled OOF IoU, then fits Platt a*z+b and the threshold on the same OOF logits.
``test`` scores ``test_core``, ``test_adjacent`` and ``bolivia`` once with both tracks;
the five fold models of a member are averaged. Baseline VH < -23.6 dB is scored on the
identical valid pixels.
"""

from __future__ import annotations

import argparse
import itertools
import json
import time

import numpy as np
import torch
from safetensors.torch import load_file

from floodvalue import paths
from floodvalue.chips import Chip, read_s1
from floodvalue.context import load_chip, target_of
from floodvalue.context_cv import (N_FOLDS, RUNS, ContextConfig, build_model, group_slices, load_part, predict,
                                   read_folds)
from floodvalue.metrics import Confusion
from floodvalue.train import DEVICE

RECIPE = paths.PIPELINE_ROOT / "research" / "fresh" / "recipe.json"
TEST_OUT = paths.PIPELINE_ROOT / "research" / "fresh" / "test_results.json"
THRESHOLDS = np.round(np.arange(0.20, 0.801, 0.01), 2)


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def parse_member(spec: str) -> tuple[str, bool]:
    """``name`` or ``name:nos2`` -> (run name, optics removed)."""
    name, _, mode = spec.partition(":")
    return name, mode == "nos2"


def uses_s2(spec: str) -> bool:
    name, no_s2 = parse_member(spec)
    config = json.loads((RUNS / name / "config.json").read_text(encoding="utf-8"))
    return "s2" in config["groups"] and not no_s2


def pooled_oof(names: list[str], specs: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """OOF logits [n_members, n_valid_pixels] and truth over the development chips."""
    zs, truth = [[] for _ in specs], []
    for n in names:
        stack = load_chip(n)
        v = stack.valid
        truth.append(target_of(stack)[v] > 0.5)
        for i, spec in enumerate(specs):
            name, no_s2 = parse_member(spec)
            zs[i].append(np.load(RUNS / name / ("oof_nos2" if no_s2 else "oof") / f"{n}.npy").astype(np.float32)[v])
    return np.stack([np.concatenate(z) for z in zs]), np.concatenate(truth)


def iou_curve(prob: np.ndarray, truth: np.ndarray) -> np.ndarray:
    """Pooled IoU at every threshold of THRESHOLDS via one histogram pass."""
    bins = np.clip((prob * 1000).astype(np.int32), 0, 999)
    pos = np.bincount(bins[truth], minlength=1000)[::-1].cumsum()[::-1]
    neg = np.bincount(bins[~truth], minlength=1000)[::-1].cumsum()[::-1]
    idx = (THRESHOLDS * 1000).round().astype(int)
    tp, fp = pos[idx], neg[idx]
    return tp / (truth.sum() + fp)


def fit_platt(z: np.ndarray, truth: np.ndarray, sample: int = 2_000_000, seed: int = 0) -> tuple[float, float]:
    from sklearn.linear_model import LogisticRegression

    rng = np.random.default_rng(seed)
    idx = rng.choice(z.size, size=min(sample, z.size), replace=False)
    lr = LogisticRegression(C=1e6, max_iter=1000).fit(z[idx, None], truth[idx])
    return float(lr.coef_[0, 0]), float(lr.intercept_[0])


def select_track(z: np.ndarray, truth: np.ndarray, specs: list[str], allowed, max_members: int) -> dict:
    candidates = []
    for size in range(1, min(max_members, len(specs)) + 1):
        for combo in itertools.combinations(range(len(specs)), size):
            members = [specs[i] for i in combo]
            if len({parse_member(m)[0] for m in members}) < len(members) or not allowed(members):
                continue
            curve = iou_curve(sigmoid(z[list(combo)].mean(axis=0)), truth)
            candidates.append({"members": members, "best_iou": float(curve.max()),
                               "iou_p05": float(curve[np.searchsorted(THRESHOLDS, 0.5)])})
    if not candidates:
        return None
    candidates.sort(key=lambda c: -c["best_iou"])
    chosen = candidates[0]
    blend = z[[specs.index(m) for m in chosen["members"]]].mean(axis=0)
    a, b = fit_platt(blend, truth)
    prob = sigmoid(a * blend + b)
    curve = iou_curve(prob, truth)
    return {"members": chosen["members"], "platt": {"a": a, "b": b},
            "threshold": float(THRESHOLDS[int(curve.argmax())]), "oof_iou_at_threshold": float(curve.max()),
            "oof_brier": float(np.mean((prob - truth) ** 2)), "candidates": candidates[:12]}


def select(specs: list[str], max_members: int = 3) -> dict:
    names = sorted(read_folds())
    z, truth = pooled_oof(names, specs)
    tracks = {
        "radar": select_track(z, truth, specs, lambda ms: not any(uses_s2(m) for m in ms), max_members),
        "optical": select_track(z, truth, specs, lambda ms: any(uses_s2(m) for m in ms), max_members),
    }
    tracks = {name: track for name, track in tracks.items() if track is not None}
    recipe = {
        "rule": "per track: equal-weight mean of fold-averaged logits of up to three members with the best pooled "
                "OOF IoU; Platt a*z+b and the threshold maximise pooled OOF IoU of that blend. Every number comes "
                "from OOF predictions of the development chips; test parts are not read.",
        "oof_pixels": int(truth.size),
        "tracks": tracks,
        "configs": {parse_member(m)[0]: json.loads((RUNS / parse_member(m)[0] / "config.json").read_text(encoding="utf-8"))
                    for t in tracks.values() for m in t["members"]},
    }
    RECIPE.write_text(json.dumps(recipe, indent=1), encoding="utf-8")
    return recipe


def fold_models(name: str) -> tuple[ContextConfig, list[torch.nn.Module]]:
    config = ContextConfig.from_json(RUNS / name / "config.json")
    models = []
    for k in range(N_FOLDS):
        model = build_model(config, pretrained=False)
        model.load_state_dict(load_file(str(RUNS / name / f"fold{k}" / "model.safetensors")))
        models.append(model.to(DEVICE).eval())
    return config, models


def fold_logits(spec: str, names: list[str]) -> dict[str, np.ndarray]:
    """Per-fold logits [5, H, W] float16 of one member on every chip (optics zeroed for ``:nos2``)."""
    name, no_s2 = parse_member(spec)
    config, models = fold_models(name)
    s2_positions = group_slices(config).get("s2", [])
    out = {}
    for start in range(0, len(names), 8):
        chunk = names[start:start + 8]
        part = load_part(chunk, config)
        if no_s2:
            part["x"][:, s2_positions] = 0
        z = torch.stack([predict(m, part["x"], config.tta) for m in models], dim=1)  # [n, folds, H, W]
        out.update({n: zi.half().cpu().numpy() for n, zi in zip(chunk, z)})
    del models
    torch.cuda.empty_cache()
    return out


def part_prediction(names: list[str], track: dict, per_member: dict, baseline: dict):
    """The v1 evaluation's PartPrediction for one track, so evaluate.score_part applies unchanged."""
    from floodvalue.chips import read_jrc
    from floodvalue.evaluate import PartPrediction

    a, b = track["platt"]["a"], track["platt"]["b"]
    band = 0 if baseline["channel"] == "VV" else 1
    fields = {k: [] for k in ("target", "valid", "permanent", "raw_prob", "prob", "member_std", "baseline_water")}
    member_conf = {m: Confusion() for m in track["members"]}
    for n in names:
        stack = load_chip(n)
        chip = Chip.from_name(n)
        folds = {m: per_member[m][n].astype(np.float32) for m in track["members"]}
        z = np.mean([f.mean(axis=0) for f in folds.values()], axis=0)
        per_model = sigmoid(a * np.concatenate(list(folds.values())) + b)
        target, valid = target_of(stack) > 0.5, stack.valid
        fields["target"].append(target)
        fields["valid"].append(valid)
        fields["permanent"].append(read_jrc(paths.SEN1FLOODS11_DIR, chip) == 1)
        fields["raw_prob"].append(sigmoid(z).astype(np.float32))
        fields["prob"].append(sigmoid(a * z + b).astype(np.float32))
        fields["member_std"].append(per_model.std(axis=0).astype(np.float32))
        fields["baseline_water"].append(read_s1(paths.SEN1FLOODS11_DIR, chip)[band] < baseline["threshold_db"])
        for m, f in folds.items():
            member_conf[m] = member_conf[m] + confusion((f.mean(axis=0) > 0)[valid], target[valid])
    return PartPrediction(names=tuple(names), **{k: np.stack(v) for k, v in fields.items()},
                          member_confusions=tuple(member_conf.values()))


def oof_pixels(track: dict):
    """Calibrated OOF p and mask errors of the track blend on the development chips (doubt selection)."""
    from floodvalue.doubt import PartPixels

    names = sorted(read_folds())
    z, truth = pooled_oof(names, track["members"])
    prob = sigmoid(track["platt"]["a"] * z.mean(axis=0) + track["platt"]["b"]).astype(np.float32)
    return PartPixels(len(names), prob, (prob >= track["threshold"]) != truth)


def test_pixels(pred, threshold: float):
    from floodvalue.doubt import PartPixels

    v = pred.valid
    return PartPixels(len(pred.names), pred.prob[v], (pred.prob[v] >= threshold) != pred.target[v], pred.member_std[v])


def reliability_record(track: dict, core: tuple, selection) -> dict:
    """The service's reliability file: test_core bins after Platt, and the OOF selection bins."""
    from floodvalue.reliability_check import calibration_metrics

    logits, target, chips = core
    a, b = track["platt"]["a"], track["platt"]["b"]
    names = sorted(read_folds())
    z, truth = pooled_oof(names, track["members"])
    blend = z.mean(axis=0)
    part = lambda lg, tg, n: {"chips": n, "pixels": int(tg.size), "water_share": round(float(tg.mean()), 6),  # noqa: E731
                              "calibrated": calibration_metrics(sigmoid(a * lg + b).astype(np.float32), tg),
                              "raw": calibration_metrics(sigmoid(lg).astype(np.float32), tg)}
    return {"command": "python -m floodvalue.context_final test", "platt": track["platt"],
            "rule": "Platt a*z+b fitted on pooled OOF of the development chips; test_core only reported",
            "test_core": part(logits, target, chips), "validation": part(blend, truth, len(names)),
            "validation_part": "oof_development"}


def confusion(pred: np.ndarray, truth: np.ndarray) -> Confusion:
    return Confusion(int((pred & truth).sum()), int((pred & ~truth).sum()), int((~pred & truth).sum()),
                     int((~pred & ~truth).sum()))


def test(parts: tuple[str, ...] = ("test_core", "test_adjacent", "bolivia")) -> dict:
    """Score every track once on the test parts; write v1-format evaluation files per track."""
    from floodvalue.doubt import build_record
    from floodvalue.evaluate import metrics_rows, probability_checks, score_part, write_metrics_csv

    if TEST_OUT.exists():
        raise SystemExit(f"{TEST_OUT} exists: the test parts were already scored once")
    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
    baseline = json.loads((paths.ARTIFACTS_DIR / "baseline" / "baseline.json").read_text(encoding="utf-8"))["model"]
    split = json.loads((paths.ARTIFACTS_DIR / "split.json").read_text(encoding="utf-8"))["parts"]
    started = time.time()
    names = [n for part in parts for n in split[part]]
    specs = sorted({m for t in recipe["tracks"].values() for m in t["members"]})
    per_member = {spec: fold_logits(spec, names) for spec in specs}
    summary = {"recipe": str(RECIPE.relative_to(paths.PIPELINE_ROOT)), "tracks": {}}
    core_logits: dict[str, tuple] = {}
    for track_name, track in recipe["tracks"].items():
        out_dir = TEST_OUT.parent / "evaluation" / track_name
        out_dir.mkdir(parents=True, exist_ok=True)
        report = {"threshold": track["threshold"], "platt": track["platt"], "members": track["members"],
                  "calibration_source": "pooled OOF predictions of the development chips (5 grouped folds)",
                  "parts": {}, "probability": {}, "members_alone_p05": {}}
        rows, reports = [], {}
        for part in parts:
            pred = part_prediction(split[part], track, per_member, baseline)
            scored = score_part(pred, track["threshold"])
            report["parts"][part] = scored
            report["probability"][part] = probability_checks(pred, track["threshold"])
            report["members_alone_p05"][part] = [c.as_dict() for c in pred.member_confusions]
            rows.extend(metrics_rows(part, scored))
            if part in ("test_core", "bolivia"):
                reports[part] = test_pixels(pred, track["threshold"])
            if part == "test_core":
                v = pred.valid
                core_logits[track_name] = (np.log(pred.raw_prob[v] / np.clip(1 - pred.raw_prob[v], 1e-7, None)),
                                           pred.target[v], len(pred.names))
            micro = {m: scored[f"all_water/{m}"]["overall"]["micro"] for m in ("main", "baseline")}
            summary["tracks"].setdefault(track_name, {})[part] = {"model_iou": micro["main"]["iou"],
                                                                  "baseline_iou": micro["baseline"]["iou"]}
            print(json.dumps({"track": track_name, "part": part, "model_iou": micro["main"]["iou"],
                              "baseline_iou": micro["baseline"]["iou"]}), flush=True)
        write_metrics_csv(rows, out_dir / "metrics.csv")
        (out_dir / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        selection = oof_pixels(track)
        doubt = build_record(selection, reports, track["threshold"])
        doubt.update({"source_part": "oof_development", "rule": doubt["rule"].replace(
            "the v1 ensemble", "the context ensemble").replace("on the valid part", "on pooled OOF of the development chips")})
        (out_dir / "doubt_threshold.json").write_text(json.dumps(doubt, indent=2), encoding="utf-8")
        (out_dir / "reliability_test_core.json").write_text(
            json.dumps(reliability_record(track, core_logits[track_name], selection), indent=2), encoding="utf-8")
    summary["seconds"] = round(time.time() - started, 1)
    TEST_OUT.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def package(out_dir=paths.ARTIFACTS_DIR / "context_model") -> dict:
    """Copy the fold weights of every recipe member as float16 and write the service recipe."""
    import hashlib

    from safetensors.torch import save_file

    from floodvalue.context import ALL_CHANNELS, S2_CHANNELS

    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    members = {}
    for name in sorted({parse_member(m)[0] for t in recipe["tracks"].values() for m in t["members"]}):
        weights = []
        for k in range(N_FOLDS):
            state = load_file(str(RUNS / name / f"fold{k}" / "model.safetensors"))
            target = out_dir / name / f"fold{k}.safetensors"
            target.parent.mkdir(parents=True, exist_ok=True)
            save_file({key: v.half() if v.is_floating_point() else v for key, v in state.items()}, str(target))
            weights.append({"path": f"{name}/fold{k}.safetensors",
                            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        members[name] = {"config": recipe["configs"][name], "weights": weights}
    packaged = {
        "method": "context U-Net ensemble: event S1 + pre-event S1 RTC statistics + HAND/DEM/slope + JRC + "
                  "WorldCover, optional Sentinel-2 L2A; five grouped-fold models per member",
        "selection": recipe["rule"],
        "tracks": {k: {kk: vv for kk, vv in v.items() if kk != "candidates"} for k, v in recipe["tracks"].items()},
        "members": members,
        "channels": list(ALL_CHANNELS),
        "s2_channels": list(S2_CHANNELS),
        "sources": {
            "event_s1": "Sen1Floods11 S1Hand (GRD dB) or Planetary Computer sentinel-1-rtc mapped by artifacts/rtc",
            "pre_event": "Planetary Computer sentinel-1-rtc, same relative orbit, 5-365 days before, 10 dates, 20 m "
                         "overview, mapped RTC->GRD by artifacts/rtc/harmonization.json",
            "terrain": "Copernicus DEM GLO-30 (Planetary Computer), GLO-30 HAND (s3://glo-30-hand)",
            "landcover": "ESA WorldCover 2021 v200 (Planetary Computer)",
            "permanent_water": "Sen1Floods11 JRCWaterHand for chips; JRC GSW for live frames",
            "optics": "Planetary Computer sentinel-2-l2a on the S1 date or up to 2 days earlier, SCL-masked",
        },
    }
    (out_dir / "recipe.json").write_text(json.dumps(packaged, indent=1), encoding="utf-8")
    return packaged


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("members", nargs="+", help="run names; append :nos2 to use an optical run without optics")
    s.add_argument("--max-members", type=int, default=3)
    sub.add_parser("test")
    sub.add_parser("package")
    args = parser.parse_args()
    if args.cmd == "select":
        r = select(args.members, args.max_members)
        print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "candidates"} for k, v in r["tracks"].items()},
                         indent=1))
    elif args.cmd == "package":
        r = package()
        print(json.dumps({"tracks": r["tracks"], "members": list(r["members"])}, indent=1))
    else:
        test()


if __name__ == "__main__":
    main()
