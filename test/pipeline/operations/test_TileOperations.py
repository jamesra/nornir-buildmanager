'''
Created on Feb 22, 2013

@author: u0490822
'''
import logging
import os
import shutil
import tempfile
import unittest

from test.pipeline.setup_pipeline import *

from nornir_buildmanager.VolumeManagerETree import *
import nornir_buildmanager.build as build
from nornir_buildmanager.operations.tile import *
from nornir_buildmanager.validation import transforms
import nornir_shared.files
import nornir_shared.misc


class EvaluateFilterTest(ImportOnlySetup):

    def runTest(self):
        self.FilterNode = self.VolumeObj.find("Block/Section[@Number='2']/Channel/Filter")
        self.assertIsNotNone(self.FilterNode)

        OutputFilterNode = Evaluate(Parameters={}, FilterNode=self.FilterNode, OutputImageName='min', Level=1, EvaluateSequenceArg='min')
        self.assertIsNotNone(OutputFilterNode)

        MinImageNode = OutputFilterNode.GetChildByAttrib('Image', 'Name', 'min')
        self.assertIsNotNone(MinImageNode)

        self.assertTrue(os.path.exists(MinImageNode.FullPath))

        # Do not run twice
        OutputFilterNode = Evaluate(Parameters={}, FilterNode=self.FilterNode, OutputImageName='min', Level=1, EvaluateSequenceArg='min')
        self.assertIsNone(OutputFilterNode)

        OutputFilterNode = Evaluate(Parameters={}, FilterNode=self.FilterNode, OutputImageName='max', Level=1, EvaluateSequenceArg='max')
        self.assertIsNotNone(OutputFilterNode)

        MaxImageNode = OutputFilterNode.GetChildByAttrib('Image', 'Name', 'max')
        self.assertIsNotNone(MaxImageNode)

        self.assertTrue(os.path.exists(MaxImageNode.FullPath))

        # Try correcting the tiles and creating a new filter
        ChannelNode = CorrectTiles(Parameters={}, FilterNode=self.FilterNode, ImageNode=MaxImageNode, OutputFilterName='ShadingCorrected', InvertSource=True, ComposeOperator=None)
        self.assertIsNotNone(ChannelNode)




class HistogramFilterTest(ImportOnlySetup):

    def runTest(self):
        self.ChannelData = self.VolumeObj.find("Block/Section[@Number='2']/Channel")
        self.assertIsNotNone(self.ChannelData)

        # Change the AutoLevelHint for a section and check that it regenerates
        self.InputFilterNode = self.ChannelData.find("Filter[@Name='Raw8']")
        self.assertIsNotNone(self.InputFilterNode)

        self.InputPyramidNode = self.InputFilterNode.find("TilePyramid")
        self.assertIsNotNone(self.InputPyramidNode)

        self.InputLevelNode = self.InputPyramidNode.find("Level[@Downsample='1']")
        self.assertIsNotNone(self.InputLevelNode)

        # Make sure the reported number of images matches the actual number of tiles on the disk
        ImagesOnDisk = glob.glob(os.path.join(self.InputLevelNode.FullPath, '*.png'))

        self.assertEqual(self.InputPyramidNode.NumberOfTiles, len(ImagesOnDisk), "Number of images on disk do not match meta-data")


        OutputPyramidNode = BuildTilePyramids(PyramidNode=self.InputPyramidNode, Levels=[2, 8, 64])
        self.assertIsNotNone(self.InputLevelNode)

        # Make sure the expected levels exist
        self.CheckThatLevelsExist(OutputPyramidNode, [1, 2, 8, 64])

        OutputPyramidNode = BuildTilePyramids(PyramidNode=self.InputPyramidNode, Levels=[2, 8, 64])
        self.assertIsNone(OutputPyramidNode)

        OutputPyramidNode = BuildTilePyramids(PyramidNode=self.InputPyramidNode, Levels=[4])
        self.assertIsNotNone(OutputPyramidNode)

        self.CheckThatLevelsExist(OutputPyramidNode, [1, 2, 4, 8, 64])


    def CheckThatLevelsExist(self, PyramidNode, ExpectedLevels):
        # Make sure the expected levels exist
        LevelNodes = PyramidNode.Levels

        self.assertEqual(len(LevelNodes), len(ExpectedLevels))

        for LNode in LevelNodes:
            # Make sure this level should exist
            self.assertTrue(LNode.Downsample in ExpectedLevels)

            ImagesOnDisk = glob.glob(os.path.join(LNode.FullPath, '*.png'))
            self.assertEqual(self.InputPyramidNode.NumberOfTiles, len(ImagesOnDisk), "Number of images on disk do not match meta-data")


class HistogramFilterTest(ImportOnlySetup):

    def runTest(self):
        self.ChannelData = self.VolumeObj.find("Block/Section[@Number='2']/Channel")
        self.assertIsNotNone(self.ChannelData)

        self.TransformNode = self.ChannelData.find("Transform[@Name='Stage']")
        self.assertIsNotNone(self.TransformNode)

        # Change the AutoLevelHint for a section and check that it regenerates
        self.InputFilterNode = self.ChannelData.find("Filter[@Name='Raw8']")
        self.assertIsNotNone(self.InputFilterNode)

        self.InputLevelNode = self.InputFilterNode.find("TilePyramid/Level[@Downsample='1']")
        self.assertIsNotNone(self.InputLevelNode)

        # The first time it is run we should get a filter node with a histogram
        OutputFilterNode = HistogramFilter(Parameters={}, FilterNode=self.InputFilterNode, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode)
        self.assertIsNotNone(OutputFilterNode)

        HistogramNode = OutputFilterNode.find("Histogram")
        self.assertIsNotNone(HistogramNode)
        self.assertIsNotNone(HistogramNode.DataNode)
        self.assertIsNotNone(HistogramNode.ImageNode)

        self.assertTrue(os.path.exists(HistogramNode.DataFullPath))
        self.assertTrue(os.path.exists(HistogramNode.ImageFullPath))

        # The second time it is run we should not regenerate a histogram and None should be returned
        SecondOutputFilterNode = HistogramFilter(Parameters={}, FilterNode=self.InputFilterNode, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode)
        self.assertIsNone(SecondOutputFilterNode)

        self.assertIsNotNone(HistogramNode)
        self.assertIsNotNone(HistogramNode.DataNode)
        self.assertIsNotNone(HistogramNode.ImageNode)


class AutoLevelHistogramTest(PrepareSetup):

    def LoadInputMetaData(self):

        self.ChannelData = self.VolumeObj.find("Block/Section[@Number='2']/Channel")

        self.TransformNode = self.ChannelData.find("Transform[@Name='Stage']")
        self.assertIsNotNone(self.TransformNode)

        # Change the AutoLevelHint for a section and check that it regenerates
        self.InputFilterNode = self.ChannelData.find("Filter[@Name='Raw8']")
        self.assertIsNotNone(self.InputFilterNode)

        self.InputLevelNode = self.InputFilterNode.find("TilePyramid/Level[@Downsample='1']")
        self.assertIsNotNone(self.InputLevelNode)

        self.HistogramNode = self.InputFilterNode.find("Histogram")
        self.assertIsNotNone(self.HistogramNode)

        self.AutoLevelHintNode = self.HistogramNode.find("AutoLevelHint")
        self.assertIsNotNone(self.AutoLevelHintNode)

    def LoadOutputMetaData(self, OutputFilterName):

        self.OutputFilterNode = self.ChannelData.find("Filter[@Name='" + OutputFilterName + "']")
        self.assertIsNotNone(self.OutputFilterNode)

        self.OutputLevelNode = self.OutputFilterNode.find("TilePyramid/Level[@Downsample='1']")
        self.assertIsNotNone(self.OutputLevelNode)

    def runTest(self):
        '''This test determines whether the userrequested attributes of the AutoLevelHint element are functioning'''
        OutputFilterName = 'LeveledTest'

        ManualMinValue = 40.0
        ManualMaxValue = 100.0

        self.LoadInputMetaData()

        # Calling the first time should generate tiles
        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNotNone(ChannelOutput)

        self.VolumeObj.Save()

        # Calling again with the same output should not regenerate the tiles
        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNone(ChannelOutput)

        self.LoadOutputMetaData(OutputFilterName)
        AutoMaxCutoff = self.OutputFilterNode.MaxIntensityCutoff
        AutoMinCutoff = self.OutputFilterNode.MinIntensityCutoff

        self.assertEqual(AutoMaxCutoff, 230.0, "Expected values for histogram have changed")
        self.assertEqual(AutoMinCutoff, 63.0, "Expected values for histogram have changed")

        AutoGamma = self.OutputFilterNode.Gamma

        # Test a max < min
        self.AutoLevelHintNode.UserRequestedMaxIntensityCutoff = ManualMinValue
        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNone(ChannelOutput)

        # Test the Max Cutoff value

        self.AutoLevelHintNode.UserRequestedMaxIntensityCutoff = ManualMaxValue
        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNotNone(ChannelOutput)

        self.LoadOutputMetaData(OutputFilterName)
        self.assertEqual(self.OutputFilterNode.MaxIntensityCutoff, ManualMaxValue)

        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNone(ChannelOutput)

        self.AutoLevelHintNode.UserRequestedMaxIntensityCutoff = None
        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNotNone(ChannelOutput)

        self.LoadOutputMetaData(OutputFilterName)

        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNone(ChannelOutput)

        self.assertEqual(self.OutputFilterNode.MaxIntensityCutoff, AutoMaxCutoff)

        # Test the Min Cutoff value

        self.AutoLevelHintNode.UserRequestedMinIntensityCutoff = ManualMinValue
        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNotNone(ChannelOutput)

        self.LoadOutputMetaData(OutputFilterName)
        self.assertEqual(self.OutputFilterNode.MinIntensityCutoff, ManualMinValue)

        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNone(ChannelOutput)

        self.AutoLevelHintNode.UserRequestedMinIntensityCutoff = None
        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNotNone(ChannelOutput)

        self.LoadOutputMetaData(OutputFilterName)
        self.assertEqual(self.OutputFilterNode.MinIntensityCutoff, AutoMinCutoff)

        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNone(ChannelOutput)

        # Test the gamma value

        self.AutoLevelHintNode.UserRequestedGamma = 1.2
        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNotNone(ChannelOutput)

        self.LoadOutputMetaData(OutputFilterName)
        self.assertEqual(self.OutputFilterNode.Gamma, self.AutoLevelHintNode.UserRequestedGamma)

        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNone(ChannelOutput)

        self.AutoLevelHintNode.UserRequestedGamma = None
        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNotNone(ChannelOutput)

        self.LoadOutputMetaData(OutputFilterName)
        self.assertEqual(self.OutputFilterNode.Gamma, AutoGamma)

        ChannelOutput = AutolevelTiles(Parameters={}, LevelNode=self.InputLevelNode, TransformNode=self.TransformNode, OutputFilterName=OutputFilterName)
        self.assertIsNone(ChannelOutput)

        self.VolumeObj.Save()

if __name__ == "__main__":
    # import syssys.argv = ['', 'Test.testName']
    unittest.main()
