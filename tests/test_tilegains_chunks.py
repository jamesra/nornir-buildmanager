"""TileGains keeps each tile's gain in the tile's own ``nornir_crush`` chunk; nothing is written beside the tiles.

James, 2026-11-06: the gain belongs in the tile image metadata next to the crush step, not in a side file.
"""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import nornir_imageregistration
import numpy as np
import pytest
from nornir_buildmanager.operations import tilegains
from nornir_imageregistration import noise_crush, tile_gains
from nornir_imageregistration.transforms.rigid import RigidNoRotation

PARAMS = noise_crush.CrushParams(k=2.5, step=100, sigma=40.0, offset=0, bits=8, source_dtype='uint16')
TILE = 128
PITCH = 100  # 28 px overlap
BRIGHTNESS = {0: 1.0, 1: 1.1, 2: 1.0, 3: 1.0}
LEVELS = (1, 2)


class _Level(SimpleNamespace):
    pass


class _Pyramid:
    def __init__(self, root: str):
        self.Levels = [_Level(Downsample=d, FullPath=os.path.join(root, f'{d:03d}')) for d in LEVELS]

    def GetLevel(self, downsample: int) -> _Level | None:
        return next((level for level in self.Levels if level.Downsample == downsample), None)


def _volume(root: str) -> tuple[SimpleNamespace, SimpleNamespace]:
    """A 2x2 crushed mosaic at levels 1 and 2 cut from one texture, tile 1 brighter; (transform, filter) nodes."""
    rng = np.random.default_rng(11)
    tissue = 100.0 + 20.0 * rng.standard_normal((PITCH + TILE, PITCH + TILE))
    offsets = {0: (0, 0), 1: (0, PITCH), 2: (PITCH, 0), 3: (PITCH, PITCH)}
    pyramid = _Pyramid(root)
    for level in pyramid.Levels:
        os.makedirs(level.FullPath)
    for tile, (y, x) in offsets.items():
        codes = np.clip(tissue[y:y + TILE, x:x + TILE] * BRIGHTNESS[tile], 1, 250).astype(np.uint8)
        for level in pyramid.Levels:
            d = level.Downsample
            noise_crush.save_crushed_png(os.path.join(level.FullPath, f'{tile:03d}.png'), codes[::d, ::d], PARAMS)
    mosaic = nornir_imageregistration.Mosaic({f'{tile:03d}.png': RigidNoRotation(np.array(offset, dtype=float))
                                              for tile, offset in offsets.items()})
    mosaic_path = os.path.join(root, 'Stage.mosaic')
    mosaic.SaveToMosaicFile(mosaic_path)
    transform = SimpleNamespace(Name='Stage', attrib={'Checksum': 'abc'}, FullPath=mosaic_path)
    filter_node = SimpleNamespace(Name='Raw8', attrib=PARAMS.to_attributes(), FullPath=root, HasTilePyramid=True,
                                  TilePyramid=pyramid)
    return transform, filter_node


def _record(filter_node: SimpleNamespace) -> None:
    """What the pipeline's save does with the attributes TileGains set on the node."""
    for key in ('TileGainsSource', 'TileGainsChecksum'):
        if key in vars(filter_node):
            filter_node.attrib[key] = vars(filter_node).pop(key)


def _chunk_gains(filter_node: SimpleNamespace, level: int) -> dict[int, float | None]:
    folder = filter_node.TilePyramid.GetLevel(level).FullPath
    return {tile: tile_gains.read_gain_png(os.path.join(folder, f'{tile:03d}.png')) for tile in BRIGHTNESS}


@pytest.fixture
def volume():
    with tempfile.TemporaryDirectory() as root:
        yield _volume(root)


def test_gains_go_into_every_tile_chunk_and_nowhere_else(volume):
    transform, filter_node = volume
    before = set(os.listdir(filter_node.FullPath))
    assert tilegains.TileGains(transform, filter_node, Downsample=1) is filter_node
    _record(filter_node)
    assert set(os.listdir(filter_node.FullPath)) == before  # no TileGains_*.json beside the tiles
    # The solve itself is tested in nornir-imageregistration (a 2x2 toy is too small for its trend removal).
    gains = _chunk_gains(filter_node, 1)
    assert all(gain is not None for gain in gains.values())
    assert _chunk_gains(filter_node, 2) == gains
    assert filter_node.attrib['TileGainsSource'] == 'Stage:abc'
    assert filter_node.attrib['TileGainsChecksum']


def test_rerun_keeps_the_stored_gains_and_checksum(volume):
    transform, filter_node = volume
    tilegains.TileGains(transform, filter_node, Downsample=1)
    _record(filter_node)
    stamped = {name: os.path.getmtime(os.path.join(filter_node.TilePyramid.Levels[0].FullPath, name))
               for name in os.listdir(filter_node.TilePyramid.Levels[0].FullPath)}
    assert tilegains.TileGains(transform, filter_node, Downsample=1) is None  # nothing changed, nothing to save
    for name, mtime in stamped.items():
        assert os.path.getmtime(os.path.join(filter_node.TilePyramid.Levels[0].FullPath, name)) == mtime


def test_a_level_left_unstamped_by_an_interrupted_run_is_completed(volume):
    transform, filter_node = volume
    tilegains.TileGains(transform, filter_node, Downsample=1)
    _record(filter_node)
    level2 = filter_node.TilePyramid.GetLevel(2).FullPath
    codes = np.zeros((TILE // 2, TILE // 2), np.uint8) + 100
    noise_crush.save_crushed_png(os.path.join(level2, '001.png'), codes, PARAMS)  # chunk without a gain
    assert tilegains.TileGains(transform, filter_node, Downsample=1) is None  # same gains and checksum
    assert _chunk_gains(filter_node, 2)[1] == _chunk_gains(filter_node, 1)[1]


def test_new_transform_with_the_same_gains_only_updates_the_source(volume):
    transform, filter_node = volume
    tilegains.TileGains(transform, filter_node, Downsample=1)
    _record(filter_node)
    checksum = filter_node.attrib['TileGainsChecksum']
    transform.attrib['Checksum'] = 'def'
    assert tilegains.TileGains(transform, filter_node, Downsample=1) is filter_node
    _record(filter_node)
    assert filter_node.attrib['TileGainsSource'] == 'Stage:def'
    assert filter_node.attrib['TileGainsChecksum'] == checksum  # derived tilesets stay current


def test_legacy_filter_records_nothing(volume):
    transform, filter_node = volume
    filter_node.attrib = {'MinIntensityCutoff': '0', 'MaxIntensityCutoff': '255'}
    assert tilegains.TileGains(transform, filter_node, Downsample=1) is None
    assert 'TileGainsChecksum' not in vars(filter_node)
