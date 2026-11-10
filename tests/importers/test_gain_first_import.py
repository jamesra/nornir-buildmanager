"""Gain-first imports: tile gains are solved from the Stage overlaps before the crush and are diagnostic afterwards."""
from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from typing import Any, cast
from unittest import mock

import numpy as np
from PIL import Image

from nornir_buildmanager.importers import shared
from nornir_buildmanager.importers.mrc import MRCImport
from nornir_buildmanager.operations import tilegains
from nornir_imageregistration import noise_crush, tile_gains
from nornir_imageregistration.noise_crush import CrushParams

PARAMS = CrushParams(k=2.5, step=40, sigma=16.0, offset=0, bits=8, source_dtype='uint16')
TILE = 128
PITCH = 96  # 32 pixels of overlap between neighbours
GRID = 9
CENTRE = 40  # tile number of the middle of the 9x9 grid


def _scene() -> np.ndarray:
    size = TILE + (GRID - 1) * PITCH
    rng = np.random.default_rng(11)
    return 4000 + 400 * np.kron(rng.normal(size=(size // 8, size // 8)), np.ones((8, 8)))


def _tile_origin(number: int) -> tuple[int, int]:
    return (number // GRID) * PITCH, (number % GRID) * PITCH


def _tiles(gains: dict[int, float]) -> dict[int, np.ndarray]:
    """The scene cut into a 9x9 grid of overlapping tiles, each dimmed by its gain (shot noise by about its root)."""
    scene = _scene()
    out = {}
    for number in range(GRID * GRID):
        y, x = _tile_origin(number)
        gain = gains.get(number, 1.0)
        rng = np.random.default_rng(100 + number)
        out[number] = np.clip(scene[y:y + TILE, x:x + TILE] * gain + rng.normal(0, 16 * np.sqrt(gain), (TILE, TILE)),
                              0, 65535).astype(np.uint16)
    return out


def _placements() -> dict[int, tile_gains.TilePlacement]:
    return {n: tile_gains.TilePlacement(float(_tile_origin(n)[0]), float(_tile_origin(n)[1]), TILE, TILE)
            for n in range(GRID * GRID)}


GAINS = {CENTRE: 0.3, 3: 1.8}


class TestSolveImportTileGains(unittest.TestCase):

    def _solve(self, tmp: str, tiles: dict[int, np.ndarray], reads: list[int] | None = None, variant: str = 'v'):
        def read(number: int) -> np.ndarray:
            if reads is not None:
                reads.append(number)
            return tiles[number]

        source = os.path.join(tmp, 'section.mrc')
        open(source, 'wb').close()
        return shared.SolveImportTileGains(os.path.join(tmp, 'cache', 'Histogram.xml'), [source], _placements(), read,
                                           variant, downsample=1)

    def test_recovers_the_gain_of_a_dim_and_a_bright_tile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gains = self._solve(tmp, _tiles(GAINS))
        self.assertAlmostEqual(gains.solved_gain(CENTRE), GAINS[CENTRE], delta=0.05)
        self.assertAlmostEqual(gains.solved_gain(3), GAINS[3], delta=0.15)
        others = [gains.solved_gain(n) for n in range(GRID * GRID) if n not in GAINS]
        self.assertAlmostEqual(float(np.median(others)), 1.0, delta=0.03)
        self.assertEqual(gains.flagged, [])

    def test_gain_first_restores_the_codes_of_a_dim_tile(self) -> None:
        """The dim tile quantizes to the same codes as the bright capture of the same tissue, to within noise."""
        with tempfile.TemporaryDirectory() as tmp:
            tiles = _tiles(GAINS)
            gains = self._solve(tmp, tiles)
        bright = _tiles({})[CENTRE]
        dim_codes = noise_crush.crush(tiles[CENTRE], PARAMS, gains.applied(CENTRE)).astype(int)
        as_captured = noise_crush.crush(tiles[CENTRE], PARAMS).astype(int)
        reference = noise_crush.crush(bright, PARAMS).astype(int)
        self.assertLess(np.abs(dim_codes - reference).mean(), 0.35 * np.abs(as_captured - reference).mean())

    def test_a_tile_with_essentially_no_beam_is_flagged_not_amplified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gains = self._solve(tmp, _tiles({CENTRE: 0.02}))
        self.assertEqual(gains.flagged, [CENTRE])
        self.assertEqual(gains.applied(CENTRE), 1.0)
        self.assertLess(gains.solved_gain(CENTRE), noise_crush.MIN_CORRECTABLE_GAIN)
        self.assertAlmostEqual(gains.applied(0), gains.solved_gain(0))

    def test_gains_are_cached_and_a_changed_variant_re_solves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tiles = _tiles(GAINS)
            reads: list[int] = []
            first = self._solve(tmp, tiles, reads)
            solved_reads = len(reads)
            self.assertGreater(solved_reads, 0)
            self.assertEqual(self._solve(tmp, tiles, reads), first)
            self.assertEqual(len(reads), solved_reads)
            self._solve(tmp, tiles, reads, variant='other')
            self.assertGreater(len(reads), solved_reads)


class TestNoiseInGainCorrectedCounts(unittest.TestCase):

    def _tile(self, tmp: str, name: str, gain: float) -> str:
        rng = np.random.default_rng(5)
        path = os.path.join(tmp, name)
        Image.fromarray((rng.normal(4000, 40, (128, 128)) * gain).astype(np.uint16)).save(path)
        return path

    def test_sigma_and_range_are_those_of_the_corrected_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tiles = [self._tile(tmp, 'a.tif', 0.5)]
            hist = os.path.join(tmp, 'Histogram.xml')

            def load(i: int) -> np.ndarray:
                return noise_crush.load_raw_tile(tiles[i])

            plain = shared.GetSectionNoise(hist, tiles, 1, load)
            corrected = shared.GetSectionNoise(hist, tiles, 1, load, tile_gains_applied={0: 0.5},
                                               gains_checksum='abc')
        self.assertAlmostEqual(corrected.sigma, plain.sigma / 0.5, places=6)
        self.assertAlmostEqual(corrected.sample_max, plain.sample_max / 0.5, places=6)

    def test_cache_is_keyed_to_the_gains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tiles = [self._tile(tmp, 'a.tif', 0.5)]
            hist = os.path.join(tmp, 'Histogram.xml')
            loads: list[int] = []

            def load(i: int) -> np.ndarray:
                loads.append(i)
                return noise_crush.load_raw_tile(tiles[i])

            shared.GetSectionNoise(hist, tiles, 1, load, tile_gains_applied={0: 0.5}, gains_checksum='abc')
            shared.GetSectionNoise(hist, tiles, 1, load, tile_gains_applied={0: 0.5}, gains_checksum='abc')
            self.assertEqual(len(loads), 1)
            shared.GetSectionNoise(hist, tiles, 1, load, tile_gains_applied={0: 0.25}, gains_checksum='def')
            shared.GetSectionNoise(hist, tiles, 1, load)
            self.assertEqual(len(loads), 3)


class _FakeFilter:
    def __init__(self, attrib: dict[str, str]):
        self.attrib = attrib
        self.FullPath = '/vol/TEM/0001/TEM/Raw8'


class _FakeChannel:
    def __init__(self, **filters: _FakeFilter):
        self._filters = filters

    def GetFilter(self, name: str) -> _FakeFilter | None:
        return self._filters.get(name)


def _channel(**filters: _FakeFilter) -> Any:
    return cast(Any, _FakeChannel(**filters))


class TestGainFirstChoice(unittest.TestCase):

    def test_new_section_is_gain_first(self) -> None:
        self.assertTrue(shared.GainFirstAllowed(_channel()))

    def test_section_with_a_crushed_filter_made_without_it_keeps_its_kind(self) -> None:
        legacy = _FakeFilter(PARAMS.to_attributes())
        self.assertFalse(shared.GainFirstAllowed(_channel(Raw8=legacy)))
        self.assertIsNone(shared.ExistingGainFirstParams(_channel(Raw8=legacy), None))

    def test_section_with_an_uncrushed_filter_is_not_blocked_here(self) -> None:
        """ResolveCrushMode already keeps an uncrushed filter; the gain-first choice has no say in it."""
        self.assertTrue(shared.GainFirstAllowed(_channel(Raw8=_FakeFilter({'MinIntensityCutoff': '1'}))))

    def test_existing_gain_first_filter_gives_its_params_when_k_matches(self) -> None:
        attrib = dict(PARAMS.to_attributes())
        attrib[noise_crush.GAIN_FIRST_ATTRIB] = '1'
        channel = _channel(Raw8=_FakeFilter(attrib))
        self.assertTrue(shared.GainFirstAllowed(channel))
        self.assertEqual(shared.ExistingGainFirstParams(channel, 2.5), PARAMS)
        self.assertIsNone(shared.ExistingGainFirstParams(channel, 3.0))

    def test_set_filter_attributes_marks_gain_first(self) -> None:
        node = types.SimpleNamespace(attrib={})
        shared.SetFilterCrushAttributes(cast(Any, node), PARAMS, gain_first=True)
        self.assertEqual(node.__dict__[noise_crush.GAIN_FIRST_ATTRIB], '1')
        plain = types.SimpleNamespace(attrib={})
        shared.SetFilterCrushAttributes(cast(Any, plain), PARAMS)
        self.assertNotIn(noise_crush.GAIN_FIRST_ATTRIB, plain.__dict__)


class TestTileGainsLeavesGainFirstAlone(unittest.TestCase):

    def test_gain_first_filter_is_skipped_so_the_correction_is_not_applied_twice(self) -> None:
        filter_node = types.SimpleNamespace(attrib={noise_crush.GAIN_FIRST_ATTRIB: '1', **PARAMS.to_attributes()},
                                            FullPath='/vol/Raw8')
        transform = types.SimpleNamespace(Name='Stage', attrib={}, FullPath='/vol/Stage.mosaic')
        with mock.patch.object(tilegains.nornir_imageregistration.Mosaic, 'LoadFromMosaicFile') as load:
            self.assertIsNone(tilegains.TileGains(transform, filter_node, Downsample=1))
        load.assert_not_called()


class TestCrushTilesWithGains(unittest.TestCase):

    def test_each_tile_is_divided_by_its_applied_gain_and_records_the_solved_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sources = {}
            for i, gain in enumerate((0.5, 1.0)):
                source = os.path.join(tmp, f'{i}.tif')
                Image.fromarray(_tiles({})[0][:32, :32] if gain == 1.0 else (_tiles({})[0][:32, :32] * 0.5)
                                .astype(np.uint16)).save(source)
                sources[source] = os.path.join(tmp, 'out', f'{i}.png')
            first, second = list(sources)
            shared.CrushTiles(sources, PARAMS, flip=False,
                              source_gains={first: (0.5, 0.5), second: (1.0, 0.02)})
            for source, target in sources.items():
                with Image.open(target) as im:
                    chunk = json.loads(im.text[noise_crush.PNG_TEXT_KEY])
                if source == first:
                    self.assertEqual((chunk['import_gain'], chunk['import_gain_corrected']), (0.5, True))
                else:
                    self.assertEqual((chunk['import_gain'], chunk['import_gain_corrected']), (0.02, False))
            np.testing.assert_array_equal(
                noise_crush.load_raw_tile(sources[first]),
                noise_crush.crush(noise_crush.load_raw_tile(first), PARAMS, 0.5))


class _FakeMrc:
    """A 9x9 stage grid of 8-pixel-bordered frames cut from the scene, dimmed by GAINS."""
    BORDER = 8

    def __init__(self) -> None:
        full = TILE + 2 * self.BORDER
        self.img_shape = np.array([full, full], dtype=np.int64)
        self.num_tiles = GRID * GRID
        scene = _scene()
        self._padded = np.pad(scene, self.BORDER, mode='edge')
        self.tile_meta = [types.SimpleNamespace(pixel_coords=np.array([float(_tile_origin(n)[0]),
                                                                       float(_tile_origin(n)[1])]))
                          for n in range(self.num_tiles)]

    def get_tile_as_numpy_memmap(self, iTile: int) -> np.ndarray:
        y, x = _tile_origin(iTile)
        full = TILE + 2 * self.BORDER
        gain = GAINS.get(iTile, 1.0)
        rng = np.random.default_rng(200 + iTile)
        frame = self._padded[y:y + full, x:x + full] * gain + rng.normal(0, 16 * np.sqrt(gain), (full, full))
        frame = np.clip(frame, 0, 65535).astype(np.uint16)
        frame[:self.BORDER] = 9999  # an artefact ring that must not reach the statistics
        frame[-self.BORDER:] = 9999
        frame[:, :self.BORDER] = 9999
        frame[:, -self.BORDER:] = 9999
        return frame


class TestMrcSolveGains(unittest.TestCase):

    def test_gains_come_from_the_cropped_tiles_at_their_stage_placements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, '0001.mrc')
            open(source, 'wb').close()
            with mock.patch.object(shared, 'IMPORT_GAIN_DOWNSAMPLE', 2), \
                    mock.patch('nornir_buildmanager.importers.mrc.MRCFile.Load', return_value=_FakeMrc()):
                gains = MRCImport.SolveGains(source, source, os.path.join(tmp, 'Histogram-1.xml'), 1,
                                             _FakeMrc.BORDER, flip_ud=False)
        self.assertAlmostEqual(gains.solved_gain(CENTRE), GAINS[CENTRE], delta=0.05)
        others = [gains.solved_gain(n) for n in range(GRID * GRID) if n not in GAINS]
        self.assertAlmostEqual(float(np.median(others)), 1.0, delta=0.05)

    def test_export_writes_the_corrected_codes_and_the_diagnostic_gain(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            fake = _FakeMrc()
            MRCImport.ExportCrushedImage(fake, out, '.png', CENTRE, PARAMS, False, _FakeMrc.BORDER, 0.3, 0.3)
            tile = next(name for name in os.listdir(out) if name.endswith('.png'))
            with Image.open(os.path.join(out, tile)) as im:
                chunk = json.loads(im.text[noise_crush.PNG_TEXT_KEY])
                codes = np.asarray(im)
            self.assertEqual(chunk['import_gain'], 0.3)
            raw = fake.get_tile_as_numpy_memmap(CENTRE)[_FakeMrc.BORDER:-_FakeMrc.BORDER,
                                                        _FakeMrc.BORDER:-_FakeMrc.BORDER]
            np.testing.assert_array_equal(codes, noise_crush.crush(raw, PARAMS, 0.3))
            self.assertIsNone(noise_crush.unit_gain_correction(json.dumps(chunk)))


if __name__ == '__main__':
    unittest.main()
