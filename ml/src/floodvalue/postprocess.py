"""Connected-component filter used only to measure the effect of post-processing.

The case requires ``flood_mask == 1`` exactly where ``p >= threshold``, so the shipped
mask is never post-processed; this module quantifies what such a filter would change.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

MIN_COMPONENT_SIZES = (0, 4, 16, 64)


def remove_small_components(mask: np.ndarray, min_pixels: int) -> np.ndarray:
    """Drop 4-connected water components smaller than ``min_pixels``."""
    if min_pixels <= 1:
        return mask.astype(bool)
    labels, _ = ndimage.label(mask)
    sizes = np.bincount(labels.ravel())
    keep = sizes >= min_pixels
    keep[0] = False
    return keep[labels]
