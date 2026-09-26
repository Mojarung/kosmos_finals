"""CPU tiled S1 fallback for the service's arbitrary RTC rectangles."""
import argparse
from pathlib import Path

import numpy as np
import torch

from floodvalue.fusion_infer import predict_fusion_recipe


def predict_tiled(s1, recipe_path, progress=None):
    height, width = s1.shape[1:]
    probability = np.zeros((height, width), dtype=np.float32)
    spread = np.zeros_like(probability)
    step, halo, size = 448, 32, 512
    tiles = [(y, x) for y in range(0, height, step) for x in range(0, width, step)]
    torch.set_num_threads(2)
    for index, (y, x) in enumerate(tiles, 1):
        patch = np.full((2, size, size), np.nan, dtype=np.float32)
        y0, x0 = max(0, y - halo), max(0, x - halo)
        y1, x1 = min(height, y + step + halo), min(width, x + step + halo)
        patch[:, y0-y+halo:y1-y+halo, x0-x+halo:x1-x+halo] = s1[:, y0:y1, x0:x1]
        with torch.inference_mode():
            _, p, u, _ = predict_fusion_recipe(recipe_path, [patch], [None], [None])
        h, w = min(step, height-y), min(step, width-x)
        probability[y:y+h, x:x+w] = p[0, halo:halo+h, halo:halo+w]
        spread[y:y+h, x:x+w] = u[0, halo:halo+h, halo:halo+w]
        if progress:
            progress("inference", index/len(tiles), f"Модель: фрагмент {index} из {len(tiles)}")
    return probability, spread


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    args = parser.parse_args()
    p, u = predict_tiled(np.load(args.input, allow_pickle=False), args.recipe)
    np.savez(args.output, probability=p, spread=u)
