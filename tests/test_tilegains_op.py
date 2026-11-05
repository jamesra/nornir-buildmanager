"""TileGains operation: the import-time guards (crushed filters only, skip when the gains are current)."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from nornir_buildmanager.operations import tilegains

CRUSH_ATTRIBUTES = {'CrushVersion': '1', 'CrushK': '2.5', 'CrushStep': '200', 'CrushSigma': '80.0', 'CrushOffset': '0',
                    'CrushBits': '8', 'CrushSourceDtype': 'uint16'}


class _Filter(SimpleNamespace):
    @property
    def HasTilePyramid(self) -> bool:
        raise AssertionError('the guard should have returned before the tile pyramid is looked at')


class TestTileGainsGuards(unittest.TestCase):

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.transform = SimpleNamespace(Name='Stage', attrib={'Checksum': 'abc'})

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_legacy_filter_is_skipped_when_crushed_only(self) -> None:
        legacy = _Filter(attrib={'MinIntensityCutoff': '1', 'MaxIntensityCutoff': '2'}, FullPath=self.folder.name)
        self.assertIsNone(tilegains.TileGains(self.transform, legacy, Downsample='1', CrushedOnly='True'))

    def test_current_gains_are_skipped(self) -> None:
        with open(os.path.join(self.folder.name, 'TileGains_Stage.json'), 'w') as f:
            json.dump({'gains': {}}, f)
        crushed = _Filter(attrib=dict(CRUSH_ATTRIBUTES, TileGainsSource='Stage:abc'), FullPath=self.folder.name)
        self.assertIsNone(tilegains.TileGains(self.transform, crushed, Downsample='1', CrushedOnly='True',
                                              SkipIfCurrent='True'))

    def test_missing_stage_transform_is_skipped(self) -> None:
        crushed = _Filter(attrib=dict(CRUSH_ATTRIBUTES), FullPath=self.folder.name)
        self.assertIsNone(tilegains.TileGains(None, crushed, Downsample='1', CrushedOnly='True'))

    def test_string_flags(self) -> None:
        self.assertTrue(tilegains._as_bool('True'))
        self.assertFalse(tilegains._as_bool('False'))
        self.assertFalse(tilegains._as_bool(False))


if __name__ == '__main__':
    unittest.main()
