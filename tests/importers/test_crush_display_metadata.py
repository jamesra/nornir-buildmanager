"""Display metadata on crushed filters (James, 2026-11-05, relayed by the Viking Client session).

Crushed imports keep the code histogram they sum while converting, as a ``<CodeHistogram>`` child of the crushed
``<Filter>``, plus ``CrushDisplayMin/Max`` at the import's -Min/-Max cumulative fractions. CreateVikingXML copies the
crush parameters, the display range and the histogram to the crushed ``<Pyramid>`` and ``<Tileset>`` so Viking can
expand codes in its shader. No nornir stage reads these as contrast, and legacy filters export unchanged.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import types
import xml.etree.ElementTree as ETree

import numpy as np
import pytest
from nornir_imageregistration import noise_crush
from nornir_imageregistration.noise_crush import CrushParams

from nornir_buildmanager.importers import shared
from nornir_buildmanager.importers.mrc import MRCImport
from nornir_buildmanager.operations import tile as tile_ops
from nornir_buildmanager.operations import vikingxml
from nornir_buildmanager.volumemanager import (
    BlockNode,
    ChannelNode,
    CodeHistogramNode,
    FilterNode,
    SectionNode,
    TilePyramidNode,
    TilesetNode,
    VolumeManager,
)

_PARAMS = CrushParams(k=2.5, step=244, sigma=97.48, offset=0, bits=8, source_dtype='uint16')
_CUTOFFS = (0.0001, 0.9999)


def _histogram() -> np.ndarray:
    counts = np.zeros(_PARAMS.max_code + 1, dtype=np.int64)
    counts[10] = 5  # below the low cutoff
    counts[20:41] = 100000
    counts[60] = 3  # above the high cutoff
    return counts


@pytest.fixture
def volume_dir():
    path = tempfile.mkdtemp(prefix='nornir-crush-display-')
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _crushed_filter(volume_dir: str) -> tuple[object, FilterNode]:
    volume = VolumeManager.Load(volume_dir, Create=True)
    (_, block) = volume.UpdateOrAddChildByAttrib(BlockNode.Create('TEM'), 'Name')
    (_, section) = block.UpdateOrAddChildByAttrib(SectionNode.Create(360), 'Number')
    (_, channel) = section.UpdateOrAddChildByAttrib(ChannelNode.Create('TEM'), 'Name')
    (_, filter_node) = channel.UpdateOrAddChildByAttrib(FilterNode.Create('Raw8'), 'Name')
    shared.SetFilterCrushAttributes(filter_node, _PARAMS)
    VolumeManager.Save(volume)
    return volume, filter_node


def _reload_filter(volume_dir: str) -> FilterNode:
    volume = VolumeManager.Load(volume_dir)
    section = volume.find('Block').GetSection(360)  # type: ignore[union-attr]
    return section.GetChannel('TEM').GetFilter('Raw8')


class TestDisplayRange:
    def test_display_range_is_the_cutoff_codes(self):
        assert shared.CrushCodeCutoffs(_histogram(), _CUTOFFS) == (20, 40)
        assert shared.CrushDisplayRange(_histogram(), _CUTOFFS) == (20.0, 40.0)

    def test_span_still_counts_both_ends(self):
        assert shared.CrushCodeSpan(_histogram(), _CUTOFFS) == 21.0

    def test_single_code_still_has_a_window(self):
        counts = np.zeros(256, dtype=np.int64)
        counts[7] = 1000
        assert shared.CrushDisplayRange(counts, _CUTOFFS) == (7.0, 8.0)

    def test_empty_histogram_has_no_range(self):
        assert shared.CrushDisplayRange(np.zeros(256, dtype=np.int64), _CUTOFFS) is None


class TestHistogramOnTheFilter:
    def test_histogram_and_display_range_are_saved_on_the_filter_node(self, volume_dir):
        (_, filter_node) = _crushed_filter(volume_dir)

        assert shared.SetFilterCodeHistogram(filter_node, _histogram(), _CUTOFFS)
        VolumeManager.Save(filter_node)  # what the importer's `yield filterObj` does

        # The filter's own VolumeData.xml (not only the channel's Filter_Link) holds it: cd0a081.
        filter_xml = ETree.parse(os.path.join(filter_node.FullPath, 'VolumeData.xml')).getroot()
        assert filter_xml.attrib['CrushDisplayMin'] == '20'
        assert filter_xml.attrib['CrushDisplayMax'] == '40'
        histogram_xml = filter_xml.find('CodeHistogram')
        assert histogram_xml is not None and histogram_xml.attrib['Bins'] == '256'
        np.testing.assert_array_equal(np.array(histogram_xml.text.split(), dtype=np.int64), _histogram())

        reloaded = _reload_filter(volume_dir)
        np.testing.assert_array_equal(shared.GetFilterCodeHistogram(reloaded, _PARAMS), _histogram())

    def test_no_contrast_values_on_the_filter(self, volume_dir):
        (_, filter_node) = _crushed_filter(volume_dir)
        shared.SetFilterCodeHistogram(filter_node, _histogram(), _CUTOFFS)

        for key in ('MinIntensityCutoff', 'MaxIntensityCutoff', 'Gamma'):
            assert key not in filter_node.attrib
        assert filter_node.MinIntensityCutoff is None and filter_node.MaxIntensityCutoff is None
        assert tile_ops.IsCrushedFilter(filter_node)

    def test_unchanged_histogram_is_not_rewritten(self, volume_dir):
        (_, filter_node) = _crushed_filter(volume_dir)
        assert shared.SetFilterCodeHistogram(filter_node, _histogram(), _CUTOFFS)
        assert not shared.SetFilterCodeHistogram(filter_node, _histogram(), _CUTOFFS)

    def test_histogram_of_the_wrong_size_is_ignored(self, volume_dir):
        (_, filter_node) = _crushed_filter(volume_dir)
        shared.SetFilterCodeHistogram(filter_node, np.ones(512, dtype=np.int64), _CUTOFFS)
        assert shared.GetFilterCodeHistogram(filter_node, _PARAMS) is None


def _write_tiles(tile_dir: str, tiles: list[np.ndarray]) -> None:
    os.makedirs(tile_dir, exist_ok=True)
    for (i, codes) in enumerate(tiles):
        noise_crush.save_crushed_png(os.path.join(tile_dir, f'{i:03d}.png'), codes, _PARAMS)


class TestUpdateFromImport:
    def _tiles(self) -> list[np.ndarray]:
        rng = np.random.default_rng(7)
        return [rng.integers(20, 41, size=(32, 32)).astype(np.uint8) for _ in range(3)]

    def _expected(self, tiles: list[np.ndarray]) -> np.ndarray:
        return sum(np.bincount(t.ravel(), minlength=256) for t in tiles)

    def test_full_conversion_uses_the_conversion_histogram(self, volume_dir):
        (_, filter_node) = _crushed_filter(volume_dir)
        stats = shared.CrushSectionStats(_histogram(), 0, 0, 3)

        # No tiles on disk: nothing is read when every tile was converted now.
        assert shared.UpdateFilterCodeHistogram(filter_node, _PARAMS, _CUTOFFS, os.path.join(volume_dir, 'none'),
                                                stats, total_tiles=3)
        np.testing.assert_array_equal(shared.GetFilterCodeHistogram(filter_node, _PARAMS), _histogram())

    def test_existing_tiles_backfill_a_missing_histogram(self, volume_dir):
        (_, filter_node) = _crushed_filter(volume_dir)
        tiles = self._tiles()
        tile_dir = os.path.join(volume_dir, 'tiles')
        _write_tiles(tile_dir, tiles)

        assert shared.UpdateFilterCodeHistogram(filter_node, _PARAMS, _CUTOFFS, tile_dir, None, total_tiles=3,
                                                image_ext='png')
        np.testing.assert_array_equal(shared.GetFilterCodeHistogram(filter_node, _PARAMS), self._expected(tiles))

    def test_stored_histogram_is_kept_when_nothing_was_converted(self, volume_dir):
        (_, filter_node) = _crushed_filter(volume_dir)
        shared.SetFilterCodeHistogram(filter_node, _histogram(), _CUTOFFS)
        tile_dir = os.path.join(volume_dir, 'tiles')
        _write_tiles(tile_dir, self._tiles())

        assert not shared.UpdateFilterCodeHistogram(filter_node, _PARAMS, _CUTOFFS, tile_dir, None, total_tiles=3)
        np.testing.assert_array_equal(shared.GetFilterCodeHistogram(filter_node, _PARAMS), _histogram())

    def test_partial_conversion_reads_every_tile(self, volume_dir):
        (_, filter_node) = _crushed_filter(volume_dir)
        shared.SetFilterCodeHistogram(filter_node, _histogram(), _CUTOFFS)
        tiles = self._tiles()
        tile_dir = os.path.join(volume_dir, 'tiles')
        _write_tiles(tile_dir, tiles)
        one_tile = shared.CrushSectionStats(np.bincount(tiles[0].ravel(), minlength=256), 0, 0, 1)

        assert shared.UpdateFilterCodeHistogram(filter_node, _PARAMS, _CUTOFFS, tile_dir, one_tile, total_tiles=3)
        np.testing.assert_array_equal(shared.GetFilterCodeHistogram(filter_node, _PARAMS), self._expected(tiles))


def _filter_with_pyramid_and_tileset(volume_dir: str, crushed: bool, tileset_crushed: bool) -> FilterNode:
    volume = VolumeManager.Load(volume_dir, Create=True)
    (_, block) = volume.UpdateOrAddChildByAttrib(BlockNode.Create('TEM'), 'Name')
    (_, section) = block.UpdateOrAddChildByAttrib(SectionNode.Create(360), 'Number')
    (_, channel) = section.UpdateOrAddChildByAttrib(ChannelNode.Create('TEM'), 'Name')
    (_, filter_node) = channel.UpdateOrAddChildByAttrib(FilterNode.Create('Raw8'), 'Name')
    filter_node.append(TilePyramidNode.Create(NumberOfTiles=3))
    tileset = TilesetNode.Create()
    tileset.attrib.update({'TileXDim': '512', 'TileYDim': '512', 'FilePrefix': '', 'FilePostfix': '.png',
                           'CoordFormat': 'X%03d_Y%03d'})
    filter_node.append(tileset)
    if crushed:
        shared.SetFilterCrushAttributes(filter_node, _PARAMS)
        shared.SetFilterCodeHistogram(filter_node, _histogram(), _CUTOFFS)
        if tileset_crushed:
            tileset.attrib.update(_PARAMS.to_attributes())
    else:
        filter_node.SetContrastValues(1772, 6399, 1.0)
    return filter_node


def _export(filter_node: FilterNode) -> tuple[ETree.Element, ETree.Element]:
    section = ETree.Element('Section')
    pyramid = vikingxml.ParsePyramidNode(filter_node, filter_node.TilePyramid, section)
    tileset = vikingxml.ParseTilesetNode(filter_node, filter_node.Tileset, section)
    return pyramid, tileset


class TestVikingXml:
    def test_crushed_pyramid_and_tileset_carry_crush_display_data(self, volume_dir):
        (pyramid, tileset) = _export(_filter_with_pyramid_and_tileset(volume_dir, crushed=True, tileset_crushed=True))

        for node in (pyramid, tileset):
            assert node.attrib['CrushVersion'] == '1'
            assert node.attrib['CrushStep'] == '244'
            assert node.attrib['CrushOffset'] == '0'
            assert node.attrib['CrushBits'] == '8'
            assert node.attrib['CrushDisplayMin'] == '20'
            assert node.attrib['CrushDisplayMax'] == '40'
            histogram = node.find('CodeHistogram')
            assert histogram is not None and histogram.attrib == {'Bins': '256'}
            np.testing.assert_array_equal(np.array(histogram.text.split(), dtype=np.int64), _histogram())
            # Viking reads no contrast cutoffs for crushed data
            assert 'MinIntensityCutoff' not in node.attrib

    def test_legacy_filter_exports_nothing_new(self, volume_dir):
        (pyramid, tileset) = _export(_filter_with_pyramid_and_tileset(volume_dir, crushed=False, tileset_crushed=False))

        for node in (pyramid, tileset):
            assert not [name for name in node.attrib if name.startswith('Crush')]
            assert node.find('CodeHistogram') is None
        assert set(pyramid.attrib) == {'Path', 'Name', 'LevelFormat'}
        assert set(tileset.attrib) == {'path', 'name', 'TileXDim', 'TileYDim', 'FilePrefix', 'FilePostfix',
                                       'CoordFormat'}

    def test_stretched_tileset_of_a_crushed_filter_is_not_marked_crushed(self, volume_dir):
        (pyramid, tileset) = _export(_filter_with_pyramid_and_tileset(volume_dir, crushed=True, tileset_crushed=False))

        assert pyramid.attrib['CrushStep'] == '244'
        assert 'CrushStep' not in tileset.attrib and tileset.find('CodeHistogram') is None


def test_code_histogram_node_is_wrapped_on_load():
    element = ETree.fromstring('<Filter><CodeHistogram Bins="3">1 2 3</CodeHistogram></Filter>')
    node = CodeHistogramNode.wrap(element[0])
    np.testing.assert_array_equal(node.Counts, [1, 2, 3])
    assert not node.NeedsValidation


def test_mrc_export_returns_the_histogram_of_the_tiles_it_wrote():
    rng = np.random.default_rng(3)
    tiles = [rng.integers(0, 12000, (40, 30)).astype(np.uint16) for _ in range(3)]
    params = noise_crush.make_crush_params(30.0, 12000, np.uint16)
    fake_mrc = types.SimpleNamespace(num_tiles=len(tiles), get_tile_as_numpy_memmap=lambda i: tiles[i])
    expected = sum(np.bincount(noise_crush.crush(np.flipud(t), params).ravel(), minlength=params.max_code + 1)
                   for t in tiles)
    with tempfile.TemporaryDirectory() as tmp:
        stats = MRCImport.ExportImages(fake_mrc, tmp, '.png', None, flip_ud=True, crush_params=params)  # type: ignore[arg-type]
        assert stats is not None and stats.tiles == 3
        np.testing.assert_array_equal(stats.code_histogram, expected)
        np.testing.assert_array_equal(shared.CodeHistogramFromTiles(tmp, params), expected)

        again = MRCImport.ExportImages(fake_mrc, tmp, '.png', None, flip_ud=True, crush_params=params)  # type: ignore[arg-type]
        assert again is not None and again.tiles == 0  # nothing rewritten: the import backfills from the tiles
