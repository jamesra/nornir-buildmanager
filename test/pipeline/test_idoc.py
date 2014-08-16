'''
Created on Feb 13, 2013

@author: u0490822
'''
import glob
import logging
import os
import shutil
import unittest
import unittest

from nornir_buildmanager.VolumeManagerETree import VolumeManager
import nornir_buildmanager.build as build
from nornir_buildmanager.importers.idoc import SerialEMLog
import nornir_buildmanager.importers.idoc as idoc
from nornir_imageregistration.files.mosaicfile import MosaicFile
import nornir_shared.files
import nornir_shared.misc
import setup_pipeline


class IDocTest(setup_pipeline.PlatformTest):

    @property
    def classname(self):
        clsstr = str(self.__class__.__name__)
        return clsstr

    @property
    def VolumePath(self):
        return "RC2_4Square"

    @property
    def Platform(self):
        return "IDoc"

#    def setUp(self):
#
#        '''Imports a PMG volume and stops, tests call pipeline functions'''
#        TestBaseDir = os.getcwd()
#        if 'TESTDIR' in os.environ:
#            TestBaseDir = os.environ["TESTDIR"]
#
#        self.PlatformFullPath = os.path.join(os.getcwd(), "test/data/PlatformRaw/IDoc")
#        self.VolumeFullPath = os.path.join(TestBaseDir, "TestOutput", self.classname)
#
#        self.assertTrue(os.path.exists(self.PlatformFullPath), "Test input does not exist")
#
#        self.idocDirs = nornir_shared.Files.RecurseSubdirectories(self.PlatformFullPath, "*.idoc")
#        self.assertTrue(len(self.idocDirs) > 0, "No test input found")
#
#        #Remove output of earlier tests
#        if os.path.exists(self.VolumeFullPath):
#            shutil.rmtree(self.VolumeFullPath)
#
#        nornir_shared.Misc.SetupLogging(os.path.join(TestBaseDir, 'Logs', self.classname))
#        self.Logger = logging.getLogger(self.classname)

#    def tearDown(self):
#
#        if os.path.exists(self.VolumeFullPath):
#            shutil.rmtree(self.VolumeFullPath)
#

class IDocSingleSectionImportTest(IDocTest):

    @property
    def VolumePath(self):
        return "RC2_Micro/%d" % self.SectionNumber

    @property
    def SectionNumber(self):
        return 17

    def LoadMetaData(self):
        '''Updates the object's meta-data variables from disk'''

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.TestOutputPath)

        self.ChannelData = self.VolumeObj.find("Block/Section[@Number='17']/Channel")
        self.assertIsNotNone(self.ChannelData, "Could not locate channel meta-data")

        # OK, by default the transforms should be correct
        self.StageTransform = self.ChannelData.GetChildByAttrib('Transform', 'Name', 'Stage')
#        self.PruneTransform = self.ChannelData.GetChildByAttrib('Transform', 'Name', 'Prune')
#        self.TranslateTransform = self.ChannelData.GetChildByAttrib('Transform', 'Name', 'Translate')
        self.GridTransform = self.ChannelData.GetChildByAttrib('Transform', 'Name', 'Grid')
#        self.ZeroGridTransform = self.ChannelData.GetChildByAttrib('Transform', 'Name', 'ZeroGrid')

        self.assertIsNotNone(self.StageTransform)
#        self.assertIsNotNone(self.PruneTransform)
#        self.assertIsNotNone(self.TranslateTransform)
#        self.assertIsNotNone(self.GridTransform)
#        self.assertIsNotNone(self.ZeroGridTransform)

    def _getFilterNode(self, BlockNode, SectionNumber):
        SectionNode = BlockNode.GetSection(SectionNumber)
        self.assertIsNotNone(SectionNode)

        ChannelNode = SectionNode.GetChannel('TEM')
        self.assertIsNotNone(ChannelNode)

        FilterNode = ChannelNode.GetFilter('Raw8')
        self.assertIsNotNone(FilterNode)

        return FilterNode

    def runTest(self):

        self.RunImport()
        self.LoadMetaData()

        SectionNodes = list(self.VolumeObj.findall("Block/Section"))
        self.assertEqual(len(SectionNodes), 1)

        IDocData = self.ChannelData.GetChildByAttrib('Data', 'Name', 'IDoc')
        self.assertIsNotNone(IDocData)

        LogData = self.ChannelData.GetChildByAttrib('Data', 'Name', 'Log')
        self.assertIsNotNone(LogData)

        BlockNode = self.VolumeObj.find('Block')
        self.assertIsNotNone(BlockNode)

        self.EnsureTilePyramidIsFull(self._getFilterNode(BlockNode, self.SectionNumber), 25)

        self.RunSetFilterLocked(str(self.SectionNumber), Channels="TEM", Filters="Raw8", Locked="1")
        self.RunSetFilterLocked(str(self.SectionNumber), Channels="TEM", Filters="Raw8", Locked="0")


# class IDocAlignOutputTest(setup_pipeline.CopySetupTestBase):
#     '''Attemps an alignment on a cached copy of the output from IDocBuildTest'''
#
#     @property
#     def VolumePath(self):
#         return "RC2_4Square_Aligned"
#
#     @property
#     def Platform(self):
#         return "IDOC"
#
#     def runTest(self):
#         # Doesn't need to run if IDocBuildTest is run, here for debugging convienience if it fails
#
#         BruteLevel = 32
#         self.RunScaleVolumeTransforms(InputGroup="Grid", InputLevel=BruteLevel / 4, OutputLevel=1)
#         self.RunSliceToVolume()
#         self.RunMosaicToVolume()
#         self.RunCreateVikingXML()
#         self.RunAssembleMosaicToVolume(Channels="TEM")

class IDocBuildTest(IDocTest):

    def runTest(self):

        self.RunImport()
        self.RunPrune()

        self.RunSetPruneCutoff(Value="7.5", Section="693", Channels="*", Filters="Raw8")

        self.RunHistogram()

        self.RunSetContrast(MinValue="125", MaxValue="NaN", GammaValue="NaN", Section="693", Channels="*", Filters="Raw8")

        self.RunAdjustContrast()

        self.RunSetFilterLocked('693', Channels="TEM", Filters="Leveled", Locked="1")
        self.RunSetFilterLocked('693', Channels="TEM", Filters="Leveled", Locked="0")

        self.RunMosaic(Filter="Leveled")
        self.RunMosaicReport()
        self.RunAssemble(Levels=[8,16])
        self.RunCreateVikingXML(StosGroup=None, StosMap=None, OutputFile="Mosaic")
        self.RunMosaicReport()

        # Copy output here to run IDocAlignTest

        BruteLevel = 32

        self.RunCreateBlobFilter(Channels="TEM", Filter="Leveled", Levels="8,16,%d" % (BruteLevel))
        self.RunAlignSections(Channels="TEM", Filters="Blob", Levels=BruteLevel)
        self.RunRefineSectionAlignment(InputGroup="StosBrute", InputLevel=BruteLevel, OutputGroup="Grid", OutputLevel=BruteLevel, Filter="Leveled")
        self.RunRefineSectionAlignment(InputGroup="Grid", InputLevel=BruteLevel, OutputGroup="Grid", OutputLevel=BruteLevel / 4, Filter="Leveled")

        # Copy output here to run IDocAlignOutputTest

        self.RunScaleVolumeTransforms(InputGroup="Grid", InputLevel=BruteLevel / 4, OutputLevel=1)
        self.RunSliceToVolume()
        self.RunMosaicToVolume()
        self.RunCreateVikingXML(StosGroup='SliceToVolume1', StosMap='SliceToVolume', OutputFile="SliceToVolume")
        self.RunAssembleMosaicToVolume(Channels="TEM")
        self.RunExportImages(Channels="Registered", Filters="Leveled", AssembleLevel=16)

# class IDocAlignTest(setup_pipeline.CopySetupTestBase):
#     '''Attemps an alignment on a cached copy of the output from IDocBuildTest'''
#
#     @property
#     def VolumePath(self):
#         return "RC2_4Square_Assembled"
#
#     @property
#     def Platform(self):
#         return "IDOC"
#
#     def runTest(self):
#          # Doesn't need to run if IDocBuildTest is run, here for debugging convienience if it fails
#         # return
#
#         BruteLevel = 32
#         self.RunCreateBlobFilter(Channels="TEM", Filter="Leveled", Levels="8,16,%d" % (BruteLevel))
#         self.RunAlignSections(Channels="TEM", Filters="Blob", Levels=BruteLevel)
#         self.RunRefineSectionAlignment(InputGroup="StosBrute", InputLevel=BruteLevel, OutputGroup="Grid", OutputLevel=BruteLevel, Filter="Leveled")
#         self.RunRefineSectionAlignment(InputGroup="Grid", InputLevel=BruteLevel, OutputGroup="Grid", OutputLevel=BruteLevel / 4, Filter="Leveled")
#
#         self.RunScaleVolumeTransforms(InputGroup="Grid", InputLevel=BruteLevel / 4, OutputLevel=1)
#         self.RunSliceToVolume()
#         self.RunMosaicToVolume()
#         self.RunCreateVikingXML()
#         self.RunAssembleMosaicToVolume(Channels="TEM")


class IdocReaderTest(IDocTest):

    @property
    def VolumePath(self):
        return "RC2_Micro"

    def runTest(self):
        NumLogTiles = 25

        idocDir = os.path.join(self.ImportedDataPath, '17/*.idoc')
        idocFiles = glob.glob(idocDir)

        self.assertEqual(len(idocFiles), 1, "Idoc file not found")

        idocFile = idocFiles[0]
        self.assertTrue(os.path.exists(idocFile))

        IDocData = idoc.IDoc.Load(idocFile)

        self.assertEqual(IDocData.ImageSeries, 1)
        self.assertEqual(IDocData.PixelSpacing, 21.76)
        self.assertEqual(IDocData.ImageSize, [4080, 4080])
        self.assertEqual(IDocData.Montage, 1)
        self.assertEqual(IDocData.DataMode, 1)

        self.assertEqual(len(IDocData.tiles), NumLogTiles, "Incorrect number of tiles found in log, expected " + str(NumLogTiles) + ", got " + str(len(IDocData.tiles)))

        # From the log file we see that the tile wtih Z=6 should have a drift of 0.69 nm/sec
        TileData = IDocData.tiles[5]
        self.assertIsNotNone(TileData)
        self.assertEqual(TileData.Image, '10005.tif')
        self.assertEqual(TileData.Magnification, 5000)
        self.assertEqual(TileData.Intensity, 0.549157)
        self.assertEqual(TileData.SpotSize, 2)
        self.assertEqual(TileData.ExposureTime, 0.75)
        self.assertEqual(TileData.RotationAngle , -178.3)
        self.assertEqual(TileData.Defocus , -6.8902)
        self.assertEqual(TileData.PieceCoordinates, [3590, 7180, 0])
        return

class LogReaderTest(IDocTest):

    @property
    def VolumePath(self):
        return "RC2_Micro"

    def validateLogEntries(self, LogData):
        NumLogTiles = 25

        self.assertEqual(LogData.Version, "3.1.1a,  built Nov  9 2011  14:20:16")
        self.assertEqual(LogData.Startup, "4/22/2012  16:03:59")
        self.assertEqual(LogData.PropertiesVersion, "Sep 30, 2011")
        self.assertEqual(LogData.MontageStart, 4719.609)
        self.assertEqual(LogData.MontageEnd, 5467.422)

        self.assertEqual(len(LogData.tileData), NumLogTiles, "Incorrect number of tiles found in log, expected " + str(NumLogTiles) + ", got " + str(len(LogData.tileData)))

        # From the log file we see that the tile wtih Z=6 should have a drift of 0.69 nm/sec
        TileData = LogData.tileData[6]
        self.assertIsNotNone(TileData)
        self.assertEqual(TileData.drift, 0.69)
        self.assertEqual(TileData.driftUnits, "nm/sec")
        self.assertEqual(len(TileData.driftStamps), 1)
        self.assertEqual(TileData.startTime, 4924.937)
        self.assertEqual(TileData.endTime, 4948.016)

         # From the log file we see that the tile wtih Z=22 should have two recorded drifts, 1.38, 0.9
        TileData = LogData.tileData[23]
        self.assertIsNotNone(TileData)
        self.assertEqual(TileData.drift, 0.9)
        self.assertEqual(TileData.driftUnits, "nm/sec")
        self.assertEqual(len(TileData.driftStamps), 2)
        self.assertEqual(TileData.stageStopTime, 5409.531)

        self.assertEqual(TileData.driftStamps[0], (5412.5 - TileData.stageStopTime, 1.38))
        self.assertEqual(TileData.driftStamps[1], (5423.234 - TileData.stageStopTime, 0.9))

        self.assertEqual(TileData.startTime, 5402.328)
        self.assertEqual(TileData.endTime, 5433.453)

    def runTest(self):
        logDir = os.path.join(self.ImportedDataPath, '17/*.log')
        logFiles = glob.glob(logDir)

        self.assertEqual(len(logFiles), 1)

        logFile = logFiles[0]
        self.assertTrue(os.path.exists(logFile))

        LogData = SerialEMLog.Load(logFile, usecache=False)
        self.validateLogEntries(LogData)

        cachedLogData = SerialEMLog.Load(logFile, usecache=True)
        self.validateLogEntries(cachedLogData)

        outputGrid = os.path.join(self.TestOutputPath, 'Grid_' + os.path.basename(logFile) + '.png')
        outputDrift = os.path.join(self.TestOutputPath, 'Drift_' + os.path.basename(logFile) + '.png')

        idoc.PlotDriftGrid(cachedLogData, outputGrid)
        idoc.PlotDriftSettleTime(cachedLogData, outputDrift)
        return

if __name__ == "__main__":
    # import syssys.argv = ['', 'Test.testName']
    unittest.main()