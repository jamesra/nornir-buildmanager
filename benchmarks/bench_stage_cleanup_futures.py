"""Cost and memory of stitch._track_stage_cleanup pruning finished futures (category 17).

Time (pyperf, default): two cases, run once against the old code and once against the new
code, then compare with ``python -m pyperf compare_to old.json new.json``. The package is an
editable install, which wins over PYTHONPATH, so for the old run swap the file in place
(``git show 281bd91^:<stitch.py> > <stitch.py>``, run, ``git checkout -- <stitch.py>``).
On this Windows host pyperf workers need ``--copy-env`` to import the venv packages.

* ``track_done_n10000``: 10,000 already-finished futures passed straight to
  _track_stage_cleanup. Worst case for the prune (every future is pruned, so a prune pass
  runs every 64 calls); isolates the bookkeeping from the executor.
* ``schedule_tiles_n2000``: 2,000 real schedule_remove_staged_tiles calls on a missing
  folder (nothing deleted), waiting on every 10th, as ExportSectionCrops bands do.

Memory (``--memory``, tracemalloc): 1 stage-tree cleanup plus 50,000 no-op tile cleanups,
worker drained every 10 schedules; prints retained futures and traced current/peak MB.
``--memory --flood`` queues all 50,000 before waiting on any.
"""

from __future__ import annotations

import sys
import tempfile
import tracemalloc
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pyperf

from nornir_buildmanager.operations.segmentationtraining import stitch

_PREFIX = "Tile"
_POSTFIX = ".png"


def _reset() -> None:
    """Empty the pending list (and the prune threshold when the code has one)."""
    stitch._stage_cleanup_pending.clear()
    if hasattr(stitch, "_stage_cleanup_prune_at"):
        stitch._stage_cleanup_prune_at = stitch._STAGE_CLEANUP_PRUNE_MIN


def _done_futures(count: int) -> list[Future[Any]]:
    futures: list[Future[Any]] = []
    for _ in range(count):
        future: Future[Any] = Future()
        future.set_result(None)
        futures.append(future)
    return futures


def _track_all(futures: list[Future[Any]]) -> None:
    """Pass every already-finished future to _track_stage_cleanup."""
    _reset()
    track = stitch._track_stage_cleanup
    for future in futures:
        track(future)


def _schedule_tiles(missing: str, count: int, drain_every: int) -> None:
    """Schedule *count* no-op tile cleanups, waiting on every *drain_every*-th one."""
    stitch.wait_stage_cleanup()
    for i in range(count):
        future = stitch.schedule_remove_staged_tiles(
            missing, [(0, 0)], prefix=_PREFIX, postfix=_POSTFIX
        )
        if future is not None and (i + 1) % drain_every == 0:
            future.result()


def _memory(flood: bool) -> None:
    """Print retained futures and tracemalloc current/peak after 50,000 tile cleanups."""
    missing = str(Path(tempfile.mkdtemp()) / "missing")
    count = 50_000
    stitch.wait_stage_cleanup()
    stitch._stage_cleanup_executor()  # create the worker thread outside the trace
    tracemalloc.start()
    tree = stitch.schedule_remove_stage_tree(missing)
    if tree is not None:
        tree.result()
    _schedule_tiles(missing, count, count if flood else 10)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    retained = len(stitch._stage_cleanup_pending)
    mode = "flood" if flood else "paced (drain every 10)"
    print(
        f"{mode}: {count} tile cleanups + 1 tree; retained futures {retained}; "
        f"traced current {current / 1e6:.2f} MB, peak {peak / 1e6:.2f} MB"
    )
    stitch.wait_stage_cleanup()


def main() -> None:
    if "--memory" in sys.argv:
        _memory("--flood" in sys.argv)
        return
    runner = pyperf.Runner()
    runner.bench_func("track_done_n10000", _track_all, _done_futures(10_000))
    missing = str(Path(tempfile.mkdtemp()) / "missing")
    runner.bench_func("schedule_tiles_n2000", _schedule_tiles, missing, 2000, 10)


if __name__ == "__main__":
    main()
