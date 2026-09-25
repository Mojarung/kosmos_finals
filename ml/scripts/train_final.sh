#!/usr/bin/env sh
# Final ensemble: the configuration chosen on validation (see artifacts/experiments.json)
# trained with five seeds; seeds 0 and 1 come from scripts/train_experiments.sh.
set -e
for seed in 2 3 4; do
  uv run floodvalue train --config configs/unet_r34_vvvh.json --seed "$seed"
done
