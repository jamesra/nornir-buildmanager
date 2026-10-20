"""Micro-benchmark for stitch._as_uint8_gray on non-uint8 tiles (category 16).

Called once per loaded tile. 8-bit tiles return unchanged; this measures the 16-bit
path with real Raw16 SEM tiles from ``TESTINPUTPATH`` (DM4BuildTest 0477, levels 4 and 1:
2250x2250 and 9000x9000 uint16), plus the float path on the level-4 tile scaled to [0, 1].
"""

from __future__ import annotations

import os

import nornir_imageregistration
import numpy as np
import pyperf

from nornir_buildmanager.operations.segmentationtraining.stitch import _as_uint8_gray

_PYRAMID = os.path.join(
    "PlatformRaw", "DM4", "DM4BuildTest", "SEM", "0477", "SEM", "Raw16", "TilePyramid"
)


def _tile(level: str) -> np.ndarray:
    """Load one real Raw16 tile as the stitcher's loader would."""
    root = os.environ.get("TESTINPUTPATH", "")
    path = os.path.join(root, _PYRAMID, level, "000.png")
    return np.asarray(nornir_imageregistration.LoadImage(path, backend="numpy"))


def main() -> None:
    """Register one benchmark per tile size and dtype."""
    runner = pyperf.Runner()
    small = _tile("004")
    runner.bench_func("as_uint8_gray_uint16_2250", _as_uint8_gray, small)
    runner.bench_func("as_uint8_gray_uint16_9000", _as_uint8_gray, _tile("001"))
    runner.bench_func(
        "as_uint8_gray_float32_2250", _as_uint8_gray, (small / np.float32(65535.0)).astype(np.float32)
    )


if __name__ == "__main__":
    main()
