"""Pooled OOF IoU of the chips predicted so far, per config (quick look while CV runs)."""

import sys

import numpy as np

from floodvalue.context import load_chip, target_of
from floodvalue.context_cv import RUNS, read_folds

fold_of = read_folds()
for name in sys.argv[1:]:
    folds = sorted({fold_of[p.stem] for p in (RUNS / name / "oof").glob("*.npy")})
    tp = fp = fn = 0
    for chip, f in fold_of.items():
        path = RUNS / name / "oof" / f"{chip}.npy"
        if not path.exists():
            continue
        s = load_chip(chip)
        z = np.load(path).astype(np.float32)[s.valid]
        t = target_of(s)[s.valid] > 0.5
        p = z > 0
        tp += int((p & t).sum()); fp += int((p & ~t).sum()); fn += int((~p & t).sum())
    print(name, "folds", folds, "IoU@0.5", round(tp / (tp + fp + fn), 4), "P", round(tp / (tp + fp), 4), "R", round(tp / (tp + fn), 4))
