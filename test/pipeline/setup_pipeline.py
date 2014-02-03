'''
Created on Feb 14, 2013

@author: u0490822
'''
import logging
import os
import shutil
import sys
import tempfile
import unittest

import test.testbase

from nornir_buildmanager.VolumeManagerETree import *
import nornir_buildmanager.build as build
from nornir_buildmanager.validation import transforms
from nornir_imageregistration.files.mosaicfile import *
import nornir_shared.misc


def VerifyVolume(test, VolumeObj, listVolumeEntries):
    '''Walk a list of volume entries and ensure each exists'''

    SearchEntry = VolumeObj
    if isinstance(VolumeObj, str):
        SearchEntry = VolumeManager.Load(VolumeObj)

    if not isinstance(listVolumeEntries, list):
        assert(isinstance(listVolumeEntries, VolumeEntry))
        listVolumeEntries = [listVolumeEntries]

    for entry in listVolumeEntries:
        NextSearchEntry = SearchEntry.find(entry.xpath)
        test.assertIsNotNone(NextSearchEntry, "Missing output in volume " + entry.xpath)
        SearchEntry = NextSearchEntry

    return SearchEntry


class VolumeEntry(object):

    xpathtemplate = "%(xpath)s[@%(AttribName)s='%(AttribValue)s']"

    def __init__(self, elementName, attributeName=None, attributeValue=None):
        self.XPath = elementName
        self.AttributeName = attributeName
        self.AttributeValue = attributeValue

    @property
    def xpath(self):
        x = self.XPath
        if not self.AttributeName is None:
            x = VolumeEntry.xpathtemplate % {'xpath' : self.XPath, 'AttribName' : self.AttributeName, 'AttribValue' : self.AttributeValue}

        return x


class PlatformTest(test.testbase.TestBase):
    '''Base class to use for tests that require executing commands on the pipeline.  Tests have gradually migrated to using this base class. 
       Eventually all platforms should have the same standard tests taking input to a volume under this framework to ensure basic functionality
       is operating.  At this time the IDOC platform is the only one with a complete test.  PMG has a thorough test not entirely integrated with
       this class'''

    @property
    def VolumeDir(self):
        raise Exception("VolumeDir property is deprecated, TestOutputPath instead")

    @property
    def VolumePath(self):
        raise Exception("VolumePath property not implemented")

    @property
    def Platform(self):
        raise Exception("Platform property not implemented")

    @property
    def PlatformFullPath(self):
        return os.path.join(self.TestDataPath, "PlatformRaw", self.Platform)

    @property
    def ImportedDataPath(self):

        if self.VolumePath and len(self.VolumePath) > 0:
            return os.path.join(self.PlatformFullPath, self.VolumePath)
        else:
            return self.PlatformFullPath

    def RunBuild(self, buildArgs):
        # Run a build, ensure the output directory exists, and return the volume obj
        build.Execute(buildArgs)
        self.assertTrue(os.path.exists(self.TestOutputPath), "Test input was not copied")

        VolumeObj = VolumeManager.Load(self.TestOutputPath)
        self.assertIsNotNone(VolumeObj)
        self.assertTrue(os.path.exists(VolumeObj.FullPath))

        return VolumeObj

    def _CreateBuildArgs(self, pipeline=None, *args):
        pargs = ['-input', self.ImportedDataPath, '-volume', self.TestOutputPath, '-debug']

        if isinstance(pipeline, str):
            pargs.append('-pipeline')
            pargs.append(pipeline)

        pargs.extend(args)

        return pargs

    def setUp(self):
        '''Imports a volume and stops, tests call pipeline functions'''
        super(PlatformTest, self).setUp()
        self.assertTrue(os.path.exists(self.PlatformFullPath), "Test data for platform does not exist:" + self.PlatformFullPath)


    def ValidateTransformChecksum(self, Node):
        '''Ensure that the reported checksum and actual file checksum match'''
        self.assertTrue(hasattr(Node, 'Checksum'))
        self.assertTrue(os.path.exists(Node.FullPath))
        FileChecksum = MosaicFile.LoadChecksum(Node.FullPath)
        self.assertEqual(Node.Checksum, FileChecksum)

    def CheckTransformInputs(self, transformNode):
        '''Walk every transform node, verify that if the inputtransform data matches the recorded data'''

        # If the object does not claim to have an input checksum then the test is not valid
        if not 'InputTransformChecksum' in transformNode.attrib:
            return

        # The transform should report the name of the input transform if it has a checksum
        self.assertTrue('InputTransform' in transformNode.attrib, "Missing InputTranform attribute:\n" + transformNode.ToElementString())

        self.ValidateTransformChecksum(transformNode)
        InputTransform = transformNode.Parent.GetChildByAttrib('Transform', 'Name', transformNode.InputTransform)
        self.assertIsNotNone(InputTransform)

        self.assertFalse(transforms.IsOutdated(transformNode, InputTransform))

        # Check that our reported checksum and actual file checksums match
        self.ValidateTransformChecksum(InputTransform)

        # Check that
        self.assertFalse(transforms.IsOutdated(self.PruneTransform, self.StageTransform))


    def ValidateAllTransforms(self, ParentNode):
        '''Check every transform in the parent node to ensure that if it refers to an input transform the values match'''

        TransformNodes = list(ParentNode.findall('Transform'))
        for tNode in TransformNodes:
            self.CheckTransformInputs(tNode)

    def RunImportThroughMosaic(self):
        self.RunImport()
        self.RunPrune()
        self.RunHistogram()
        self.RunAdjustContrast()
        self.RunMosaic()

    def RunImportThroughMosaicAssemble(self):
        self.RunImportThroughMosaic()
        self.RunAssemble()

    def RunImport(self):
        buildArgs = self._CreateBuildArgs(pipeline=None)
        self.RunBuild(buildArgs)

    def RunPrune(self):
        # Prune
        buildArgs = self._CreateBuildArgs('Prune', '-OutputTransform', 'Prune', '-Downsample', '4', '-Threshold', '1.0')
        volumeNode = self.RunBuild(buildArgs)

        self.assertIsNotNone(volumeNode, "No volume node returned from build")

        PruneNode = volumeNode.find("Block/Section/Channel/Transform[@Name='Prune']")
        self.assertIsNotNone(PruneNode, "No prune node produced")

        return volumeNode

    def RunHistogram(self):
        # Adjust Contrast
        buildArgs = self._CreateBuildArgs('Histogram', '-Filters', 'Raw8', '-Downsample', '4', '-InputTransform', 'Prune')
        volumeNode = self.RunBuild(buildArgs)

        HistogramNode = volumeNode.find("Block/Section/Channel/Filter[@Name='Raw8']/Histogram")
        self.assertIsNotNone(HistogramNode, "No histogram node produced for histogram")

        return volumeNode

    def RunAdjustContrast(self):

        # Adjust Contrast
        buildArgs = self._CreateBuildArgs('AdjustContrast', '-InputFilter', 'Raw8', '-OutputFilter', 'Leveled', '-InputTransform', 'Prune')
        volumeNode = self.RunBuild(buildArgs)

        FilterNode = volumeNode.find("Block/Section/Channel/Filter[@Name='Leveled']")
        self.assertIsNotNone(FilterNode, "No filter node produced for contrast adjustment")

        return volumeNode

    def RunMosaic(self):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('Mosaic', '-InputTransform', 'Prune', '-InputFilter', 'Leveled', '-OutputTransform', 'Grid')
        volumeNode = self.RunBuild(buildArgs)

        TransformNode = volumeNode.find("Block/Section/Channel/Transform[@Name='Grid']")
        self.assertIsNotNone(TransformNode, "No final transform node produced by Mosaic pipeline")

        return volumeNode

    def RunAssemble(self, Level=8):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('Assemble', '-Transform', 'Grid', '-Filters', 'Leveled', '-Downsample', str(Level), '-NoInterlace')
        volumeNode = self.RunBuild(buildArgs)

        ChannelNode = volumeNode.find("Block/Section/Channel")

        AssembledImageNode = ChannelNode.find("Filter[@Name='Leveled']/ImageSet/Level[@Downsample='%d']/Image" % Level)
        self.assertIsNotNone(AssembledImageNode, "No Image node produced from assemble pipeline")

        return volumeNode

    def RunMosaicReport(self, ContrastFilter=None):
        if ContrastFilter is None:
            ContrastFilter = "Leveled"

        buildArgs = self._CreateBuildArgs('MosaicReport', '-PruneFilter', 'Raw8', '-ContrastFilter', 'Leveled', '-AssembleFilter', 'Leveled', '-AssembleDownsample', '8')
        volumeNode = self.RunBuild(buildArgs)

        OutputHtml = glob.glob(os.path.join(self.TestOutputPath, '*.html'))
        self.assertTrue(len(OutputHtml) > 0)

        return volumeNode



    def RunCreateBlobFilter(self, Levels):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('CreateBlobFilter', '-Channels', 'AssembledTEM', '-InputFilter', 'Leveled', '-Levels', Levels, '-OuputFilter', 'Blob')
        volumeNode = self.RunBuild(buildArgs)

        ChannelNode = volumeNode.find("Block/Section/Channel[@Name='AssembledTEM']")

        AssembledImageNode = ChannelNode.find("Filter[@Name='Blob']/ImageSet/Level[@Downsample='%d']/Image" % 8)
        self.assertIsNotNone(AssembledImageNode, "No blob Image node produced from CreateBlobFilter pipeline")

        self.assertTrue(os.path.exists(AssembledImageNode.FullPath), "No file found for assembled image node")

        return volumeNode

    def RunAlignSections(self, Levels):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('AlignSections', '-NumAdjacentSections', '1', '-Filters', 'Blob', '-StosUseMasks', 'True', '-Downsample', str(Levels), '-Channels', 'AssembledTEM')
        volumeNode = self.RunBuild(buildArgs)

        PotentialStosMap = volumeNode.find("Block/StosMap[@Name='PotentialRegistrationChain']")
        self.assertIsNotNone(PotentialStosMap)

        FinalStosMap = volumeNode.find("Block/StosMap[@Name='FinalStosMap']")
        self.assertIsNotNone(FinalStosMap)

        StosBruteGroupNode = volumeNode.find("Block/StosGroup[@Name='StosBrute32']")
        self.assertIsNotNone(StosBruteGroupNode, "No Stos Group node produced")

        return volumeNode

    def RunRefineSectionAlignment(self, InputGroup, InputLevel, OutputGroup, OutputLevel):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('RefineSectionAlignment', '-InputGroup', InputGroup, '-InputDownsample', str(InputLevel), '-OutputGroup', OutputGroup, '-OutputDownsample', str(OutputLevel), '-Filter', 'Leveled', '-StosUseMasks', 'True')
        volumeNode = self.RunBuild(buildArgs)

        StosGroupNode = volumeNode.find("Block/StosGroup[@Name='%s%d']" % (OutputGroup, OutputLevel))
        self.assertIsNotNone(StosGroupNode, "No %s%d Stos Group node produced" % (OutputGroup, OutputLevel))

        return volumeNode

    def RunScaleVolumeTransforms(self, InputGroup, InputLevel, OutputLevel=1):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('ScaleVolumeTransforms', '-InputGroup', InputGroup, '-InputDownsample', str(InputLevel), '-OutputDownsample', str(OutputLevel))
        volumeNode = self.RunBuild(buildArgs)

        StosGroupNode = volumeNode.find("Block/StosGroup[@Name='%s%d']" % (InputGroup, OutputLevel))
        self.assertIsNotNone(StosGroupNode, "No %s%d Stos Group node produced" % (InputGroup, OutputLevel))

        return volumeNode

    def RunSliceToVolume(self, Level=1):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('SliceToVolume', '-InputDownsample', str(Level), '-InputGroup', 'Grid', '-OutputGroup', 'SliceToVolume')
        volumeNode = self.RunBuild(buildArgs)

        StosGroupNode = volumeNode.find("Block/StosGroup[@Name='SliceToVolume%d']" % Level)
        self.assertIsNotNone(StosGroupNode, "No SliceToVolume%d stos group node created" % Level)

        return volumeNode

    def RunMosaicToVolume(self):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('MosaicToVolume', '-InputTransform', 'Grid', '-OutputTransform', 'ChannelToVolume', '-Channels', 'TEM')
        volumeNode = self.RunBuild(buildArgs)

        MosaicToVolumeTransformNode = volumeNode.find("Block/Section/Channel[@Name='TEM']/Transform[@Name='ChannelToVolume']")
        self.assertIsNotNone(MosaicToVolumeTransformNode, "No mosaic to volume transform created")

        return volumeNode

    def RunAssembleMosaicToVolume(self, AssembleLevel=8):
        # Build Mosaics
        imageOutputPath = os.path.join(self.TestOutputPath, 'AssembleOutput')

        buildArgs = self._CreateBuildArgs('Assemble', '-ChannelPrefix', 'Registered_',
                                                               '-Channels', 'TEM',
                                                               '-Filters', 'Leveled',
                                                               '-Downsample', str(AssembleLevel),
                                                               '-Transform', 'ChannelToVolume',
                                                               '-NoInterlace',
                                                               '-Output', imageOutputPath)
        volumeNode = self.RunBuild(buildArgs)

        OutputChannelNode = volumeNode.find("Block/Section/Channel[@Name='Registered_TEM']")
        self.assertIsNotNone(OutputChannelNode, "Output channel not created")

        OutputPngs = glob.glob(os.path.join(imageOutputPath, '*.png'))
        self.assertTrue(len(OutputPngs) > 0)

        return volumeNode

    def RunCreateVikingXML(self, StosGroup=None, StosMap=None):

        if StosGroup is None:
            StosGroup = 'SliceToVolume1'

        if StosMap is None:
            StosMap = 'SliceToVolume'

        buildArgs = self._CreateBuildArgs('CreateVikingXML', '-StosGroup', StosGroup, '-StosMap', StosMap)

        if not self.TestOutputURL is None:
            buildArgs.extend(['-Host', self.TestOutputURL])

        volumeNode = self.RunBuild(buildArgs)

        self.assertTrue(os.path.exists(os.path.join(volumeNode.FullPath, "SliceToVolume1.VikingXML")), "No vikingxml file created")


class CopySetupTestBase(PlatformTest):
    '''Copies data from Platform data directory to test output directory at setup'''

    def setUp(self):
        super(CopySetupTestBase, self).setUp()

        self.assertTrue(os.path.exists(self.PlatformFullPath))

        if os.path.exists(self.TestOutputPath):
            shutil.rmtree(self.TestOutputPath)

        shutil.copytree(self.ImportedDataPath, self.TestOutputPath)


# nornir-build -volume %1 -pipeline CreateBlobFilter -Channels AssembledTEM -InputFilter Leveled -Levels 16,32 -OutputFilter Blob
# nornir-build -volume %1 -pipeline AlignSections -NumAdjacentSections 1 -Filters Blob -StosUseMasks True -Downsample 32 -Channels AssembledTEM
# nornir-build -volume %1 -pipeline RefineSectionAlignment -InputGroup StosBrute -InputDownsample 32 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled -StosUseMasks True
# nornir-build -volume %1 -pipeline RefineSectionAlignment -InputGroup Grid -InputDownsample 32 -OutputGroup Grid -OutputDownsample 16 -Filter Leveled -StosUseMasks True
# nornir-build -volume %1 -pipeline ScaleVolumeTransforms -InputGroup Grid -InputDownsample 16 -OutputDownsample 1
# nornir-build -volume %1 -pipeline SliceToVolume -InputDownsample 1 -InputGroup Grid -OutputGroup SliceToVolume
#
# nornir-build -volume %1 -pipeline MosaicToVolume -InputTransform Grid -OutputTransform ChannelToVolume -Channels TEM
#
# nornir-build -volume %1 -pipeline Assemble -Channels TEM -Filters Leveled -AssembleDownsample 8,16,32 -NoInterlace -Transform ChannelToVolume


class ImportOnlySetup(PlatformTest):
    '''Calls prepare on a PMG volume.  Used as a base class for more complex tests'''

    def setUp(self):
        super(ImportOnlySetup, self).setUp()

        # Import the files
        buildArgs = ['Build.py', '-input', self.ImportedDataPath, '-volume', self.TestOutputPath, '-debug']
        build.Execute(buildArgs)

        self.assertTrue(os.path.exists(self.TestOutputPath), "Test input was not copied")

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.TestOutputPath)
        self.assertIsNotNone(self.VolumeObj)

class PrepareSetup(PlatformTest):
    '''Calls prepare on a PMG volume.  Used as a base class for more complex tests'''
    def setUp(self):
        super(PrepareSetup, self).setUp()

        self.RunImport()
        self.RunPrune()
        self.RunHistogram()

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.TestOutputPath)
        self.assertIsNotNone(self.VolumeObj)


class PrepareAndMosaicSetup(PlatformTest):
    '''Calls prepare and mosaic pipelines on a PMG volume.  Used as a base class for more complex tests'''

    def setUp(self):

        super(PrepareAndMosaicSetup, self).setUp()
        # Import the files

        self.RunImportThroughMosaic()

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.TestOutputPath)
        self.assertIsNotNone(self.VolumeObj)

class PrepareThroughAssembleSetup(PlatformTest):
    '''Calls prepare and mosaic pipelines on a PMG volume.  Used as a base class for more complex tests'''

    def setUp(self):

        super(PrepareAndMosaicSetup, self).setUp()
        # Import the files

        self.RunImportThroughMosaicAssemble()

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.TestOutputPath)
        self.assertIsNotNone(self.VolumeObj)

if __name__ == "__main__":
    # import syssys.argv = ['', 'Test.testName']
    unittest.main()