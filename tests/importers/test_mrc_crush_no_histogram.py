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
            for node in mrc.MRCImport.ToMosaic(volume, _SECTION, (0.0001, 0.9999)):
                node.Save()  # as the pipeline does for each yielded node
        return volume, histogram

    @staticmethod
    def _filter(volume):
        """The crushed Quantized filter of section 1."""
        return volume.find('Block').GetSection(1).GetChannel('TEM').GetFilter('Quantized')

    def test_crushed_import_never_calls_calculate_histogram(self) -> None:
        volume, histogram = self._import()
        histogram.assert_not_called()
        self.assertIsNotNone(self._filter(volume))

    def test_code_span_comes_from_the_written_tiles(self) -> None:
        volume, _ = self._import()
        filter_node = self._filter(volume)
        self.assertIsNotNone(filter_node.CrushCodeSpan)
        self.assertGreater(filter_node.CrushCodeSpan, 1)

    def test_reimport_after_deleting_the_filter_saves_the_channel_link(self) -> None:
        """RC1, 2026-11-10: with the Quantized dir and its Filter_Link removed by hand, a re-import wrote the tiles but
        the channel XML never regained the link."""
        volume, _ = self._import()
        channel = volume.find('Block').GetSection(1).GetChannel('TEM')
        channel_xml = os.path.join(channel.FullPath, 'VolumeData.xml')
        shutil.rmtree(self._filter(volume).FullPath)
        with open(channel_xml, encoding='utf-8') as f:
            text = f.read()
        kept = [line for line in text.splitlines(keepends=True) if 'Path="Quantized"' not in line or 'Filter_Link' not in line]
        self.assertLess(len(kept), len(text.splitlines(keepends=True)), 'no Filter_Link line to remove')
        with open(channel_xml, 'w', encoding='utf-8') as f:
            f.write(''.join(kept))

        volume = VolumeManager.Load(os.path.join(self._dir, 'volume'))
        with mock.patch.object(mrc.MRCImport, 'CalculateHistogram', side_effect=AssertionError('histogram read')):
            for node in mrc.MRCImport.ToMosaic(volume, _SECTION, (0.0001, 0.9999)):
                node.Save()  # as the pipeline does for each yielded node
        with open(channel_xml, encoding='utf-8') as f:
            self.assertIn('Filter_Link', f.read())


if __name__ == '__main__':
    unittest.main()
