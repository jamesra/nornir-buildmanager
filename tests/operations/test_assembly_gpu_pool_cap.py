"""Tile assembly caps this process's CuPy pool per section and returns the cache when the section ends.

Each CuPy pool keeps its peak, so on a GPU shared by several AssembleTiles processes (RC3: 2-3 lanes on one
24 GB card) the first process to peak held ~19 GB and the others ran out of memory.
"""
from __future__ import annotations

import logging
import unittest
from unittest import mock

import nornir_imageregistration

from nornir_buildmanager.operations import stos_brute_pairs, tile

_GiB = 2 ** 30


class _FakePool:
    def __init__(self, limit: int = 0) -> None:
        self.limit = limit
        self.freed = 0

    def get_limit(self) -> int:
        return self.limit

    def set_limit(self, size: int) -> None:
        self.limit = size

    def free_all_blocks(self) -> None:
        self.freed += 1


class TestAssemblyPoolCap(unittest.TestCase):

    def setUp(self) -> None:
        self.pool = _FakePool()
        self.pinned = _FakePool()
        fake_cp = mock.Mock()
        fake_cp.get_default_memory_pool.return_value = self.pool
        fake_cp.get_default_pinned_memory_pool.return_value = self.pinned
        patches = [
            mock.patch.object(nornir_imageregistration, 'cp', fake_cp),
            mock.patch.object(nornir_imageregistration, 'UsingCupy', return_value=True),
            mock.patch.object(tile, '_assembly_pool_limit', None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.logger = logging.getLogger(__name__)

    def _cap(self, free_bytes: int) -> None:
        """Run the cap as if *free_bytes* of device memory were free."""
        real = stos_brute_pairs.gpu_worker_pool_limit
        with mock.patch.object(stos_brute_pairs, 'gpu_worker_pool_limit',
                               side_effect=lambda workers: real(workers, free_bytes)):
            tile._cap_cupy_pool_for_assembly(self.logger)

    def test_the_cap_is_the_free_memory_share_for_one_worker(self) -> None:
        self._cap(20 * _GiB)
        self.assertEqual(self.pool.limit, stos_brute_pairs.gpu_worker_pool_limit(1, 20 * _GiB))
        self.assertGreater(self.pool.limit, 18 * _GiB)
        self.assertLess(self.pool.limit, 20 * _GiB)
        self.assertEqual(self.pool.freed, 1, "the process's own cache is returned before free memory is read")

    def test_each_section_recomputes_its_own_cap(self) -> None:
        self._cap(10 * _GiB)
        first = self.pool.limit
        self._cap(20 * _GiB)
        self.assertGreater(self.pool.limit, first)

    def test_a_limit_set_elsewhere_is_left_alone(self) -> None:
        self.pool.limit = 3 * _GiB
        self._cap(20 * _GiB)
        self.assertEqual(self.pool.limit, 3 * _GiB)

    def test_numpy_runs_are_not_capped(self) -> None:
        with mock.patch.object(nornir_imageregistration, 'UsingCupy', return_value=False):
            self._cap(20 * _GiB)
        self.assertEqual(self.pool.limit, 0)

    def test_release_returns_device_and_pinned_blocks(self) -> None:
        tile._release_cupy_cache()
        self.assertEqual((self.pool.freed, self.pinned.freed), (1, 1))


if __name__ == '__main__':
    unittest.main()
