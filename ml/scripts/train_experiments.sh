#!/usr/bin/env sh
# Controlled experiment for criterion 8: inputs (VV+VH vs +VV-VH) and ImageNet init vs scratch.
# Every run selects its checkpoint on validation only; test parts are never touched here.
set -e
for config in unet_r34_vvvh unet_r34_vvvh_diff unet_r34_vvvh_scratch; do
  for seed in 0 1; do
    uv run floodvalue train --config "configs/${config}.json" --seed "$seed"
  done
done
