'''
Created on Apr 25, 2013

@author: u0490822
'''
import glob
import unittest

from nornir_buildmanager.operations.block import *
import nornir_buildmanager.volumemanager.blocknode
from pipeline.setup_pipeline import CopySetupTestBase, EmptyVolumeTestBase
import pipeline.test_sectionimage as test_sectionimage


def FetchStosTransform(test, VolumeObj, groupName: str, ControlSection, MappedSection):
    groupNode = VolumeObj.find("Block/StosGroup[@Name='" + groupName + "']")
    test.assertIsNotNone(groupNode, "Could not find StosGroup " + groupName)

    sectionMappingNode = groupNode.GetSectionMapping(MappedSection)
    test.assertIsNotNone(sectionMappingNode, "Could not find SectionMappings for " + str(MappedSection))

    Transform = sectionMappingNode.GetChildByAttrib("Transform", 'ControlSectionNumber', str(ControlSection))
    test.assertIsNotNone(Transform, "Could not find Transform for %d -> %d" % (MappedSection, ControlSection))

    return Transform


#
# class SliceToSliceRegistrationMosaicToVolume(CopySetupTestBase):
#
#     @property
#     def Platform(self):
#         '''Input for this test is a cached copy of the SliceToSliceRegistrationSkipBrute test.  If the output
#         of that test changes the new output must be manually copied to the test platform directory.'''
#
#         return "SliceToSliceRegistrationSkipBrute"
#
#     def testMosaicToVolume(self):
#         buildArgs = ['Build.py', self.TestOutputPath, \
#                      '-pipeline', 'MosaicToVolume', \
#                      '-debug', \
#                      '-InputTransform', 'Grid', \
#                      '-OutputTransform', 'ChannelToVolume',
#                      '-Channels', 'LeveledShading.*']
#
#         self.VolumeObj = self.RunBuild(buildArgs)
#
#         TransformNode = self.VolumeObj.find("Block/Section/Channel/Transform[@Name='ChannelToVolume']")
#         self.assertIsNotNone(TransformNode, "Stos pipeline did not complete")


class SliceToSliceRegistrationBruteOnlyTest(test_sectionimage.ImportLMImages):

    @property
    def VolumePath(self):
        return "6872_small"

    def testAlignSectionsPipeline(self):
        # Import the files
        buildArgs = [self.TestOutputPath, '-debug', 'AlignSections', \
                     '-Downsample', '16', \
                     '-Center', '5', \
                     '-Channels', 'LeveledShading.*']
        VolumeObj = self.RunBuild(buildArgs)

        StosMapNode = VolumeObj.find("Block/StosMap")
        self.assertIsNotNone(StosMapNode, "Stos pipeline did not complete")

        StosGroupNode = VolumeObj.find("Block/StosGroup")
        self.assertIsNotNone(StosGroupNode, "Stos pipeline did not complete")


class SliceToSliceRegistrationSkipBrute(CopySetupTestBase):

    @property
    def Platform(self):
        '''Input for this test is a cached copy of the SliceToSliceRegistrationBruteOnlyTest test.  If the output
        of that test changes the new output must be manually copied to the test platform directory.'''
        return "PMG"

    @property
    def VolumePath(self):
        return "SliceToSliceRegistrationSkipBrute"

    def setUp(self):
        super(SliceToSliceRegistrationSkipBrute, self).setUp()

    def InjectManualStosFiles(self, StosGroup, TargetDir):

        stosFiles = glob.glob(os.path.join(self.ImportedDataPath, StosGroup, "Manual", "*.stos"))
        numSourceFiles = len(stosFiles)
        self.assertTrue(numSourceFiles > 0, "Could not locate manual registration stos files for test")

        for stosFile in stosFiles:
            shutil.copy(stosFile, os.path.join(TargetDir, os.path.basename(stosFile)))

        targetFiles = glob.glob(os.path.join(TargetDir, "*.stos"))
        numTargetFiles = len(targetFiles)

        self.assertEqual(numSourceFiles, numTargetFiles)

    def ValidateTransforms(self, AutoOutputTransform, AutoInputTransform, ManualOutputTransform=None,
                           ManualInputTransform=None):
        '''ManualInputTransform can be none after the first refine call since the manual override file does not have a <transform> node.
           On the second pass or later ManualInputTransform refers to the transform that should have replaced the original output in the earlier pass'''

        self.assertIsNotNone(AutoInputTransform, msg='Parameter is required to not be none, missing output?')
        self.assertIsNotNone(AutoOutputTransform, msg='Parameter is required to not be none, missing output?')

        self.assertEqual(AutoOutputTransform.InputTransformChecksum,
                         AutoInputTransform.Checksum,
                         "Output transform InputTransformChecksum from automatic input should match checksum of automatic input transform")

        if ManualOutputTransform is None:
            return

        self.assertNotEqual(ManualOutputTransform.InputTransformChecksum,
                            AutoInputTransform.Checksum,
                            "Output transform InputTransformChecksum from automatic input should match checksum of automatic input transform")

        self.assertNotEqual(AutoInputTransform.Checksum,
                            ManualOutputTransform.Checksum,
                            "Manual input Transform checksum should not match automatic input.  Test is invalid because output is not regenerated when checksums match.")

        self.assertNotEqual(AutoOutputTransform.Checksum,
                            ManualOutputTransform.Checksum,
                            "Output transform checksum with manual input should not match result from automatic input.  Possible but extremely unlikely.")

        self.assertNotEqual(AutoOutputTransform.InputTransformChecksum,
                            ManualOutputTransform.InputTransformChecksum,
                            "Output transform InputTransformChecksum of manual input should be different from automatic input")

        if ManualInputTransform is None:
            return

        self.assertNotEqual(ManualInputTransform.Checksum,
                            AutoInputTransform.Checksum,
                            "Transform from earlier pass should have a different checksum compared to automatic input")

        self.assertEqual(ManualOutputTransform.InputTransformChecksum,
                         ManualInputTransform.Checksum,
                         "Output manual transform InputTransformChecksum should match input transform checksum")

    def testRefineSectionAlignment(self):
        
        #The data for section 7 appears to be missing from the test input folder now. 
        # I need to dig through old copies and see if can be tracked down.  No idea
        # why the input data for this test went missing. 
        # C:\src\git\nornir-testdata\PlatformRaw\PMG\SliceToSliceRegistrationSkipBrute
        

        groupNames = ['StosBrute16', 'StosGrid16', 'StosGrid8', 'SliceToVolume8']

        # Import the files
        buildArgs = [self.TestOutputPath, '-debug', 'AlignSections', \
 \
                     '-Downsample', '16', \
                     '-Center', '5', \
                     '-Channels', 'LeveledShading.*']
        self.VolumeObj = self.RunBuild(buildArgs)

        StosMapNode = self.VolumeObj.find("Block/StosMap")
        self.assertIsNotNone(StosMapNode, "Stos pipeline did not complete")

        SixToFiveAutomaticBruteFirstPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosBrute16', 6, 7)

        # Try to refine the results
        FirstRefineBuildArgs = [self.TestOutputPath, '-debug', 'RefineSectionAlignment', \
 \
                                '-Filter', '.*mosaic.*', \
                                '-InputGroup', 'StosBrute', \
                                '-InputDownsample', '16', \
                                '-OutputDownsample', '16', \
                                '-SectionMap', 'PotentialRegistrationChain']
        self.VolumeObj = self.RunBuild(FirstRefineBuildArgs)

        FirstRefineGroupNode = self.VolumeObj.find("Block/StosGroup[@Name='" + groupNames[1] + "']")
        self.assertIsNotNone(FirstRefineGroupNode, "Could not find StosGroup " + groupNames[1])

        SixToFiveAutomaticGridFirstPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosGrid16', 6, 7)

        self.ValidateTransforms(AutoInputTransform=SixToFiveAutomaticBruteFirstPassTransform,
                                AutoOutputTransform=SixToFiveAutomaticGridFirstPassTransform)

        #        self.assertEqual(SixToFiveAutomaticBruteFirstPassTransform.Checksum,
        #                         SixToFiveAutomaticGridFirstPassTransform.InputTransformChecksum,
        #                         "InputChecksum of first pass refine transform should match checksum of brute transform")

        # Try to scale the transforms to full size
        ScaleArgs = [self.TestOutputPath, '-debug', 'ScaleVolumeTransforms', \
                     '-InputGroup', 'StosGrid', \
                     '-InputDownsample', '16', \
                     '-OutputDownsample', '1']
        self.VolumeObj = self.RunBuild(ScaleArgs)

        VolumeImageArgs = [self.TestOutputPath, '-debug', 'VolumeImage', \
                           '-InputGroup', 'StosGrid', \
                           '-InputDownsample', '16']
        self.VolumeObj = self.RunBuild(VolumeImageArgs)

        ScaleAndVolumeImageGroupNode = self.VolumeObj.find("Block/StosGroup[@Name='StosGrid1']")
        self.assertIsNotNone(ScaleAndVolumeImageGroupNode, "Could not find StosGroup " + groupNames[1])

        ScaledTransformFromSixteen = FetchStosTransform(self, self.VolumeObj, 'StosGrid1', 6, 7)

        self.ValidateTransforms(AutoInputTransform=SixToFiveAutomaticGridFirstPassTransform,
                                AutoOutputTransform=ScaledTransformFromSixteen)

        VolumeImagesArgs = [self.TestOutputPath, '-debug', 'VolumeImage', \
                            '-InputGroup', 'StosGrid', \
                            '-InputDownsample', '1']
        self.VolumeObj = self.RunBuild(VolumeImagesArgs)
        VolumeImageNode = self.VolumeObj.findall("Block/StosGroup[@Name='StosGrid1']/Images")
        self.assertIsNotNone(VolumeImageNode)

        # Try to refine the of stos-grid
        SecondRefineBuildArgs = [self.TestOutputPath, '-debug', 'RefineSectionAlignment', \
                                 '-InputGroup', 'StosGrid', \
                                 '-Filter', '.*mosaic.*', \
                                 '-InputDownsample', '16', \
                                 '-OutputDownsample', '8']
        self.VolumeObj = self.RunBuild(SecondRefineBuildArgs)

        SecondRefineGroupNode = self.VolumeObj.find("Block/StosGroup[@Name='" + groupNames[2] + "']")
        self.assertIsNotNone(FirstRefineGroupNode, "Could not find StosGroup " + groupNames[2])

        SixToFiveAutomaticGridSecondPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosGrid8', 6, 7)

        self.ValidateTransforms(AutoInputTransform=SixToFiveAutomaticGridFirstPassTransform,
                                AutoOutputTransform=SixToFiveAutomaticGridSecondPassTransform)

        # self.assertEqual(SixToFiveAutomaticGridFirstPassTransform.Checksum,
        # SixToFiveAutomaticGridSecondPassTransform.InputTransformChecksum,
        # "InputChecksum of second pass transform should match checksum of first pass transform")

        # Try to refine the of stos-grid
        SliceToVolumeBuildArgs = [self.TestOutputPath, '-debug', 'SliceToVolume', \
                                  '-InputGroup', 'StosGrid', \
                                  '-Downsample', '8']
        self.VolumeObj = self.RunBuild(SliceToVolumeBuildArgs)

        SixToFiveAutomaticSliceToVolumeTransform = FetchStosTransform(self, self.VolumeObj, 'SliceToVolume8', 5, 7)

        self.ValidateTransforms(AutoInputTransform=SixToFiveAutomaticGridSecondPassTransform,
                                AutoOutputTransform=SixToFiveAutomaticSliceToVolumeTransform)

        # self.assertEqual(SixToFiveAutomaticGridSecondPassTransform.Checksum,
        # SixToFiveAutomaticSliceToVolumeTransform.InputTransformChecksum,
        # "InputChecksum of slice to volume transform should match checksum of input transform")

        ##################
        # OK, replace the automatic stos files with manually created stos files.  Ensure
        # the upstream stos files regenerate
        manualDir = os.path.join(FirstRefineGroupNode.FullPath, 'Manual')
        self.InjectManualStosFiles("StosGrid16", manualDir)

        self.VolumeObj = self.RunBuild(FirstRefineBuildArgs)

        SixToFiveManualGridFirstPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosGrid16', 6, 7)

        self.ValidateTransforms(AutoInputTransform=SixToFiveAutomaticBruteFirstPassTransform,
                                AutoOutputTransform=SixToFiveAutomaticGridFirstPassTransform,
                                ManualOutputTransform=SixToFiveManualGridFirstPassTransform)

        #        self.assertNotEqual(SixToFiveAutomaticGridFirstPassTransform.Checksum,
        #                            SixToFiveManualGridFirstPassTransform.Checksum,
        #                            "Transform checksum should not match automatic input when manual input provided")
        #
        #        self.assertNotEqual(SixToFiveAutomaticGridFirstPassTransform.InputTransformChecksum,
        #                            SixToFiveManualGridFirstPassTransform.InputTransformChecksum,
        #                            "Output transform InputTransformChecksum checksum should be different when manual input was provided")

        self.VolumeObj = self.RunBuild(SecondRefineBuildArgs)
        SixToFiveRebuiltGridSecondPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosGrid8', 6, 7)

        self.ValidateTransforms(AutoInputTransform=SixToFiveAutomaticGridFirstPassTransform,
                                AutoOutputTransform=SixToFiveAutomaticGridSecondPassTransform,
                                ManualOutputTransform=SixToFiveRebuiltGridSecondPassTransform,
                                ManualInputTransform=SixToFiveManualGridFirstPassTransform)

        #        self.assertNotEqual(SixToFiveAutomaticGridSecondPassTransform.Checksum,
        #                            SixToFiveRebuiltGridSecondPassTransform.Checksum,
        #                            "Transform checksum should not match automatic input when manual input provided")
        #
        #        self.assertEqual(SixToFiveRebuiltGridSecondPassTransform.InputTransformChecksum,
        #                            SixToFiveManualGridFirstPassTransform.Checksum,
        #                             "InputChecksum of second pass transform should match checksum of first pass transform after manual replacement of input stos")

        self.VolumeObj = self.RunBuild(SliceToVolumeBuildArgs)

        SixToFiveRebuiltSliceToVolumeTransform = FetchStosTransform(self, self.VolumeObj, 'SliceToVolume8', 5, 7)

        self.ValidateTransforms(AutoInputTransform=SixToFiveAutomaticGridSecondPassTransform,
                                AutoOutputTransform=SixToFiveAutomaticSliceToVolumeTransform,
                                ManualOutputTransform=SixToFiveRebuiltSliceToVolumeTransform,
                                ManualInputTransform=SixToFiveRebuiltGridSecondPassTransform)

        #        self.assertNotEqual(SixToFiveAutomaticSliceToVolumeTransform.Checksum,
        #                            SixToFiveRebuiltSliceToVolumeTransform.Checksum,
        #                            "Transform checksum should not match automatic input when manual input provided")
        #
        #        self.assertEqual(SixToFiveRebuiltSliceToVolumeTransform.InputTransformChecksum,
        #                            SixToFiveRebuiltGridSecondPassTransform.Checksum,
        #                             "InputChecksum of second pass transform should match checksum of first pass transform after manual replacement of input stos")

        ###################

        # Inject the override for 6-5 at the DS 8 level.
        self.InjectManualStosFiles("StosGrid8", os.path.join(SecondRefineGroupNode.FullPath, 'Manual'))
        self.VolumeObj = self.RunBuild(SecondRefineBuildArgs)
        SixToFiveManualGridSecondPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosGrid8', 6, 7)

        self.ValidateTransforms(AutoInputTransform=SixToFiveManualGridFirstPassTransform,
                                AutoOutputTransform=SixToFiveRebuiltGridSecondPassTransform,
                                ManualOutputTransform=SixToFiveManualGridSecondPassTransform)

        self.VolumeObj = self.RunBuild(SliceToVolumeBuildArgs)

        SixToFiveRebuiltFromManualSliceToVolumeTransform = FetchStosTransform(self, self.VolumeObj, 'SliceToVolume8', 5,
                                                                              7)

        self.ValidateTransforms(AutoInputTransform=SixToFiveRebuiltGridSecondPassTransform,
                                AutoOutputTransform=SixToFiveRebuiltSliceToVolumeTransform,
                                ManualOutputTransform=SixToFiveRebuiltFromManualSliceToVolumeTransform,
                                ManualInputTransform=SixToFiveManualGridSecondPassTransform)

        ###################

        SliceToVolumeScaleArgs = [self.TestOutputPath, '-debug', 'ScaleVolumeTransforms', \
                                  '-InputGroup', 'SliceToVolume', \
                                  '-InputDownsample', '8', \
                                  '-OutputDownsample', '1']
        self.VolumeObj = self.RunBuild(SliceToVolumeScaleArgs)
        SliceToVolumeScaleAndVolumeImageGroupNode = self.VolumeObj.find("Block/StosGroup[@Name='SliceToVolume1']")
        self.assertIsNotNone(SliceToVolumeScaleAndVolumeImageGroupNode, "Could not find StosGroup SliceToVolume1")

        SliceToVolumeScaleTransformFromEight = FetchStosTransform(self, self.VolumeObj, 'SliceToVolume1', 5, 7)

        self.ValidateTransforms(AutoInputTransform=SixToFiveRebuiltFromManualSliceToVolumeTransform,
                                AutoOutputTransform=SliceToVolumeScaleTransformFromEight)

        SliceToVolumeImagesArgs = [self.TestOutputPath, '-debug', 'VolumeImage', \
                                   '-InputGroup', 'SliceToVolume', \
                                   '-InputDownsample', '1']
        self.VolumeObj = self.RunBuild(SliceToVolumeImagesArgs)
        # SliceToVolumeScaleAndVolumeImageGroupNode = self.VolumeObj.find("Block/StosGroup[@Name='SliceToVolume1']")
        # self.assertIsNotNone(SliceToVolumeScaleAndVolumeImageGroupNode, "Could not find StosGroup SliceToVolume1")

    def testBlob(self):
        buildArgs = [self.TestOutputPath, '-debug', 'CreateBlobFilter', \
                     '-InputFilter', 'mosaic',
                     '-OutputFilter', 'Blob_mosaic',
                     '-Radius', '1',
                     '-Median', '1',
                     '-Levels', '1,4']

        VolumeObj = self.RunBuild(buildArgs)
        BlobFilterNode = VolumeObj.find('Block/Section/Channel/Filter[@Name="Blob_mosaic"]')
        self.assertIsNotNone(BlobFilterNode)

        BlobImageNode = BlobFilterNode.GetImage(1)
        self.assertTrue(os.path.exists(BlobImageNode.FullPath), "Blob output image file does not exist")

        BlobImageNodeDSFour = BlobFilterNode.GetImage(4)
        self.assertTrue(os.path.exists(BlobImageNodeDSFour.FullPath), "Blob output image file does not exist")

        oldstat = os.stat(BlobImageNode.FullPath)
        oldstatDSFour = os.stat(BlobImageNodeDSFour.FullPath)

        VolumeObj = self.RunBuild(buildArgs)

        newstat = os.stat(BlobImageNode.FullPath)
        newstatDSFour = os.stat(BlobImageNodeDSFour.FullPath)

        self.assertEqual(oldstat.st_ctime, newstat.st_ctime, "Blob image recreated after second call to build")
        self.assertEqual(oldstatDSFour.st_ctime, newstatDSFour.st_ctime,
                         "Blob image recreated after second call to build")


class StosGroupTest(EmptyVolumeTestBase):

    @property
    def VolumePath(self):
        return "StosGroupTest"

    def setUp(self):
        super(StosGroupTest, self).setUp()

        volumeObj = self.LoadOrCreateVolume()
        BlockObj = nornir_buildmanager.volumemanager.blocknode.BlockNode.Create('TEM')
        [saveBlock, BlockObj] = volumeObj.UpdateOrAddChild(BlockObj)
        volumeObj.Save()

    def _GetResetBlockNode(self):
        VolumeObj = self.LoadOrCreateVolume()
        BlockNode = VolumeObj.find("Block")
        self.assertIsNotNone(BlockNode)

        return BlockNode

    def RunCreateStosGroup(self, StosGroupName, Downsample, BlockName=None, ):

        buildArgs = self._CreateBuildArgs('CreateStosGroup', '-StosGroup', StosGroupName, '-Downsample',
                                          str(Downsample))

        if BlockName is not None:
            buildArgs.extend(['-Block', BlockName])

        volumeNode = self.RunBuild(buildArgs)

    def RunRemoveStosGroup(self, StosGroupName, Downsample, BlockName=None):

        buildArgs = self._CreateBuildArgs('RemoveStosGroup', '-StosGroup', StosGroupName, '-Downsample',
                                          str(Downsample))

        if BlockName is not None:
            buildArgs.extend(['-Block', BlockName])

        volumeNode = self.RunBuild(buildArgs)

    def RunListStosGroups(self, BlockName=None):

        buildArgs = self._CreateBuildArgs('ListStosGroups')

        if BlockName is not None:
            buildArgs.extend(['-Block', BlockName])

        volumeNode = self.RunBuild(buildArgs)

    def RunListStosGroupContents(self, StosGroupName, Downsample, BlockName=None):

        buildArgs = self._CreateBuildArgs('ListGroupSectionMappings', '-StosGroup', StosGroupName, '-Downsample',
                                          str(Downsample))

        if BlockName is not None:
            buildArgs.extend(['-Block', BlockName])

        volumeNode = self.RunBuild(buildArgs)

    def HasStosGroup(self, StosGroupName, Downsample):
        BlockNode = self._GetResetBlockNode()
        StosGroupNode = BlockNode.GetStosGroup(StosGroupName + str(Downsample), Downsample)
        return StosGroupNode is not None

    def AssertHasStosGroup(self, StosGroupName, Downsample):
        self.assertTrue(self.HasStosGroup(StosGroupName, Downsample), "Missing StosGroup {0:s}".format(StosGroupName))

    def AssertNoStosGroup(self, StosGroupName, Downsample):
        self.assertFalse(self.HasStosGroup(StosGroupName, Downsample),
                         "Should not have StosGroup {0:s}".format(StosGroupName))

    def testCRUDOperations(self):
        self.AssertNoStosGroup("TestStosGroup", 1)
        self.RunListStosGroups()  # Just ensure we don't crash during the list call, we don't check output
        self.RunCreateStosGroup("TestStosGroup", 1)

        self.RunListStosGroups()  # Just ensure we don't crash during the list call, we don't check output
        self.RunListStosGroupContents("TestStosGroup",
                                      1)  # Just ensure we don't crash during the list call, we don't check output
        self.AssertHasStosGroup("TestStosGroup", 1)
        self.AssertNoStosGroup("TestStosGroup", 2)

        self.RunRemoveStosGroup("TestStosGroup", 1)
        self.AssertNoStosGroup("TestStosGroup", 1)
        self.RunListStosGroupContents("TestStosGroup",
                                      1)  # Just ensure we don't crash during the list call, we don't check output


class StosMapTest(EmptyVolumeTestBase):

    @property
    def VolumePath(self):
        return "StosMapTest"

    def setUp(self):
        super(StosMapTest, self).setUp()

        volumeObj = self.LoadOrCreateVolume()
        BlockObj = nornir_buildmanager.volumemanager.blocknode.BlockNode.Create('TEM')
        [saveBlock, BlockObj] = volumeObj.UpdateOrAddChild(BlockObj)
        volumeObj.Save()

    def testCRUDOperations(self):
        return


if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
