"""Per-tile task submission must not queue a whole pyramid level at once.

`InvertFilter` and `_CorrectTilesDeprecated` globbed every tile of a level and queued one
task per tile before collecting anything, so queue depth -- and, for the invert path, a
retained task list -- scaled with tile count rather than with the pool. On a NAS-sized
level that is tens of thousands of outstanding entries.

Submission now slides a bounded window sized to the pool, matching the shape already used
by the MRC histogram import and tile composition.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from nornir_buildmanager.operations import tile as tile_ops


class _Task:
    def __init__(self, name: str, pool: '_Pool') -> None:
        self.name = name
        self._pool = pool
        self.raise_on_wait: BaseException | None = None

    def wait(self):
        self._pool.on_wait(self)
        if self.raise_on_wait is not None:
            raise self.raise_on_wait


class _Pool:
    """Counts submitted-but-unawaited tasks and records the high-water mark."""

    def __init__(self, max_workers: int | None = None) -> None:
        if max_workers is not None:
            self.max_workers = max_workers
        self.submitted: list[str] = []
        self.outstanding = 0
        self.peak = 0
        self.wait_order: list[str] = []
        self.completion_waits = 0

    def _make(self, name: str) -> _Task:
        task = _Task(name, self)
        self.submitted.append(name)
        self.outstanding += 1
        self.peak = max(self.peak, self.outstanding)
        return task

    def add_task(self, name, func, *args, **kwargs) -> _Task:
        return self._make(name)

    def add_process(self, name, func, *args, **kwargs) -> _Task:
        return self._make(name)

    def on_wait(self, task: _Task) -> None:
        self.outstanding -= 1
        self.wait_order.append(task.name)

    def wait_completion(self) -> None:
        self.completion_waits += 1
        self.outstanding = 0


class TestTheBoundedWindow(unittest.TestCase):
    """Properties of the shared `_submit_bounded` helper."""

    def _run(self, count: int, cap: int) -> _Pool:
        pool = _Pool()
        for task in tile_ops._submit_bounded(lambda i: pool.add_task(str(i), None),
                                            range(count), max_in_flight=cap):
            task.wait()
        return pool

    def test_the_window_never_exceeds_the_cap(self) -> None:
        for count, cap in ((100, 4), (5000, 8), (10000, 16), (7, 100)):
            with self.subTest(count=count, cap=cap):
                pool = self._run(count, cap)
                self.assertEqual(count, len(pool.submitted))
                self.assertLessEqual(pool.peak, cap)

    def test_peak_stops_depending_on_item_count(self) -> None:
        """The property that matters: peak is flat as the level grows."""
        peaks = {n: self._run(n, 8).peak for n in (50, 500, 5000)}
        self.assertEqual({8}, set(peaks.values()), f'peak varied with count: {peaks}')

    def test_every_item_is_submitted_exactly_once(self) -> None:
        pool = self._run(500, 8)
        self.assertEqual([str(i) for i in range(500)], pool.submitted)

    def test_tasks_are_yielded_in_submission_order(self) -> None:
        pool = self._run(200, 8)
        self.assertEqual(pool.submitted, pool.wait_order)

    def test_all_tasks_are_drained(self) -> None:
        pool = self._run(53, 8)
        self.assertEqual(0, pool.outstanding)
        self.assertEqual(53, len(pool.wait_order))

    def test_an_empty_input_submits_nothing(self) -> None:
        pool = self._run(0, 8)
        self.assertEqual([], pool.submitted)

    def test_a_nonpositive_cap_is_clamped_not_fatal(self) -> None:
        for cap in (0, -5):
            with self.subTest(cap=cap):
                pool = self._run(10, cap)
                self.assertEqual(10, len(pool.submitted))
                self.assertLessEqual(pool.peak, 1)

    def test_submission_is_lazy(self) -> None:
        """Nothing is submitted until the caller starts consuming."""
        pool = _Pool()
        gen = tile_ops._submit_bounded(lambda i: pool.add_task(str(i), None),
                                       range(100), max_in_flight=4)
        self.assertEqual([], pool.submitted)
        next(gen)
        self.assertEqual(4, len(pool.submitted))


class TestTheWindowIsSizedToThePool(unittest.TestCase):

    def test_it_follows_the_pool_worker_count(self) -> None:
        self.assertEqual(6, tile_ops._tile_task_max_in_flight(_Pool(max_workers=6)))

    def test_it_falls_back_to_the_cpu_count(self) -> None:
        pool = _Pool()
        self.assertFalse(hasattr(pool, 'max_workers'))
        self.assertEqual(os.cpu_count() or 1, tile_ops._tile_task_max_in_flight(pool))

    def test_it_is_never_below_one(self) -> None:
        for workers in (0, None):
            with self.subTest(workers=workers):
                pool = _Pool(max_workers=workers)  # type: ignore[arg-type]
                self.assertGreaterEqual(tile_ops._tile_task_max_in_flight(pool), 1)


class _Level:
    def __init__(self, path: str, downsample: int = 1) -> None:
        self.FullPath = path
        self.Downsample = downsample


class _Pyramid:
    def __init__(self, level: _Level) -> None:
        self.MaxResLevel = level
        self.Type = 'tile'
        self.NumberOfTiles = 0
        self.LevelFormat = '%03d'
        self.ImageFormatExt = '.png'

    def GetOrCreateTilePyramid(self):
        return [False, self]

    def GetOrCreateLevel(self, level, create):
        return [False, _Level('out')]


class _Filter:
    def __init__(self, pyramid: _Pyramid) -> None:
        self.TilePyramid = pyramid
        self.BitsPerPixel = 8
        self.Parent = self

    def GetOrCreateFilter(self, name):
        return [False, self]

    def GetOrCreateTilePyramid(self):
        return [False, self.TilePyramid]


class TestTheInvertFilterPath(unittest.TestCase):
    """The live site: dispatched from Pipelines.xml as the InvertFilter pipeline."""

    def _invert(self, tile_count: int, workers: int,
                fail_indices: set[int] | None = None) -> _Pool:
        pool = _Pool(max_workers=workers)
        tiles = [f'tile_{i:05d}.png' for i in range(tile_count)]
        fail_indices = fail_indices or set()

        real_add_task = pool.add_task

        def add_task(name, func, *args, **kwargs):
            task = real_add_task(name, func, *args, **kwargs)
            index = int(name.rsplit('_', 1)[-1].split('.')[0]) if tile_count else 0
            if index in fail_indices:
                task.raise_on_wait = OSError(f'boom {index}')
            return task

        pyramid = _Pyramid(_Level('in'))
        filter_node = _Filter(pyramid)

        with patch.object(tile_ops.glob, 'glob', return_value=tiles), \
             patch.object(tile_ops.os, 'makedirs'), \
             patch.object(tile_ops.prettyoutput, 'Log'), \
             patch.object(tile_ops.prettyoutput, 'LogErr'), \
             patch.object(tile_ops.nornir_pools, 'GetGlobalThreadPool', return_value=pool), \
             patch.object(pool, 'add_task', side_effect=add_task):
            list(tile_ops.InvertFilter({}, filter_node, 'Inverted'))
        return pool

    def test_a_large_level_does_not_queue_every_tile(self) -> None:
        pool = self._invert(tile_count=5000, workers=8)
        self.assertEqual(5000, len(pool.submitted))
        self.assertLessEqual(pool.peak, 8,
                             f'{pool.peak} tasks were outstanding at once')

    def test_peak_is_flat_as_the_level_grows(self) -> None:
        peaks = {n: self._invert(tile_count=n, workers=8).peak
                 for n in (100, 1000, 5000)}
        self.assertEqual({8}, set(peaks.values()), f'peak varied with tile count: {peaks}')

    def test_every_tile_is_still_inverted(self) -> None:
        pool = self._invert(tile_count=257, workers=8)
        self.assertEqual(257, len(pool.submitted))
        self.assertEqual(257, len(pool.wait_order))

    def test_a_failing_tile_does_not_abort_the_level(self) -> None:
        """The OSError handler must still swallow per-tile failures."""
        pool = self._invert(tile_count=100, workers=8, fail_indices={3, 47, 99})
        self.assertEqual(100, len(pool.submitted))
        self.assertEqual(100, len(pool.wait_order))

    def test_an_empty_level_is_handled(self) -> None:
        pool = self._invert(tile_count=0, workers=8)
        self.assertEqual([], pool.submitted)


class TestTheOldShapeIsGone(unittest.TestCase):

    def test_the_invert_path_no_longer_retains_a_task_list(self) -> None:
        import inspect
        source = inspect.getsource(tile_ops.InvertFilter)
        self.assertNotIn('tasks.append', source)
        self.assertNotIn('tasks.pop(0)', source)

    def test_both_sites_use_the_shared_window(self) -> None:
        import inspect
        for func in (tile_ops.InvertFilter, tile_ops._CorrectTilesDeprecated):
            with self.subTest(func=func.__name__):
                self.assertIn('_submit_bounded', inspect.getsource(func))


if __name__ == '__main__':
    unittest.main()
