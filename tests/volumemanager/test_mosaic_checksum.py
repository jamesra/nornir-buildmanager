"""Tests for MosaicBaseNode checksum caching.

The lazily-computed checksum was written straight into attrib without setting
_AttributesChanged, so it never reached VolumeData.xml, and a missing file
stored None, which breaks XML serialization.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from xml.etree import ElementTree

from nornir_buildmanager.volumemanager.mosaicbasenode import MosaicBaseNode

MOSAIC_TEXT = """number_of_images: 2
pixel_spacing: 1
use_std_mask: 0
image: 1.png LegendrePolynomialTransform_double_2_2_1 vp 6 1 0 1 1 1 0 fp 4 0 0 2040 2040
image: 2.png LegendrePolynomialTransform_double_2_2_1 vp 6 1 0 1 1 1 0 fp 4 100 0 2040 2040
"""


class _StandaloneMosaicNode(MosaicBaseNode):
    """A mosaic node whose FullPath does not depend on a parent volume."""

    def __init__(self, full_path: str, **kwargs):
        super().__init__(tag='Transform',
                         attrib={'Path': os.path.basename(full_path),
                                 'Name': 'test',
                                 'Type': 'test'},
                         **kwargs)
        self._full_path = full_path

    @property
    def FullPath(self) -> str:
        return self._full_path


class TestMosaicChecksumCaching(unittest.TestCase):
    """A computed checksum has to be marked dirty so it survives the save."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.mkdtemp(prefix='nornir-mosaic-checksum-')
        self.addCleanup(lambda: shutil.rmtree(self._temp_dir, ignore_errors=True))
        self.mosaic_path = os.path.join(self._temp_dir, 'test.mosaic')

    def _write_mosaic(self, text: str = MOSAIC_TEXT) -> None:
        with open(self.mosaic_path, 'w') as handle:
            handle.write(text)

    def _node(self) -> _StandaloneMosaicNode:
        node = _StandaloneMosaicNode(self.mosaic_path)
        node._AttributesChanged = False
        return node

    def test_computed_checksum_marks_attributes_changed(self):
        """Without the flag the value is dropped on the next save."""
        self._write_mosaic()
        node = self._node()

        checksum = node.Checksum

        self.assertTrue(checksum)
        self.assertEqual(node.attrib['Checksum'], checksum)
        self.assertTrue(node._AttributesChanged)

    def test_cached_checksum_is_reused(self):
        self._write_mosaic()
        node = self._node()

        first = node.Checksum
        node._AttributesChanged = False
        second = node.Checksum

        self.assertEqual(first, second)
        self.assertFalse(node._AttributesChanged,
                         'a cache hit must not dirty the element')

    def test_missing_file_does_not_store_none(self):
        """None in attrib raises TypeError when the XML is serialized."""
        node = self._node()

        checksum = node.Checksum

        self.assertEqual(checksum, '')
        self.assertNotIn('Checksum', node.attrib)
        for value in node.attrib.values():
            self.assertIsInstance(value, str)

    def test_missing_file_keeps_element_serializable(self):
        node = self._node()

        node.Checksum  # noqa: B018 - reading is what used to poison attrib

        element = ElementTree.Element(node.tag, attrib=dict(node.attrib))
        ElementTree.tostring(element)

    def test_reset_checksum_clears_stale_value_when_file_missing(self):
        """A stale checksum must not outlive the file it describes."""
        self._write_mosaic()
        node = self._node()
        self.assertTrue(node.Checksum)

        os.remove(self.mosaic_path)
        node.ResetChecksum()

        self.assertNotIn('Checksum', node.attrib)
        self.assertTrue(node._AttributesChanged)

    def test_reset_checksum_recomputes_after_content_change(self):
        self._write_mosaic()
        node = self._node()
        original = node.Checksum

        changed = MOSAIC_TEXT.replace('fp 4 100 0', 'fp 4 250 0')
        self._write_mosaic(changed)
        node.ResetChecksum()

        self.assertNotEqual(node.Checksum, original)
        self.assertTrue(node._AttributesChanged)


if __name__ == '__main__':
    unittest.main()
