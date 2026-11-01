"""Tests for histogram cache ensure/freshness and contrast resolution."""

from __future__ import annotations

import errno
import os
import tempfile
import time
import unittest
from unittest import mock

import numpy as np

import nornir_pools
from nornir_buildmanager.importers import shared
from nornir_buildmanager.importers.shared import ContrastValue
from nornir_imageregistration.image_stats import HistogramOfArray
from nornir_shared.histogram import Histogram


class _PoolCleanupTestCase(unittest.TestCase):

    def tearDown(self) -> None:
        nornir_pools.ClosePools()
        super().tearDown()


class TestEnsureHistogramCache(_PoolCleanupTestCase):

    def _write_png(self, path: str, arr: np.ndarray) -> None:
        import nornir_imageregistration
        nornir_imageregistration.SaveImage(path, arr, bpp=8)

    def _make_hist(self) -> Histogram:
        arr = np.arange(256, dtype=np.uint8).reshape(16, 16)
        return HistogramOfArray(arr, bpp=8, num_bins=256, min_val=0, max_val=255)

    def test_ensure_writes_xml_and_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tile = os.path.join(tmp, 't0.png')
            self._write_png(tile, np.full((32, 32), 128, dtype=np.uint8))
            xml_path = os.path.join(tmp, 'Histogram.xml')
            calls = {'n': 0}

            def calc() -> Histogram:
                calls['n'] += 1
                return self._make_hist()

            h1 = shared.ensure_histogram_cache(xml_path, calc, [tile], stride=4)
            self.assertTrue(os.path.exists(xml_path))
            self.assertTrue(os.path.exists(shared.histogram_cache_sidecar_path(xml_path)))
            self.assertEqual(calls['n'], 1)

            h2 = shared.ensure_histogram_cache(xml_path, calc, [tile], stride=4)
            self.assertEqual(calls['n'], 1)
            self.assertEqual(h1.NumBins, h2.NumBins)

    def test_stale_on_newer_tile_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tile = os.path.join(tmp, 't0.png')
            self._write_png(tile, np.full((32, 32), 100, dtype=np.uint8))
            xml_path = os.path.join(tmp, 'Histogram.xml')
            calls = {'n': 0}

            def calc() -> Histogram:
                calls['n'] += 1
                return self._make_hist()

            shared.ensure_histogram_cache(xml_path, calc, [tile], stride=4)
            self.assertEqual(calls['n'], 1)

            newer = os.path.getmtime(xml_path) + 30.0
            os.utime(tile, (newer, newer))
            shared.ensure_histogram_cache(xml_path, calc, [tile], stride=4)
            self.assertEqual(calls['n'], 2)

    def test_fresh_on_tile_mtime_within_smb_skew(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tile = os.path.join(tmp, 't0.png')
            self._write_png(tile, np.full((32, 32), 100, dtype=np.uint8))
            xml_path = os.path.join(tmp, 'Histogram.xml')
            calls = {'n': 0}

            def calc() -> Histogram:
                calls['n'] += 1
                return self._make_hist()

            shared.ensure_histogram_cache(xml_path, calc, [tile], stride=4)
            self.assertEqual(calls['n'], 1)

            skewed = os.path.getmtime(xml_path) + 1.0
            os.utime(tile, (skewed, skewed))
            shared.ensure_histogram_cache(xml_path, calc, [tile], stride=4)
            self.assertEqual(calls['n'], 1)

    def test_stale_on_removed_tile_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t0 = os.path.join(tmp, 't0.png')
            t1 = os.path.join(tmp, 't1.png')
            self._write_png(t0, np.full((16, 16), 50, dtype=np.uint8))
            self._write_png(t1, np.full((16, 16), 200, dtype=np.uint8))
            xml_path = os.path.join(tmp, 'Histogram.xml')
            calls = {'n': 0}

            def calc() -> Histogram:
                calls['n'] += 1
                return self._make_hist()

            shared.ensure_histogram_cache(xml_path, calc, [t0, t1], stride=4)
            self.assertEqual(calls['n'], 1)

            # Remove t1 from inputs but leave file (and keep old mtimes by not touching).
            shared.ensure_histogram_cache(xml_path, calc, [t0], stride=4)
            self.assertEqual(calls['n'], 2)

    def test_stale_on_added_tile_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t0 = os.path.join(tmp, 't0.png')
            t1 = os.path.join(tmp, 't1.png')
            self._write_png(t0, np.full((16, 16), 50, dtype=np.uint8))
            xml_path = os.path.join(tmp, 'Histogram.xml')
            calls = {'n': 0}

            def calc() -> Histogram:
                calls['n'] += 1
                return self._make_hist()

            shared.ensure_histogram_cache(xml_path, calc, [t0], stride=4)
            self._write_png(t1, np.full((16, 16), 200, dtype=np.uint8))
            # Make t1 older than XML so only fingerprint triggers recompute.
            xml_mtime = os.path.getmtime(xml_path)
            os.utime(t1, (xml_mtime - 100, xml_mtime - 100))

            shared.ensure_histogram_cache(xml_path, calc, [t0, t1], stride=4)
            self.assertEqual(calls['n'], 2)

    def test_stale_on_stride_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tile = os.path.join(tmp, 't0.png')
            self._write_png(tile, np.full((16, 16), 10, dtype=np.uint8))
            xml_path = os.path.join(tmp, 'Histogram.xml')
            calls = {'n': 0}

            def calc() -> Histogram:
                calls['n'] += 1
                return self._make_hist()

            shared.ensure_histogram_cache(xml_path, calc, [tile], stride=4)
            shared.ensure_histogram_cache(xml_path, calc, [tile], stride=1)
            self.assertEqual(calls['n'], 2)


class TestReadOnlyAndLegacyHistogramCache(_PoolCleanupTestCase):
    """RC1's MRCs sit on a read-only share beside Histogram-N.xml files from a 2024 import, with no
    cache sidecars. Saving the cache there raised EROFS, and every section was re-histogrammed
    (~32 GB read each) because a cache without a sidecar counted as stale."""

    def _make_hist(self) -> Histogram:
        arr = np.arange(256, dtype=np.uint8).reshape(16, 16)
        return HistogramOfArray(arr, bpp=8, num_bins=256, min_val=0, max_val=255)

    def _input(self, tmp: str) -> str:
        path = os.path.join(tmp, '0022_redo.mrc')
        with open(path, 'wb') as f:
            f.write(b'mrc')
        return path

    def test_an_unwritable_cache_is_logged_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mrc = self._input(tmp)
            xml_path = os.path.join(tmp, 'Histogram-22.xml')
            with mock.patch.object(Histogram, 'Save', side_effect=OSError(errno.EROFS, 'Read-only file system')), \
                    mock.patch.object(shared.prettyoutput, 'Log') as logged:
                histogram = shared.ensure_histogram_cache(xml_path, self._make_hist, [mrc])
            self.assertEqual(256, histogram.NumBins)
            self.assertIn('Could not cache the histogram', str(logged.call_args))

    def test_a_legacy_histogram_newer_than_its_input_is_reused_and_cached_elsewhere(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mrc = self._input(tmp)
            legacy = os.path.join(tmp, 'Histogram-22.xml')
            self._make_hist().Save(legacy)
            os.utime(mrc, (os.path.getmtime(legacy) - 100,) * 2)
            cache = os.path.join(tmp, 'cache', 'Histogram-22.xml')
            calls = {'n': 0}

            def calc() -> Histogram:
                calls['n'] += 1
                return self._make_hist()

            shared.ensure_histogram_cache(cache, calc, [mrc], legacy_cache_path=legacy)
            self.assertEqual(0, calls['n'])
            self.assertTrue(os.path.exists(cache))
            self.assertTrue(os.path.exists(shared.histogram_cache_sidecar_path(cache)))
            self.assertFalse(os.path.exists(shared.histogram_cache_sidecar_path(legacy)),
                             'the source directory is left untouched')

            shared.ensure_histogram_cache(cache, calc, [mrc], legacy_cache_path=legacy)
            self.assertEqual(0, calls['n'], 'the second import uses the new cache')

    def test_a_legacy_histogram_in_place_gets_a_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mrc = self._input(tmp)
            legacy = os.path.join(tmp, 'Histogram-22.xml')
            self._make_hist().Save(legacy)
            os.utime(mrc, (os.path.getmtime(legacy) - 100,) * 2)
            shared.ensure_histogram_cache(legacy, self._make_hist, [mrc], legacy_cache_path=legacy)
            self.assertTrue(os.path.exists(shared.histogram_cache_sidecar_path(legacy)))

    def test_a_legacy_histogram_older_than_its_input_is_recomputed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, 'Histogram-22.xml')
            self._make_hist().Save(legacy)
            mrc = self._input(tmp)
            os.utime(mrc, (os.path.getmtime(legacy) + 100,) * 2)
            calls = {'n': 0}

            def calc() -> Histogram:
                calls['n'] += 1
                return self._make_hist()

            shared.ensure_histogram_cache(os.path.join(tmp, 'cache', 'Histogram-22.xml'), calc, [mrc],
                                          legacy_cache_path=legacy)
            self.assertEqual(1, calls['n'])

    def test_without_a_legacy_path_a_sidecarless_cache_is_still_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mrc = self._input(tmp)
            xml_path = os.path.join(tmp, 'Histogram-22.xml')
            self._make_hist().Save(xml_path)
            os.utime(mrc, (os.path.getmtime(xml_path) - 100,) * 2)
            calls = {'n': 0}

            def calc() -> Histogram:
                calls['n'] += 1
                return self._make_hist()

            shared.ensure_histogram_cache(xml_path, calc, [mrc])
            self.assertEqual(1, calls['n'], 'IDoc imports keep the stricter rule')

    def test_plotting_a_missing_cache_does_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shared.PlotHistogram(os.path.join(tmp, 'Histogram-22.xml'), 22, 0, 255, force_recreate=True)
            self.assertEqual([], os.listdir(tmp))


class TestGetSectionContrastSettings(_PoolCleanupTestCase):

    def test_full_overrides_skip_autolevel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xml_path = os.path.join(tmp, 'Histogram.xml')
            tile = os.path.join(tmp, 't.png')
            import nornir_imageregistration
            nornir_imageregistration.SaveImage(tile, np.full((32, 32), 128, dtype=np.uint8), bpp=8)

            hist = HistogramOfArray(
                np.arange(256, dtype=np.uint8).reshape(16, 16), bpp=8, num_bins=256)

            with mock.patch.object(Histogram, 'AutoLevel', wraps=hist.AutoLevel) as al:
                settings = shared.GetSectionContrastSettings(
                    section_number=5,
                    contrast_map={5: ContrastValue(5, 40, 200, 1.2)},
                    contrast_cutoffs=(0.0001, 0.9999),
                    calculate_histogram=lambda: hist,
                    histogram_cache_path=xml_path,
                    cache_inputs=[tile],
                    histogram_stride=4)
                self.assertEqual(settings.min, 40)
                self.assertEqual(settings.max, 200)
                self.assertEqual(settings.gamma, 1.2)
                al.assert_not_called()
            self.assertTrue(os.path.exists(xml_path))

    def test_no_overrides_uses_autolevel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xml_path = os.path.join(tmp, 'Histogram.xml')
            tile = os.path.join(tmp, 't.png')
            import nornir_imageregistration
            nornir_imageregistration.SaveImage(tile, np.full((32, 32), 128, dtype=np.uint8), bpp=8)

            hist = HistogramOfArray(
                np.linspace(0, 255, 4096, dtype=np.uint8).reshape(64, 64),
                bpp=8, num_bins=256)

            settings = shared.GetSectionContrastSettings(
                section_number=1,
                contrast_map={},
                contrast_cutoffs=(0.01, 0.99),
                calculate_histogram=lambda: hist,
                histogram_cache_path=xml_path,
                cache_inputs=[tile],
                histogram_stride=1)
            al_min, al_max = hist.AutoLevel(0.01, 0.01)
            self.assertAlmostEqual(settings.min, al_min, places=3)
            self.assertAlmostEqual(settings.max, al_max, places=3)


class TestHistogramStride(_PoolCleanupTestCase):

    def test_stride_autolevel_near_full(self) -> None:
        rng = np.random.default_rng(0)
        img = rng.integers(0, 256, size=(512, 512), dtype=np.uint8)
        full = HistogramOfArray(img, bpp=8, num_bins=256, min_val=0, max_val=255)
        strided = HistogramOfArray(img, bpp=8, num_bins=256, min_val=0, max_val=255, stride=4)
        fmin, fmax = full.AutoLevel(0.0001, 0.0001)
        smin, smax = strided.AutoLevel(0.0001, 0.0001)
        # Bin width is 1 for 256 bins over 0..255; allow a few bins of slack.
        self.assertLess(abs(fmin - smin), 8.0)
        self.assertLess(abs(fmax - smax), 8.0)


class TestHistogramCorruptSkip(_PoolCleanupTestCase):

    def test_all_unreadable_raises(self) -> None:
        from nornir_imageregistration import image_stats
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'bad.png')
            with open(bad, 'wb') as f:
                f.write(b'not a png')
            with self.assertRaises(ValueError):
                image_stats.Histogram([bad, bad], Bpp=8, numBins=256)


if __name__ == '__main__':
    unittest.main()
