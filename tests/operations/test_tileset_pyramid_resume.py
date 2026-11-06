"""An interrupted tileset pyramid is finished on rerun, not accepted because one tile of the last column exists."""
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


def _tile_name(x: int, y: int) -> str:
    return f'{_PREFIX}X{x:03d}_Y{y:03d}{_POSTFIX}'


def _write_tile(path: str) -> None:
    Image.fromarray(np.zeros((4, 4), dtype=np.uint8)).save(path)


class _Level:
    """A LevelNode stand-in: a directory of tiles whose IsValid only spot-checks, as the real one does."""

    def __init__(self, root: str, downsample: int, grid: tuple[int, int]) -> None:
        self.Downsample = downsample
        self.FullPath = os.path.join(root, f'{downsample:03d}')
        self.GridDimX, self.GridDimY = grid
        os.makedirs(self.FullPath, exist_ok=True)

    def IsValid(self) -> tuple[bool, str]:
        return bool(os.listdir(self.FullPath)), ''

    def write(self, coords) -> None:
        for x, y in coords:
            _write_tile(os.path.join(self.FullPath, _tile_name(x, y)))


class _TileSet:
    TileXDim = TileYDim = 4
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


class TestTilesetPyramidResume(unittest.TestCase):

    def test_a_level_missing_tiles_is_rebuilt_even_though_one_last_column_tile_exists(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            finest = _Level(root, 1, (4, 4))
            finest.write((x, y) for x in range(4) for y in range(4))
            tile_set = _TileSet(root, finest)
            level2 = _Level(root, 2, (2, 2))
            level2.write([(1, 0)])  # a killed run: a last-column tile exists, the other three do not
            tile_set.levels[2] = level2
            built: list[str] = []

            def fake_build(source, dest, **kwargs) -> list:
                built.append(os.path.basename(dest))
                grid = kwargs['DestGridDimensions']
                for y in range(grid[0]):
                    for x in range(grid[1]):
                        _write_tile(os.path.join(dest, _tile_name(x, y)))
                return []

            with mock.patch.object(tile_ops, 'BuildTilesetLevelWithPillow', side_effect=fake_build), \
                    mock.patch.object(tile_ops, 'get_temp_dir_for_tileset_level', return_value=os.path.join(root, 'tmp')), \
                    mock.patch.object(tile_ops.tileset_functions, 'ClearTempDirectories'), \
                    mock.patch.object(tile_ops.nornir_buildmanager.volumemanager.LevelNode, 'Create',
                                      side_effect=lambda downsample: SimpleNamespace(Downsample=downsample)):
                list(tile_ops._build_tileset_pyramid_levels(tile_set))

            self.assertIn('002', built)
            self.assertEqual(len(os.listdir(level2.FullPath)), 4)

    def test_a_complete_level_is_not_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            finest = _Level(root, 1, (4, 4))
            finest.write((x, y) for x in range(4) for y in range(4))
            tile_set = _TileSet(root, finest)
            level2 = _Level(root, 2, (2, 2))
            level2.write((x, y) for x in range(2) for y in range(2))
            tile_set.levels[2] = level2

            with mock.patch.object(tile_ops, 'BuildTilesetLevelWithPillow') as build, \
                    mock.patch.object(tile_ops, 'get_temp_dir_for_tileset_level', return_value=os.path.join(root, 'tmp')), \
                    mock.patch.object(tile_ops.tileset_functions, 'ClearTempDirectories'), \
                    mock.patch.object(tile_ops.nornir_buildmanager.volumemanager.LevelNode, 'Create',
                                      side_effect=lambda downsample: SimpleNamespace(Downsample=downsample)):
                list(tile_ops._build_tileset_pyramid_levels(tile_set))

            build.assert_not_called()


if __name__ == '__main__':
    unittest.main()
