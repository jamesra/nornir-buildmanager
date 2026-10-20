"""Micro-benchmark for circles.cells_for_circles (category 18).

One section of a densely annotated volume: crops are 1024 px windows at downsample 4
on the half-crop lattice of an ~80k x 80k px mosaic, circles are annotated or
inscribed circles with radii of 20-400 mosaic px scattered over the same area.
``write_circle_annotations`` calls cells_for_circles once per section with every
crop and circle of that section.
"""

from __future__ import annotations

import random

import pyperf

from nornir_buildmanager.operations.segmentationtraining.circles import (
    CropRef,
    MosaicCircle,
    cells_for_circles,
)
from nornir_buildmanager.operations.segmentationtraining.geometry import CropWindow

_DOWNSAMPLE = 4
_CROP = 1024
_MOSAIC = 80_000


def _crops(count: int, rng: random.Random) -> list[CropRef]:
    lattice = _MOSAIC // _DOWNSAMPLE // (_CROP // 2)
    crops = []
    for index in range(count):
        x0 = rng.randrange(lattice) * (_CROP // 2)
        y0 = rng.randrange(lattice) * (_CROP // 2)
        key = f"bench_{index}_D{_DOWNSAMPLE}_X{x0}-{x0 + _CROP}_Y{y0}-{y0 + _CROP}"
        crops.append(CropRef(key, _DOWNSAMPLE, CropWindow(x0, y0, _CROP, _CROP)))
    return crops


def _circles(count: int, rng: random.Random) -> list[MosaicCircle]:
    return [
        MosaicCircle(
            location_id=index,
            center_x=rng.uniform(0, _MOSAIC),
            center_y=rng.uniform(0, _MOSAIC),
            radius=rng.uniform(20, 400),
            structure_label="bench",
            has_polygon_mask=bool(index % 2),
            source="inscribed",
        )
        for index in range(count)
    ]


def main() -> None:
    runner = pyperf.Runner()
    for n_crops, n_circles in ((2000, 3000), (300, 500), (20, 30)):
        rng = random.Random(11)
        crops = _crops(n_crops, rng)
        circles = _circles(n_circles, rng)
        runner.bench_func(
            f"cells_for_circles_{n_crops}crops_{n_circles}circles",
            cells_for_circles,
            crops,
            circles,
        )


if __name__ == "__main__":
    main()
