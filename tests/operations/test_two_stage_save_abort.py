"""The two-stage tile save stops its threads when assembly fails, so the process can exit.

Regression (RC2 1240, 2026-11-05): an allocation failure in AssembleTiles left the copy consumers blocked on their
queue and nornir-build hung in interpreter shutdown.
"""

from __future__ import annotations

import os

import numpy as np
from nornir_buildmanager.operations import tile as tile_ops


def test_abort_stops_copy_consumers_after_saving_started_tiles(tmp_path) -> None:
    pipeline = tile_ops._TwoStageTileSavePipeline(encode_workers=2, copy_workers=3, bpp=8, optimize=False)
    tile = np.full((32, 32), 0.5, dtype=np.float32)
    dest = tmp_path / "dest"
    temp = tmp_path / "temp"
    dest.mkdir()
    temp.mkdir()
    for i in range(4):
        pipeline.submit(str(dest / f"{i}.png"), str(temp / f"{i}.png"), tile)

    pipeline.abort()

    assert all(future.done() for future in pipeline._copy_futures)
    # Tiles whose encode had started are still copied; none is left half-written in dest
    for name in os.listdir(dest):
        assert os.path.getsize(dest / name) > 0


def test_abort_with_nothing_submitted_returns() -> None:
    pipeline = tile_ops._TwoStageTileSavePipeline(encode_workers=1, copy_workers=2, bpp=8)
    pipeline.abort()
    assert all(future.done() for future in pipeline._copy_futures)
