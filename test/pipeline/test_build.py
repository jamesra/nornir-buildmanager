'''
Created on Feb 22, 2013

@author: u0490822
'''
import glob
import unittest

from setup_pipeline import *


class PrepareThenMosaicTest(PipelineTest):
    '''Run the build with prepare, then run again with mosiac'''

    TransformNames = ["translate", "grid", "zerogrid", "stage"]

    def CheckTransformsExist(self, VolumeObj, TransformNames=None):

        if TransformNames is None:
            TransformNames = PrepareThenMosaicTest.TransformNames

        ChannelNode = VolumeObj.find("Block/Section/Channel")
        self.assertIsNotNone(ChannelNode)

        for tname in TransformNames:
            TransformNode = ChannelNode.GetChildByAttrib("Transform", "Name", tname)
            self.assertIsNotNone(ChannelNode)


    def TileFiles(self, tilesetNode, downsample):
        levelNode = tilesetNode.GetLevel(downsample)
        self.assertIsNotNone(levelNode)

        files = glob.glob(os.path.join(levelNode.FullPath, "*" + tilesetNode.FilePostfix))
        self.assertGreater(len(files), 0, "Missing tiles")

        return files


    def CheckTilesetExists(self, VolumeObj):

        TilesetNode = VolumeObj.find("Block/Section/Channel/Filter/Tileset")
        self.assertIsNotNone(TilesetNode)

        FullResTiles = self.TileFiles(TilesetNode, 1)
        DSTwoTiles = self.TileFiles(TilesetNode, 2)
        self.assertGreaterEqual(len(DSTwoTiles), len(FullResTiles) / 4, "Downsample level seems to be missing assembled tiles")

        FullResTiles.sort()
        DSTwoTiles.sort()

        self.assertEqual(os.path.basename(FullResTiles[0]),
                         os.path.basename(DSTwoTiles[0]),
                         "Tiles at different downsample levels should use the same naming convention")


    def runTest(self):
        # Import the files
        buildArgs = ['Build.py', '-input', self.TestDataSource, '-volume', self.VolumeDir, '-pipeline', 'TEMPrepare', '-debug'];
        build.Execute(buildArgs);

        self.assertTrue(os.path.exists(self.VolumeDir), "Test input was not copied");

        buildArgs = ['Build.py', '-input', self.TestDataSource, '-volume', self.VolumeDir, '-pipeline', 'AdjustContrast', '-debug'];
        build.Execute(buildArgs);

        self.assertTrue(os.path.exists(self.VolumeDir), "Test input was not copied");

        buildArgs = ['Build.py', '-volume', self.VolumeDir, '-pipeline', 'TEMMosaic', '-debug'];
        build.Execute(buildArgs);

        VolumeObj = VolumeManager.Load(self.VolumeDir)
        self.CheckTransformsExist(VolumeObj)

        buildArgs = ['Build.py', '-volume', self.VolumeDir, '-pipeline', 'AssembleTiles', '-debug'];
        build.Execute(buildArgs);

        # Load the meta-data from the volumedata.xml file
        VolumeObj = VolumeManager.Load(self.VolumeDir)

        self.CheckTilesetExists(VolumeObj)

if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()