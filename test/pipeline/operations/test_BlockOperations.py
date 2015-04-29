'''
Created on Apr 25, 2013

@author: u0490822
'''
import glob
import unittest

from test.pipeline.setup_pipeline import VerifyVolume, VolumeEntry, \
    CopySetupTestBase

import nornir_buildmanager.build as build
from nornir_buildmanager.operations.block import *
from nornir_imageregistration.transforms import registrationtree
import test.pipeline.test_sectionimage as test_sectionimage
import nornir_buildmanager.VolumeManagerETree as volman


def _RTNodesToNumberList(Nodes):
    nums = []
    for n in Nodes:
        nums.append(n.SectionNumber)

    return nums

def ValidateStosMap(test, StosMapNode, expectedRT):

    rt = RegistrationTreeFromStosMapNode(StosMapNode)

    test.assertEqual(len(rt.Nodes), len(expectedRT.Nodes))

    for expectedNode in expectedRT.Nodes.values():
        actualNode = rt.Nodes[expectedNode.SectionNumber]
        actualNodeNumbers = _RTNodesToNumberList(actualNode.Children)
        expectedNodeNumbers = _RTNodesToNumberList(expectedNode.Children)
        test.assertEqual(actualNodeNumbers, expectedNodeNumbers)


def FetchStosTransform(test, VolumeObj, groupName, ControlSection, MappedSection):
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


class SectionToSectionMappingTest(test_sectionimage.ImportLMImages):


    def _GetResetBlockNode(self):
        VolumeObj = self.LoadVolumeObj()
        BlockNode = VolumeObj.find("Block")
        self.assertIsNotNone(BlockNode)

        return BlockNode

    def SetNonStosSectionList(self, BlockNode, NonStosNumberList, **kwargs):

        StosExemptNode = VolumeManagerETree.XElementWrapper(tag='NonStosSectionNumbers')
        (added, StosExemptNode) = BlockNode.UpdateOrAddChild(StosExemptNode)

        # Fetch the list of the exempt nodes from the element text
        if len(NonStosNumberList) > 0:
            StosExemptNode.text = ','.join(str(x) for x in NonStosNumberList)
        else:
            StosExemptNode.text = ""

    def _GenerateExpectedRT(self, GoodSections, BadSections, center, adjacentThreshold):
        # Verify that the sections are mapped correctly
        expectedRT = registrationtree.RegistrationTree.CreateRegistrationTree(GoodSections, adjacentThreshold=adjacentThreshold, center=center)
        expectedRT.AddNonControlSections(BadSections)
        return expectedRT


    def testCreateSectionToSectionMapping(self):

        center = 5
        self.BasicStosMapGeneratorCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.BasicStosMapGeneratorCheck(center, adjacentThreshold=2, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=2, Logger=self.Logger)

        self.CheckRemoveDuplicateMappings(center, adjacentThreshold=2, Logger=self.Logger)

        self.StosMapGeneratorAddSectionLaterCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.StosMapGeneratorAddSectionLaterCheck(center, adjacentThreshold=2, Logger=self.Logger)

        center = 1
        self.BasicStosMapGeneratorCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.BasicStosMapGeneratorCheck(center, adjacentThreshold=2, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=2, Logger=self.Logger)

        center = 12
        self.BasicStosMapGeneratorCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.BasicStosMapGeneratorCheck(center, adjacentThreshold=2, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=2, Logger=self.Logger)

        center = 0
        self.BasicStosMapGeneratorCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.BasicStosMapGeneratorCheck(center, adjacentThreshold=2, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=2, Logger=self.Logger)

        center = 13
        self.BasicStosMapGeneratorCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.BasicStosMapGeneratorCheck(center, adjacentThreshold=2, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=1, Logger=self.Logger)
        self.StosMapGeneratorWithInvalidCheck(center, adjacentThreshold=2, Logger=self.Logger)


    def BasicStosMapGeneratorCheck(self, center, adjacentThreshold, Logger):

        BlockNode = self._GetResetBlockNode()

        GoodSections = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        OutputBlockNode = CreateSectionToSectionMapping(Parameters={'NumAdjacentSections' : adjacentThreshold, 'CenterSection' : center}, BlockNode=BlockNode, Logger=self.Logger)
        self.assertIsNotNone(OutputBlockNode)

        # VolumeManagerETree.VolumeManager.Save(self.TestOutputPath, VolumeObj)

        volumechecklist = [VolumeEntry("StosMap", "Name", "PotentialRegistrationChain")]

        StosMapNode = VerifyVolume(self, BlockNode, volumechecklist)

        # Verify that the sections are mapped correctly
        expectedRT = self._GenerateExpectedRT(GoodSections, [], center, adjacentThreshold)
        ValidateStosMap(self, StosMapNode, expectedRT)


    def StosMapGeneratorWithInvalidCheck(self, center, adjacentThreshold, Logger):

        GoodSections = [2, 3, 4, 6, 7, 8, 9]
        BadSections = [1, 5, 10]
        BlockNode = self._GetResetBlockNode()
        self._StosMapGeneratorWithInvalidCheckWithBlock(BlockNode, GoodSections, BadSections, center, adjacentThreshold, Logger)

    def _StosMapGeneratorWithInvalidCheckWithBlock(self, BlockNode, GoodSections, BadSections, center, adjacentThreshold, Logger, expectedRT=None):

        self.SetNonStosSectionList(BlockNode, BadSections)

        OutputBlockNode = CreateSectionToSectionMapping(Parameters={'NumAdjacentSections' : adjacentThreshold, 'CenterSection' : center}, BlockNode=BlockNode, Logger=self.Logger)
        self.assertIsNotNone(OutputBlockNode)

        volumechecklist = [VolumeEntry("StosMap", "Name", "PotentialRegistrationChain")]
        StosMapNode = VerifyVolume(self, BlockNode, volumechecklist)

        if expectedRT is None:
            expectedRT = self._GenerateExpectedRT(GoodSections, BadSections, center, adjacentThreshold)

        ValidateStosMap(self, StosMapNode, expectedRT)

        return OutputBlockNode


    def StosMapGeneratorAddSectionLaterCheck(self, center, adjacentThreshold, Logger):

        BlockNode = self._GetResetBlockNode()

        print("Remove section 7")
        omitSectionNode = BlockNode.GetSection(7)
        self.assertIsNotNone(omitSectionNode)
        BlockNode.remove(omitSectionNode)

        GoodSections = [2, 3, 4, 6, 8, 9]
        BadSections = [1, 5, 10]

        self.SetNonStosSectionList(BlockNode, [1, 5, 10])

        OutputBlockNode = self._StosMapGeneratorWithInvalidCheckWithBlock(BlockNode, GoodSections, BadSections, center, adjacentThreshold, Logger)
        self.assertIsNotNone(OutputBlockNode)

        # OK, add the section back and make sure it is included in the updated stos map

        print("Add section 7")
        OutputBlockNode.append(omitSectionNode)
        GoodSections = [2, 3, 4, 6, 7, 8, 9]
        BadSections = [1, 5, 10]

        expectedRT = self._GenerateExpectedRT(GoodSections, BadSections, center, adjacentThreshold)

        # We expect extra mappings for section 8 since section 7 did not exxist in the original
        if(adjacentThreshold == 1):
            expectedRT.AddPair(6, 8)
        elif(adjacentThreshold == 2):
            expectedRT.AddPair(4, 8)
            expectedRT.AddPair(6, 9)
        else:
            self.fail("Test not tweaked for adjacentThreshold > 2")

        OutputBlockNode = self._StosMapGeneratorWithInvalidCheckWithBlock(OutputBlockNode, GoodSections, BadSections, center, adjacentThreshold, Logger, expectedRT)
        self.assertIsNotNone(OutputBlockNode)

        print("Done!")

    def CheckRemoveDuplicateMappings(self, center, adjacentThreshold, Logger):

        GoodSections = [2, 3, 4, 6, 7, 8, 9]
        BadSections = [1, 5, 10]
        BlockNode = self._GetResetBlockNode()
        self._StosMapGeneratorWithInvalidCheckWithBlock(BlockNode, GoodSections, BadSections, center, adjacentThreshold, Logger)

        volumechecklist = [VolumeEntry("StosMap", "Name", "PotentialRegistrationChain")]
        StosMapNode = VerifyVolume(self, BlockNode, volumechecklist)

        # Add some extra stosmap nodes and make sure they get cleaned up

        ExtraMapNode = volman.MappingNode(4, 10)
        StosMapNode.append(ExtraMapNode)

        removed = StosMapNode.RemoveDuplicateControlEntries(3)
        self.assertFalse(removed, "No duplicate should return false")

        removed = StosMapNode.RemoveDuplicateControlEntries(4)
        self.assertTrue(removed, "Duplicate should be removed and return true")

        listMapFour = StosMapNode.GetMappingsForControl(4)
        self.assertEqual(len(listMapFour), 1, "Duplicate StosMap was not removed")

        expectedRT = self._GenerateExpectedRT(GoodSections, BadSections, center, adjacentThreshold)
        expectedRT.AddPair(4, 10)

        ValidateStosMap(self, StosMapNode, expectedRT)

        BannedSections = [4]
        StosMapNode.ClearBannedControlMappings(BannedSections)
        listMapFour = StosMapNode.GetMappingsForControl(4)
        self.assertEqual(len(listMapFour), 0, "Banned section should be removed")




class SliceToSliceRegistrationBruteOnlyTest(test_sectionimage.ImportLMImages):

    @property
    def VolumePath(self):

        return "6872_small"

    def testAlignSectionsPipeline(self):

        # Import the files
        buildArgs = [ self.TestOutputPath, '-debug', 'AlignSections', \
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
        return "SliceToSliceRegistrationBruteOnly"

    def setUp(self):
        super(SliceToSliceRegistrationSkipBrute, self).setUp()

    def InjectManualStosFiles(self, StosGroup, TargetDir):

        stosFiles = glob.glob(os.path.join(self.ImportedDataPath, StosGroup, "Manual", "*.stos"))
        self.assertTrue(len(stosFiles) > 0, "Could not locate manual registration stos files for test")

        for stosFile in stosFiles:
            shutil.copy(stosFile, os.path.join(TargetDir, os.path.basename(stosFile)))


    def ValidateTransforms(self, AutoOutputTransform, AutoInputTransform, ManualOutputTransform=None, ManualInputTransform=None):
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

        groupNames = ['StosBrute16', 'StosGrid16', 'StosGrid8', 'SliceToVolume8']

        # Import the files
        buildArgs = [self.TestOutputPath, '-debug', 'AlignSections',  \
                      \
                     '-Downsample', '16', \
                     '-Center', '5', \
                     '-Channels', 'LeveledShading.*']
        self.VolumeObj = self.RunBuild(buildArgs)

        StosMapNode = self.VolumeObj.find("Block/StosMap")
        self.assertIsNotNone(StosMapNode, "Stos pipeline did not complete")

        SixToFiveAutomaticBruteFirstPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosBrute16', 6, 7)

        # Try to refine the results
        FirstRefineBuildArgs = [ self.TestOutputPath, '-debug', 'RefineSectionAlignment', \
                      \
                     '-Filter', '.*mosaic.*', \
                     '-InputGroup', 'StosBrute', \
                     '-InputDownsample', '16', \
                     '-OutputDownsample', '16']
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

        VolumeImageArgs = [self.TestOutputPath, '-debug', 'VolumeImage',  \
                     '-InputGroup', 'StosGrid', \
                     '-InputDownsample', '16']
        self.VolumeObj = self.RunBuild(VolumeImageArgs)

        ScaleAndVolumeImageGroupNode = self.VolumeObj.find("Block/StosGroup[@Name='StosGrid1']")
        self.assertIsNotNone(ScaleAndVolumeImageGroupNode, "Could not find StosGroup " + groupNames[1])

        ScaledTransformFromSixteen = FetchStosTransform(self, self.VolumeObj, 'StosGrid1', 6, 7)

        self.ValidateTransforms(AutoInputTransform=SixToFiveAutomaticGridFirstPassTransform,
                                AutoOutputTransform=ScaledTransformFromSixteen)

        VolumeImagesArgs = [self.TestOutputPath, '-debug', 'VolumeImage',  \
                             '-InputGroup', 'StosGrid', \
                             '-InputDownsample', '1']
        self.VolumeObj = self.RunBuild(VolumeImagesArgs)
        VolumeImageNode = self.VolumeObj.findall("Block/StosGroup[@Name='StosGrid1']/Images")
        self.assertIsNotNone(VolumeImageNode)

        # Try to refine the of stos-grid
        SecondRefineBuildArgs = [self.TestOutputPath, '-debug', 'RefineSectionAlignment',  \
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
        SliceToVolumeBuildArgs = [self.TestOutputPath, '-debug', 'SliceToVolume',  \
                     '-InputGroup', 'StosGrid', \
                     '-InputDownsample', '8']
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

        SixToFiveRebuiltFromManualSliceToVolumeTransform = FetchStosTransform(self, self.VolumeObj, 'SliceToVolume8', 5, 7)

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
        buildArgs = [self.TestOutputPath, '-debug', 'CreateBlobFilter',  \
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
        self.assertEqual(oldstatDSFour.st_ctime, newstatDSFour.st_ctime, "Blob image recreated after second call to build")



if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()