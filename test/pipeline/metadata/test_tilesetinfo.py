'''
Created on Oct 26, 2017

@author: u0490822
'''
import unittest
import os

import nornir_buildmanager.metadata.tilesetinfo
import test.testbase


class TestTilesetInfo(test.testbase.TestBase):
 
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
