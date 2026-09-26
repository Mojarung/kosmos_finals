"""Command line entry point: ``uv run floodvalue <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from floodvalue import paths


def _cmd_download(args: argparse.Namespace) -> None:
    from floodvalue.download import download

    manifest = download(paths.SEN1FLOODS11_DIR, workers=args.workers)
    print(f"manifest: {manifest}")


SPLIT_PATH = paths.ARTIFACTS_DIR / "split.json"
BASELINE_PATH = paths.ARTIFACTS_DIR / "baseline" / "baseline.json"


def _cmd_check(_args: argparse.Namespace) -> None:
    from floodvalue.check import check_dataset
    from floodvalue.split import read_author_split

    names = sorted({n for part in read_author_split(paths.SEN1FLOODS11_DIR).values() for n in part})
    report = check_dataset(paths.SEN1FLOODS11_DIR, names, paths.ARTIFACTS_DIR / "data_check.json")
    print(f"checked {report['chips_checked']} chips, failed {report['chips_failed']}, "
          f"with S1 NaN {report['chips_with_s1_nan']}, mean water share {report['mean_water_share']:.3f}")


def _cmd_split(_args: argparse.Namespace) -> None:
    from floodvalue.split import make_split

    split = make_split(paths.SEN1FLOODS11_DIR, SPLIT_PATH)
    print(json.dumps(split["counts"]))


def _cmd_baseline(_args: argparse.Namespace) -> None:
    from floodvalue.baseline_run import fit_baseline
    from floodvalue.split import load_split

    result = fit_baseline(paths.SEN1FLOODS11_DIR, load_split(SPLIT_PATH), BASELINE_PATH)
    print(json.dumps(result["model"]), {ch: r["valid"]["iou"] for ch, r in result["per_channel"].items()})


def _cmd_train(args: argparse.Namespace) -> None:
    from floodvalue.split import load_split
    from floodvalue.train import TrainConfig, run

    config = TrainConfig.from_json(Path(args.config), seed=args.seed, epochs=args.epochs)
    split = load_split(SPLIT_PATH)
    if args.overfit:
        # Wiring check from the case: a model must be able to memorise a few training chips.
        # Whole chips without augmentation, so memorising pixel positions is possible.
        config = replace(config, crop=512, augment=False, batch_size=args.overfit, crops_per_chip=1, lr=1e-3)
        chips = split["train"][: args.overfit]
        run_dir = paths.RUNS_DIR / "overfit" / f"{config.name}_n{args.overfit}"
        summary = run(config, paths.SEN1FLOODS11_DIR, split, run_dir, train_names=chips, valid_names=chips)
    else:
        run_dir = paths.RUNS_DIR / config.name / f"seed{config.seed}"
        summary = run(config, paths.SEN1FLOODS11_DIR, split, run_dir)
    print(json.dumps({"run_dir": str(run_dir), "best": summary["best"]["iou"], "epoch": summary["best"]["epoch"]}))


MODEL_DIR = paths.ARTIFACTS_DIR / "model"
EVALUATION_DIR = paths.ARTIFACTS_DIR / "evaluation"
CALIBRATION_PATH = EVALUATION_DIR / "calibration.json"


def _cmd_experiments(args: argparse.Namespace) -> None:
    from floodvalue.package import summarize_experiments

    result = summarize_experiments(paths.RUNS_DIR, args.configs, paths.ARTIFACTS_DIR / "experiments.json")
    for config in result["ranking"]:
        row = result["configs"][config]
        print(f"{config}: valid IoU {row['valid_iou_mean']:.4f} ± {row['valid_iou_std'] or 0:.4f} "
              f"({len(row['seeds'])} seeds)")


def _cmd_package(args: argparse.Namespace) -> None:
    from floodvalue.package import package_members

    card = package_members([Path(p) for p in args.runs], MODEL_DIR)
    print(json.dumps(card, indent=2))


def _cmd_evaluate(_args: argparse.Namespace) -> None:
    from floodvalue.baseline_run import load_baseline
    from floodvalue.ensemble import load_members
    from floodvalue.evaluate import evaluate
    from floodvalue.figures import make_figures
    from floodvalue.package import member_dirs
    from floodvalue.report import render
    from floodvalue.split import load_split

    members = load_members(member_dirs(MODEL_DIR))
    report = evaluate(paths.SEN1FLOODS11_DIR, load_split(SPLIT_PATH), members, load_baseline(BASELINE_PATH),
                      EVALUATION_DIR)
    make_figures(EVALUATION_DIR, BASELINE_PATH)
    render(EVALUATION_DIR)
    for part, scored in report["parts"].items():
        base, main = (scored[f"all_water/{m}"]["overall"]["micro"]["iou"] for m in ("baseline", "main"))
        print(f"{part}: baseline IoU {base:.4f}  main IoU {main:.4f}")


def _cmd_infer(args: argparse.Namespace) -> None:
    from floodvalue.baseline_run import load_baseline
    from floodvalue.chips import Chip, layer_path
    from floodvalue.ensemble import load_members
    from floodvalue.infer import infer_s1, load_calibration
    from floodvalue.package import member_dirs

    s1_path = Path(args.s1) if args.s1 else layer_path(paths.SEN1FLOODS11_DIR, Chip.from_name(args.chip), "S1Hand")
    if args.recipe:
        recipe = json.loads(Path(args.recipe).read_text())
        if "optical_member" in recipe or "fusion_members" in recipe:
            from floodvalue.optical import event_dates, infer_optical
            if bool(args.s1_date) != bool(args.s2_date):
                raise ValueError("provide both --s1-date and --s2-date")
            acquisition = ({"s1_date": args.s1_date, "s2_date": args.s2_date} if args.s1_date else None)
            s2_path = Path(args.s2) if args.s2 else None
            if args.chip and not args.no_optical:
                chip = Chip.from_name(args.chip)
                acquisition = acquisition or event_dates(paths.SEN1FLOODS11_DIR).get(chip.event)
                candidate = layer_path(paths.SEN1FLOODS11_DIR, chip, "S2Hand")
                s2_path = s2_path or (candidate if candidate.exists() else None)
            if s2_path and acquisition is None:
                raise ValueError("custom optical input requires --s1-date and --s2-date")
            report = infer_optical(s1_path, Path(args.recipe), Path(args.out),
                                   None if args.no_optical else s2_path, acquisition)
            print(json.dumps(report, indent=2))
            return
        if args.s2 or args.s1_date or args.s2_date or args.no_optical:
            raise ValueError("optical arguments require an S1+S2 recipe")
        from floodvalue.refine import infer_recipe
        report = infer_recipe(s1_path, Path(args.recipe), Path(args.out), load_baseline(BASELINE_PATH))
        print(json.dumps(report, indent=2))
        return
    if args.s2 or args.s1_date or args.s2_date or args.no_optical:
        raise ValueError("optical arguments require --recipe with an S1+S2 model")
    platt, threshold = load_calibration(CALIBRATION_PATH)
    members = load_members(member_dirs(MODEL_DIR))
    report = infer_s1(s1_path, members, platt, threshold, Path(args.out), load_baseline(BASELINE_PATH))
    print(json.dumps(report, indent=2))


DOUBT_PATH = EVALUATION_DIR / "doubt_threshold.json"
# The value the service used before this command existed; kept in the record as provenance.
REPLACED_DOUBT = {"source_part": "test_core", "entropy_top_decile_bits": 0.4080650210380554,
                  "map_error_chance_threshold": 0.0817,
                  "reason": "read from evaluation.json test_core deciles; the final check must not tune the service"}


def _cmd_doubt_threshold(_args: argparse.Namespace) -> None:
    from floodvalue.doubt import doubt_threshold
    from floodvalue.ensemble import load_members
    from floodvalue.infer import load_calibration
    from floodvalue.package import member_dirs
    from floodvalue.split import load_split
    from floodvalue.train import DEVICE

    platt, threshold = load_calibration(CALIBRATION_PATH)
    dirs = member_dirs(MODEL_DIR)
    record = doubt_threshold(paths.SEN1FLOODS11_DIR, load_split(SPLIT_PATH), load_members(dirs), platt, threshold)
    record["model"] = {"members": [path.relative_to(paths.PIPELINE_ROOT).as_posix() for path in dirs],
                       "calibration": CALIBRATION_PATH.relative_to(paths.PIPELINE_ROOT).as_posix(),
                       "platt": platt.as_dict(), "device": DEVICE}
    record["replaces"] = REPLACED_DOUBT
    DOUBT_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    report = record["report_only"]["parts"]
    print(json.dumps({"entropy_top_decile_bits": record["entropy_top_decile_bits"],
                      "map_error_chance_threshold": record["map_error_chance_threshold"],
                      "valid": record["valid_at_threshold"], "measure_auroc": record["measure_auroc"],
                      "report_only": {part: {key: row[key] for key in ("doubtful_share", "error_rate_doubtful",
                                                                        "auroc_entropy", "auroc_member_std",
                                                                        "own_top_decile_bits")}
                                      for part, row in report.items()}}, indent=2))


NORM_CHECK_PATH = EVALUATION_DIR / "norm_stats_check.json"


def _cmd_norm_check(_args: argparse.Namespace) -> None:
    from floodvalue.features import NormStats
    from floodvalue.norm_check import LazyChips, norm_check
    from floodvalue.package import member_dirs
    from floodvalue.split import load_split

    dirs = member_dirs(MODEL_DIR)
    shipped = [NormStats.from_dict(json.loads((d / "norm_stats.json").read_text(encoding="utf-8"))) for d in dirs]
    record = norm_check(LazyChips(paths.SEN1FLOODS11_DIR, load_split(SPLIT_PATH)["train"]), shipped)
    record = {"command": "uv run --project ml floodvalue norm-check", "part": "train",
              "member_files": [(d / "norm_stats.json").relative_to(paths.PIPELINE_ROOT).as_posix() for d in dirs],
              **record}
    NORM_CHECK_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps({key: record[key] for key in ("recomputed_float64", "clip_bounds_equal_shipped",
                                                   "old_float32_reduction_replayed", "nodata")}, indent=2))


def _cmd_failure(args: argparse.Namespace) -> None:
    from floodvalue.failure import run_failure
    report = run_failure(args.chip, args.reference, EVALUATION_DIR)
    target = report["target"]
    print(json.dumps({"chip": report["chip"], "model": target["model"], "baseline": target["baseline"],
                      "missed": target["missed"], "fragments": target["fragments"], "doubt": target["doubt"],
                      "label_shift": {key: target["label_shift"][key] for key in ("zero_shift", "best")}},
                     ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="floodvalue", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    download = commands.add_parser("download", help="fetch hand-labeled Sen1Floods11 v1.1 from the official bucket")
    download.add_argument("--workers", type=int, default=16)
    download.set_defaults(handler=_cmd_download)

    commands.add_parser("check", help="validate bands, CRS, grids and labels").set_defaults(handler=_cmd_check)
    commands.add_parser("split", help="authors' split + adjacency check").set_defaults(handler=_cmd_split)
    commands.add_parser("baseline", help="fit and freeze the threshold baseline").set_defaults(handler=_cmd_baseline)

    train = commands.add_parser("train", help="train one U-Net member")
    train.add_argument("--config", required=True)
    train.add_argument("--seed", type=int)
    train.add_argument("--epochs", type=int)
    train.add_argument("--overfit", type=int, default=0, help="memorise N train chips (wiring check)")
    train.set_defaults(handler=_cmd_train)

    experiments = commands.add_parser("experiments", help="compare trained configs on validation")
    experiments.add_argument("configs", nargs="+")
    experiments.set_defaults(handler=_cmd_experiments)

    package = commands.add_parser("package", help="ship chosen runs as the ensemble in artifacts/model")
    package.add_argument("runs", nargs="+")
    package.set_defaults(handler=_cmd_package)

    commands.add_parser("evaluate", help="calibrate on valid, score both methods on test parts, draw figures") \
        .set_defaults(handler=_cmd_evaluate)

    commands.add_parser("doubt-threshold",
                        help="fix the service's doubt threshold on valid; report test_core and bolivia only") \
        .set_defaults(handler=_cmd_doubt_threshold)

    commands.add_parser("norm-check",
                        help="shipped normalisation constants against float64 train statistics") \
        .set_defaults(handler=_cmd_norm_check)

    failure = commands.add_parser("failure", help="explain where the frozen ensemble misses water on one chip")
    failure.add_argument("--chip", default="Bolivia_242570", help="official chip name")
    failure.add_argument("--reference", default="bolivia", help="split part to compare against")
    failure.set_defaults(handler=_cmd_failure)
    infer = commands.add_parser("infer", help="write flood_probability.tif / flood_mask.tif for one S1 chip")
    source = infer.add_mutually_exclusive_group(required=True)
    source.add_argument("--chip", help="official chip name, e.g. Ghana_313799")
    source.add_argument("--s1", help="path to any S1Hand GeoTIFF")
    infer.add_argument("--out", required=True)
    infer.add_argument("--recipe", help="optional frozen refinement recipe; defaults to the shipped v1 ensemble")
    optical = infer.add_mutually_exclusive_group()
    optical.add_argument("--s2", help="optional 13-band S2Hand GeoTIFF for an S1+S2 recipe")
    optical.add_argument("--no-optical", action="store_true", help="force S1 fallback for an S1+S2 recipe")
    infer.add_argument("--s1-date", help="S1 acquisition date YYYY-MM-DD for custom optical input")
    infer.add_argument("--s2-date", help="S2 acquisition date YYYY-MM-DD for custom optical input")
    infer.set_defaults(handler=_cmd_infer)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.handler(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
