"""Micro-benchmark for stitch.missing_covering_tiles with a per-pass memo (category 19).

A section pass checks every crop window; half-step crop windows (stride 2 tiles, 4x4 tiles
each) overlap so a tile is checked by up to four windows. Complete 60x60 tile grid on local
disk, so the saving here is a lower bound on a CIFS share where each isfile is a round trip.
"""

from __future__ import annotations

import inspect
import os
import tempfile

import pyperf

from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect
from nornir_buildmanager.operations.segmentationtraining.stitch import (
    missing_covering_tiles,
    tile_filename,
)

_HAS_MEMO = "known" in inspect.signature(missing_covering_tiles).parameters


def _pass(level: str, windows: list[TileRect]) -> int:
    known: dict | None = {} if _HAS_MEMO else None
    found = 0
    for rect in windows:
        if _HAS_MEMO:
            found += len(missing_covering_tiles(rect, level, "", ".png", known))
        else:
            found += len(missing_covering_tiles(rect, level, "", ".png"))
    return found


def main() -> None:
    runner = pyperf.Runner()
    with tempfile.TemporaryDirectory() as root:
        level = os.path.join(root, "001")
        os.makedirs(level)
        for iy in range(60):
            for ix in range(60):
                with open(os.path.join(level, tile_filename("", ".png", ix, iy)), "wb"):
                    pass
        windows = [TileRect(x, x + 4, y, y + 4) for y in range(0, 56, 2) for x in range(0, 56, 2)]
        runner.bench_func("missing_covering_tiles_60x60_halfstep", _pass, level, windows)


if __name__ == "__main__":
    main()
