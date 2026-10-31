"""C13-B012: a .mosaic whose stored checksum predates the 2026-08-10 format change stays valid."""
import os
import tempfile
import unittest
from unittest import mock

import nornir_buildmanager.volumemanager as vm
from nornir_buildmanager.volumemanager.xfileelementwrapper import XFileElementWrapper
from nornir_imageregistration.files import mosaicfile
from nornir_shared import checksum

_MOSAIC = """format_version_number: 1
number_of_images: 2
pixel_spacing: 1
use_std_mask: 0
image:
000.png
Rigid2DTransform_double_2_2 vp 3 0 0 0 fp 2 0 0
image:
001.png
Rigid2DTransform_double_2_2 vp 3 0 0 4000 fp 2 0 0
"""


class TestMosaicLegacyChecksum(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'Grid.mosaic')
        with open(self.path, 'w') as f:
            f.write(_MOSAIC)
        mosaic = mosaicfile.MosaicFile.Load(self.path)
        self.new_checksum = mosaic.Checksum
        self.legacy_checksum = checksum.LegacyListChecksum(sorted(mosaic.ImageToTransformString.values()))

    def _validate(self, stored: str) -> tuple[bool, str, str]:
        node = vm.TransformNode.Create('Grid', 'Mosaic', Path='Grid.mosaic')
        node.attrib['Checksum'] = stored
        cls = type(node)
        with mock.patch.object(cls, 'FullPath', new_callable=mock.PropertyMock, return_value=self.path), \
                mock.patch.object(cls, 'FileSystemModifiedSinceLastValidation', new_callable=mock.PropertyMock,
                                  return_value=True), \
                mock.patch.object(cls, 'UpdateValidationTime'), \
                mock.patch.object(XFileElementWrapper, 'IsValid', return_value=(True, '')):
            valid, reason = node.IsValid()
        return valid, reason, node.attrib['Checksum']

    def test_formats_differ(self):
        self.assertNotEqual(self.new_checksum, self.legacy_checksum)

    def test_current_checksum_is_valid(self):
        valid, _, stored = self._validate(self.new_checksum)
        self.assertTrue(valid)
        self.assertEqual(stored, self.new_checksum)

    def test_legacy_checksum_is_valid_and_kept(self):
        valid, _, stored = self._validate(self.legacy_checksum)
        self.assertTrue(valid)
        self.assertEqual(stored, self.legacy_checksum)

    def test_wrong_checksum_is_invalid(self):
        valid, reason, _ = self._validate('0' * 32)
        self.assertFalse(valid)
        self.assertIn('does not match', reason)


if __name__ == '__main__':
    unittest.main()
