"""Micro-benchmark for grouping.group_snaps (category 19).

Each item scans the sorted candidate windows for the first one that contains its tight
rect. Dense case: a 40x40 tile grid where every cell owns a 2x2 window starting at its min
tile (1600 candidates), 5000 items whose tight rects are single tiles or 1x2 pairs, as a
densely annotated section produces.
"""

from __future__ import annotations

import random

import pyperf

from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect
from nornir_buildmanager.operations.segmentationtraining.grouping import group_snaps


def _items(count: int, grid: int) -> list[tuple[int, TileRect, TileRect, int]]:
    rng = random.Random(7)
    items = []
    for location_id in range(count):
        x, y = rng.randrange(grid), rng.randrange(grid)
        tight = TileRect(x, x + 1, y, y + rng.randint(1, 2))
        window = TileRect(x, x + 2, y, y + 2)
        items.append((location_id, tight, window, 1))
    return items


def main() -> None:
    runner = pyperf.Runner()
    for count, grid in ((5000, 40), (1000, 20)):
        items = _items(count, grid)
        runner.bench_func(f"group_snaps_n{count}_grid{grid}", group_snaps, items)


if __name__ == "__main__":
    main()
