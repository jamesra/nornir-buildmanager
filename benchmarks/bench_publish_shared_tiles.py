"""Peak-memory and time benchmark for stitch.prefetch_shared_tiles (category 17).

Each column band of a segmentation-training crop export decodes its unique tiles on a
thread pool and then publishes each one into read-only shared memory. This measures the
process peak working set of one band end to end (thread-pool load plus publish), with a
fake loader that returns ``side x side`` uint8 tiles, so only the band's own memory shows.

Usage (from ``nornir-buildmanager``)::

    python benchmarks/bench_publish_shared_tiles.py memory [tiles] [side] [repeats]
    python benchmarks/bench_publish_shared_tiles.py --copy-env -p 10 -o out.json

The first form runs each repeat in a fresh child process and prints peak working set
growth over the post-import baseline. The second is a pyperf timing of the publish step
plus releasing the host tiles (tile decode excluded; shared blocks unlinked outside the
timed region).
"""

from __future__ import annotations

import gc
import json
import statistics
import subprocess
import sys
import time
from collections.abc import Callable

import nornir_pools
import numpy as np
import psutil
from nornir_imageregistration.type_info import Shape
from numpy.typing import NDArray

from nornir_buildmanager.operations.segmentationtraining import stitch
from nornir_buildmanager.operations.segmentationtraining.geometry import TileRect

_MIB = 1024 * 1024


def _peak_bytes() -> int:
    """Process peak working set in bytes (Windows only: psutil ``peak_wset``)."""
    return int(psutil.Process().memory_info().peak_wset)  # type: ignore[attr-defined]


def _band(tiles: int, side: int) -> list[stitch.StitchJob]:
    """One job spanning *tiles* tiles in a single row."""
    return [
        stitch.StitchJob(
            image_key="band",
            snap=TileRect(0, tiles, 0, 1),
            downsample=1,
            tile_shape=Shape(y=side, x=side),
            image_path="",
        )
    ]


def _fake_loader(side: int) -> Callable[[int, int], NDArray[np.uint8]]:
    """Loader returning a freshly allocated, fully touched uint8 tile per call."""

    def load(ix: int, iy: int) -> NDArray[np.uint8]:
        return np.full((side, side), (ix * 7 + iy) % 256, dtype=np.uint8)

    return load


def _memory_child(tiles: int, side: int) -> None:
    """Run one band in this process and print peak growth as JSON."""
    gc.collect()
    base = _peak_bytes()
    shared, handles = stitch.prefetch_shared_tiles(
        _band(tiles, side), _fake_loader(side), io_workers=8
    )
    peak = _peak_bytes()
    stitch._unlink_handles(handles)
    nornir_pools.ClosePools(timeout=10)
    print(json.dumps({"growth_mib": (peak - base) / _MIB, "tiles": len(shared)}))


def _memory_parent(tiles: int, side: int, repeats: int) -> None:
    """Spawn *repeats* children and report the median peak growth."""
    growths: list[float] = []
    for _ in range(repeats):
        out = subprocess.run(
            [sys.executable, __file__, "_child", str(tiles), str(side)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().splitlines()[-1]
        growths.append(json.loads(out)["growth_mib"])
    band_mib = tiles * side * side / _MIB
    print(
        f"tiles={tiles} side={side} band={band_mib:.0f} MiB repeats={repeats} "
        f"peak growth MiB median={statistics.median(growths):.1f} "
        f"min={min(growths):.1f} max={max(growths):.1f} all={[round(g, 1) for g in growths]}"
    )


def _time_publish(loops: int, tiles: int, side: int) -> float:
    """pyperf time function: publish one band and release its host tiles, *loops* times.

    Releasing the input dict is inside the timed region because prefetch_shared_tiles
    drops it on return either way; only where the frees happen differs.
    """
    loader = _fake_loader(side)
    total = 0.0
    for _ in range(loops):
        arrays: dict[tuple[int, int], NDArray] = {(ix, 0): loader(ix, 0) for ix in range(tiles)}
        start = time.perf_counter()
        _shared, handles = stitch._publish_shared_tiles(arrays)
        del arrays
        total += time.perf_counter() - start
        stitch._unlink_handles(handles)
    return total


def main() -> None:
    """Dispatch to memory mode, a memory child, or the pyperf timing runner."""
    if len(sys.argv) >= 2 and sys.argv[1] == "_child":
        _memory_child(int(sys.argv[2]), int(sys.argv[3]))
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "memory":
        args = [int(a) for a in sys.argv[2:]]
        tiles, side, repeats = (args + [200, 1024, 5][len(args):])[:3]
        _memory_parent(tiles, side, repeats)
        return
    import pyperf

    runner = pyperf.Runner()
    runner.bench_time_func("publish_shared_tiles_64x1024", _time_publish, 64, 1024)


if __name__ == "__main__":
    main()
