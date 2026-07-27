"""Tests for resilient VolumeData.xml save behavior."""

from __future__ import annotations

import os
import unittest
from unittest import mock
from xml.etree import ElementTree

from nornir_buildmanager.volumemanager.xcontainerelementwrapper import XContainerElementWrapper
from nornir_buildmanager.volumemanager.xelementwrapper import XElementWrapper


class _TestContainer(XContainerElementWrapper):
    """Minimal concrete container for exercising save paths."""

    def __init__(self, full_path: str, tag: str = 'TestContainer') -> None:
        path_name = os.path.basename(full_path.rstrip(os.sep))
        super().__init__(tag, attrib={'Path': path_name})
        self._full_path = full_path
        os.makedirs(full_path, exist_ok=True)

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


class TestDuplicateLinkCleanup(unittest.TestCase):
    """Saving must drop same-Path link duplicates from the in-memory tree."""

    def setUp(self) -> None:
        import shutil
        import tempfile

        self._temp_dir = tempfile.mkdtemp(prefix='nornir-dup-link-')
        self.addCleanup(lambda: shutil.rmtree(self._temp_dir, ignore_errors=True))
        self._parent = _TestContainer(self._temp_dir)
        self._child_dir = os.path.join(self._temp_dir, 'TEM')

    def _same_path_children(self) -> list[ElementTree.Element]:
        return [
            child for child in list(self._parent)
            if child.attrib.get('Path') == 'TEM'
            and (child.tag == 'TestContainer' or child.tag == 'TestContainer_Link')
        ]

    def test_two_loaded_containers_same_path_keeps_one(self) -> None:
        first = _TestContainer(self._child_dir)
        second = _TestContainer(self._child_dir)
        self._parent.append(first)
        self._parent.append(second)
        self._parent.ChildrenChanged = True

        self._parent._Save(recurse=True)

        remaining = self._same_path_children()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].tag, 'TestContainer')

        saved = ElementTree.parse(os.path.join(self._temp_dir, 'VolumeData.xml')).getroot()
        links = saved.findall("TestContainer_Link[@Path='TEM']")
        self.assertEqual(len(links), 1)

    def test_stub_then_loaded_prefers_loaded(self) -> None:
        stub = XElementWrapper('TestContainer_Link', attrib={'Path': 'TEM'})
        loaded = _TestContainer(self._child_dir)
        self._parent.append(stub)
        self._parent.append(loaded)
        self._parent.ChildrenChanged = True

        self._parent._Save(recurse=True)

        remaining = self._same_path_children()
        self.assertEqual(len(remaining), 1)
        self.assertIs(remaining[0], loaded)

        saved = ElementTree.parse(os.path.join(self._temp_dir, 'VolumeData.xml')).getroot()
        links = saved.findall("TestContainer_Link[@Path='TEM']")
        self.assertEqual(len(links), 1)

    def test_loaded_then_stub_prefers_loaded(self) -> None:
        loaded = _TestContainer(self._child_dir)
        stub = XElementWrapper('TestContainer_Link', attrib={'Path': 'TEM'})
        self._parent.append(loaded)
        self._parent.append(stub)
        self._parent.ChildrenChanged = True

        self._parent._Save(recurse=True)

        remaining = self._same_path_children()
        self.assertEqual(len(remaining), 1)
        self.assertIs(remaining[0], loaded)

        saved = ElementTree.parse(os.path.join(self._temp_dir, 'VolumeData.xml')).getroot()
        links = saved.findall("TestContainer_Link[@Path='TEM']")
        self.assertEqual(len(links), 1)


if __name__ == '__main__':
    unittest.main()
