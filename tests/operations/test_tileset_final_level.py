"""The tileset pyramid ends in one coarsest level that fits the section in one tile, or in one row of tiles.

James's rule (2026-11-19): the lowest resolution level fits the width or the height in one tile, and both when
shrinking for the larger dimension leaves the smaller one more than half a tile (no tall, skinny column tiles).
Its downsample need not be a power of 2, so it is built by resampling the finer level as a whole.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np
from PIL import Image

from nornir_buildmanager.operations import tile as tile_ops

_PREFIX = 'S'
_POSTFIX = '.png'
_TILE = 16


def _tile_name(x: int, y: int) -> str:
    return f'{_PREFIX}X{x:03d}_Y{y:03d}{_POSTFIX}'


class _Level:
    """A LevelNode stand-in: a directory of tiles at one downsample."""

    def __init__(self, root: str, downsample: int, grid: tuple[int, int]) -> None:
        self.Downsample = downsample
        self.attrib: dict[str, str] = {}
        self.FullPath = os.path.join(root, f'{downsample:03d}')
        self.GridDimX, self.GridDimY = grid
        os.makedirs(self.FullPath, exist_ok=True)

    def IsValid(self) -> tuple[bool, str]:
        return bool(os.listdir(self.FullPath)), ''


class _TileSet:
    TileXDim = TileYDim = _TILE
    FilePrefix = _PREFIX
    FilePostfix = _POSTFIX

    def __init__(self, root: str, finest: _Level) -> None:
        self.root = root
        self.levels = {finest.Downsample: finest}
        self.MaxResLevel = finest
        self.FullPath = root

    def UpdateOrAddChildByAttrib(self, new_level, _attrib):
        existing = self.levels.get(new_level.Downsample)
        if existing is not None:
            return False, existing
        level = _Level(self.root, new_level.Downsample, (0, 0))
        self.levels[level.Downsample] = level
        return True, level

    def GetTileAtLevel(self, level):
        names = os.listdir(level.FullPath)
        return os.path.join(level.FullPath, names[0]) if names else None


def _section(width: int, height: int) -> np.ndarray:
    """A smooth, non-zero test image, so resampling differences stay small."""
    y, x = np.mgrid[0:height, 0:width]
    return (40 + 150 * (x / width) * 0.6 + 150 * (y / height) * 0.4).astype(np.uint8)


def _write_finest(level: _Level, image: np.ndarray, grid: tuple[int, int]) -> None:
    padded = np.zeros((grid[1] * _TILE, grid[0] * _TILE), dtype=np.uint8)
    padded[:image.shape[0], :image.shape[1]] = image
    for y in range(grid[1]):
        for x in range(grid[0]):
            Image.fromarray(padded[y * _TILE:(y + 1) * _TILE, x * _TILE:(x + 1) * _TILE]).save(
                os.path.join(level.FullPath, _tile_name(x, y)))


def _build(tile_set: _TileSet, root: str) -> None:
    with mock.patch.object(tile_ops, 'get_temp_dir_for_tileset_level',
                           side_effect=lambda level: os.path.join(root, 'tmp', os.path.basename(level.FullPath))), \
            mock.patch.object(tile_ops.nornir_buildmanager.volumemanager.LevelNode, 'Create',
                              side_effect=lambda downsample: SimpleNamespace(Downsample=downsample)):
        list(tile_ops._build_tileset_pyramid_levels(tile_set))


class TestTilesetFinalDownsample(unittest.TestCase):

    def test_a_section_under_twice_as_long_as_wide_ends_in_one_tile(self) -> None:
        # James: "A 1024 x 945 image would stop with one tile at the highest downsample"
        self.assertEqual(tile_ops.tileset_final_downsample(1024, 945, 1024, 1024), 1)
        self.assertEqual(tile_ops.tileset_final_downsample(3000, 2600, 1024, 1024), 3)

    def test_a_section_twice_as_long_as_wide_ends_in_a_row_of_tiles(self) -> None:
        # James: "a 1024x2048 image would stop when there were 1x2 tiles for 1024x1024 tiles"; exactly half a
        # tile is a skinny column, so the short side fills the tile.
        self.assertEqual(tile_ops.tileset_final_downsample(1024, 2048, 1024, 1024), 1)
        self.assertEqual(tile_ops.tileset_final_downsample(2048, 1024, 1024, 1024), 1)

    def test_a_long_section_fits_its_short_side(self) -> None:
        self.assertEqual(tile_ops.tileset_final_downsample(8192, 2048, 512, 512), 4)  # 4x1 tiles
        self.assertEqual(tile_ops.tileset_final_downsample(18 * 512, 3 * 512, 512, 512), 3)  # 6x1 tiles

    def test_the_downsample_is_an_integer_that_still_fits(self) -> None:
        downsample = tile_ops.tileset_final_downsample(2500, 2000, 512, 512)
        self.assertIsInstance(downsample, int)
        self.assertEqual(downsample, 5)
        self.assertLessEqual(2500 / downsample, 512)


class TestTilesetPyramidFinalLevel(unittest.TestCase):

    def test_a_squarish_section_gets_a_single_tile_level_at_a_non_power_of_2_downsample(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            image = _section(5 * _TILE, 4 * _TILE)
            finest = _Level(root, 1, (5, 4))
            _write_finest(finest, image, (5, 4))
            tile_set = _TileSet(root, finest)

            _build(tile_set, root)

            self.assertEqual(sorted(tile_set.levels), [1, 2, 4, 5])
            final = tile_set.levels[5]
            self.assertEqual((final.GridDimX, final.GridDimY), (1, 1))
            self.assertEqual(os.listdir(final.FullPath), [_tile_name(0, 0)])
            with Image.open(os.path.join(final.FullPath, _tile_name(0, 0))) as built:
                built_tile = np.asarray(built)
            self.assertEqual(built_tile.shape, (_TILE, _TILE))
            # The section is 5 tiles wide at downsample 1, so it fills the tile's width at downsample 5.
            expected = np.asarray(Image.fromarray(image).resize((_TILE, image.shape[0] // 5),
                                                                resample=Image.Resampling.BOX))
            content = built_tile[1:expected.shape[0] - 1, 1:_TILE - 1].astype(float)
            self.assertLess(np.abs(content - expected[1:-1, 1:-1]).mean(), 3.0)
            self.assertEqual(int(built_tile[expected.shape[0] + 1:].max(initial=0)), 0)

    def test_a_long_section_stops_at_a_row_of_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            finest = _Level(root, 1, (18, 3))
            _write_finest(finest, _section(18 * _TILE, 3 * _TILE), (18, 3))
            tile_set = _TileSet(root, finest)

            _build(tile_set, root)

            # Before: power-of-2 levels down to 2x1 at downsample 16, a column a fifth of a tile tall.
            self.assertEqual(sorted(tile_set.levels), [1, 2, 3])
            final = tile_set.levels[3]
            self.assertEqual((final.GridDimX, final.GridDimY), (6, 1))
            self.assertEqual(len(os.listdir(final.FullPath)), 6)

    def test_a_power_of_2_final_level_is_built_by_the_pyramid(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            finest = _Level(root, 1, (4, 3))
            _write_finest(finest, _section(4 * _TILE, 3 * _TILE), (4, 3))
            tile_set = _TileSet(root, finest)

            _build(tile_set, root)

            self.assertEqual(sorted(tile_set.levels), [1, 2, 4])
            self.assertEqual((tile_set.levels[4].GridDimX, tile_set.levels[4].GridDimY), (1, 1))

    def test_a_current_final_level_is_not_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            finest = _Level(root, 1, (5, 4))
            _write_finest(finest, _section(5 * _TILE, 4 * _TILE), (5, 4))
            tile_set = _TileSet(root, finest)
            _build(tile_set, root)

            with mock.patch.object(tile_ops, '_build_rescaled_tileset_level') as rebuild:
                _build(tile_set, root)

            rebuild.assert_not_called()


if __name__ == '__main__':
    unittest.main()
