"""A pyramid level whose tiles fit in RAM is built without the local temp directory and matches the disk build."""
from __future__ import annotations

import logging
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

import nornir_buildmanager.templates
from nornir_buildmanager.operations import tile as tile_ops
from nornir_imageregistration import tileset_functions

_LOG = logging.getLogger(__name__)
_TILE = 16


def _name(x: int, y: int) -> str:
    """Tile file name as the pyramid builder spells it."""
    return nornir_buildmanager.templates.Current.GridTileName('X', x, y, '.png')


def _write_level(directory: str, size_xy: tuple[int, int]) -> None:
    """Write a grid of distinct 16x16 tiles named like a tileset level."""
    os.makedirs(directory, exist_ok=True)
    rng = np.random.default_rng(7)
    for y in range(size_xy[1]):
        for x in range(size_xy[0]):
            data = rng.integers(0, 40, (_TILE, _TILE), dtype=np.uint8)
            Image.fromarray(data).save(os.path.join(directory, _name(x, y)))


def _read_level(directory: str) -> dict[str, bytes]:
    """Pixel bytes of every tile in a level directory."""
    return {name: np.asarray(Image.open(os.path.join(directory, name))).tobytes()
            for name in sorted(os.listdir(directory))}


def _build(root: str, label: str, in_memory: bool) -> str:
    """Build the 4x4 -> 2x2 level, returning the destination directory."""
    source = os.path.join(root, 'source')
    dest = os.path.join(root, f'dest_{label}')
    temp_out = os.path.join(root, f'temp_{label}')
    os.makedirs(dest)
    outputs = tileset_functions.TileMemoryCache() if in_memory else None
    tile_ops.BuildTilesetLevelWithPillow(
        source, dest, DestGridDimensions=(2, 2), TileDim=(_TILE, _TILE), FilePrefix='X', FilePostfix='.png',
        temp_input_dir=None, temp_output_dir=temp_out, memory_outputs=outputs)
    if in_memory:
        assert outputs is not None and len(outputs) == 4
        assert not os.path.exists(temp_out), 'an in-memory level must not create the temp directory'
    return dest


class TestPyramidInMemory(unittest.TestCase):

    def test_memory_level_matches_disk_level(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_level(os.path.join(root, 'source'), (4, 4))
            self.assertEqual(_read_level(_build(root, 'disk', False)), _read_level(_build(root, 'mem', True)))

    def test_next_level_reads_the_held_tiles_and_releases_them(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_level(os.path.join(root, 'source'), (4, 4))
            first = tileset_functions.TileMemoryCache()
            tile_ops.BuildTilesetLevelWithPillow(
                os.path.join(root, 'source'), os.path.join(root, 'l2'), DestGridDimensions=(2, 2),
                TileDim=(_TILE, _TILE), FilePrefix='X', FilePostfix='.png', temp_input_dir=None,
                temp_output_dir=os.path.join(root, 't2'), memory_outputs=first)
            # The finer level is not on the share any more: only the held bytes can supply the next level.
            second = tileset_functions.TileMemoryCache()
            tile_ops.BuildTilesetLevelWithPillow(
                os.path.join(root, 'missing_l2'), os.path.join(root, 'l3'), DestGridDimensions=(1, 1),
                TileDim=(_TILE, _TILE), FilePrefix='X', FilePostfix='.png', temp_input_dir=None,
                temp_output_dir=os.path.join(root, 't3'), memory_inputs=first, memory_outputs=second)
            self.assertEqual(len(first), 0)
            self.assertEqual(len(second), 1)
            self.assertTrue(os.path.isfile(os.path.join(root, 'l3', _name(0, 0))))

    def test_destination_write_failure_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_level(os.path.join(root, 'source'), (2, 2))
            with mock.patch('nornir_shared.files.write_file_bytes', side_effect=PermissionError('denied')):
                with self.assertRaises(PermissionError):
                    tile_ops.BuildTilesetLevelWithPillow(
                        os.path.join(root, 'source'), os.path.join(root, 'l2'), DestGridDimensions=(1, 1),
                        TileDim=(_TILE, _TILE), FilePrefix='X', FilePostfix='.png', temp_input_dir=None,
                        temp_output_dir=os.path.join(root, 't2'),
                        memory_outputs=tileset_functions.TileMemoryCache())


class TestLevelFit(unittest.TestCase):

    def test_estimate_extrapolates_the_mean_tile_size_with_a_margin(self) -> None:
        # 15,500 tiles of about 73 KB, the RC1_Crush section 203 level 2 shape.
        estimate = tile_ops.pyramid_level_memory_estimate([70_000, 76_000], 15_500)
        self.assertAlmostEqual(estimate / 1e9, 73_000 * 15_500 * 1.25 / 1e9, places=3)

    def test_fits_only_when_the_estimate_is_within_the_budget(self) -> None:
        held = tileset_functions.TileMemoryCache()
        for i in range(4):
            held.put(f't{i}', b'x' * 1000)  # 4,000 bytes held: 1,000 per tile
        with mock.patch.object(tile_ops, 'pyramid_memory_budget_bytes', return_value=2_000_000):
            self.assertTrue(tile_ops.pyramid_level_fits_in_memory('', frozenset(), held, 1000, _LOG))  # ~1.25 MB
            self.assertFalse(tile_ops.pyramid_level_fits_in_memory('', frozenset(), held, 2000, _LOG))  # ~2.5 MB

    def test_a_disabled_or_unavailable_budget_never_fits(self) -> None:
        held = tileset_functions.TileMemoryCache()
        held.put('t', b'x')
        with mock.patch.dict(os.environ, {tile_ops.PYRAMID_IN_MEMORY_ENV: '0'}):
            self.assertEqual(tile_ops.pyramid_memory_budget_bytes(), 0)
            self.assertFalse(tile_ops.pyramid_level_fits_in_memory('', frozenset(), held, 1, _LOG))

    def test_source_tile_sizes_are_sampled_from_the_share_for_the_first_level(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_level(root, (2, 2))
            names = frozenset(os.listdir(root))
            with mock.patch.object(tile_ops, 'pyramid_memory_budget_bytes', return_value=10**9):
                self.assertTrue(tile_ops.pyramid_level_fits_in_memory(root, names, None, 1, _LOG))
            with mock.patch.object(tile_ops, 'pyramid_memory_budget_bytes', return_value=10):
                self.assertFalse(tile_ops.pyramid_level_fits_in_memory(root, names, None, 1, _LOG))


if __name__ == '__main__':
    unittest.main()
