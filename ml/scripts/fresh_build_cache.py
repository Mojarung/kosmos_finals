"""Build the context input caches for every hand-labelled chip in parallel.

``--optics`` also builds the Sentinel-2 L2A cache and marks it ready for the CV queue.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from floodvalue.context import ALL_CHANNELS, S2_CACHE_DIR, S2_CHANNELS, load_chip, load_s2, s2_allowed

S1_DIR = Path(__file__).resolve().parents[1] / "data/sen1floods11/data/flood_events/HandLabeled/S1Hand"


def base(name: str) -> tuple[str, float, float]:
    stack = load_chip(name)
    pre = stack.x[ALL_CHANNELS.index("pre_valid")].astype(np.float32)
    return name, float(pre.mean()), float(stack.valid.mean())


def optics(name: str) -> tuple[str, float]:
    return name, float(load_s2(name)[S2_CHANNELS.index("s2_valid")].astype(np.float32).mean())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--optics", action="store_true")
    args = parser.parse_args()
    names = sorted(p.name.removesuffix("_S1Hand.tif") for p in S1_DIR.glob("*_S1Hand.tif"))
    with ProcessPoolExecutor(10) as pool:
        if args.optics:
            rows = list(pool.map(optics, names, chunksize=4))
            by_event: dict[str, list[float]] = {}
            for name, share in rows:
                by_event.setdefault(name.rsplit("_", 1)[0], []).append(share)
            for event, shares in sorted(by_event.items()):
                print(f"{event:10s} allowed={s2_allowed(event)!s:5s} clear share {np.mean(shares):.3f} "
                      f"chips without optics {sum(s < 0.05 for s in shares)}/{len(shares)}")
            (S2_CACHE_DIR / "READY").write_text("ok", encoding="utf-8")
        else:
            rows = list(pool.map(base, names, chunksize=4))
            low = [r for r in rows if r[1] < 0.9]
            print(f"{len(rows)} chips cached; pre-event coverage < 90% on {len(low)}: {low[:10]}")
