'''
Created on Feb 6, 2013

@author: u0490822
'''

import glob
import logging
import os
import shutil
import unittest

import nornir_buildmanager.VolumeManagerETree
from nornir_buildmanager.importers.pmg import ParsePMGFilename, PMGInfo
import nornir_buildmanager.importers.pmg as pmg
from nornir_imageregistration.files.mosaicfile import MosaicFile
import nornir_shared.files
import nornir_shared.misc
import setup_pipeline


PMGData = {"6750_10677D_WDF_20x_02_G.pmg" : PMGInfo(Slide=6750,
                                                 Block='10677D',
                                                 Initials='WDF',
                                                 Mag='20x',
                                                 Spot=2,
                                                 Probe='G',
                                                 NumberOfImages=10),
           "6750_10677D_WDF_20x_03_E.pmg" : PMGInfo(Slide=6750,
                                                 Block='10677D',
                                                 Initials='WDF',
                                                 Mag='20x',
                                                 Spot=3,
                                                 Probe='E',
                                                 NumberOfImages=8),
           "6259_9778_WDF_40xOil_04_Dapi.pmg" : PMGInfo(Slide=6259,
                                                 Block='9778',
                                                 Initials='WDF',
                                                 Mag='40xOil',
                                                 Spot=4,
                                                 Probe='G',
                                                 NumberOfImages=96),
           "6259_9778_WDF_40xOil_04_YY.pmg" : PMGInfo(Slide=6259,
                                                 Block='9778',
                                                 Initials='WDF',
                                                 Mag='40xOil',
                                                 Spot=4,
                                                 Probe='G',
                                                 NumberOfImages=96)
           }

class PMGTest(setup_pipeline.PlatformTest):

    @property
    def VolumePath(self):
        return "6750"

    @property
    def Platform(self):
        return "PMG"

    @property
    def classname(self):
        clsstr = str(self.__class__.__name__)
        return clsstr

class ParseBasicFilename(PMGTest):

    def runTest(self):

        filename = os.path.join("FakeDir1", "FakeDir2", '1234_5678_ja_40x_04_yy.pmg')

        info = ParsePMGFilename(filename)

        self.assertEqual(info.Slide, 1234, "Incorrect slide number")
        self.assertEqual(info.Block, '5678', "Incorrect block number")
        self.assertEqual(info.Initials, 'ja', "Incorrect initials")
        self.assertEqual(info.Mag, '40x', "Incorrect mag string")
        self.assertEqual(info.Spot, 4, "Incorrect spot string")
        self.assertEqual(info.Probe, 'yy', "Incorrect probe name")

        pass

class ParseSectionFilename(PMGTest):

    def runTest(self):

        filename = os.path.join("FakeDir1", '1234_5678_0001_ja_40x_04_yy.pmg')

        info = ParsePMGFilename(filename)

        self.assertEqual(info.Slide, 1234, "Incorrect slide number")
        self.assertEqual(info.Block, '5678', "Incorrect block number")
        self.assertEqual(info.Section, 1, "Incorrect section number")
        self.assertEqual(info.Initials, 'ja', "Incorrect initials")
        self.assertEqual(info.Mag, '40x', "Incorrect mag string")
        self.assertEqual(info.Spot, 4, "Incorrect spot string")
        self.assertEqual(info.Probe, 'yy', "Incorrect probe name")
        pass

class ParseSpacesInFilename(PMGTest):

    def runTest(self):

        filename = os.path.join("FakeDir1", "FakeDir2", '1234_5678_0001_ja_40x_04_yy GFP.pmg')

        info = ParsePMGFilename(filename)

        self.assertEqual(info.Slide, 1234, "Incorrect slide number")
        self.assertEqual(info.Block, '5678', "Incorrect block number")
        self.assertEqual(info.Section, 1, "Incorrect section number")
        self.assertEqual(info.Initials, 'ja', "Incorrect initials")
        self.assertEqual(info.Mag, '40x', "Incorrect mag string")
        self.assertEqual(info.Spot, 4, "Incorrect spot string")
        self.assertEqual(info.Probe, 'yy GFP', "Incorrect probe name")
        pass

class ImportPMG(PMGTest):

    def runTest(self):

        pmgImportDir = os.path.join(self.PlatformFullPath, "6750")

        pmgSectionDirs = nornir_shared.files.RecurseSubdirectories(pmgImportDir, "*.pmg")

        for pmgDir in pmgSectionDirs:

            VolumeObj = nornir_buildmanager.VolumeManagerETree.VolumeManager.Load(self.TestOutputPath, Create=True)
            pmgFile = glob.glob(os.path.join(pmgDir, "*.pmg"))
            self.assertEqual(len(pmgFile), 1, "Unexpected extra PMG in dir: " + pmgDir)
            pmgFile = pmgFile[0]
            pmgFileKey = os.path.basename(pmgFile)

            pmgData = PMGData[pmgFileKey]
            self.assertIsNotNone(pmgData)

            pmg.PMGImport.ToMosaic(VolumeObj, pmgFile, VolumeObj.FullPath, debug=True)

            VolumeObj.Save()
            del VolumeObj

            self.CheckVolumeData(pmgData)

    def CheckVolumeData(self, pmgData):

        self.assertIsNotNone(pmgData)

        '''Ensure the import produced valid meta-data'''
        VolumeObj = nornir_buildmanager.VolumeManagerETree.VolumeManager.Load(self.TestOutputPath, Create=True)

        SectionNumber = pmgData.Section
        if SectionNumber is None:
            SectionNumber = pmgData.Spot
        self.assertIsNotNone(SectionNumber)

        SectionObj = VolumeObj.find("Block/Section[@Number='" + str(SectionNumber) + "']")
        self.assertIsNotNone(SectionObj)

        ChannelObj = SectionObj.GetChildByAttrib('Channel', 'Name', pmgData.Probe)
        self.assertIsNotNone(ChannelObj)

        TransformObj = ChannelObj.GetChildByAttrib('Transform', 'Name', 'Stage')
        self.assertIsNotNone(TransformObj)
        self.assertTrue(os.path.exists(TransformObj.FullPath))

        mfile = MosaicFile.Load(TransformObj.FullPath)
        self.assertIsNotNone(mfile)
        self.assertEqual(mfile.NumberOfImages, pmgData.NumberOfImages)

        XPathTemplate = "Block/Section[@Number='%(section)d']/Channel[@Name='%(probe)s']/Filter[@Name='Raw8']/TilePyramid"
        XPath = XPathTemplate % {'section' : SectionNumber,
                                 'probe' : pmgData.Probe}
        TilePyramidObj = VolumeObj.find(XPath)
        self.assertIsNotNone(TransformObj)
        self.assertEqual(TilePyramidObj.NumberOfTiles, pmgData.NumberOfImages)

 
class PMGBuildTest(PMGTest):
  
    @property
    def VolumePath(self):
        return "6263_NoDapi"
      
    @property
    def Grid32ManualStosFullPath(self):
        return os.path.join(self.PlatformFullPath, '6263_ManualStos')
       
  
    def runTest(self):
  
        self.RunImport()
        self.RunShadingCorrection(ChannelPattern="(?![D|d]api)", CorrectionType='brightfield', FilterPattern="Raw8")
        self.RunShadingCorrection(ChannelPattern="([D|d]api)", CorrectionType='darkfield', FilterPattern="Raw8")
        self.RunPrune(Filter="ShadingCorrected", Downsample=2)
        self.RunHistogram(Filter="ShadingCorrected", Downsample=4)
        self.RunAdjustContrast(Sections=None, Filter="ShadingCorrected", Gamma=1.0)
        self.RunMosaic(Filter="Leveled")
        self.RunAssemble(Levels=[1])
        self.RunAssemble(Filter="ShadingCorrected", Levels=1)
        self.RunExportImages(Channels="(?!Registered)", Filters="Leveled", AssembleLevel=1, Output="Mosaics")
  
        self.RunMosaicReport(ContrastFilter="Leveled", AssembleFilter="ShadingCorrected", AssembleDownsample=1)
  
        BruteLevel = 8
  
        self.RunCreateBlobFilter(Channels="*", Levels=[8,16,BruteLevel], Filter="Leveled")
        self.RunAlignSections(Channels="*", Filters="Blob", Levels=BruteLevel)
          
        self.RunAssembleStosOverlays(Group="StosBrute", Downsample=BruteLevel, StosMap='PotentialRegistrationChain')
        self.RunSelectBestRegistrationChain(Group="StosBrute", Downsample=BruteLevel, InputStosMap='PotentialRegistrationChain', OutputStosMap='FinalStosMap')
  
        volumeNode = self.RunRefineSectionAlignment(InputGroup="StosBrute", InputLevel=BruteLevel, OutputGroup="Grid", OutputLevel=BruteLevel, Filter="Leveled")
  
        listReplacedTransformNodes = self.CopyManualStosFiles(self.Grid32ManualStosFullPath, StosGroupName='%s%d' % ('Grid', BruteLevel))
          
        self.RunRefineSectionAlignment(InputGroup="StosBrute", InputLevel=BruteLevel, OutputGroup="Grid", OutputLevel=BruteLevel, Filter="Leveled")
          
        self.RunRefineSectionAlignment(InputGroup="Grid", InputLevel=BruteLevel, OutputGroup="Grid", OutputLevel=BruteLevel / 4, Filter="Leveled")
        self.RunScaleVolumeTransforms(InputGroup="Grid", InputLevel=BruteLevel / 4, OutputLevel=1)
        self.RunSliceToVolume()
        self.RunCreateVikingXML(StosGroup='SliceToVolume1', StosMap='SliceToVolume', OutputFile="SliceToVolume")
        self.RunMosaicToVolume()
        self.RunAssembleMosaicToVolume(Channels="(?!Registered)", Filters="ShadingCorrected", AssembleLevel=1)
        self.RunExportImages(Channels="Registered", Filters="ShadingCorrected", AssembleLevel=1, Output="Registered")
         
     
             
#      
#   
# class PMGBuildTest(setup_pipeline.CopySetupTestBase):
#  
#     @property
#     def VolumePath(self):
#         return "PMGBuildTest"
#  
#     @property
#     def Platform(self):
#         return "PMG"
#  
#     def runTest(self):
#  
#  
#         #=======================================================================
#          self.RunImport()
#          self.RunShadingCorrection(ChannelPattern="(?![D|d]api)", CorrectionType='brightfield', FilterPattern="Raw8")
#          self.RunShadingCorrection(ChannelPattern="([D|d]api)", CorrectionType='darkfield', FilterPattern="Raw8")
#          self.RunPrune(Filter="ShadingCorrected", Downsample=2)
#          self.RunHistogram(Filter="ShadingCorrected", Downsample=4)
#          self.RunAdjustContrast(Filter="ShadingCorrected", Gamma=1.0)
#         self.RunMosaic(Filter="Leveled")
#         self.RunAssemble(Levels=[1])
#         self.RunAssemble(Filter="ShadingCorrected", Levels=1)
#         self.RunExportImages(Channels="(?!Registered)", Filters="Leveled", AssembleLevel=1, Output="Mosaics")
#   
#         self.RunMosaicReport(ContrastFilter="Leveled", AssembleFilter="ShadingCorrected", AssembleDownsample=1)
#   
#         BruteLevel = 8
#   
#         self.RunCreateBlobFilter(Channels="*", Levels="8,16,%d" % (BruteLevel), Filter="Leveled")
#         self.RunAlignSections(Channels="*", Filters="Blob", Levels=BruteLevel)
#           
#         self.RunAssembleStosOverlays(Group="StosBrute", Downsample=BruteLevel, StosMap='PotentialRegistrationChain')
#         self.RunSelectBestRegistrationChain(Group="StosBrute", Downsample=BruteLevel, InputStosMap='PotentialRegistrationChain', OutputStosMap='FinalStosMap')
#   
#         volumeNode = self.RunRefineSectionAlignment(InputGroup="StosBrute", InputLevel=BruteLevel, OutputGroup="Grid", OutputLevel=BruteLevel, Filter="Leveled")
#   
#         listReplacedTransformNodes = self.CopyManualStosFiles(self.Grid32ManualStosFullPath, StosGroupName='%s%d' % ('Grid', BruteLevel))
#           
#         self.RunRefineSectionAlignment(InputGroup="StosBrute", InputLevel=BruteLevel, OutputGroup="Grid", OutputLevel=BruteLevel, Filter="Leveled")
#           
#         self.RunRefineSectionAlignment(InputGroup="Grid", InputLevel=BruteLevel, OutputGroup="Grid", OutputLevel=BruteLevel / 4, Filter="Leveled")
#         self.RunScaleVolumeTransforms(InputGroup="Grid", InputLevel=BruteLevel / 4, OutputLevel=1)
#         self.RunSliceToVolume()
#         self.RunCreateVikingXML(StosGroup='SliceToVolume1', StosMap='SliceToVolume', OutputFile="SliceToVolume")
#         self.RunMosaicToVolume()
#         self.RunAssembleMosaicToVolume(Channels="(?!Registered)", Filters="ShadingCorrected", AssembleLevel=1)
#         self.RunExportImages(Channels="Registered", Filters="ShadingCorrected", AssembleLevel=1, Output="Registered")

# class PMGAlignTest(setup_pipeline.CopySetupTestBase):
#
#     @property
#     def VolumePath(self):
#         return "6259_Assembled"
#
#     @property
#     def Platform(self):
#         return "PMG"
#
#     def runTest(self):
#         return
#
#         self.RunMosaicReport(ContrastFilter="Leveled", AssembleFilter="Leveled", AssembleDownsample=1)
#
#         BruteLevel = 8
#
#         self.RunCreateBlobFilter(Channels="*", Levels="8,16,%d" % (BruteLevel), Filter="Leveled")
#         self.RunAlignSections(Channels="*", Filters="Blob", Levels=BruteLevel)
#         self.RunRefineSectionAlignment(InputGroup="StosBrute", InputLevel=BruteLevel, OutputGroup="Grid", OutputLevel=BruteLevel, Filter="Leveled")
#         self.RunRefineSectionAlignment(InputGroup="Grid", InputLevel=BruteLevel, OutputGroup="Grid", OutputLevel=BruteLevel / 4, Filter="Leveled")
#
#         self.RunScaleVolumeTransforms(InputGroup="Grid", InputLevel=BruteLevel / 4, OutputLevel=1)
#         self.RunSliceToVolume()
#         self.RunMosaicToVolume()
#         self.RunCreateVikingXML()
#         self.RunAssembleMosaicToVolume(Channels="(?!Registered_)", Filters="ShadingCorrected")
#
# class PMGMosicToVolumeTest(setup_pipeline.CopySetupTestBase):
#
#     @property
#     def VolumePath(self):
#         return "6259_Registered"
#
#     @property
#     def Platform(self):
#         return "PMG"
#
#     def runTest(self):
#         BruteLevel = 32
#
# #         self.RunScaleVolumeTransforms(InputGroup="Grid", InputLevel=BruteLevel / 4, OutputLevel=1)
# #         self.RunSliceToVolume()
# #         self.RunMosaicToVolume()
# #         self.RunCreateVikingXML()
#         self.RunAssembleMosaicToVolume(Channels="*", Filters="ShadingCorrected")


class ParsePMG(PMGTest):


    def setUp(self):

        super(PMGTest, self).setUp()

        self.pmgDirs = nornir_shared.files.RecurseSubdirectories(os.path.join(self.PlatformFullPath, '6259_small'), "*.pmg")
        self.pmgDirs.extend(nornir_shared.files.RecurseSubdirectories(os.path.join(self.PlatformFullPath, '6750'), "*.pmg"))
        self.assertTrue(len(self.pmgDirs) > 0, "No test input found")

    def runTest(self):

        global PMGData

        for pmgDir in self.pmgDirs:
            pmgFile = glob.glob(os.path.join(pmgDir, "*.pmg"))
            self.assertEqual(len(pmgFile), 1, "Unexpected extra PMG in dir: " + pmgDir)
            pmgFile = pmgFile[0]
            pmgFileKey = os.path.basename(pmgFile)

            pmgData = PMGData[pmgFileKey]

            FilesDict = pmg.ParsePMG(pmgFile)

            self.assertTrue(len(FilesDict) == pmgData.NumberOfImages)

            for f in FilesDict.keys():
                self.assertTrue(os.path.exists(os.path.join(pmgDir, f)))

if __name__ == "__main__":
    # import syssys.argv = ['', 'Test.testpmg']
    unittest.main()