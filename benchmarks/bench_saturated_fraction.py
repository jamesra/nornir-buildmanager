"""Micro-benchmark for stitch.saturated_fraction (category 16).

Called once per stitched crop (and again per crop that is checked for blankness). Realistic
crop: 1024x1024 uint8 TEM with a few percent saturated pixels, plus a 2048x2048 case.
"""

from __future__ import annotations

import numpy as np
import pyperf

from nornir_buildmanager.operations.segmentationtraining.stitch import saturated_fraction


def _crop(side: int) -> np.ndarray:
    rng = np.random.default_rng(7)
    array = rng.integers(0, 256, size=(side, side), dtype=np.uint8)
    array[: side // 16] = 255
    array[:, : side // 32] = 0
    return np.ascontiguousarray(array)


def main() -> None:
    runner = pyperf.Runner()
    for side in (1024, 2048):
        runner.bench_func(f"saturated_fraction_{side}", saturated_fraction, _crop(side))


if __name__ == "__main__":
    main()
