"""Tests for resilient VolumeData.xml save behavior."""

from __future__ import annotations

import os
import unittest
from unittest import mock
from xml.etree import ElementTree

from nornir_buildmanager.volumemanager.xcontainerelementwrapper import XContainerElementWrapper


class _TestContainer(XContainerElementWrapper):
    """Minimal concrete container for exercising __SaveXML."""

    def __init__(self, full_path: str) -> None:
        super().__init__('TestContainer', attrib={'Path': os.path.basename(full_path)})
        self._full_path = full_path

    @property
    def FullPath(self) -> str:
        return self._full_path


class TestSaveXml(unittest.TestCase):
    """VolumeData.xml backup and atomic write behavior."""

    def setUp(self) -> None:
        import shutil
        import tempfile

        self._temp_dir = tempfile.mkdtemp(prefix='nornir-save-xml-')
        self.addCleanup(lambda: shutil.rmtree(self._temp_dir, ignore_errors=True))
        self._container = _TestContainer(self._temp_dir)
        self._element = ElementTree.Element('Volume', attrib={'Name': 'test'})

    def test_atomic_write_creates_volume_data(self) -> None:
        """New saves write valid XML via temp + replace."""
        self._container._XContainerElementWrapper__SaveXML('VolumeData.xml', self._element)

        xml_path = os.path.join(self._temp_dir, 'VolumeData.xml')
        self.assertTrue(os.path.isfile(xml_path))
        self.assertGreater(os.path.getsize(xml_path), 0)
        self.assertFalse(os.path.exists(xml_path + '.tmp'))

    def test_backup_failure_does_not_block_write(self) -> None:
        """CIFS-style backup rename failure still writes new metadata."""
        import nornir_buildmanager.volumemanager.xcontainerelementwrapper as xce

        xml_path = os.path.join(self._temp_dir, 'VolumeData.xml')
        with open(xml_path, 'wb') as handle:
            handle.write(b'<Volume Name="old"/>')

        original_replace = os.replace
        replace_calls = {'count': 0}

        def fake_replace(src: str, dst: str) -> None:
            replace_calls['count'] += 1
            if replace_calls['count'] == 1:
                raise FileNotFoundError('stale CIFS stat')
            return original_replace(src, dst)

        with mock.patch.object(xce.os, 'replace', side_effect=fake_replace):
            self._container._XContainerElementWrapper__SaveXML('VolumeData.xml', self._element)

        self.assertEqual(replace_calls['count'], 2)
        with open(xml_path, 'rb') as handle:
            saved = handle.read()
        self.assertIn(b'Name="test"', saved)
        self.assertNotIn(b'Name="old"', saved)


if __name__ == '__main__':
    unittest.main()
