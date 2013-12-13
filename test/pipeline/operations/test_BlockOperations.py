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


def ValidateStosMap(test, StosMapNode, expectedRT):

    rt = RegistrationTreeFromStosMapNode(StosMapNode)

    test.assertEqual(len(rt.Nodes), len(expectedRT.Nodes))

    for expectedNode in expectedRT.Nodes.values():
        actualNode = rt.Nodes[expectedNode.SectionNumber]
        test.assertEqual(actualNode.Children, expectedNode.Children)


def FetchStosTransform(test, VolumeObj, groupName, ControlSection, MappedSection):
    groupNode = VolumeObj.find("Block/StosGroup[@Name='" + groupName + "']")
    test.assertIsNotNone(groupNode, "Could not find StosGroup " + groupName)

    sectionMappingNode = groupNode.GetSectionMapping(MappedSection)
    test.assertIsNotNone(sectionMappingNode, "Could not find SectionMappings for " + str(MappedSection))

    Transform = sectionMappingNode.GetChildByAttrib("Transform", 'ControlSectionNumber', str(ControlSection))
    test.assertIsNotNone(sectionMappingNode, "Could not find Transform for %d -> %d" % (MappedSection, ControlSection))

    return Transform


class SectionToSectionMapping(test_sectionimage.ImportLMImages):

    def testCreateSectionToSectionMapping(self):

        VolumeObj = self.LoadVolumeObj()

        BlockNode = VolumeObj.find("Block")
        self.assertIsNotNone(BlockNode)

        OutputBlockNode = CreateSectionToSectionMapping(Parameters={'NumAdjacentSections' : 2}, BlockNode=BlockNode, Logger=self.Logger)
        self.assertIsNotNone(OutputBlockNode)

        VolumeManagerETree.VolumeManager.Save(self.VolumeDir, VolumeObj)

        volumechecklist = [VolumeEntry("StosMap", "Name", "PotentialRegistrationChain")]

        StosMapNode = VerifyVolume(self, BlockNode, volumechecklist)

        # Verify that the sections are mapped correctly
        expectedRT = registrationtree.RegistrationTree.CreateRegistrationTree([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], adjacentThreshold=2)
        ValidateStosMap(self, StosMapNode, expectedRT)


class SliceToSliceRegistrationBruteOnly(test_sectionimage.ImportLMImages):

    @property
    def VolumePath(self):
        return "6872_small"

    def testAlignSectionsPipeline(self):

        # Import the files
        buildArgs = ['Build.py', '-volume', self.VolumeDir, \
                     '-pipeline', 'AlignSections', \
                     '-debug', \
                     '-AlignDownsample', '16', \
                     '-Center', '5', \
                     '-StosChannels', 'LeveledShading.*']
        VolumeObj = self.RunBuild(buildArgs)

        StosMapNode = VolumeObj.find("Block/StosMap")
        self.assertIsNotNone(StosMapNode, "Stos pipeline did not complete")


class SliceToSliceRegistrationSkipBrute(CopySetupTestBase):

    @property
    def Platform(self):
        return "SliceToSliceRegistrationBruteOnly"

    def InjectManualStosFiles(self, StosGroup, TargetDir):

        stosFiles = glob.glob(os.path.join(self.PlatformFullPath, StosGroup, "Manual", "*.stos"))
        self.assertTrue(len(stosFiles) > 0, "Could not locate manual registration stos files for test")

        for stosFile in stosFiles:
            shutil.copy(stosFile, os.path.join(TargetDir, os.path.basename(stosFile)))


    def ValidateTransforms(self, AutoOutputTransform, AutoInputTransform, ManualOutputTransform=None, ManualInputTransform=None):
        '''ManualInputTransform can be none after the first refine call since the manual override file does not have a <transform> node.
           On the second pass or later ManualInputTransform refers to the transform that should have replaced the original output in the earlier pass'''

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
        buildArgs = ['Build.py', '-volume', self.TestOutputPath, \
                     '-pipeline', 'AlignSections', \
                     '-debug', \
                     '-AlignDownsample', '16', \
                     '-Center', '5', \
                     '-StosChannels', 'LeveledShading.*']
        self.VolumeObj = self.RunBuild(buildArgs)

        StosMapNode = self.VolumeObj.find("Block/StosMap")
        self.assertIsNotNone(StosMapNode, "Stos pipeline did not complete")

        SixToFiveAutomaticBruteFirstPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosBrute16', 5, 6)

        # Try to refine the results
        FirstRefineBuildArgs = ['Build.py', '-volume', self.TestOutputPath, \
                     '-pipeline', 'RefineSectionAlignment', \
                     '-debug', \
                     '-Filter', '.*mosaic.*', \
                     '-InputGroup', 'StosBrute', \
                     '-InputDownsample', '16', \
                     '-OutputDownsample', '16']
        self.VolumeObj = self.RunBuild(FirstRefineBuildArgs)

        FirstRefineGroupNode = self.VolumeObj.find("Block/StosGroup[@Name='" + groupNames[1] + "']")
        self.assertIsNotNone(FirstRefineGroupNode, "Could not find StosGroup " + groupNames[1])

        SixToFiveAutomaticGridFirstPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosGrid16', 5, 6)

        self.ValidateTransforms(AutoInputTransform=SixToFiveAutomaticBruteFirstPassTransform,
                                AutoOutputTransform=SixToFiveAutomaticGridFirstPassTransform)

#        self.assertEqual(SixToFiveAutomaticBruteFirstPassTransform.Checksum,
#                         SixToFiveAutomaticGridFirstPassTransform.InputTransformChecksum,
#                         "InputChecksum of first pass refine transform should match checksum of brute transform")

        # Try to scale the transforms to full size
        ScaleAndVolumeImagesArgs = ['Build.py', '-volume', self.TestOutputPath, \
                             '-pipeline', 'ScaleVolumeTransforms', 'VolumeImage', \
                             '-debug', \
                             '-ScaleGroupName', 'StosGrid', \
                             '-ScaleInputDownsample', '16', \
                             '-ScaleOutputDownsample', '1', \
                             '-VolumeImageGroupName', 'StosGrid', \
                             '-VolumeImageDownsample', '1']
        self.VolumeObj = self.RunBuild(ScaleAndVolumeImagesArgs)
        ScaleAndVolumeImageGroupNode = self.VolumeObj.find("Block/StosGroup[@Name='StosGrid1']")
        self.assertIsNotNone(ScaleAndVolumeImageGroupNode, "Could not find StosGroup " + groupNames[1])

        ScaledTransformFromSixteen = FetchStosTransform(self, self.VolumeObj, 'StosGrid1', 5, 6)

        self.ValidateTransforms(AutoInputTransform=SixToFiveAutomaticGridFirstPassTransform,
                                AutoOutputTransform=ScaledTransformFromSixteen)

        # Try to refine the of stos-grid
        SecondRefineBuildArgs = ['Build.py', '-volume', self.TestOutputPath, \
                     '-pipeline', 'RefineSectionAlignment', \
                     '-debug', \
                     '-InputGroup', 'StosGrid', \
                     '-Filter', '.*mosaic.*', \
                     '-InputDownsample', '16', \
                     '-OutputDownsample', '8']
        self.VolumeObj = self.RunBuild(SecondRefineBuildArgs)

        SecondRefineGroupNode = self.VolumeObj.find("Block/StosGroup[@Name='" + groupNames[2] + "']")
        self.assertIsNotNone(FirstRefineGroupNode, "Could not find StosGroup " + groupNames[2])

        SixToFiveAutomaticGridSecondPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosGrid8', 5, 6)

        self.ValidateTransforms(AutoInputTransform=SixToFiveAutomaticGridFirstPassTransform,
                                AutoOutputTransform=SixToFiveAutomaticGridSecondPassTransform)


        # self.assertEqual(SixToFiveAutomaticGridFirstPassTransform.Checksum,
        # SixToFiveAutomaticGridSecondPassTransform.InputTransformChecksum,
        # "InputChecksum of second pass transform should match checksum of first pass transform")

        # Try to refine the of stos-grid
        SliceToVolumeBuildArgs = ['Build.py', '-volume', self.TestOutputPath, \
                     '-pipeline', 'SliceToVolume', \
                     '-debug', \
                     '-InputGroup', 'StosGrid', \
                     '-InputDownsample', '8', \
                     '-OutputDownsample', '8']
        self.VolumeObj = self.RunBuild(SliceToVolumeBuildArgs)

        SixToFiveAutomaticSliceToVolumeTransform = FetchStosTransform(self, self.VolumeObj, 'SliceToVolume8', 5, 6)

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

        SixToFiveManualGridFirstPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosGrid16', 5, 6)

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
        SixToFiveRebuiltGridSecondPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosGrid8', 5, 6)

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

        SixToFiveRebuiltSliceToVolumeTransform = FetchStosTransform(self, self.VolumeObj, 'SliceToVolume8', 5, 6)

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
        SixToFiveManualGridSecondPassTransform = FetchStosTransform(self, self.VolumeObj, 'StosGrid8', 5, 6)

        self.ValidateTransforms(AutoInputTransform=SixToFiveManualGridFirstPassTransform,
                                AutoOutputTransform=SixToFiveRebuiltGridSecondPassTransform,
                                ManualOutputTransform=SixToFiveManualGridSecondPassTransform)

        self.VolumeObj = self.RunBuild(SliceToVolumeBuildArgs)

        SixToFiveRebuiltFromManualSliceToVolumeTransform = FetchStosTransform(self, self.VolumeObj, 'SliceToVolume8', 5, 6)

        self.ValidateTransforms(AutoInputTransform=SixToFiveRebuiltGridSecondPassTransform,
                                AutoOutputTransform=SixToFiveRebuiltSliceToVolumeTransform,
                                ManualOutputTransform=SixToFiveRebuiltFromManualSliceToVolumeTransform,
                                ManualInputTransform=SixToFiveManualGridSecondPassTransform)

###################

        SliceToVolumeScaleArgs = ['Build.py', '-volume', self.TestOutputPath, \
                             '-pipeline', 'ScaleVolumeTransforms', \
                             '-debug', \
                             '-VolumeImageGroupName', 'SliceToVolume', \
                             '-ScaleInputDownsample', '8', \
                             '-ScaleOutputDownsample', '1']
        self.VolumeObj = self.RunBuild(SliceToVolumeScaleArgs)
        SliceToVolumeScaleAndVolumeImageGroupNode = self.VolumeObj.find("Block/StosGroup[@Name='SliceToVolume1']")
        self.assertIsNotNone(SliceToVolumeScaleAndVolumeImageGroupNode, "Could not find StosGroup SliceToVolume1")

        SliceToVolumeScaleTransformFromEight = FetchStosTransform(self, self.VolumeObj, 'StosGrid8', 5, 6)

        self.ValidateTransforms(AutoInputTransform=SixToFiveRebuiltFromManualSliceToVolumeTransform,
                                AutoOutputTransform=SliceToVolumeScaleTransformFromEight)

        SliceToVolumeImagesArgs = ['Build.py', '-volume', self.TestOutputPath, \
                             '-pipeline', 'VolumeImage', \
                             '-debug', \
                             '-VolumeImageGroupName', 'SliceToVolume', \
                             '-VolumeImageDownsample', '1']
        self.VolumeObj = self.RunBuild(SliceToVolumeImagesArgs)
        # SliceToVolumeScaleAndVolumeImageGroupNode = self.VolumeObj.find("Block/StosGroup[@Name='SliceToVolume1']")
        # self.assertIsNotNone(SliceToVolumeScaleAndVolumeImageGroupNode, "Could not find StosGroup SliceToVolume1")



    def testBlob(self):
        buildArgs = ['Build.py', '-volume', self.TestOutputPath,
                                 '-pipeline', 'CreateBlobFilter',
                                 '-debug', \
                                 '-BlobFilters', 'mosaic',
                                 '-BlobRadius', '1',
                                 '-BlobMedian', '1',
                                 '-BlobLevels', '1,4',
                                 '-NumAdjacentSections', '1',
                                 '-debug']

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