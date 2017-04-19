'''
Created on Feb 14, 2013

@author: u0490822
'''
import os
import unittest

from nornir_buildmanager.operations.pruneobj import PruneObj
from nornir_imageregistration.files.mosaicfile import MosaicFile
import test.pipeline.setup_pipeline


class PruneTest(test.pipeline.setup_pipeline.ImportOnlySetup):

    @property
    def VolumePath(self):
        return "6750"

    @property
    def Platform(self):
        return "PMG"

    def LoadMetaData(self):
        self.ChannelNode = self.VolumeObj.find("Block/Section[@Number='2']/Channel");
        self.assertIsNotNone(self.ChannelNode);

        self.FilterNode = self.VolumeObj.find("Block/Section[@Number='2']/Channel/Filter");
        self.assertIsNotNone(self.FilterNode);

        self.TilePyramidNode = self.FilterNode.find("TilePyramid")
        self.assertIsNotNone(self.TilePyramidNode);

        self.LevelNode = self.TilePyramidNode.GetChildByAttrib('Level', 'Downsample', 1)
        self.assertIsNotNone(self.LevelNode);

        self.StageTransformNode = self.ChannelNode.GetChildByAttrib('Transform', 'Name', 'Stage')
        self.assertIsNotNone(self.StageTransformNode)

    def testPruneScoreGeneration(self):
        '''Figure out if we can change the prune cutoff and obtain a result'''

        # Load the prune data and adjust the cutoff, ensure the tiles have been removed and the volume rebuilds properly


        # filterObj = self.VolumeObj.find("Block/Section[@Number='2']/Channel[@Name='G']/Filter[@Name='Raw8']");
        # pruneObj = filterObj.find("Prune");
        self.LoadMetaData()

        PruneFileFullPath = os.path.join(self.FilterNode.FullPath, 'PruneData.txt');

        # Call calculate prune scores
        OutputFilterNode = PruneObj.CalculatePruneScores({}, self.FilterNode, 2, self.StageTransformNode, OutputFile='PruneData', Logger=self.Logger)
        self.assertIsNotNone(OutputFilterNode);

        PruneNode = OutputFilterNode.find('Prune');
        self.assertIsNotNone(PruneNode);

        PruneDataNode = PruneNode.find('Data');
        self.assertIsNotNone(PruneDataNode);
        self.assertTrue(os.path.exists(PruneDataNode.FullPath));

        pruneObj = PruneObj.ReadPruneMap(PruneDataNode.FullPath);
        self.assertIsNotNone(pruneObj);

        # Make sure we have an entry for every tile
        self.assertEqual(len(pruneObj.MapImageToScore), self.TilePyramidNode.NumberOfTiles);

        # Call again and make sure it does not regenerate the output
        OutputFilterNode = PruneObj.CalculatePruneScores({}, self.FilterNode, 2, self.StageTransformNode, OutputFile=PruneFileFullPath, Logger=self.Logger)
        self.assertIsNone(OutputFilterNode);

        # OK, try to see what .mosaic we get with the prune scores
        (TransformParent, PruneParent) = PruneObj.PruneMosaic({}, PruneNode=PruneNode, TransformNode=self.StageTransformNode, OutputTransformName='Prune', Logger=self.Logger)
        self.assertIsNotNone(TransformParent)
        self.assertIsNotNone(PruneParent)

        PruneTransform = TransformParent.GetChildByAttrib('Transform', 'Name', 'Prune')
        self.assertIsNotNone(PruneTransform)

        OriginalPruneTransformChecksum = PruneTransform.Checksum

        # Load the prune transform, make sure the number of tiles matches the number in the TilePyramid
        pruneMosaic = MosaicFile.Load(PruneTransform.FullPath)
        self.assertIsNotNone(pruneMosaic)

        self.assertEqual(pruneMosaic.NumberOfImages, self.TilePyramidNode.NumberOfTiles)

        del pruneMosaic

        # Run PruneMosaic again and make sure we get a None result
        Output = PruneObj.PruneMosaic({}, PruneNode=PruneNode, TransformNode=self.StageTransformNode, OutputTransformName='Prune', Logger=self.Logger)
        self.assertIsNone(Output)

        # OK, change the prune threshold in the meta-data, verify the prune.mosaic is updated
        Scores = []
        for f in pruneObj.MapImageToScore.keys():
            Scores.append(pruneObj.MapImageToScore[f])

        Scores.sort()

        MeanScore = Scores[len(Scores) / 2]

        NewThreshold = MeanScore - 0.01

        PruneNode.UserRequestedCutoff = NewThreshold  # Using int as a floor command

        (TransformParent, PruneParent) = PruneObj.PruneMosaic({}, PruneNode=PruneNode, TransformNode=self.StageTransformNode, OutputTransformName='Prune', Logger=self.Logger)
        self.assertIsNotNone(TransformParent)
        self.assertIsNotNone(PruneParent)

        PruneTransform = TransformParent.GetChildByAttrib('Transform', 'Name', 'Prune')
        self.assertIsNotNone(PruneTransform)

        # Load the prune transform, make sure the number of tiles matches the number in the TilePyramid
        pruneMosaic = MosaicFile.Load(PruneTransform.FullPath)
        self.assertIsNotNone(pruneMosaic)

        self.assertEqual(pruneMosaic.NumberOfImages, self.TilePyramidNode.NumberOfTiles / 2)

        # These transforms have a different number of images, no excuse for the transforms checksums to match
        self.assertNotEqual(OriginalPruneTransformChecksum, PruneTransform.Checksum)

        Output = PruneObj.PruneMosaic({}, PruneNode=PruneNode, TransformNode=self.StageTransformNode, OutputTransformName='Prune', Logger=self.Logger)
        self.assertIsNone(Output)


if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
