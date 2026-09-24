"""Regression tests for tile identity across missing-image removal (#153).

``RemoveMissingTiles`` drops tiles whose image is absent from disk, and
``CreateTilesFromIDocTileData`` then numbered the survivors from zero. Those two agree only
when nothing was dropped. With one image absent, every later tile shifted down: source
``002.tif`` was written as target ``001.png``.

Measured on a six-tile section, the consequence that matters is re-import. With ``001.tif``
absent and then restored -- a tile still copying off the scope, a transient mount -- 5 of 6
target filenames changed which source image they held. Anything keyed by target filename
(prune scores, per-tile artifacts) silently referred to a different image, under the same name.

The fix numbers targets from the tile's position in the idoc, recorded at parse time so
removal cannot disturb it. For a section with no missing tiles the two numbering schemes are
identical, so healthy imports are byte-for-byte unaffected; only sections that were already
shifted change, and they change to the correct answer. The tests below pin both halves of that
claim, because the "healthy imports unaffected" half is what makes the fix safe to apply to
existing volumes.

A partial drop also used to pass in silence -- the importer only reported the case where no
tiles at all remained -- so it is now logged.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from nornir_buildmanager.importers.idoc import IDoc, IDocTileData, NornirTileset

_TILE_COUNT = 6


class _SectionFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def write_section(self, present: list[str], declared_extension: str = 'tif') -> str:
        lines = ['ImageSize = 8 8', 'DataMode = 6', 'PixelSpacing = 2.18', '']
        for index in range(_TILE_COUNT):
            lines.append(f'[Image = {index:03d}.{declared_extension}]')
            lines.append(f'PieceCoordinates = {index * 100} {index * 10} 0')
            lines.append('MinMaxMean = 0 65535 1000')
            lines.append('')

        idoc_path = os.path.join(self.directory, '1.idoc')
        with open(idoc_path, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(lines))

        blank = Image.fromarray(np.zeros((8, 8), dtype=np.uint16))
        for name in present:
            blank.save(os.path.join(self.directory, name))

        return idoc_path

    def tileset_for(self, present: list[str]) -> NornirTileset:
        idoc_path = self.write_section(present)
        idoc = IDoc.Load(idoc_path, usecache=False)
        with mock.patch('nornir_buildmanager.importers.idoc.prettyoutput.LogErr'):
            idoc.RemoveMissingTiles(self.directory)
        return NornirTileset.CreateTilesFromIDocTileData(
            idoc.tiles, InputTileDir=self.directory,
            OutputTileDir=os.path.join(self.directory, 'out'), OutputImageExt='png')

    @staticmethod
    def mapping(tileset: NornirTileset) -> dict[str, str]:
        """target filename -> source filename"""
        return {os.path.basename(t.TargetImageFullPath): os.path.basename(t.SourceImageFullPath)
                for t in tileset.Tiles}

    @staticmethod
    def all_tiles() -> list[str]:
        return [f'{i:03d}.tif' for i in range(_TILE_COUNT)]


class TestTargetNamesTheSourceItHolds(_SectionFixture):
    """The identity property, stated directly."""

    def test_with_every_tile_present(self):
        for target, source in self.mapping(self.tileset_for(self.all_tiles())).items():
            self.assertEqual(os.path.splitext(target)[0], os.path.splitext(source)[0],
                             f'{target} holds {source}')

    def test_with_one_tile_missing(self):
        present = [n for n in self.all_tiles() if n != '001.tif']
        mapping = self.mapping(self.tileset_for(present))

        self.assertEqual(5, len(mapping))
        for target, source in mapping.items():
            self.assertEqual(os.path.splitext(target)[0], os.path.splitext(source)[0],
                             f'{target} holds {source} -- identity shifted')

    def test_with_two_tiles_missing(self):
        present = [n for n in self.all_tiles() if n not in ('000.tif', '003.tif')]
        mapping = self.mapping(self.tileset_for(present))

        self.assertEqual({'001.png': '001.tif', '002.png': '002.tif',
                          '004.png': '004.tif', '005.png': '005.tif'}, mapping)

    def test_with_the_first_tile_missing(self):
        present = [n for n in self.all_tiles() if n != '000.tif']
        mapping = self.mapping(self.tileset_for(present))
        self.assertNotIn('000.png', mapping,
                         'target 000 should be absent, not filled by source 001')
        self.assertEqual('001.tif', mapping['001.png'])

    def test_with_only_one_tile_present(self):
        mapping = self.mapping(self.tileset_for(['004.tif']))
        self.assertEqual({'004.png': '004.tif'}, mapping)

    def test_the_stage_position_travels_with_its_own_tile(self):
        # Positions were always attached to the right tile object; the bug was the name. Pin
        # it so a future numbering change cannot silently decouple the two.
        present = [n for n in self.all_tiles() if n != '001.tif']
        for tile in self.tileset_for(present).Tiles:
            index = int(os.path.splitext(os.path.basename(tile.SourceImageFullPath))[0])
            self.assertEqual((index * 100, index * 10), tuple(tile.Position))


class TestReimportIsStable(_SectionFixture):
    """The consequence that made this worth fixing."""

    def test_a_restored_tile_does_not_rename_the_others(self):
        without = self.mapping(self.tileset_for(
            [n for n in self.all_tiles() if n != '001.tif']))

        self.setUp()  # a fresh section directory for the second import
        with_all = self.mapping(self.tileset_for(self.all_tiles()))

        for target, source in without.items():
            self.assertEqual(source, with_all[target],
                             f'{target} changed which source it holds once the missing '
                             f'tile reappeared')

    def test_only_the_restored_tile_is_new(self):
        without = self.mapping(self.tileset_for(
            [n for n in self.all_tiles() if n != '001.tif']))
        self.setUp()
        with_all = self.mapping(self.tileset_for(self.all_tiles()))

        self.assertEqual({'001.png'}, set(with_all) - set(without))
        self.assertEqual(set(), set(without) - set(with_all))


class TestHealthyImportsAreUnaffected(_SectionFixture):
    """What makes the change safe for existing volumes."""

    def test_a_complete_section_numbers_exactly_as_before(self):
        # The old rule was "position among the tiles handed over". With nothing dropped that
        # equals the idoc position, so a healthy section's filenames must be untouched.
        tileset = self.tileset_for(self.all_tiles())
        expected = [f'{i:03d}.png' for i in range(_TILE_COUNT)]
        self.assertEqual(expected,
                         [os.path.basename(t.TargetImageFullPath) for t in tileset.Tiles])

    def test_the_tile_order_is_preserved(self):
        tileset = self.tileset_for(self.all_tiles())
        sources = [os.path.basename(t.SourceImageFullPath) for t in tileset.Tiles]
        self.assertEqual(self.all_tiles(), sources)


class TestTheIndexSurvivesRemoval(_SectionFixture):
    """The mechanism: the index is recorded at parse time, not derived from the list."""

    def test_every_parsed_tile_gets_its_position(self):
        idoc = IDoc.Load(self.write_section(self.all_tiles()), usecache=False)
        self.assertEqual(list(range(_TILE_COUNT)), [t.TileIndex for t in idoc.tiles])

    def test_the_index_is_unchanged_by_removal(self):
        present = [n for n in self.all_tiles() if n != '001.tif']
        idoc = IDoc.Load(self.write_section(present), usecache=False)
        with mock.patch('nornir_buildmanager.importers.idoc.prettyoutput.LogErr'):
            idoc.RemoveMissingTiles(self.directory)

        self.assertEqual([0, 2, 3, 4, 5], [t.TileIndex for t in idoc.tiles],
                         'removal renumbered the survivors')

    def test_the_index_is_not_written_into_the_metadata(self):
        # AddIdocNode copies non-underscore tile attributes into VolumeData.xml, so the
        # backing attribute is underscore-prefixed on purpose.
        idoc = IDoc.Load(self.write_section(self.all_tiles()), usecache=False)
        public = [k for k in vars(idoc.tiles[0]) if not k.startswith('_')]
        self.assertNotIn('TileIndex', public,
                         'the tile index would be recorded as scope meta-data')


class TestAnOlderCacheStillNumbers(unittest.TestCase):
    """Tiles from a pickle predating TileIndex must not crash or produce None names."""

    def test_a_tile_without_an_index_falls_back_to_sequence(self):
        tiles = []
        for index in range(3):
            tile = IDocTileData(f'{index:03d}.tif')
            tile.PieceCoordinates = (index * 100, index * 10, 0)
            tiles.append(tile)

        for tile in tiles:
            self.assertIsNone(tile.TileIndex)

        tileset = NornirTileset.CreateTilesFromIDocTileData(
            tiles, InputTileDir='in', OutputTileDir='out', OutputImageExt='png')

        self.assertEqual(['000.png', '001.png', '002.png'],
                         [os.path.basename(t.TargetImageFullPath) for t in tileset.Tiles])


class TestTheRealCorpusRenaming(unittest.TestCase):
    """Records that this fix renames targets for the shipped fixture, and why.

    The RC2_4Square idocs each declare 813 tiles (10000.tif-10812.tif) but ship only the
    leading one or four images, to keep the fixture small. The survivors are not contiguous:
    they sit at idoc positions 0, 15, 397 and 398. So the old rule named them 000-003 and the
    new rule names them 000, 015, 397, 398.

    That is the fix working -- 015.png now holds 10015.tif rather than being the third file in
    a resequenced run -- but it is a visible output change on real data, so it is asserted
    here rather than left to be discovered.
    """

    def setUp(self):
        if not os.environ.get('TESTINPUTPATH'):
            self.skipTest('TESTINPUTPATH is not set')
        self.corpus = os.path.join(os.environ['TESTINPUTPATH'],
                                   'PlatformRaw', 'IDOC', 'RC2_4Square')
        if not os.path.isdir(self.corpus):
            self.skipTest(f'{self.corpus} is not present')

    def sections(self):
        import glob
        return sorted(glob.glob(os.path.join(self.corpus, '*', '*.idoc')))

    def test_each_target_names_the_source_it_holds(self):
        for idoc_path in self.sections():
            idoc = IDoc.Load(idoc_path, usecache=False)
            with mock.patch('nornir_buildmanager.importers.idoc.prettyoutput.LogErr'):
                idoc.RemoveMissingTiles(os.path.dirname(idoc_path))

            tileset = NornirTileset.CreateTilesFromIDocTileData(
                idoc.tiles, InputTileDir=os.path.dirname(idoc_path),
                OutputTileDir='out', OutputImageExt='png')

            for tile in tileset.Tiles:
                source = os.path.basename(tile.SourceImageFullPath)
                target = os.path.basename(tile.TargetImageFullPath)
                # Sources carry the old 10000 offset, so compare the trailing digits.
                source_number = int(os.path.splitext(source)[0]) - 10000
                target_number = int(os.path.splitext(target)[0])
                self.assertEqual(source_number, target_number,
                                 f'{idoc_path}: {target} holds {source}')

    def test_the_surviving_tiles_are_not_contiguous(self):
        # The premise of the renaming. If the fixture ever ships contiguous tiles this test
        # says so, and the renaming note above stops applying.
        non_contiguous = 0
        for idoc_path in self.sections():
            idoc = IDoc.Load(idoc_path, usecache=False)
            with mock.patch('nornir_buildmanager.importers.idoc.prettyoutput.LogErr'):
                idoc.RemoveMissingTiles(os.path.dirname(idoc_path))
            indices = [t.TileIndex for t in idoc.tiles]
            if indices != list(range(len(indices))):
                non_contiguous += 1

        self.assertGreater(non_contiguous, 0,
                           'the fixture now ships contiguous tiles, so this fix no longer '
                           'changes its target filenames')


class TestAPartialDropIsReported(_SectionFixture):
    """It used to pass in silence unless every tile was gone."""

    def test_the_missing_tile_is_named(self):
        self.write_section([n for n in self.all_tiles() if n != '001.tif'])
        idoc = IDoc.Load(os.path.join(self.directory, '1.idoc'), usecache=False)

        with mock.patch('nornir_buildmanager.importers.idoc.prettyoutput.LogErr') as log:
            idoc.RemoveMissingTiles(self.directory)

        log.assert_called_once()
        message = log.call_args.args[0]
        self.assertIn('001.tif', message)
        self.assertIn('1 of 6', message)

    def test_nothing_is_reported_when_every_tile_is_present(self):
        self.write_section(self.all_tiles())
        idoc = IDoc.Load(os.path.join(self.directory, '1.idoc'), usecache=False)

        with mock.patch('nornir_buildmanager.importers.idoc.prettyoutput.LogErr') as log:
            idoc.RemoveMissingTiles(self.directory)

        log.assert_not_called()

    def test_a_long_list_is_truncated(self):
        self.write_section([])
        idoc = IDoc.Load(os.path.join(self.directory, '1.idoc'), usecache=False)
        # Widen past the cap so the summary rather than the whole list is checked.
        for index in range(_TILE_COUNT, 20):
            tile = IDocTileData(f'{index:03d}.tif')
            tile._TileIndex = index
            idoc.tiles.append(tile)

        with mock.patch('nornir_buildmanager.importers.idoc.prettyoutput.LogErr') as log:
            idoc.RemoveMissingTiles(self.directory)

        message = log.call_args.args[0]
        self.assertIn('and 10 more', message)
        self.assertIn('20 of 20', message)

    def test_removal_still_returns_the_surviving_tiles(self):
        self.write_section([n for n in self.all_tiles() if n != '001.tif'])
        idoc = IDoc.Load(os.path.join(self.directory, '1.idoc'), usecache=False)

        with mock.patch('nornir_buildmanager.importers.idoc.prettyoutput.LogErr'):
            idoc.RemoveMissingTiles(self.directory)

        self.assertEqual(['000.tif', '002.tif', '003.tif', '004.tif', '005.tif'],
                         [t.Image for t in idoc.tiles])


if __name__ == '__main__':
    unittest.main()
