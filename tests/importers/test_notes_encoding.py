"""TryAddNotes records the codec actually used to decode notes (#246)."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from xml.sax.saxutils import escape

from nornir_buildmanager.importers.shared import TryAddNotes, _read_notes_text_with_encoding
from nornir_buildmanager.volumemanager import BlockNode, ChannelNode, VolumeManager


class TestReadNotesEncoding(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self._directory, ignore_errors=True))

    def test_utf8_file_labelled_utf8(self) -> None:
        path = os.path.join(self._directory, 'utf8.txt')
        with open(path, 'wb') as handle:
            handle.write('café'.encode('utf-8'))
        text, encoding = _read_notes_text_with_encoding(path)
        self.assertEqual('utf-8', encoding)
        self.assertEqual('café', text)

    def test_cp1252_only_bytes_labelled_cp1252(self) -> None:
        path = os.path.join(self._directory, 'cp1252.txt')
        # 0xE9 is é in cp1252 and is not valid UTF-8 alone.
        with open(path, 'wb') as handle:
            handle.write(b'caf\xe9')
        text, encoding = _read_notes_text_with_encoding(path)
        self.assertEqual('cp1252', encoding)
        self.assertEqual('café', text)


class TestTryAddNotesEncodingAttribute(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.mkdtemp(prefix='nornir-notes-enc-')
        self.addCleanup(lambda: shutil.rmtree(self._temp_dir, ignore_errors=True))
        volume = VolumeManager.Load(os.path.join(self._temp_dir, 'volume'), Create=True)
        assert volume is not None
        [_a, block] = volume.UpdateOrAddChildByAttrib(BlockNode.Create('TEM'), 'Name')
        [_b, section] = block.GetOrCreateSection(9)
        [_c, self.channel] = section.UpdateOrAddChildByAttrib(ChannelNode.Create('TEM'), 'Name')
        self.source_dir = os.path.join(self._temp_dir, '0009_Source')
        os.makedirs(self.source_dir, exist_ok=True)

    def test_notes_node_encoding_matches_decode(self) -> None:
        notes_path = os.path.join(self.source_dir, 'notes.txt')
        with open(notes_path, 'wb') as handle:
            handle.write(b'caf\xe9 scope')
        self.assertTrue(TryAddNotes(self.channel, self.source_dir, None))
        notes_node = self.channel.find('Notes')
        self.assertIsNotNone(notes_node)
        self.assertEqual('cp1252', notes_node.attrib.get('encoding'))
        self.assertEqual(escape('café scope'), notes_node.text)


if __name__ == '__main__':
    unittest.main()
