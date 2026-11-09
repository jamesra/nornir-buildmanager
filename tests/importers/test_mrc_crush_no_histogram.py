"""A crushed MRC import does not read the whole file to build a section histogram (RC1, 2026-11-08).

``CalculateHistogram`` memory-maps every tile from the source path; on RC1 that took 27 minutes for 14.7 GB (about
9 MB/s). Crushed tiles ignore the histogram, so the import takes the prune scale from the tiles it writes instead,
as the IDOC importer does.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

from nornir_buildmanager.importers import mrc
from nornir_buildmanager.volumemanager import VolumeManager

_SECTION = os.path.join(os.environ.get('TESTINPUTPATH', ''), 'PlatformRaw', 'MRC', 'SmallMouse', '0001', '1.mrc')


@unittest.skipUnless(os.path.isfile(_SECTION), 'SmallMouse MRC not under TESTINPUTPATH')
class TestCrushedImportSkipsTheHistogram(unittest.TestCase):

    def setUp(self) -> None:
        self._dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._dir, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, {'NORNIR_IMPORT_CACHE_PATH': os.path.join(self._dir, 'cache')})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _import(self):
        """Import SmallMouse section 1 crushed; any histogram read fails the import."""
        volume = VolumeManager.Load(os.path.join(self._dir, 'volume'), Create=True)
        with mock.patch.object(mrc.MRCImport, 'CalculateHistogram',
                               side_effect=AssertionError('histogram read')) as histogram:
            for _ in mrc.MRCImport.ToMosaic(volume, _SECTION, (0.0001, 0.9999)):
                pass
        return volume, histogram

    @staticmethod
    def _filter(volume):
        """The crushed Raw8 filter of section 1."""
        return volume.find('Block').GetSection(1).GetChannel('TEM').GetFilter('Raw8')

    def test_crushed_import_never_calls_calculate_histogram(self) -> None:
        volume, histogram = self._import()
        histogram.assert_not_called()
        self.assertIsNotNone(self._filter(volume))

    def test_code_span_comes_from_the_written_tiles(self) -> None:
        volume, _ = self._import()
        filter_node = self._filter(volume)
        self.assertIsNotNone(filter_node.CrushCodeSpan)
        self.assertGreater(filter_node.CrushCodeSpan, 1)


if __name__ == '__main__':
    unittest.main()
