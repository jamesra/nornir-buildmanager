"""Regression tests for the unguarded value index in the idoc parser (#152).

``values = parts[1].split()`` is empty when a key has no value -- ``Magnification =``, or a
value that is only whitespace. Indexing it raised ``IndexError`` out of ``IDoc.Load``, and
because ``ToMosaic`` re-raises, one such line aborted the import of the entire section.

The issue's evidence pointed at ``vTemp[0].isdigit()``, but that index was never the problem:
``split()`` with no argument discards empty tokens, so every element is non-empty. The crash
was ``values[0]`` on the line above. Both are guarded now, but the tests below pin the real one.

An empty value is skipped, leaving the attribute unset. That is not a new convention -- it is
already how the parser treats a non-numeric value, which falls through the same ``if`` with
``value`` still None.

The real RC2_4Square corpus has no such line (0 of 110,624 key=value lines), so this was
latent. It is fixed rather than closed as unreachable because the blast radius is the whole
section for a single malformed line, and idocs are third-party scope output.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from nornir_buildmanager.importers.idoc import IDoc

_MINIMAL = """\
ImageSize = 4080 4080
DataMode = 6
PixelSpacing = 2.18

[Image = 000.tif]
PieceCoordinates = 0 0 0
MinMaxMean = 0 65535 1000
"""


class _IdocFixture(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.mkdtemp()

    def load(self, text: str) -> IDoc:
        path = os.path.join(self._directory, 'probe.idoc')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
        return IDoc.Load(path, usecache=False)

    def load_with_extra(self, line: str) -> IDoc:
        return self.load(_MINIMAL.replace('DataMode = 6', f'DataMode = 6\n{line}'))


class TestAnEmptyValueNoLongerAborts(_IdocFixture):
    """The crash itself."""

    def test_a_key_with_no_value_loads(self):
        idoc = self.load_with_extra('Magnification =')
        self.assertEqual(1, idoc.NumTiles)

    def test_a_whitespace_only_value_loads(self):
        for value in ['   ', '\t', ' \t ']:
            idoc = self.load_with_extra(f'Magnification ={value}')
            self.assertEqual(1, idoc.NumTiles, f'failed for value {value!r}')

    def test_an_empty_value_on_a_tile_loads(self):
        idoc = self.load(_MINIMAL + 'TargetDefocus =\n')
        self.assertEqual(1, idoc.NumTiles)

    def test_several_empty_values_load(self):
        idoc = self.load_with_extra('Magnification =\nTargetDefocus =\nSpotSize =   ')
        self.assertEqual(1, idoc.NumTiles)

    def test_an_empty_value_does_not_raise_index_error(self):
        try:
            self.load_with_extra('Magnification =')
        except IndexError as error:
            self.fail(f'an empty value still aborts the load: {error}')

    def test_the_rest_of_the_file_is_still_parsed(self):
        # The point of not raising: everything after the bad line must survive. Under the old
        # code the load died before reaching the tile at all.
        idoc = self.load_with_extra('Magnification =')
        self.assertEqual(6, idoc.DataMode)
        self.assertEqual([4080, 4080], idoc.ImageSize)
        self.assertEqual(2.18, idoc.PixelSpacing)
        self.assertEqual('000.tif', idoc.tiles[0].Image)
        self.assertEqual(65535, idoc.tiles[0].Max)

    def test_a_bad_line_before_the_tiles_does_not_lose_them(self):
        idoc = self.load(_MINIMAL.replace('ImageSize = 4080 4080',
                                          'Magnification =\nImageSize = 4080 4080'))
        self.assertEqual(1, idoc.NumTiles)


class TestTheEmptyValueIsSkipped(_IdocFixture):
    """What an empty value means, and that it matches the existing convention."""

    def test_the_attribute_is_left_unset(self):
        idoc = self.load_with_extra('Magnification =')
        self.assertIsNone(getattr(idoc, 'Magnification', None),
                          'an empty value should not be recorded as a value')

    def test_a_non_numeric_value_is_skipped_the_same_way(self):
        # Pins that skipping is the parser's existing behaviour rather than something this
        # fix invented: a non-numeric value already falls through with value still None.
        idoc = self.load_with_extra('SomeName = notanumber')
        self.assertIsNone(getattr(idoc, 'SomeName', None))

    def test_a_tile_attribute_with_an_empty_value_stays_at_its_default(self):
        idoc = self.load(_MINIMAL + 'TargetDefocus =\n')
        self.assertIsNone(idoc.tiles[0].TargetDefocus)


class TestValidValuesAreUnaffected(_IdocFixture):
    """The guard must not change how real values parse."""

    def test_integers_floats_and_lists(self):
        idoc = self.load_with_extra('SpotSize = 2\nDefocusValue = -1.25\nPair = 3 4')
        self.assertEqual(2, idoc.SpotSize)
        self.assertEqual(-1.25, idoc.DefocusValue)
        self.assertEqual([3, 4], idoc.Pair)

    def test_a_negative_value_still_parses(self):
        # The '-' arm of the same condition the guard wraps.
        idoc = self.load_with_extra('TiltAngle = -0.5')
        self.assertEqual(-0.5, idoc.TiltAngle)

    def test_a_zero_value_still_parses(self):
        idoc = self.load_with_extra('SpotSize = 0')
        self.assertEqual(0, idoc.SpotSize)

    def test_the_baseline_file_is_unchanged(self):
        idoc = self.load(_MINIMAL)
        self.assertEqual(1, idoc.NumTiles)
        self.assertEqual(6, idoc.DataMode)
        self.assertEqual(16, idoc.GetImageBpp())
        self.assertEqual([0, 0, 0], idoc.tiles[0].PieceCoordinates)
        self.assertEqual(0, idoc.tiles[0].Min)
        self.assertEqual(65535, idoc.tiles[0].Max)

    def test_a_line_with_no_equals_is_still_skipped(self):
        idoc = self.load_with_extra('JustAKeyWithNoEquals')
        self.assertEqual(1, idoc.NumTiles)

    def test_an_image_tag_with_an_empty_name_does_not_crash(self):
        # The Image branch reads parts[1] directly rather than splitting, so it was never
        # part of this crash. Pinned so the guard is not later assumed to cover it.
        idoc = self.load(_MINIMAL.replace('[Image = 000.tif]', '[Image = ]'))
        self.assertEqual(1, idoc.NumTiles)
        self.assertEqual('', idoc.tiles[0].Image)


if __name__ == '__main__':
    unittest.main()
