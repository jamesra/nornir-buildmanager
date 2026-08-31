"""Tests for the dead bpp fallback removed from IDoc.GetImageBpp (#151).

The issue read the dead ``else`` arm as lost capability: an idoc without a DataMode line
"returns None instead of deriving bpp from Max". Measuring turned that around. The arm was
indeed unreachable -- ``__init__`` assigns DataMode, so the ``hasattr`` guard was always true --
but reviving it would have made things worse, not better.

Both callers already fall back to reading the bit depth out of the first image file, which is
authoritative. ``Max`` is the brightest *intensity*, so ``ceil(log2(Max))`` only equals the
storage depth for a section that happens to saturate. On the RC2_4Square corpus it agreed at 16
bpp for all 8 sections, but only because every one of them peaks at 65535. A 16-bit capture
peaking at 4000 would have been reported as 12 bpp -- and because the guess sits *before* the
caller's file read, it would have pre-empted the correct answer rather than backing it up.

So the arm was removed. For every reachable input the behaviour is identical, and most of these
tests pass on both sides to pin that. There is one exception, and it is the point: an instance
whose DataMode attribute is genuinely absent -- an idoc unpickled from a cache predating it --
used to fall into the arm and receive the guess. It now reports an unknown depth and lets the
caller read the file. That case fails before the fix and passes after, alongside the structural
tests. The arithmetic tests record why reviving the arm would be wrong, so the next reader of
this issue does not have to re-derive it.
"""

from __future__ import annotations

import glob
import math
import os
import unittest

from nornir_buildmanager.importers import idoc as idoc_module
from nornir_buildmanager.importers.idoc import IDoc, IDocTileData

_IDOC_SOURCE = idoc_module.__file__

_CORPUS = os.path.join(os.environ.get('TESTINPUTPATH', ''),
                       'PlatformRaw', 'IDOC', 'RC2_4Square')


def _idoc_code() -> str:
    with open(_IDOC_SOURCE, 'r', encoding='utf-8') as handle:
        text = handle.read()

    # The removal is explained in a docstring that names what it replaced, so a raw search
    # would match the explanation as readily as a reintroduced line.
    lines = []
    in_docstring = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') and stripped.count('"""') == 1:
            in_docstring = not in_docstring
            continue
        if in_docstring or stripped.startswith('#'):
            continue
        lines.append(line)

    return '\n'.join(lines)


def build_idoc(data_mode, tile_maxima: list[int]) -> IDoc:
    idoc = IDoc()
    idoc.DataMode = data_mode
    for index, maximum in enumerate(tile_maxima):
        tile = IDocTileData(f'{index:03d}.tif')
        tile.MinMaxMean = [0, maximum, maximum / 2.0]
        idoc.tiles.append(tile)
    return idoc


class TestTheDeclaredDepthStillWorks(unittest.TestCase):
    """The live half of the function, unchanged."""

    def test_the_mapped_data_modes(self):
        self.assertEqual(8, build_idoc(0, [255]).GetImageBpp())
        self.assertEqual(16, build_idoc(1, [65535]).GetImageBpp())
        self.assertEqual(16, build_idoc(6, [65535]).GetImageBpp())

    def test_an_unmapped_data_mode_returns_none(self):
        # e.g. DataMode 2 is float; the caller reads the file instead of guessing.
        for data_mode in (2, 3, 4, 5, 7, 99):
            self.assertIsNone(build_idoc(data_mode, [65535]).GetImageBpp(),
                              f'DataMode {data_mode} should not map to a depth')

    def test_no_data_mode_line_returns_none(self):
        # An idoc with no DataMode line leaves the __init__ value in place.
        self.assertIsNone(build_idoc(None, [65535]).GetImageBpp())

    def test_it_does_not_need_tiles(self):
        # The removed arm read self.Max, which raises on an empty tile list. The live path
        # must not depend on tiles at all.
        self.assertEqual(16, build_idoc(6, []).GetImageBpp())

    def test_it_does_not_raise_when_tile_maxima_are_missing(self):
        idoc = IDoc()
        idoc.DataMode = 6
        idoc.tiles.append(IDocTileData('000.tif'))  # no MinMaxMean, so Max is None
        self.assertEqual(16, idoc.GetImageBpp())


class TestAnOlderPickleDoesNotCrash(unittest.TestCase):
    """Instances also arrive via PickleLoad, possibly predating the attribute."""

    def test_an_instance_without_the_attribute_returns_none(self):
        idoc = build_idoc(6, [65535])
        del idoc.DataMode
        self.assertIsNone(idoc.GetImageBpp(),
                          'a cached idoc predating DataMode should report an unknown depth '
                          'rather than raising')

    def test_it_does_not_raise_attribute_error(self):
        idoc = build_idoc(6, [65535])
        del idoc.DataMode
        try:
            idoc.GetImageBpp()
        except AttributeError as error:
            self.fail(f'GetImageBpp raised on an instance without DataMode: {error}')


class TestTheDeadArmIsGone(unittest.TestCase):
    """Structural: these are the tests that fail before the fix."""

    def test_the_always_true_hasattr_guard_is_gone(self):
        self.assertNotIn("hasattr(self, 'DataMode')", _idoc_code(),
                         'the guard is always true because __init__ assigns DataMode, so it '
                         'only served to make the arm behind it unreachable')

    def test_the_max_based_guess_is_gone(self):
        code = _idoc_code()
        self.assertNotIn('math.log2(self.Max)', code,
                         'the bpp guess derived from peak intensity is back')

    def test_get_image_bpp_no_longer_reads_max(self):
        source_lines = _idoc_code().splitlines()
        start = next(i for i, line in enumerate(source_lines)
                     if 'def GetImageBpp(self)' in line)
        body = []
        for line in source_lines[start + 1:]:
            if line.strip().startswith('def ') or line.strip().startswith('@'):
                break
            body.append(line)

        self.assertNotIn('self.Max', '\n'.join(body),
                         'GetImageBpp should report the declared depth only, leaving the '
                         'fallback to the callers that can read the file')


class TestTheGuardReallyWasAlwaysTrue(unittest.TestCase):
    """Premise: why the arm was dead in the first place."""

    def test_a_fresh_idoc_always_has_the_attribute(self):
        self.assertTrue(hasattr(IDoc(), 'DataMode'),
                        '__init__ no longer assigns DataMode, so the original hasattr guard '
                        'would not have been dead code')

    def test_the_attribute_is_none_rather_than_absent(self):
        # This is the distinction the original guard got wrong: it tested for presence when
        # it meant to test for a value.
        self.assertIsNone(IDoc().DataMode)


class TestWhyRevivingItWouldBeWrong(unittest.TestCase):
    """The measurement that turned the issue's conclusion around."""

    def test_the_guess_is_only_right_for_a_saturating_section(self):
        self.assertEqual(16, math.ceil(math.log2(65535)))

    def test_the_guess_understates_the_depth_of_an_underexposed_section(self):
        # A 16-bit capture that never approaches saturation. Each of these would have been
        # reported as a shallower image than it is.
        for brightest, guessed in [(30000, 15), (16000, 14), (4000, 12), (1000, 10), (250, 8)]:
            self.assertEqual(guessed, math.ceil(math.log2(brightest)))
            self.assertLess(guessed, 16,
                            f'a 16-bit section peaking at {brightest} would be understated')

    def test_the_callers_have_a_better_fallback(self):
        code = _idoc_code()
        # SerialEMIDocImport.GetImageBpp and CalculateHistogram both read the depth off disk
        # when the idoc does not declare one. That is the path the removal defers to.
        self.assertIn('ImageBpp = GetImageBpp(SourceImageFullPath)', code,
                      'the importer no longer reads the depth from the first tile')
        self.assertIn('bpp = nornir_shared.images.GetImageBpp(listfilenames[0])', code,
                      'the histogram path no longer reads the depth from a tile')


class TestTheRealCorpus(unittest.TestCase):
    """Records what the corpus actually says, and why it did not expose the difference."""

    def setUp(self):
        if not os.environ.get('TESTINPUTPATH'):
            self.skipTest('TESTINPUTPATH is not set')
        self.paths = sorted(glob.glob(os.path.join(_CORPUS, '*', '*.idoc')))
        if not self.paths:
            self.skipTest(f'no idoc files under {_CORPUS}')

    def test_every_section_declares_its_depth(self):
        # So the removed arm would not have been consulted for this corpus even if it were
        # reachable. Worth pinning: it is the reason the corpus could not have caught this.
        for path in self.paths:
            idoc = IDoc.Load(path, usecache=False)
            self.assertEqual(6, idoc.DataMode, f'{path} no longer declares DataMode 6')
            self.assertEqual(16, idoc.GetImageBpp(), f'{path} reports an unexpected depth')

    def test_the_corpus_saturates_which_is_why_the_guess_looked_correct(self):
        for path in self.paths:
            idoc = IDoc.Load(path, usecache=False)
            self.assertEqual(65535, idoc.Max,
                             f'{path} no longer saturates, so the agreement measured for '
                             f'this corpus no longer holds')


if __name__ == '__main__':
    unittest.main()
