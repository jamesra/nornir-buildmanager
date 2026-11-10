"""-CameraBpp and noise crushing.

1. ``CameraBppForImport`` rejects -CameraBpp unless -NoCrush is set (James, 2026-11-10; it was dropped with a warning
   from 2026-11-05, because it is unnecessary when crushing).
2. What -CameraBpp could have changed in a crushed import (Build2 asked whether RC1_Crush, imported with
   -CameraBpp 14, needs reimporting). The crush is ``floor((raw - offset) / step)`` clipped to ``2**bits - 1``.
   sigma comes from the sampled tiles, step from k and sigma, offset from the dtype and the sampled minimum:
   none of them sees CameraBpp. CameraBpp only reaches the section histogram (its value range), and the
   histogram's top occupied bin feeds ``section_max`` and so ``bits``. These tests show the parameters and the
   codes are identical for in-range data, and that ``bits`` differs only when raw values exceed the camera range.
"""
import logging

import numpy as np
import pytest
from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.importers import shared
from nornir_imageregistration import noise_crush
from nornir_imageregistration.image_stats import HistogramOfArray


def _tiles(rng: np.random.Generator, count: int = 6, mean: float = 3000, sigma: float = 40) -> list[np.ndarray]:
    return [np.clip(rng.normal(mean, sigma, (256, 256)), 0, 65535).astype(np.uint16) for _ in range(count)]


def _section_noise(tiles: list[np.ndarray], sampled: list[int]) -> shared.SectionNoise:
    sigmas = [noise_crush.estimate_noise_sigma(tiles[i]) for i in sampled]
    return shared.SectionNoise(noise_crush.section_sigma(sigmas), float(min(tiles[i].min() for i in sampled)),
                               float(max(tiles[i].max() for i in sampled)), 'uint16')


def _histogram(tiles: list[np.ndarray], max_val: int):
    """Composite histogram the way the IDoc importer builds it: 2048 bins over [0, max_val]."""
    return HistogramOfArray(np.concatenate([t.ravel() for t in tiles])[None, :], num_bins=2048, max_val=max_val)


class TestCameraBppForImport:
    def test_an_error_when_crushing(self):
        with pytest.raises(NornirUserException, match='-CameraBpp 14 is not allowed with crush imports'):
            shared.CameraBppForImport(14, no_crush=False)

    def test_kept_for_legacy_imports(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert shared.CameraBppForImport(14, no_crush=True) == 14
        assert not caplog.records

    def test_absent_stays_absent(self):
        assert shared.CameraBppForImport(None, no_crush=False) is None


class TestCameraBppEffectOnCrush:
    def test_in_range_data_gives_identical_params_and_codes(self):
        tiles = _tiles(np.random.default_rng(1))
        noise = _section_noise(tiles, sampled=[0, 2, 4])
        with_camera = shared.SectionCrushParams(noise, _histogram(tiles, (1 << 14) - 1), k=2.5)
        without_camera = shared.SectionCrushParams(noise, _histogram(tiles, 65535), k=2.5)
        assert with_camera == without_camera
        for t in tiles:
            np.testing.assert_array_equal(noise_crush.crush(t, with_camera), noise_crush.crush(t, without_camera))

    def test_only_bits_can_change_and_only_above_the_camera_range(self):
        tiles = _tiles(np.random.default_rng(2))
        tiles[5][10, 10] = 40000  # a hot value above 14 bits, in a tile the noise sample did not read
        noise = _section_noise(tiles, sampled=[0, 2, 4])
        with_camera = shared.SectionCrushParams(noise, _histogram(tiles, (1 << 14) - 1), k=2.5)
        without_camera = shared.SectionCrushParams(noise, _histogram(tiles, 65535), k=2.5)
        assert (with_camera.sigma, with_camera.step, with_camera.offset) == \
               (without_camera.sigma, without_camera.step, without_camera.offset)
        assert with_camera.bits == 8 and without_camera.bits > 8
        # Every in-range pixel gets the same code either way; only the out-of-range value is clipped differently.
        a, b = noise_crush.crush(tiles[5], with_camera), noise_crush.crush(tiles[5], without_camera)
        differs = a.astype(np.int64) != b.astype(np.int64)
        assert differs.sum() == 1 and differs[10, 10]
