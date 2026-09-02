"""Tests the in-flight bound on ImageMagick tileset assembly.

BuildTilesetLevel enqueues one `magick montage | magick convert` pipeline per
destination tile. The old gate ran once per row and waited on that row's *first* task, so a
row was fully enqueued before anything was awaited: grid width set the number of concurrent
subprocesses. The gate now bounds queued-but-unawaited tasks directly.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from nornir_buildmanager.operations import tile as tile_ops

_MAX_IN_FLIGHT_ENV = 'NORNIR_TILESET_MAX_IN_FLIGHT'
_DEFAULT_MAX_IN_FLIGHT = (os.cpu_count() or 1) * 4


class _Task:
    """Records when the caller awaits it."""

    def __init__(self, index: int, tracker: '_Pool') -> None:
        self.index = index
        self._tracker = tracker
        self.waited = False

    def wait(self) -> None:
        self.waited = True
        self._tracker.on_wait(self)


class _TaskQueue:
    """Stands in for ``pool.tasks``, which nornir_pools.ThreadPool exposes."""

    def __init__(self, pool: '_Pool') -> None:
        self._pool = pool

    def qsize(self) -> int:
        return self._pool.outstanding


class _Pool:
    """Counts queued-but-unawaited tasks and records the high-water mark.

    Exposes ``tasks`` so the pre-fix ``pool.tasks.qsize() > 256`` branch is live here;
    without it the old gate did nothing at all and the comparison would be unfair.
    """

    def __init__(self) -> None:
        self.tasks_added = 0
        self.outstanding = 0
        self.peak_outstanding = 0
        self.wait_order: list[int] = []
        self.completion_waits = 0
        self.tasks = _TaskQueue(self)

    def add_process(self, name, func, *args, **kwargs) -> _Task:
        del name, func, args, kwargs
        task = _Task(self.tasks_added, self)
        self.tasks_added += 1
        self.outstanding += 1
        self.peak_outstanding = max(self.peak_outstanding, self.outstanding)
        return task

    def on_wait(self, task: _Task) -> None:
        self.outstanding -= 1
        self.wait_order.append(task.index)

    def wait_completion(self) -> None:
        self.completion_waits += 1
        self.outstanding = 0


class _TilesetRun:
    """Runs the assembler against a synthetic grid without touching ImageMagick."""

    def __init__(self, rows: int, cols: int, max_in_flight: int | None = None) -> None:
        self.rows, self.cols = rows, cols
        self.pool = _Pool()
        self.max_in_flight = max_in_flight

    def run(self) -> _Pool:
        # The cap is passed as an argument and also exported to the environment. Against the
        # pre-fix code the argument is absorbed by **kwargs and the environment is unread, so
        # these tests fail on measured concurrency rather than erroring on a missing helper.
        env = ({} if self.max_in_flight is None
               else {_MAX_IN_FLIGHT_ENV: str(self.max_in_flight)})
        patches = [
            patch.dict(os.environ, env),
            # Every source tile "exists", so no cell is skipped for a null count of four.
            patch.object(tile_ops.os.path, 'exists', return_value=True),
            patch.object(tile_ops.os, 'makedirs'),
            patch.object(tile_ops.prettyoutput, 'Log'),
            patch.object(tile_ops, 'report_iterate'),
        ]
        for p in patches:
            p.start()
        try:
            tile_ops.BuildTilesetLevel(
                SourcePath='src',
                DestPath='dest',
                DestGridDimensions=(self.rows, self.cols),
                TileDim=(256, 256),
                FilePrefix='',
                FilePostfix='.png',
                pool=self.pool,
                max_in_flight=self.max_in_flight)
        finally:
            for p in reversed(patches):
                p.stop()
        return self.pool


class TestTheInFlightWindowIsBounded(unittest.TestCase):

    def test_a_wide_row_does_not_queue_one_task_per_column(self) -> None:
        """The defect: grid width used to set the concurrent subprocess count."""
        pool = _TilesetRun(rows=1, cols=400, max_in_flight=8).run()
        self.assertEqual(400, pool.tasks_added)
        self.assertLessEqual(pool.peak_outstanding, 8,
                             f'{pool.peak_outstanding} subprocesses were outstanding at once')

    def test_the_bound_holds_across_many_rows(self) -> None:
        pool = _TilesetRun(rows=25, cols=40, max_in_flight=6).run()
        self.assertEqual(1000, pool.tasks_added)
        self.assertLessEqual(pool.peak_outstanding, 6)

    def test_the_bound_scales_with_the_configured_cap(self) -> None:
        peaks = {}
        for cap in (2, 4, 16):
            pool = _TilesetRun(rows=4, cols=60, max_in_flight=cap).run()
            peaks[cap] = pool.peak_outstanding
            self.assertLessEqual(pool.peak_outstanding, cap)
        self.assertLess(peaks[2], peaks[16],
                        'a larger cap should allow more concurrency, not less')

    def test_a_cap_of_one_serialises(self) -> None:
        pool = _TilesetRun(rows=2, cols=5, max_in_flight=1).run()
        self.assertEqual(10, pool.tasks_added)
        self.assertLessEqual(pool.peak_outstanding, 1)

    def test_the_oldest_task_is_awaited_first(self) -> None:
        pool = _TilesetRun(rows=1, cols=20, max_in_flight=4).run()
        self.assertEqual(sorted(pool.wait_order), pool.wait_order,
                         'the window should drain oldest-first')

    def test_a_narrow_grid_never_blocks(self) -> None:
        """Fewer tiles than the cap means no intermediate waits at all."""
        pool = _TilesetRun(rows=1, cols=3, max_in_flight=8).run()
        self.assertEqual(3, pool.tasks_added)
        self.assertEqual([], pool.wait_order)

    def test_everything_is_still_drained_at_the_end(self) -> None:
        pool = _TilesetRun(rows=3, cols=9, max_in_flight=4).run()
        self.assertEqual(27, pool.tasks_added)
        self.assertEqual(1, pool.completion_waits,
                         'wait_completion must still cover tasks left inside the window')


class TestThePoolTypeNoLongerDecidesWhetherGatingHappens(unittest.TestCase):
    """The old gate was ``hasattr``-driven, so it was dead for pools exposing neither
    ``tasks`` nor ``ActiveTasks`` -- LocalMachinePool is one. The bound is now unconditional.
    """

    def test_a_pool_without_a_queue_attribute_is_still_bounded(self) -> None:
        run = _TilesetRun(rows=4, cols=50, max_in_flight=8)
        del run.pool.tasks
        self.assertFalse(hasattr(run.pool, 'tasks'))
        self.assertFalse(hasattr(run.pool, 'ActiveTasks'))
        pool = run.run()
        self.assertEqual(200, pool.tasks_added)
        self.assertLessEqual(pool.peak_outstanding, 8)


class TestEveryTileIsStillSubmitted(unittest.TestCase):

    def test_the_task_count_matches_the_grid(self) -> None:
        for rows, cols in ((1, 1), (1, 17), (7, 1), (5, 11)):
            with self.subTest(rows=rows, cols=cols):
                pool = _TilesetRun(rows=rows, cols=cols, max_in_flight=3).run()
                self.assertEqual(rows * cols, pool.tasks_added)

    def test_an_empty_grid_does_nothing(self) -> None:
        pool = _TilesetRun(rows=0, cols=0, max_in_flight=4).run()
        self.assertEqual(0, pool.tasks_added)


class TestTheCapIsConfigurable(unittest.TestCase):

    def test_the_argument_wins_over_the_environment(self) -> None:
        with patch.dict(os.environ, {_MAX_IN_FLIGHT_ENV: '999'}):
            pool = _TilesetRun(rows=2, cols=40, max_in_flight=5).run()
        self.assertLessEqual(pool.peak_outstanding, 5)

    def test_the_environment_applies_when_no_argument_is_given(self) -> None:
        run = _TilesetRun(rows=2, cols=40, max_in_flight=None)
        with patch.dict(os.environ, {_MAX_IN_FLIGHT_ENV: '7'}):
            pool = run.run()
        self.assertLessEqual(pool.peak_outstanding, 7)

    def test_the_env_name_is_the_documented_one(self) -> None:
        self.assertEqual(_MAX_IN_FLIGHT_ENV, tile_ops._TILESET_MAX_IN_FLIGHT_ENV)

    def test_the_default_is_per_processor(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_MAX_IN_FLIGHT_ENV, None)
            self.assertEqual(_DEFAULT_MAX_IN_FLIGHT,
                             tile_ops._tileset_max_in_flight_tasks())

    def test_the_environment_override_is_honoured(self) -> None:
        with patch.dict(os.environ, {_MAX_IN_FLIGHT_ENV: '9'}):
            self.assertEqual(9, tile_ops._tileset_max_in_flight_tasks())

    def test_an_invalid_override_falls_back(self) -> None:
        for bad in ('abc', '0', '-3', ''):
            with self.subTest(value=bad):
                with patch.dict(os.environ, {_MAX_IN_FLIGHT_ENV: bad}):
                    self.assertEqual(_DEFAULT_MAX_IN_FLIGHT,
                                     tile_ops._tileset_max_in_flight_tasks())


class TestTheOldGateIsGone(unittest.TestCase):

    def test_the_first_task_gate_is_removed(self) -> None:
        import inspect
        source = inspect.getsource(tile_ops.BuildTilesetLevel)
        self.assertNotIn('FirstTaskForRow', source)

    def test_the_queue_depth_thresholds_are_gone(self) -> None:
        import inspect
        source = inspect.getsource(tile_ops.BuildTilesetLevel)
        for marker in ('qsize() > 256', 'ActiveTasks > 512'):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, source)


class TestBuildTilesetLevelDefaultPool(unittest.TestCase):
    """#224: default must support add_process (not the global thread pool)."""

    def test_none_pool_uses_global_process_pool(self) -> None:
        process_pool = _Pool()
        with patch.object(tile_ops.nornir_pools, 'GetGlobalProcessPool', return_value=process_pool) as gpp, \
                patch.object(tile_ops.nornir_pools, 'GetGlobalThreadPool') as gtp, \
                patch.object(tile_ops.os.path, 'exists', return_value=True), \
                patch.object(tile_ops.os, 'makedirs'), \
                patch.object(tile_ops.prettyoutput, 'Log'), \
                patch.object(tile_ops, 'report_iterate'):
            tile_ops.BuildTilesetLevel(
                SourcePath='src',
                DestPath='dest',
                DestGridDimensions=(1, 1),
                TileDim=(256, 256),
                FilePrefix='',
                FilePostfix='.png',
                pool=None,
                max_in_flight=4)
        gpp.assert_called()
        gtp.assert_not_called()
        self.assertGreaterEqual(process_pool.tasks_added, 1)


if __name__ == '__main__':
    unittest.main()
