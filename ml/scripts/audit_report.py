"""Reporting-only correction: undefined metrics stay null for zero-label chips.

The frozen inference runner is retained unchanged. This calculator uses the same
confusion formulas and fixed predictions; no threshold, model, or pixel selection
is changed. Ghana_83483 and Paraguay_34417 have zero valid evaluation pixels.
"""
import hashlib
import json
import numpy as np
from audit_models import EVIDENCE, OUT, PARTS, VERSIONS, SEED, counts, ratios, read, write, verified_protocol


def main():
    from sklearn.metrics import roc_auc_score
    protocol, fingerprint = verified_protocol()
    rows, pools, samples = [], {}, {}
    for part in PARTS:
        for name in protocol["parts"][part]:
            with np.load(OUT / f"{name}.npz") as a:
                assert str(a["fingerprint"]) == fingerprint
                valid, truth = a["valid"], a["target"][a["valid"]]
                event = name.rsplit("_", 1)[0]
                seed = SEED + int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
                sample = np.random.default_rng(seed).choice(len(truth), min(10000, len(truth)), replace=False)
                for version in ("baseline", *VERSIONS):
                    p = None if version == "baseline" else a[version][valid]
                    pred = a["baseline"][valid] if p is None else p >= protocol["thresholds"][version]
                    conf = counts(pred, truth)
                    row = {"part": part, "chip": name, "event": event, "model": version, **ratios(conf)}
                    bins = np.zeros((15, 3), np.float64)
                    if p is not None:
                        row["brier"] = float(np.mean((p.astype(np.float64)-truth)**2)) if len(p) else None
                        bin_id = np.minimum((p*15).astype(int), 14)
                        bins[:, 0] = np.bincount(bin_id, minlength=15)
                        bins[:, 1] = np.bincount(bin_id, weights=p, minlength=15)
                        bins[:, 2] = np.bincount(bin_id, weights=truth, minlength=15)
                        row["ece"] = float(np.abs(bins[:, 1]-bins[:, 2]).sum()/len(p)) if len(p) else None
                        samples.setdefault((part, version), []).append((
                            (pred != truth)[sample], a[version + "_spread"][valid][sample],
                            np.where(pred, 1-p, p)[sample]))
                    rows.append(row)
                    for scope in (part, part + ":" + event):
                        key = (scope, version)
                        pool = pools.setdefault(key, {"confusion": np.zeros(4, np.int64), "bins": np.zeros((15, 3)), "brier_sum": 0.0, "chips": 0})
                        pool["confusion"] += conf
                        pool["bins"] += bins
                        pool["brier_sum"] += (row.get("brier") or 0)*len(truth)
                        pool["chips"] += 1
    aggregate = {}
    for (scope, version), pool in pools.items():
        result = {**ratios(pool["confusion"]), "chips": pool["chips"]}
        if version != "baseline":
            b = pool["bins"]
            result.update(brier=pool["brier_sum"]/result["n_pixels"] if result["n_pixels"] else None, ece=float(np.abs(b[:, 1]-b[:, 2]).sum()/result["n_pixels"]) if result["n_pixels"] else None,
                          reliability=[{"count": int(n), "mean_p": float(p/n) if n else None,
                                        "water_fraction": float(y/n) if n else None} for n, p, y in b])
        aggregate.setdefault(scope, {})[version] = result
    for (part, version), values in samples.items():
        errors, spread, chance = (np.concatenate([v[i] for v in values]) for i in range(3))
        if len(np.unique(errors)) == 2:
            aggregate[part][version].update(uncertainty_auroc=float(roc_auc_score(errors, spread)),
                                           map_error_chance_auroc=float(roc_auc_score(errors, chance)),
                                           uncertainty_sample_pixels=len(errors))
    deltas = []
    for part in PARTS:
        for control in ("service_s1", "previous_s1s2"):
            groups = sorted({r["event"] if part != "bolivia" else r["chip"] for r in rows if r["part"] == part})
            tables = {}
            for model in (control, "latest_s1s2"):
                tables[model] = np.array([np.sum([[r[k] for k in ("tp", "fp", "fn", "tn")]
                    for r in rows if r["part"] == part and r["model"] == model and
                    (r["event"] if part != "bolivia" else r["chip"]) == group], axis=0) for group in groups])
            indices = np.random.default_rng(SEED).integers(0, len(groups), (2000, len(groups)))
            values = []
            for model in (control, "latest_s1s2"):
                c = tables[model][indices].sum(axis=1)
                values.append(c[:, 0]/np.maximum(c[:, :3].sum(axis=1), 1))
            delta = values[1]-values[0]
            deltas.append({"part": part, "control": control,
                           "iou_delta": aggregate[part]["latest_s1s2"]["iou"]-aggregate[part][control]["iou"],
                           "descriptive_paired_bootstrap_95": np.quantile(delta, [.025, .975]).tolist(),
                           "unit": "chip (one event; spatial correlation remains)" if part == "bolivia" else "event",
                           "n_groups": len(groups), "blind_test": False})
    write(EVIDENCE / "metrics.json", {"protocol_sha256": fingerprint, "aggregate": aggregate, "paired_deltas": deltas, "per_chip": rows})
    timings = [read(OUT / f"{n}.json") for p in PARTS for n in protocol["parts"][p]]
    write(EVIDENCE / "inference_inputs.json", timings)
    print(json.dumps({p: {v: {k: r[k] for k in ("iou", "f1", "precision", "recall", "n_pixels")}
                          for v, r in aggregate[p].items()} for p in PARTS}, indent=2))


if __name__ == "__main__":
    main()
