"""``TryAddHistogram`` copies Histogram.xml as the data node, not the image (2026-11-05).

The XML branch copied ``Histogram.png`` under the XML's name, so a source folder holding only
``Histogram.xml`` (the plot written elsewhere, e.g. under NORNIR_IMPORT_CACHE_PATH) failed the whole
ImportIDoc with FileNotFoundError after every tile had been written (RC2_Micro/17, -NoCrush).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from nornir_buildmanager.importers.shared import TryAddHistogram
from nornir_buildmanager.volumemanager import BlockNode, ChannelNode, FilterNode, VolumeManager


class TestTryAddHistogram(unittest.TestCase):

    def setUp(self) -> None:
        self._temp_dir = tempfile.mkdtemp(prefix='nornir-tryadd-histogram-')
        self.addCleanup(lambda: shutil.rmtree(self._temp_dir, ignore_errors=True))
        volume = VolumeManager.Load(os.path.join(self._temp_dir, 'volume'), Create=True)
        assert volume is not None
        [_a, block] = volume.UpdateOrAddChildByAttrib(BlockNode.Create('TEM'), 'Name')
        [_b, section] = block.GetOrCreateSection(17)
        [_c, channel] = section.UpdateOrAddChildByAttrib(ChannelNode.Create('TEM'), 'Name')
        [_d, self.filter] = channel.UpdateOrAddChildByAttrib(FilterNode.Create(Name='Raw8'), 'Name')
        os.makedirs(self.filter.FullPath, exist_ok=True)
        self.source_dir = os.path.join(self._temp_dir, 'source')
        os.makedirs(self.source_dir, exist_ok=True)

    def _write(self, name: str, text: str) -> None:
        with open(os.path.join(self.source_dir, name), 'w', encoding='utf-8') as f:
            f.write(text)

    def _copied(self, name: str) -> str:
        with open(os.path.join(self.filter.FullPath, name), encoding='utf-8') as f:
            return f.read()

    def test_xml_only_is_copied_as_xml(self) -> None:
        self._write('Histogram.xml', '<Histogram/>')
        TryAddHistogram(self.filter, self.source_dir, image_ext='.png')
        self.assertEqual(self._copied('RawDataHistogram.xml'), '<Histogram/>')
        self.assertFalse(os.path.exists(os.path.join(self.filter.FullPath, 'RawDataHistogram.png')))

    def test_image_and_xml_each_keep_their_own_content(self) -> None:
        self._write('Histogram.xml', '<Histogram/>')
        self._write('Histogram.png', 'png bytes')
        TryAddHistogram(self.filter, self.source_dir, image_ext='.png')
        self.assertEqual(self._copied('RawDataHistogram.xml'), '<Histogram/>')
        self.assertEqual(self._copied('RawDataHistogram.png'), 'png bytes')
