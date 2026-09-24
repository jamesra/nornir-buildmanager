"""The MRC importer must not shut down pools it did not create.

``ExportImages`` fanned one task per tile onto ``GetGlobalThreadPool()`` and then called
``pool.shutdown()`` on it. That pool is a process-wide singleton with 26 submission sites
across nornir-imageregistration, nornir-buildmanager and nornir-pyre, and ``shutdown()`` is
not a private teardown: it sets ``shutdown_event`` *and* drops the pool from
``dictKnownPools``.

Measured before the fix:

``````
=== is it a singleton? ===
  same object            = True
  pool name              = 'Global local thread pool'
=== is the cached holder stranded? ===
  shutdown_event set     = True
  still in registry      = False
  add_task on cached ref = AssertionError:
=== does a re-fetch get a working pool? ===
  new object             = True
  new pool works         = True
``````

Two things make this nastier than it looks. The failure is a *bare* assert with no message,
and it is compiled out entirely under ``-O`` -- at which point ``add_task`` on a dead pool
enqueues onto a pool with no workers instead of complaining. And a caller that re-fetches
via ``GetGlobalThreadPool()`` silently receives a different, working pool, so whether a
component breaks depends on whether it happened to cache the reference.

``tile.py`` had already commented out its own ``pool.shutdown()`` calls in favour of
dropping the reference. This brings the MRC importer in line: collect the tasks, wait on
those.

Review issue #223 (found while auditing pool usage for #85).
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

import nornir_pools

from nornir_buildmanager.importers import mrc


class _FakeTask:
    def __init__(self, name, exception=None):
        self.name = name
        self.exception = exception
        self.waited = False

    def wait(self):
        self.waited = True
        if self.exception is not None:
            raise self.exception


class _RecordingPool:
    """Stands in for a shared pool, and objects if anyone tries to tear it down."""

    def __init__(self):
        self.submitted = []
        self.shutdown_calls = 0

    def add_task(self, name, func, *args, **kwargs):
        task = _FakeTask(name)
        self.submitted.append(task)
        return task

    def shutdown(self):
        self.shutdown_calls += 1

    @property
    def max_workers(self):
        return 4


class _FakeMRC:
    num_tiles = 5
    tile_meta = list(range(5))


class TestExportImagesLeavesTheSharedPoolAlone(unittest.TestCase):

    def _run_export(self, pool):
        with mock.patch.object(nornir_pools, 'GetGlobalThreadPool', return_value=pool):
            mrc.MRCImport.ExportImages(_FakeMRC(), 'out', '.png', min_max_gamma=None)

    def test_it_does_not_shut_down_the_global_pool(self):
        pool = _RecordingPool()

        self._run_export(pool)

        self.assertEqual(0, pool.shutdown_calls,
                         'ExportImages shut down a pool it did not create')

    def test_it_still_submits_one_task_per_tile(self):
        pool = _RecordingPool()

        self._run_export(pool)

        self.assertEqual(_FakeMRC.num_tiles, len(pool.submitted))

    def test_it_waits_for_every_task_it_submitted(self):
        pool = _RecordingPool()

        self._run_export(pool)

        self.assertTrue(all(task.waited for task in pool.submitted),
                        'every submitted task must be waited on')

    def test_a_failing_tile_is_raised_rather_than_swallowed(self):
        """shutdown() -> wait_completion() -> tasks.join() did not re-raise."""
        pool = _RecordingPool()
        boom = ValueError('tile 3 is unreadable')

        original = pool.add_task

        def add_task(name, func, *args, **kwargs):
            task = original(name, func, *args, **kwargs)
            if name == '3':
                task.exception = boom
            return task

        pool.add_task = add_task  # type: ignore[method-assign]

        with self.assertRaises(ValueError):
            self._run_export(pool)

    def test_the_failing_tile_number_is_reported(self):
        pool = _RecordingPool()
        tasks = [_FakeTask('0'), _FakeTask('7', ValueError('bad tile'))]

        with mock.patch.object(mrc.prettyoutput, 'LogErr') as logged:
            with self.assertRaises(ValueError):
                mrc._wait_for_tile_tasks(tasks, 'export')

        self.assertTrue(logged.called, 'the failure should be logged')
        self.assertIn('7', str(logged.call_args),
                      'the log should name which tile failed')


class TestCacheTilesLeavesItsPoolAlone(unittest.TestCase):

    def test_cache_tiles_does_not_shut_down_its_pool(self):
        pool = _RecordingPool()

        with mock.patch.object(nornir_pools, 'GetThreadPool', return_value=pool):
            mrc.MRCImport.cache_tiles(_FakeMRC(), 'out', '.png', min_max_gamma=None)

        self.assertEqual(0, pool.shutdown_calls)
        self.assertEqual(_FakeMRC.num_tiles, len(pool.submitted))
        self.assertTrue(all(task.waited for task in pool.submitted))


class TestTheHazardIsReal(unittest.TestCase):
    """Pins the pool behaviour the fix depends on, so a change here is visible."""

    def test_the_global_pool_is_a_singleton(self):
        first = nornir_pools.GetGlobalThreadPool()
        second = nornir_pools.GetGlobalThreadPool()

        self.assertIs(first, second)

    def test_shutting_it_down_strands_a_cached_reference(self):
        """Why ExportImages must not call shutdown: this is what it did to 26 other sites."""
        pool = nornir_pools.GetThreadPool('t223-strand-victim', num_threads=2)
        self.assertEqual(4, pool.add_task('t', abs, -4).wait_return(),
                         'premise: the pool works before shutdown')

        pool.shutdown()

        self.assertTrue(pool.shutdown_event.is_set())
        self.assertNotIn(pool, nornir_pools.dictKnownPools.values(),
                         'shutdown drops the pool from the registry')
        with self.assertRaises(AssertionError):
            pool.add_task('after', int, '0')

    def test_a_refetch_after_shutdown_hides_the_breakage(self):
        """The stranded holder and the healthy re-fetch are different objects."""
        name = 't223-refetch'
        first = nornir_pools.GetThreadPool(name, num_threads=2)
        first.shutdown()

        second = nornir_pools.GetThreadPool(name, num_threads=2)
        self.addCleanup(second.shutdown)

        self.assertIsNot(first, second)
        self.assertEqual(4, second.add_task('t', abs, -4).wait_return())


if __name__ == '__main__':
    unittest.main()
