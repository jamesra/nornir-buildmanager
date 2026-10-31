"""Tests for noise-crushed import plumbing: crush mode decisions, noise cache, tile dispatch, MRC export, args."""

from __future__ import annotations

import os
import tempfile
import time
import types
import unittest
from typing import Any, cast
from unittest import mock

import nornir_pools
import numpy as np
from nornir_buildmanager.build import BuildParserRoot
from nornir_buildmanager.importers import shared
from nornir_buildmanager.importers.mrc import MRCImport
from nornir_buildmanager.operations.pruneobj import PruneObj
from nornir_imageregistration import noise_crush
from nornir_imageregistration.noise_crush import CrushParams
from nornir_shared.histogram import Histogram
from PIL import Image

PARAMS = CrushParams(k=2.5, step=100, sigma=40.0, offset=0, bits=8, source_dtype='uint16')


class _FakeFilter:
    def __init__(self, attrib: dict[str, str], locked: bool = False):
        self.attrib = attrib
        self.Locked = locked
        self.FullPath = '/vol/TEM/0001/TEM/Raw8'
        self.cleaned_reason: str | None = None

    def Clean(self, reason: str | None = None) -> None:
        self.cleaned_reason = reason


class _FakeChannel:
    def __init__(self, filter_node: _FakeFilter | None):
        self._filter = filter_node

    def GetFilter(self, name: str) -> _FakeFilter | None:
        return self._filter


def _channel(filter_node: _FakeFilter | None) -> Any:
    return cast(Any, _FakeChannel(filter_node))


class TestResolveCrushMode(unittest.TestCase):

    def setUp(self) -> None:
        self.logger = mock.Mock()

    def test_new_filter_uses_requested(self) -> None:
        self.assertEqual(shared.ResolveCrushMode(_channel(None), 'Raw8', PARAMS, self.logger), (PARAMS, False))
        self.assertEqual(shared.ResolveCrushMode(_channel(None), 'Raw8', None, self.logger), (None, False))

    def test_existing_uncrushed_filter_is_kept(self) -> None:
        legacy = _FakeFilter({'MinIntensityCutoff': '100'})
        self.assertEqual(shared.ResolveCrushMode(_channel(legacy), 'Raw8', PARAMS, self.logger), (None, False))
        self.assertIsNone(legacy.cleaned_reason)

    def test_matching_crushed_filter_is_reused(self) -> None:
        existing = _FakeFilter(PARAMS.to_attributes())
        requested = CrushParams(k=2.5, step=100, sigma=40.001, offset=0, bits=8, source_dtype='uint16')
        (params, removed) = shared.ResolveCrushMode(_channel(existing), 'Raw8', requested, self.logger)
        self.assertEqual((params, removed), (PARAMS, False))
        self.assertIsNone(existing.cleaned_reason)

    def test_changed_step_cleans_filter(self) -> None:
        existing = _FakeFilter(PARAMS.to_attributes())
        requested = CrushParams(k=3.0, step=120, sigma=40.0, offset=0, bits=8, source_dtype='uint16')
        self.assertEqual(shared.ResolveCrushMode(_channel(existing), 'Raw8', requested, self.logger),
                         (requested, True))
        self.assertIn('100 -> 120', existing.cleaned_reason or '')

    def test_no_crush_cleans_crushed_filter(self) -> None:
        existing = _FakeFilter(PARAMS.to_attributes())
        self.assertEqual(shared.ResolveCrushMode(_channel(existing), 'Raw8', None, self.logger), (None, True))
        self.assertIn('turned off', existing.cleaned_reason or '')

    def test_locked_crushed_filter_keeps_its_params(self) -> None:
        existing = _FakeFilter(PARAMS.to_attributes(), locked=True)
        self.assertEqual(shared.ResolveCrushMode(_channel(existing), 'Raw8', None, self.logger), (PARAMS, False))
        requested = CrushParams(k=3.0, step=120, sigma=40.0, offset=0, bits=8, source_dtype='uint16')
        self.assertEqual(shared.ResolveCrushMode(_channel(existing), 'Raw8', requested, self.logger),
                         (PARAMS, False))
        self.assertIsNone(existing.cleaned_reason)


class TestCrushHelpers(unittest.TestCase):

    def test_counts_to_codes(self) -> None:
        params = CrushParams(k=2.5, step=50, sigma=20.0, offset=-100, bits=8, source_dtype='int16')
        self.assertAlmostEqual(shared.CountsToCrushCodes(400, params), 10.0)

    def test_filter_name_follows_bits(self) -> None:
        self.assertEqual(shared.CrushedFilterName(PARAMS), 'Raw8')
        wide = CrushParams(k=2.5, step=1, sigma=0.4, offset=0, bits=12, source_dtype='uint16')
        self.assertEqual(shared.CrushedFilterName(wide), 'Raw16')

    def test_section_params_use_histogram_top_bin(self) -> None:
        noise = shared.SectionNoise(sigma=4.0, sample_min=0, sample_max=500, source_dtype='uint16')
        self.assertEqual(shared.SectionCrushParams(noise, None, k=2.5).bits, 8)
        histogram = Histogram.Init(0, 4000, 40)
        histogram.Bins[35] = 1
        self.assertGreater(shared.SectionCrushParams(noise, histogram, k=2.5).bits, 8)

    def test_set_filter_attributes_round_trip(self) -> None:
        node = cast(Any, types.SimpleNamespace(attrib={}))
        shared.SetFilterCrushAttributes(node, PARAMS)
        self.assertEqual(CrushParams.from_attributes(node.attrib), PARAMS)

    def test_prune_score_scale(self) -> None:
        crushed = types.SimpleNamespace(attrib=PARAMS.to_attributes(), MinIntensityCutoff=5.0,
                                        MaxIntensityCutoff=56.0)
        self.assertEqual(PruneObj.CrushedScoreScale(crushed), 5.0)
        legacy = types.SimpleNamespace(attrib={}, MinIntensityCutoff=5.0, MaxIntensityCutoff=56.0)
        self.assertIsNone(PruneObj.CrushedScoreScale(legacy))


class TestGetSectionNoise(unittest.TestCase):

    def _tiles(self, tmp: str, count: int = 3) -> list[str]:
        rng = np.random.default_rng(0)
        paths = []
        for i in range(count):
            path = os.path.join(tmp, f'{i:03d}.tif')
            Image.fromarray(rng.normal(5000, 40, (256, 256)).astype(np.uint16)).save(path)
            paths.append(path)
        return paths

    def test_estimate_is_cached_and_invalidated_by_newer_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tiles = self._tiles(tmp)
            hist_path = os.path.join(tmp, 'cache', 'Histogram.xml')
            loads: list[int] = []

            def load(i: int):
                loads.append(i)
                return noise_crush.load_raw_tile(tiles[i])

            first = shared.GetSectionNoise(hist_path, tiles, len(tiles), load)
            self.assertAlmostEqual(first.sigma, 40.0, delta=3.0)
            self.assertEqual(first.source_dtype, 'uint16')
            self.assertEqual(len(loads), 3)

            self.assertEqual(shared.GetSectionNoise(hist_path, tiles, len(tiles), load), first)
            self.assertEqual(len(loads), 3)

            future = time.time() + 60
            os.utime(tiles[1], (future, future))
            shared.GetSectionNoise(hist_path, tiles, len(tiles), load)
            self.assertEqual(len(loads), 6)

    def test_unwritable_cache_still_returns_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tiles = self._tiles(tmp, 1)
            hist_path = os.path.join(tmp, 'ro', 'Histogram.xml')
            with mock.patch.object(shared.os, 'makedirs', side_effect=OSError(30, 'Read-only file system')):
                noise = shared.GetSectionNoise(hist_path, tiles, 1, lambda i: noise_crush.load_raw_tile(tiles[i]))
            self.assertGreater(noise.sigma, 0)
            self.assertFalse(os.path.exists(os.path.dirname(hist_path)))


class _FakeTask:
    def __init__(self, pool: _FakePool, fn, args):
        self._pool = pool
        self._fn = fn
        self._args = args

    def wait(self):
        self._pool.outstanding -= 1
        return self._fn(*self._args)


class _FakePool:
    def __init__(self):
        self.outstanding = 0
        self.max_outstanding = 0

    def add_task(self, name, fn, *args):
        self.outstanding += 1
        self.max_outstanding = max(self.max_outstanding, self.outstanding)
        return _FakeTask(self, fn, args)

    def wait_completion(self):
        pass

    def shutdown(self):
        pass


class TestCrushTiles(unittest.TestCase):

    def tearDown(self) -> None:
        nornir_pools.ClosePools()

    def _sources(self, tmp: str, count: int) -> dict[str, str]:
        rng = np.random.default_rng(1)
        mapping = {}
        for i in range(count):
            source = os.path.join(tmp, f'{i:03d}.tif')
            Image.fromarray(rng.integers(0, 20000, (32, 24), dtype=np.uint16)).save(source)
            mapping[source] = os.path.join(tmp, 'out', f'{i:03d}.png')
        return mapping

    def test_writes_flipped_codes_with_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mapping = self._sources(tmp, 3)
            shared.CrushTiles(mapping, PARAMS, flip=True)
            for (source, target) in mapping.items():
                expected = noise_crush.crush(np.flip(noise_crush.load_raw_tile(source), 0), PARAMS)
                np.testing.assert_array_equal(noise_crush.load_raw_tile(target), expected)
                self.assertEqual(noise_crush.read_crush_params(target), PARAMS)

    def test_in_flight_tasks_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mapping = self._sources(tmp, 40)
            pool = _FakePool()
            with mock.patch.object(shared.nornir_pools, 'GetMultithreadingPool', return_value=pool), \
                    mock.patch.object(shared.os, 'cpu_count', return_value=4):
                shared.CrushTiles(mapping, PARAMS, flip=False)
            self.assertEqual(pool.outstanding, 0)
            self.assertLessEqual(pool.max_outstanding, 8)
            self.assertTrue(all(os.path.exists(t) for t in mapping.values()))


class TestMRCExportCrushed(unittest.TestCase):

    def test_export_image_writes_crushed_signed_tile(self) -> None:
        rng = np.random.default_rng(2)
        tile = rng.integers(-300, 12000, (40, 30)).astype(np.int16)
        params = noise_crush.make_crush_params(30.0, 12000, np.int16, section_min=-300)
        fake_mrc = cast(Any, types.SimpleNamespace(get_tile_as_numpy_memmap=lambda i: tile))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(MRCImport.ExportImage(fake_mrc, tmp, '.png', 0, None, True, params))
            written = [f for f in os.listdir(tmp) if f.endswith('.png')]
            self.assertEqual(len(written), 1)
            path = os.path.join(tmp, written[0])
            np.testing.assert_array_equal(noise_crush.load_raw_tile(path), noise_crush.crush(np.flipud(tile), params))
            self.assertEqual(noise_crush.read_crush_params(path), params)


class TestImportArguments(unittest.TestCase):

    def test_crush_is_default_and_opt_out(self) -> None:
        parser = BuildParserRoot()
        for pipeline in ('ImportIDoc', 'ImportMRC'):
            default = parser.parse_args([pipeline, '/vol', '/raw'])
            self.assertFalse(default.NoCrush)
            self.assertEqual(default.CrushK, 2.5)
            opted_out = parser.parse_args([pipeline, '/vol', '/raw', '-NoCrush', '-CrushK', '3'])
            self.assertTrue(opted_out.NoCrush)
            self.assertEqual(opted_out.CrushK, 3.0)


if __name__ == '__main__':
    unittest.main()
