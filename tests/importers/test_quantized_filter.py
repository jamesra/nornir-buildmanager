"""Crushed imports write the ``Quantized`` filter and legacy imports stay in ``Raw8``/``Raw16``.

The two kinds no longer share a filter name, so one volume can hold sections of both (the old same-kind guard is
gone); AdjustContrast builds Leveled from either one so TEMAlign sees every section.
"""

from __future__ import annotations

import types
import unittest

from nornir_buildmanager.importers import shared
from nornir_buildmanager.operations import tile
from nornir_imageregistration import noise_crush

PARAMS = noise_crush.CrushParams(step=40, offset=0, bits=6, sigma=16.0, k=2.5, source_dtype="uint16")
WIDE = noise_crush.CrushParams(step=1, offset=0, bits=12, sigma=0.4, k=2.5, source_dtype="uint16")


def _channel(**filters: dict[str, str]) -> types.SimpleNamespace:
    """A channel holding the named filters (name -> attribute dict)."""
    return types.SimpleNamespace(
        GetFilter=lambda name: types.SimpleNamespace(attrib=filters[name]) if name in filters else None)


class TestQuantizedFilterName(unittest.TestCase):

    def test_crushed_filter_is_quantized(self) -> None:
        self.assertEqual(shared.CRUSHED_FILTER_NAME, 'Quantized')

    def test_legacy_raw8_in_the_channel_does_not_affect_gain_first(self) -> None:
        """A legacy Raw8 neighbour is not a crushed filter, whatever its attributes."""
        channel = _channel(Raw8={'MinIntensityCutoff': '1'})
        self.assertTrue(shared.GainFirstAllowed(channel))
        self.assertIsNone(shared.ExistingGainFirstParams(channel, None))

    def test_crushed_filter_without_gain_first_blocks_gain_first(self) -> None:
        self.assertFalse(shared.GainFirstAllowed(_channel(Quantized=PARAMS.to_attributes())))

    def test_gain_first_quantized_filter_is_reused(self) -> None:
        attrib = {**PARAMS.to_attributes(), noise_crush.GAIN_FIRST_ATTRIB: '1'}
        channel = _channel(Quantized=attrib)
        self.assertTrue(shared.GainFirstAllowed(channel))
        self.assertEqual(shared.ExistingGainFirstParams(channel, PARAMS.k), PARAMS)


class TestContrastStageBpp(unittest.TestCase):

    def test_crushed_codes_are_read_at_their_stored_depth(self) -> None:
        # bits is the width of the codes (6 here); the PNGs are stored as 8-bit, or 16-bit past 8 code bits.
        narrow = types.SimpleNamespace(attrib=PARAMS.to_attributes(), BitsPerPixel=6)
        wide = types.SimpleNamespace(attrib=WIDE.to_attributes(), BitsPerPixel=12)
        self.assertEqual(tile.ContrastStageBpp(narrow), 8)
        self.assertEqual(tile.ContrastStageBpp(wide), 16)

    def test_legacy_filter_keeps_its_own_depth(self) -> None:
        self.assertEqual(tile.ContrastStageBpp(types.SimpleNamespace(attrib={}, BitsPerPixel='16')), 16)
        self.assertIsNone(tile.ContrastStageBpp(types.SimpleNamespace(attrib={}, BitsPerPixel=None)))


if __name__ == '__main__':
    unittest.main()
