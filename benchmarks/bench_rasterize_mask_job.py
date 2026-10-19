"""Micro-benchmark for masks.rasterize_mask_job (category 16).

One 2048x2048 crop (the max texture size) with a 240-vertex cell outline of radius 700 px
and a 60-vertex hole, end to end: raster, 1-bit PNG save, RLE sidecar, bbox and area.
Output files go under TESTOUTPUTPATH (or the temp folder), overwritten each call.
"""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import pyperf

from nornir_buildmanager.operations.segmentationtraining.masks import (
    MaskJob,
    rasterize_mask_job,
)

SIZE = 2048


def _ring(cx: float, cy: float, radius: float, count: int) -> tuple[tuple[int, int], ...]:
    """Integer polygon approximating a slightly wobbly circle."""
    return tuple(
        (
            round(cx + radius * (1 + 0.08 * math.sin(5 * t)) * math.cos(t)),
            round(cy + radius * (1 + 0.08 * math.sin(5 * t)) * math.sin(t)),
        )
        for t in (2 * math.pi * i / count for i in range(count))
    )


def _job() -> MaskJob:
    """2048x2048 job with one large polygon and one hole."""
    root = Path(os.environ.get("TESTOUTPUTPATH") or tempfile.gettempdir()) / "bench_rasterize_mask_job"
    polygon = (_ring(1024, 1024, 700, 240), _ring(1100, 980, 150, 60))
    return MaskJob(
        location_id=1,
        image_key="bench",
        width=SIZE,
        height=SIZE,
        rings=(polygon,),
        mask_path=str(root / "masks" / "bench_1.png"),
        rle_path=str(root / "rle" / "bench_1.json"),
    )


def main() -> None:
    runner = pyperf.Runner()
    runner.bench_func("rasterize_mask_job_2048", rasterize_mask_job, _job())


if __name__ == "__main__":
    main()
