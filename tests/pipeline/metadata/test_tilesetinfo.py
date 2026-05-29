"""
Created on Oct 26, 2017

@author: u0490822
"""
import os
import importlib.util
import sys
import unittest
from pathlib import Path
import nornir_buildmanager.metadata.tilesetinfo


def _load_local_testbase_module():
    module_name = "nornir_buildmanager_tests_testbase"
    existing = sys.modules.get(module_name, None)
    if existing is not None:
        return existing

    module_path = Path(__file__).resolve().parents[2] / "testbase.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create import spec for {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


testbase = _load_local_testbase_module()


class TestTilesetInfo(testbase.TestBase):

    def testTilesetInfo(self):
        OutputFileName = "TestTileInfo.xml"
        OutputFileFullPath = os.path.join(self.TestOutputPath, OutputFileName)

        info = nornir_buildmanager.metadata.tilesetinfo.TilesetInfo()

        info.Downsample = 1
        info.FilePostfix = None
        info.FilePrefix = None
        info.GridDimX = 10
        info.GridDimY = 5
        info.TileDimX = 256
        info.TileDimY = 512

        info.Save(OutputFileFullPath)

        self.assertTrue(os.path.exists(OutputFileFullPath))

        loadedInfo = nornir_buildmanager.metadata.tilesetinfo.TilesetInfo.Load(OutputFileFullPath)

        self.assertEqual(info.Downsample, loadedInfo.Downsample, "Downsample mismatch on reload")
        self.assertEqual(info.GridDimX, loadedInfo.GridDimX, "GridDimX mismatch on reload")
        self.assertEqual(info.GridDimY, loadedInfo.GridDimY, "GridDimY mismatch on reload")
        self.assertEqual(info.TileDimX, loadedInfo.TileDimX, "TileDimX mismatch on reload")
        self.assertEqual(info.TileDimY, loadedInfo.TileDimY, "TileDimY mismatch on reload")
        self.assertEqual(info.FilePostfix, loadedInfo.FilePostfix, "FilePostfix mismatch on reload")
        self.assertEqual(info.FilePrefix, loadedInfo.FilePrefix, "FilePrefix mismatch on reload")


if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testTilesetInfo']
    unittest.main()
