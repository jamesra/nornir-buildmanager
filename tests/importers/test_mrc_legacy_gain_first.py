"""Legacy (-NoCrush) MRC imports divide each tile by its brightness gain before the unchanged contrast stretch."""

from __future__ import annotations

import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from nornir_buildmanager.importers import mrc, shared
from nornir_buildmanager.importers.mrc import MRCImport
from nornir_buildmanager.volumemanager import VolumeManager
from nornir_imageregistration import noise_crush

_SECTION = os.path.join(os.environ.get('TESTINPUTPATH', ''), 'PlatformRaw', 'MRC', 'SmallMouse', '0001', '1.mrc')


class _FakeMrc:
    num_tiles = 1

    def get_tile_as_numpy_memmap(self, iTile: int) -> np.ndarray:
        return np.full((8, 8), 1000, dtype=np.uint16)


class TestLegacyExportAppliesTheGain(unittest.TestCase):

    def _export(self, gain: float) -> float:
        with tempfile.TemporaryDirectory() as out:
            MRCImport.ExportImage(_FakeMrc(), out, '.png', 0, shared.MinMaxGamma(0, 4000, 1.0), flip_ud=False,
                                  gain=gain)
            with Image.open(os.path.join(out, '000.png')) as im:
                return float(np.asarray(im).mean())

    def test_a_dim_tile_is_brought_up_by_its_gain(self) -> None:
        self.assertAlmostEqual(self._export(0.5) / self._export(1.0), 2.0, places=3)

    def test_the_gain_never_pushes_a_pixel_past_the_range(self) -> None:
        self.assertLessEqual(self._export(0.01), 65535)


@unittest.skipUnless(os.path.isfile(_SECTION), 'SmallMouse MRC not under TESTINPUTPATH')
class TestLegacyImportIsGainFirst(unittest.TestCase):

    def test_nocrush_import_marks_the_filter_gain_first_without_crush_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {'NORNIR_IMPORT_CACHE_PATH': os.path.join(tmp, 'cache')}):
            volume = VolumeManager.Load(os.path.join(tmp, 'volume'), Create=True)
            for node in mrc.MRCImport.ToMosaic(volume, _SECTION, (0.0001, 0.9999), NoCrush=True, CameraBpp=14):
                node.Save()
            channel = volume.find('Block').GetSection(1).GetChannel('TEM')
            filters = [f for f in channel.findall('Filter')]
            self.assertEqual(1, len(filters))
            node = filters[0]
            self.assertEqual('1', node.attrib.get('GainFirst'))
            self.assertTrue(node.attrib.get('GainsChecksum'))
            self.assertIsNone(noise_crush.CrushParams.from_attributes(node.attrib))
            self.assertIsNone(node.attrib.get(noise_crush.GAIN_FIRST_ATTRIB))


if __name__ == '__main__':
    unittest.main()
