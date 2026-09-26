"""Run the frozen context ensemble on a Sen1Floods11 chip or on any live frame.

A packaged recipe (``artifacts/context_model/recipe.json``) lists the member networks
and their fold weights, the channel groups, Platt calibration and the mask threshold.
Frames of any size are cut into 512 px tiles with a 32 px halo, like the v1 service
path. Optical channels are optional: without a usable Sentinel-2 L2A pixel the optical
channels are zero, which is how the network saw missing optics in training.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from safetensors.torch import load_file

from floodvalue import paths
from floodvalue.context import (S2_CHANNELS, assemble_layers, channel_index, s2_features, stack_layers)
from floodvalue.context_cv import DIHEDRAL, ContextConfig, build_model

PACKAGE_DIR = paths.ARTIFACTS_DIR / "context_model"
TILE, HALO = 512, 32


@dataclass
class Member:
    spec: str
    config: ContextConfig
    models: list[torch.nn.Module]
    optics: bool  # False for ``name:nos2`` members and for members without the s2 group


@dataclass
class ContextEnsemble:
    track: str
    members: list[Member]
    platt_a: float
    platt_b: float
    threshold: float
    recipe: dict

    @property
    def uses_s2(self) -> bool:
        return any(m.optics for m in self.members)


def load_ensemble(recipe_path: Path = PACKAGE_DIR / "recipe.json", track: str = "radar",
                  device: str = "cpu") -> ContextEnsemble:
    """Fold models of every member of one track; weights are checked against their sha256."""
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    chosen = recipe["tracks"][track]
    base = recipe_path.parent
    members = []
    for spec in chosen["members"]:
        name, _, mode = spec.partition(":")
        entry = recipe["members"][name]
        config = ContextConfig(**{**entry["config"], "groups": tuple(entry["config"]["groups"])})
        models = []
        for weights in entry["weights"]:
            path = base / weights["path"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != weights["sha256"]:
                raise ValueError(f"checksum mismatch: {path}")
            model = build_model(config, pretrained=False)
            model.load_state_dict({k: v.float() for k, v in load_file(str(path)).items()})
            models.append(model.to(device).eval())
        members.append(Member(spec, config, models, "s2" in config.groups and mode != "nos2"))
    return ContextEnsemble(track, members, chosen["platt"]["a"], chosen["platt"]["b"], chosen["threshold"], recipe)


def member_input(config: ContextConfig, x_base: np.ndarray, s2: np.ndarray | None) -> np.ndarray:
    x = x_base[channel_index(config.groups)]
    if "s2" in config.groups:
        optical = s2 if s2 is not None else np.zeros((len(S2_CHANNELS), *x_base.shape[1:]), np.float16)
        x = np.concatenate([x, optical])
    return x


@torch.inference_mode()
def _logits(model: torch.nn.Module, x: torch.Tensor, tta: int) -> torch.Tensor:
    out = torch.zeros(x.shape[0], x.shape[-2], x.shape[-1], device=x.device)
    for k, flip in DIHEDRAL[:tta]:
        t = torch.rot90(x, k, dims=(-2, -1))
        if flip:
            t = torch.flip(t, dims=(-1,))
        z = model(t)[:, 0]
        if flip:
            z = torch.flip(z, dims=(-1,))
        out += torch.rot90(z, -k, dims=(-2, -1))
    return out / tta


def predict_frame(ensemble: ContextEnsemble, x_base: np.ndarray, s2: np.ndarray | None = None,
                  tta: int = 1, device: str = "cpu", progress=None) -> tuple[np.ndarray, np.ndarray]:
    """Calibrated probability and the spread (std) of the per-model probabilities, both [H, W]."""
    height, width = x_base.shape[1:]
    step = TILE - 2 * HALO
    tiles = [(y, x) for y in range(0, height, step) for x in range(0, width, step)]
    n_models = sum(len(m.models) for m in ensemble.members)
    prob_sum = np.zeros((height, width), np.float64)
    prob_sq = np.zeros((height, width), np.float64)
    logit_sum = np.zeros((height, width), np.float64)
    inputs = [member_input(m.config, x_base, s2 if m.optics else None) for m in ensemble.members]
    for index, (y, x) in enumerate(tiles, 1):
        y0, x0 = max(0, y - HALO), max(0, x - HALO)
        y1, x1 = min(height, y + step + HALO), min(width, x + step + HALO)
        h, w = min(step, height - y), min(step, width - x)
        oy, ox = y - y0, x - x0
        for member, full in zip(ensemble.members, inputs):
            patch = np.zeros((full.shape[0], TILE, TILE), np.float32)
            patch[:, :y1 - y0, :x1 - x0] = full[:, y0:y1, x0:x1]
            tensor = torch.from_numpy(patch)[None].to(device)
            member_logits = []
            for model in member.models:
                z = _logits(model, tensor, tta)[0].float().cpu().numpy()[oy:oy + h, ox:ox + w]
                member_logits.append(z)
            # Members are blended as the mean of their fold-averaged logits (see context_final).
            logit_sum[y:y + h, x:x + w] += np.mean(member_logits, axis=0)
            for z in member_logits:
                p = 1 / (1 + np.exp(-(ensemble.platt_a * z + ensemble.platt_b)))
                prob_sum[y:y + h, x:x + w] += p
                prob_sq[y:y + h, x:x + w] += p * p
        if progress:
            progress(index / len(tiles), f"Модель: фрагмент {index} из {len(tiles)}")
    z = logit_sum / len(ensemble.members)
    prob = 1 / (1 + np.exp(-(ensemble.platt_a * z + ensemble.platt_b)))
    mean = prob_sum / n_models
    spread = np.sqrt(np.clip(prob_sq / n_models - mean * mean, 0, None))
    return prob.astype(np.float32), spread.astype(np.float32)


def frame_inputs(grid: dict, s1_grd_db: np.ndarray, event_day, relative_orbit: int, jrc: np.ndarray | None,
                 use_s2: bool, progress=None) -> tuple[np.ndarray, np.ndarray | None, dict]:
    """Model inputs of a live frame: context layers fetched for the grid, optics when usable."""
    from floodvalue.context_sources import as_day, fetch_pre_event, fetch_s2_l2a, fetch_static

    day = as_day(event_day)
    say = progress or (lambda *_: None)
    say("context", 0.0, "Рельеф и покрытие: Copernicus DEM, HAND, WorldCover")
    static = fetch_static(grid)
    say("context", 0.3, "Радар до события: тот же трек за год")
    pre, dates = fetch_pre_event(grid, day, relative_orbit,
                                 progress=lambda f, d: say("context", 0.3 + 0.5 * f, f"Радар до события: {d}"))
    lat = (grid["bounds"][1] + grid["bounds"][3]) / 2
    jrc = np.zeros(s1_grd_db.shape[1:], np.float32) if jrc is None else jrc
    layers, _ = assemble_layers(s1_grd_db, pre, static, jrc, grid["transform"], lat)
    x_base = stack_layers(layers)
    provenance = {"pre_event_dates": dates, "relative_orbit": relative_orbit,
                  "pre_event_coverage": float(np.mean(np.asarray(pre["pre_n"]) > 0))}
    s2 = None
    if use_s2:
        say("context", 0.85, "Оптика Sentinel-2 L2A за 0-2 дня до радара")
        reflectance, usable, items = fetch_s2_l2a(grid, day)
        provenance.update({"s2_items": items, "s2_usable_share": float(usable.mean())})
        if usable.any():
            s2 = s2_features(reflectance, usable)
    return x_base, s2, provenance


# S1 date and relative orbit of each Sen1Floods11 event (v1.1 Sen1Floods11_Metadata.geojson),
# so a container without the dataset still finds the pre-event radar of an official chip.
SEN1FLOODS11_ACQUISITIONS = {
    "Bolivia": ("2018-02-15", 156), "Ghana": ("2018-09-18", 147), "India": ("2016-08-12", 77),
    "Mekong": ("2018-08-05", 26), "Nigeria": ("2018-09-21", 103), "Pakistan": ("2017-06-28", 5),
    "Paraguay": ("2018-10-31", 68), "Somalia": ("2018-05-07", 116), "Spain": ("2019-09-17", 110),
    "Sri-Lanka": ("2017-05-30", 19), "USA": ("2019-05-22", 136),
}


def chip_acquisition(s1_path: Path) -> tuple[str, int] | None:
    """S1 date and relative orbit of a Sen1Floods11 chip named EVENT_ID_S1Hand.tif."""
    from floodvalue.chips import Chip

    try:
        event = Chip.from_filename(s1_path.name).event
    except ValueError:
        return None
    return SEN1FLOODS11_ACQUISITIONS.get(event)


def cached_inputs(name: str, grid: dict, s1: np.ndarray, jrc: np.ndarray | None,
                  want_optics: bool) -> tuple[np.ndarray, np.ndarray | None, dict] | None:
    """Layers already fetched by scripts/fresh_fetch_aux.py for an official chip, else None.

    The same files built the training and test stacks, so a chip scored here gets exactly
    the inputs of the evaluation; a clean clone has no data/aux and fetches them instead.
    """
    from floodvalue.chips import Chip
    from floodvalue.context import AUX_DIR, merge_l2a, s2_allowed

    static_path, pre_path = AUX_DIR / "static" / f"{name}.npz", AUX_DIR / "pre" / f"{name}.npz"
    if not (static_path.exists() and pre_path.exists()):
        return None
    lat = (grid["bounds"][1] + grid["bounds"][3]) / 2
    jrc = np.zeros(s1.shape[1:], np.float32) if jrc is None else jrc
    layers, _ = assemble_layers(s1, np.load(pre_path), np.load(static_path), jrc, grid["transform"], lat)
    manifest = AUX_DIR / "pre_manifest.json"
    dates = json.loads(manifest.read_text(encoding="utf-8")).get(name, {}).get("dates") if manifest.exists() else None
    provenance = {"layers": "local data/aux (fresh_fetch_aux.py)", "pre_event_dates": dates}
    s2 = None
    if want_optics and s2_allowed(Chip.from_name(name).event) and (AUX_DIR / "s2l2a" / name).exists():
        reflectance, usable = merge_l2a(name)
        provenance["s2_usable_share"] = float(usable.mean())
        if usable.any():
            s2 = s2_features(reflectance, usable)
    return stack_layers(layers), s2, provenance


def infer_chip(s1_path: Path, out_dir: Path, recipe_path: Path = PACKAGE_DIR / "recipe.json",
               jrc_path: Path | None = None, s1_date: str | None = None, relative_orbit: int | None = None,
               optics: bool = True, tta: int = 8, device: str | None = None) -> dict:
    """Context-model rasters for one S1 GeoTIFF on its own grid (same contract as ``infer_s1``)."""
    import rasterio

    from floodvalue.baseline_run import load_baseline
    from floodvalue.infer import MASK_NODATA, PROB_NODATA, _write, sha256

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    with rasterio.open(s1_path) as src:
        s1, profile = src.read().astype(np.float32), dict(src.profile)
        grid = {"crs": src.crs, "transform": src.transform, "width": src.width, "height": src.height,
                "bounds": tuple(src.bounds)}
    if s1_date is None or relative_orbit is None:
        known = chip_acquisition(s1_path)
        if known is None:
            raise ValueError("S1 date and relative orbit are needed for the pre-event radar (--s1-date, --orbit)")
        s1_date, relative_orbit = s1_date or known[0], relative_orbit or known[1]
    jrc = None
    if jrc_path is not None and Path(jrc_path).exists():
        with rasterio.open(jrc_path) as src:
            jrc = (src.read(1) == 1).astype(np.float32)
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    want_optics = optics and "optical" in recipe["tracks"]
    cached = cached_inputs(s1_path.name.removesuffix("_S1Hand.tif"), grid, s1, jrc, want_optics)
    x_base, s2, provenance = cached or frame_inputs(grid, s1, s1_date, relative_orbit, jrc, want_optics)
    track = "optical" if s2 is not None else "radar"
    ensemble = load_ensemble(recipe_path, track, device)
    prob, spread = predict_frame(ensemble, x_base, s2, tta=tta, device=device)
    valid = np.isfinite(s1).all(axis=0)
    mask = np.where(valid, (prob >= ensemble.threshold).astype(np.uint8), MASK_NODATA)
    files = {
        "flood_probability.tif": (np.where(valid, prob, PROB_NODATA), "float32", PROB_NODATA),
        "flood_mask.tif": (mask, "uint8", MASK_NODATA),
        "flood_uncertainty.tif": (np.where(valid, spread, PROB_NODATA), "float32", PROB_NODATA),
    }
    water, _ = load_baseline(paths.ARTIFACTS_DIR / "baseline" / "baseline.json").predict(s1)
    files["baseline_mask.tif"] = (np.where(valid, water.astype(np.uint8), MASK_NODATA), "uint8", MASK_NODATA)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, (array, dtype, nodata) in files.items():
        _write(out_dir / name, profile, array, dtype, nodata)
    report = {
        "s1": str(s1_path), "method": "context ensemble", "track": track, "members": [m.spec for m in ensemble.members],
        "threshold": ensemble.threshold, "platt": {"a": ensemble.platt_a, "b": ensemble.platt_b}, "tta": tta,
        "s1_date": s1_date, **provenance,
        "valid_pixels": int(valid.sum()),
        "water_share_of_valid": float((mask[valid] == 1).mean()) if valid.any() else None,
        "files": {name: sha256(out_dir / name) for name in files},
    }
    (out_dir / "inference.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def run_frame(s1_path: Path, jrc_path: Path | None, bounds: list[float], event_at: str, relative_orbit: int,
              out_path: Path, recipe_path: Path = PACKAGE_DIR / "recipe.json", tta: int = 2) -> dict:
    """Service entry for a live frame on an EPSG:4326 grid: arrays in .npy, result in .npz + json info."""
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds

    s1 = np.load(s1_path)
    height, width = s1.shape[1:]
    grid = {"crs": CRS.from_epsg(4326), "transform": from_bounds(*bounds, width, height),
            "width": width, "height": height, "bounds": tuple(bounds)}
    jrc = np.load(jrc_path).astype(np.float32) if jrc_path else None
    say = lambda _stage, fraction, message: print(json.dumps({"progress": fraction, "message": message}), flush=True)  # noqa: E731
    x_base, s2, provenance = frame_inputs(grid, s1, event_at, relative_orbit, jrc, use_s2=True, progress=say)
    track = "optical" if s2 is not None else "radar"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ensemble = load_ensemble(recipe_path, track, device)
    prob, spread = predict_frame(ensemble, x_base, s2, tta=tta, device=device,
                                 progress=lambda f, m: print(json.dumps({"progress": f, "message": m}), flush=True))
    optical_pixels = int(np.asarray(s2[-1] > 0).sum()) if s2 is not None else 0
    info = {"track": track, "members": [m.spec for m in ensemble.members], "threshold": ensemble.threshold,
            "platt": {"a": ensemble.platt_a, "b": ensemble.platt_b}, "tta": tta,
            "optical_used_pixels": optical_pixels, **provenance}
    np.savez(out_path, probability=prob, spread=spread)
    out_path.with_suffix(".json").write_text(json.dumps(info), encoding="utf-8")
    return info


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="context model on a live frame (called by the service)")
    parser.add_argument("--input", type=Path, required=True, help="S1 VV/VH in GRD dB, [2, H, W] .npy")
    parser.add_argument("--jrc", type=Path, help="permanent water 0/1 [H, W] .npy")
    parser.add_argument("--bounds", type=float, nargs=4, required=True)
    parser.add_argument("--event-at", required=True)
    parser.add_argument("--orbit", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, default=PACKAGE_DIR / "recipe.json")
    parser.add_argument("--tta", type=int, default=2)
    args = parser.parse_args()
    run_frame(args.input, args.jrc, args.bounds, args.event_at, args.orbit, args.output, args.recipe, args.tta)


if __name__ == "__main__":
    main()
