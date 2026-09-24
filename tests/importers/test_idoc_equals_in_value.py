"""IDoc.Load must keep the full value side of key=value lines (#247).

``line.split('=')`` with no maxsplit truncates any value that itself contains ``=``.
The RC2_4Square corpus has eight ``T = ... Tilt axis angle = ...`` lines that lose text
today; a SerialEM note with ``=`` would be recorded mangled into VolumeData.xml.
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


class TestIdocEqualsInValue(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.mkdtemp()

    def load(self, text: str) -> IDoc:
        path = os.path.join(self._directory, 'probe.idoc')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
        return IDoc.Load(path, usecache=False)

    def test_serialem_note_keeps_equals_in_value(self):
        text = _MINIMAL.replace(
            'DataMode = 6',
            'DataMode = 6\nT = SerialEM: gain=on mode=super',
        )
        idoc = self.load(text)
        self.assertEqual('gain=on mode=super', idoc.note)

    def test_tilt_axis_t_line_does_not_truncate_at_first_equals(self):
        """Corpus shape: value starts after the first '=', so maxsplit must keep the rest."""
        text = _MINIMAL.replace(
            'DataMode = 6',
            'DataMode = 6\nT =     Tilt axis angle = 91.7, binning = 1  spot = 2  camera = 0',
        )
        idoc = self.load(text)
        # Not a SerialEM note line, so note stays unset — but the parser must not crash
        # and must still load the tile that follows.
        self.assertIsNone(getattr(idoc, 'note', None))
        self.assertEqual(1, idoc.NumTiles)

    def test_numeric_attributes_still_parse_with_maxsplit(self):
        idoc = self.load(_MINIMAL)
        self.assertEqual(4080, idoc.ImageSize[0])
        self.assertEqual(1, idoc.NumTiles)
