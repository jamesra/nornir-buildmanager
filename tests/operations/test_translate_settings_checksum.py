"""TranslateTransform must not write path-string settings checksums (#219)."""
from __future__ import annotations

import inspect
import unittest

from nornir_buildmanager.operations import registration


class TestTranslateTransformChecksumWrites(unittest.TestCase):
    def test_dead_path_checksum_writes_are_gone(self) -> None:
        source = inspect.getsource(registration.TranslateTransform)
        self.assertNotIn('TranslateSettingsChecksum =', source)
        self.assertNotIn('ManualMosaicOffsetsChecksum =', source)
        self.assertNotIn('DataChecksum(settings_data_node.FullPath)', source)
        self.assertNotIn('DataChecksum(manual_offsets_data_node.FullPath)', source)
        self.assertIn('ResetChecksum', source)


if __name__ == '__main__':
    unittest.main()
