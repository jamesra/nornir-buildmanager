"""A section whose GPU assembly stays out of memory is finished on NumPy instead of failing the batch.

RC3's AssembleTiles shared srv's GPU with RC1's masked StosBrute (2026-11-21): the strip retries ran out and
the whole batch stopped.
"""
from __future__ import annotations

import logging
import unittest
from unittest import mock

import nornir_imageregistration

from nornir_buildmanager.operations import tile


class _FakeTile:
    def __init__(self) -> None:
        self.released = False

    def ReleaseLoadedImage(self) -> None:
        self.released = True


class _FakeTileset(dict):
    """Yields (row, col, image) for a 2x2 grid; raises *error* after *fail_after* tiles."""

    def __init__(self, fail_after: int | None = None, error: Exception | None = None) -> None:
        super().__init__({0: _FakeTile(), 1: _FakeTile()})
        self.fail_after = fail_after
        self.error = error
        self.backend_seen: list[object] = []

    def GenerateOptimizedTiles(self, **_kwargs):
        self.backend_seen.append(nornir_imageregistration.GetActiveComputationLib())
        for n, cell in enumerate([(0, 0), (0, 1), (1, 0), (1, 1)]):
            if self.fail_after is not None and n == self.fail_after:
                raise self.error  # type: ignore[misc]
            yield cell[0], cell[1], f'image{cell}'


class TestNumpyFallback(unittest.TestCase):

    def setUp(self) -> None:
        self.calls: list[object] = []
        state = {'lib': nornir_imageregistration.ComputationLib.cupy}

        def set_lib(lib, **_kwargs):
            self.calls.append(lib)
            state['lib'] = lib

        patches = [
            mock.patch.object(nornir_imageregistration, 'GetActiveComputationLib', side_effect=lambda: state['lib']),
            mock.patch.object(nornir_imageregistration, 'SetActiveComputationLib', side_effect=set_lib),
            mock.patch.object(nornir_imageregistration, 'UsingCupy',
                              side_effect=lambda: state['lib'] == nornir_imageregistration.ComputationLib.cupy),
            mock.patch.object(tile, '_release_cupy_cache'),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.state = state

    def _run(self, first: _FakeTileset, second: _FakeTileset) -> list[tuple[int, int, str]]:
        return list(tile._optimized_tiles_with_numpy_fallback(
            first, lambda: second, target_space_scale=1.0, tile_dims=None, max_temp_image_area=None,
            section_label='821', Logger=logging.getLogger(__name__)))

    def test_out_of_memory_finishes_the_section_on_numpy(self) -> None:
        first = _FakeTileset(fail_after=2, error=RuntimeError('cudaErrorMemoryAllocation: out of memory'))
        second = _FakeTileset()
        tiles = self._run(first, second)
        self.assertEqual([(r, c) for r, c, _ in tiles], [(0, 0), (0, 1), (1, 0), (1, 1)])  # each tile once
        self.assertEqual(second.backend_seen, [nornir_imageregistration.ComputationLib.numpy])
        self.assertTrue(all(t.released for t in first.values()), "the GPU tileset's images are dropped")
        tile._release_cupy_cache.assert_called_once()
        self.assertEqual(self.state['lib'], nornir_imageregistration.ComputationLib.cupy, "the backend is restored")

    def test_other_errors_propagate(self) -> None:
        first = _FakeTileset(fail_after=1, error=ValueError('bad mosaic'))
        with self.assertRaises(ValueError):
            self._run(first, _FakeTileset())
        self.assertEqual(self.calls, [])

    def test_no_error_uses_the_gpu_tileset_only(self) -> None:
        second = _FakeTileset()
        tiles = self._run(_FakeTileset(), second)
        self.assertEqual(len(tiles), 4)
        self.assertEqual(second.backend_seen, [])


if __name__ == '__main__':
    unittest.main()
