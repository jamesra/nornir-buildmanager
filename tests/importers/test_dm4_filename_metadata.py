"""Tests for DM4 filename metadata parsing (#154).

``GetMetaFromFilename`` caught everything with a bare ``except:`` and returned None for
whatever it could not read. Its own comment already said an exception was probably what was
wanted, and measuring agreed: neither caller can use None.

| value | where it lands | before |
|-------|----------------|--------|
| section None | ``'%04d' % None`` in GetOrCreateSection | TypeError |
| tile None | GetFileNameForTileNumber | TypeError |
| tile None | ``tile_number // XDim`` for the grid position | TypeError |

``Import`` walks every ``*.dm4`` under the import path and calls ``ToMosaic`` on each, so one
stray filename aborted the entire import with a TypeError naming neither the file nor the
expected layout. ``Glumi1_stack_00_slice_0476 (copy).dm4`` is enough to trigger it, which is
not an exotic name for a file someone duplicated.

The bare ``except:`` also swallowed KeyboardInterrupt and SystemExit, confirmed by probe.

So the failure moved to the point of parsing, where the file and the expected layout can
actually be named. Both before and after, a bad filename stops the import -- no capability is
lost, only the diagnosis improves.
"""

from __future__ import annotations

import unittest

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.importers.dm4 import DigitalMicrograph4Import

_parse = DigitalMicrograph4Import.GetMetaFromFilename


class TestTheExpectedLayoutStillParses(unittest.TestCase):
    """The real Neitz naming, unchanged."""

    def test_the_corpus_filename(self):
        self.assertEqual((476, 0), _parse('Glumi1_3VBSED_stack_00_slice_0476.dm4'))

    def test_a_different_tile_and_section(self):
        self.assertEqual((1, 12), _parse('Glumi1_3VBSED_stack_12_slice_0001.dm4'))

    def test_a_full_path_is_accepted(self):
        self.assertEqual(
            (476, 0),
            _parse(r'D:\data\PlatformRaw\DM4\Neitz\Glumi1_3VBSED_stack_00_slice_0476.dm4'))

    def test_the_extension_is_optional(self):
        self.assertEqual((476, 0), _parse('Glumi1_3VBSED_stack_00_slice_0476'))

    def test_exactly_three_parts_is_enough(self):
        # The minimum the layout needs: parts[-3] and parts[-1] both exist.
        self.assertEqual((7, 3), _parse('3_x_7.dm4'))

    def test_leading_zeros_are_not_treated_as_octal(self):
        self.assertEqual((476, 8), _parse('a_08_x_0476.dm4'))

    def test_a_negative_section_is_read_as_written(self):
        # int() accepts a sign. Not expected from the scope, but pinned so the narrowed
        # except is not later assumed to reject it.
        self.assertEqual((-5, 0), _parse('a_00_x_-5.dm4'))


class TestAnUnreadableNameStopsTheImport(unittest.TestCase):
    """Each shape that used to yield None."""

    def test_a_non_numeric_section(self):
        with self.assertRaises(NornirUserException):
            _parse('Glumi1_3VBSED_stack_00_slice_final.dm4')

    def test_a_copy_suffix(self):
        # The realistic trigger: someone duplicated a file in the import directory.
        with self.assertRaises(NornirUserException):
            _parse('Glumi1_stack_00_slice_0476 (copy).dm4')

    def test_a_non_numeric_tile(self):
        with self.assertRaises(NornirUserException):
            _parse('Glumi1_3VBSED_stack_XX_slice_0476.dm4')

    def test_too_few_parts_for_a_tile_number(self):
        for name in ['slice_0476.dm4', '0476.dm4']:
            with self.assertRaises(NornirUserException, msg=f'{name} should be rejected'):
                _parse(name)

    def test_no_numbers_at_all(self):
        with self.assertRaises(NornirUserException):
            _parse('capture.dm4')

    def test_an_empty_name(self):
        with self.assertRaises(NornirUserException):
            _parse('')

    def test_a_name_that_is_only_an_extension(self):
        with self.assertRaises(NornirUserException):
            _parse('.dm4')


class TestTheMessageIsActuallyUseful(unittest.TestCase):
    """The point of the change: the old TypeError named nothing."""

    def test_it_names_the_file(self):
        with self.assertRaises(NornirUserException) as caught:
            _parse('capture.dm4')
        self.assertIn('capture.dm4', str(caught.exception))

    def test_it_names_the_expected_layout(self):
        with self.assertRaises(NornirUserException) as caught:
            _parse('capture.dm4')
        message = str(caught.exception)
        self.assertIn('<section>', message)
        self.assertIn('Glumi1_3VBSED_stack_00_slice_0476', message,
                      'the message should show a working example')

    def test_it_says_which_number_it_could_not_read(self):
        section_only = str(self.assertRaisesMessage('Glumi1_3VBSED_stack_XX_slice_0476.dm4'))
        self.assertIn('tile number', section_only)
        self.assertNotIn('section number', section_only,
                         'the section parsed fine here; do not report it as unreadable')

        tile_only = str(self.assertRaisesMessage('Glumi1_3VBSED_stack_00_slice_final.dm4'))
        self.assertIn('section number', tile_only)
        self.assertNotIn('tile number', tile_only,
                         'the tile parsed fine here; do not report it as unreadable')

    def test_it_reports_both_when_both_fail(self):
        message = str(self.assertRaisesMessage('capture.dm4'))
        self.assertIn('section number', message)
        self.assertIn('tile number', message)

    def test_it_quotes_what_it_found(self):
        message = str(self.assertRaisesMessage('Glumi1_3VBSED_stack_00_slice_final.dm4'))
        self.assertIn("'final'", message,
                      'the message should show the part it tried to convert')

    def test_it_says_how_many_parts_when_there_are_too_few(self):
        message = str(self.assertRaisesMessage('0476.dm4'))
        self.assertIn('part', message)

    def assertRaisesMessage(self, name: str):
        with self.assertRaises(NornirUserException) as caught:
            _parse(name)
        return caught.exception


class TestTheInterruptsAreNoLongerSwallowed(unittest.TestCase):
    """The bare except caught BaseException, including Ctrl-C."""

    def test_a_keyboard_interrupt_propagates(self):
        from unittest import mock

        # int() is the call the excepts wrap. If a KeyboardInterrupt arrives while it runs,
        # it must reach the caller rather than being read as an unparseable name.
        with mock.patch('builtins.int', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                _parse('Glumi1_3VBSED_stack_00_slice_0476.dm4')

    def test_a_system_exit_propagates(self):
        from unittest import mock

        with mock.patch('builtins.int', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                _parse('Glumi1_3VBSED_stack_00_slice_0476.dm4')

    def test_an_unexpected_error_is_not_relabelled(self):
        from unittest import mock

        # Only ValueError and IndexError mean "this name does not match". Anything else is a
        # real fault and should surface as itself.
        with mock.patch('builtins.int', side_effect=MemoryError('out of memory')):
            with self.assertRaises(MemoryError):
                _parse('Glumi1_3VBSED_stack_00_slice_0476.dm4')


class TestTheRealCorpusIsUnaffected(unittest.TestCase):
    """Nothing that imports today should start failing."""

    def setUp(self):
        import os

        test_input = os.environ.get('TESTINPUTPATH')
        if not test_input:
            self.skipTest('TESTINPUTPATH is not set')

        self.dm4_dir = os.path.join(test_input, 'PlatformRaw', 'DM4', 'Neitz')
        if not os.path.isdir(self.dm4_dir):
            self.skipTest(f'DM4 corpus not found at {self.dm4_dir}')

        self.names = sorted(f for f in os.listdir(self.dm4_dir) if f.endswith('.dm4'))

    def test_every_corpus_filename_parses(self):
        self.assertTrue(self.names, 'expected DM4 files in the corpus')
        for name in self.names:
            with self.subTest(name=name):
                section, tile = _parse(name)
                self.assertIsInstance(section, int)
                self.assertIsInstance(tile, int)

    def test_the_corpus_covers_two_sections(self):
        sections = {_parse(name)[0] for name in self.names}
        self.assertEqual({476, 477}, sections)


class TestTheOldFallbackReallyWasUnusable(unittest.TestCase):
    """Premise guard: None could not be consumed by either caller."""

    def test_a_none_section_cannot_be_formatted(self):
        import nornir_buildmanager.templates

        with self.assertRaises(TypeError):
            ('%' + nornir_buildmanager.templates.Current.SectionFormat) % None

    def test_a_none_tile_cannot_name_a_file(self):
        from nornir_buildmanager.importers import GetFileNameForTileNumber

        with self.assertRaises(TypeError):
            GetFileNameForTileNumber(None)

    def test_a_none_tile_cannot_index_the_grid(self):
        with self.assertRaises(TypeError):
            None // 4


if __name__ == '__main__':
    unittest.main()
