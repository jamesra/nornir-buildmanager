"""ComputeCodeHistograms backfills a crushed filter imported before importers recorded its code histogram."""
from __future__ import annotations

import os
import shutil
import tempfile
import types
from unittest import mock

import numpy as np
import pytest
from nornir_imageregistration import noise_crush
from nornir_imageregistration.noise_crush import CrushParams

from nornir_buildmanager.importers import shared
from nornir_buildmanager.operations import codehistogram
from nornir_buildmanager.volumemanager import BlockNode, ChannelNode, FilterNode, SectionNode, VolumeManager

_PARAMS = CrushParams(k=2.5, step=244, sigma=97.48, offset=0, bits=8, source_dtype='uint16')


@pytest.fixture
def volume_dir():
    path = tempfile.mkdtemp(prefix='nornir-codehist-')
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _filter_with_tiles(volume_dir: str, crushed: bool = True) -> tuple[FilterNode, list[np.ndarray], str]:
    volume = VolumeManager.Load(volume_dir, Create=True)
    (_, block) = volume.UpdateOrAddChildByAttrib(BlockNode.Create('TEM'), 'Name')
    (_, section) = block.UpdateOrAddChildByAttrib(SectionNode.Create(200), 'Number')
    (_, channel) = section.UpdateOrAddChildByAttrib(ChannelNode.Create('TEM'), 'Name')
    (_, filter_node) = channel.UpdateOrAddChildByAttrib(FilterNode.Create('Raw8'), 'Name')
    if crushed:
        shared.SetFilterCrushAttributes(filter_node, _PARAMS)
    tile_dir = os.path.join(volume_dir, 'level1')
    os.makedirs(tile_dir)
    rng = np.random.default_rng(3)
    tiles = [rng.integers(20, 41, size=(32, 32)).astype(np.uint8) for _ in range(4)]
    for (i, codes) in enumerate(tiles):
        noise_crush.save_crushed_png(os.path.join(tile_dir, f'{i:03d}.png'), codes, _PARAMS)
    return filter_node, tiles, tile_dir


def _with_level_one(tile_dir: str):
    level = types.SimpleNamespace(FullPath=tile_dir)
    pyramid = types.SimpleNamespace(FullPath=tile_dir, GetLevel=lambda downsample: level if downsample == 1 else None)
    return (mock.patch.object(FilterNode, 'HasTilePyramid', new=True, create=True),
            mock.patch.object(FilterNode, 'TilePyramid', new=pyramid, create=True))


def _run(filter_node: FilterNode, tile_dir: str, **kwargs) -> list:
    (a, b) = _with_level_one(tile_dir)
    with a, b:
        return list(codehistogram.ComputeCodeHistogram(filter_node, **kwargs))


def test_histogram_and_display_range_match_what_the_importer_records(volume_dir):
    (filter_node, tiles, tile_dir) = _filter_with_tiles(volume_dir)

    assert _run(filter_node, tile_dir) == [filter_node]

    expected = sum(np.bincount(t.ravel(), minlength=256) for t in tiles)
    np.testing.assert_array_equal(shared.GetFilterCodeHistogram(filter_node, _PARAMS), expected)
    display = shared.CrushDisplayRange(expected, shared.DEFAULT_CONTRAST_CUTOFFS)
    assert filter_node.CrushDisplayRange == (round(display[0], 3), round(display[1], 3))


def test_a_filter_with_a_histogram_is_left_alone_unless_forced(volume_dir):
    (filter_node, _, tile_dir) = _filter_with_tiles(volume_dir)
    _run(filter_node, tile_dir)

    assert _run(filter_node, tile_dir) == []
    shared.SetFilterCodeHistogram(filter_node, np.ones(256, dtype=np.int64), shared.DEFAULT_CONTRAST_CUTOFFS)
    assert _run(filter_node, tile_dir, Force='true') == [filter_node]


def test_legacy_filters_are_skipped(volume_dir):
    (filter_node, _, tile_dir) = _filter_with_tiles(volume_dir, crushed=False)

    assert _run(filter_node, tile_dir) == []
    assert filter_node.CrushDisplayRange is None


def test_sample_every_reads_a_subset_of_the_tiles(volume_dir):
    (filter_node, tiles, tile_dir) = _filter_with_tiles(volume_dir)

    _run(filter_node, tile_dir, SampleEvery=2)

    expected = sum(np.bincount(t.ravel(), minlength=256) for t in tiles[::2])
    np.testing.assert_array_equal(shared.GetFilterCodeHistogram(filter_node, _PARAMS), expected)
