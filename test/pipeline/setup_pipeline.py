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
from nornir_buildmanager.VolumeManagerHelpers import SearchCollection
import nornir_buildmanager.build as build
from nornir_buildmanager.validation import transforms
from nornir_imageregistration.files.mosaicfile import *
from nornir_buildmanager.argparsexml import NumberList
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


def MatchingSections(SectionNodes, sectionNumberList):
    '''Return section nodes with numbers existing in the string representing a list of integers'''
    for sectionNode in SectionNodes:
        if sectionNode.Number in sectionNumberList:
            yield sectionNode


def MatchingFilters(SectionNodes, Channels, Filters):
    '''Generator which returns a list of matching filters contained under a list of section nodes
    :param list SectionNodes: List of sections to search
    :param str Channels: Regular expression for channel names 
    :param str Filters: Regular expression for channel names
    :return: Generator of matching filters
    :rtype: FilterNode'''

    for sectionNode in SectionNodes:
        ChannelNodes = SearchCollection(sectionNode.Channels, AttribName='Name', RegExStr=Channels)

        for c in ChannelNodes:
            FilterNodes = SearchCollection(c.Filters, AttribName='Name', RegExStr=Filters)

            for f in FilterNodes:
                yield f


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

        pargs = ['-debug']

        if isinstance(pipeline, str):
            # pargs.append('-pipeline')
            pargs.append(pipeline)

        pargs.extend(['-volume', self.TestOutputPath])

        pargs.extend(args)

        return pargs

    def _CreateImportArgs(self, importpath, *args):

        pargs = [ '-debug', 'import', importpath, ]

        pargs.extend(['-volume', self.TestOutputPath])

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

    def EnsureTilePyramidIsFull(self, FilterNode, NumExpectedTiles):

        TilePyramidNode = FilterNode.TilePyramid
        self.assertIsNotNone(TilePyramidNode)

        LevelOneNode = TilePyramidNode.GetLevel(1)
        self.assertIsNotNone(LevelOneNode)

        globpath = os.path.join(LevelOneNode.FullPath, '*' + TilePyramidNode.ImageFormatExt)
        tiles = glob.glob(globpath)
        self.assertEqual(NumExpectedTiles, len(tiles), 'Did not find %d tile in %s' % (NumExpectedTiles, globpath))
        self.assertEqual(TilePyramidNode.NumberOfTiles, len(tiles), "Did not find %d tiles reported by meta-data in %s" % (TilePyramidNode.NumberOfTiles, globpath))
        return

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
        self.RunMosaic(Filter="Leveled")

    def RunImportThroughMosaicAssemble(self):
        self.RunImportThroughMosaic()
        self.RunAssemble()

    def RunImport(self):
        buildArgs = self._CreateImportArgs(self.ImportedDataPath)
        self.RunBuild(buildArgs)

    def RunPrune(self, Filter=None, Downsample=None):
        if Filter is None:
            Filter = "Raw8"

        if Downsample is None:
            Downsample = 4

        # Prune
        buildArgs = self._CreateBuildArgs('Prune', '-InputFilter', Filter, '-OutputTransform', 'Prune', '-Downsample', str(Downsample), '-Threshold', '1.0')
        volumeNode = self.RunBuild(buildArgs)

        self.assertIsNotNone(volumeNode, "No volume node returned from build")

        PruneNode = volumeNode.find("Block/Section/Channel/Transform[@Name='Prune']")
        self.assertIsNotNone(PruneNode, "No prune node produced")

        return volumeNode


    def RunSetPruneCutoff(self, Value, Section, Channels, Filters):

        buildArgs = self._CreateBuildArgs('SetPruneCutoff', '-Sections', Section, '-Channels', Channels, '-Filters', Filters, '-Value', str(Value))
        volumeNode = self.RunBuild(buildArgs)
        self.assertIsNotNone(volumeNode, "No volume node returned from build")

        SectionNode = volumeNode.find("Block/Section[@Number='%s']" % str(Section))
        self.assertIsNotNone(volumeNode, "No section node found")

        Filters = list(MatchingFilters([SectionNode], Channels, Filters))

        for fnode in Filters:
            pNode = fnode.find("Prune")
            self.assertEqual(pNode.UserRequestedCutoff, float(Value), "Value on prune node should match the passed value")

        return


    def RunSetContrast(self, MinValue, MaxValue, GammaValue, Section, Channels, Filters):

        buildArgs = self._CreateBuildArgs('SetContrast', '-Sections', Section, '-Channels',
                                           Channels, '-Filters', Filters,
                                            '-Min', str(MinValue),
                                            '-Max', str(MaxValue),
                                            '-Gamma', str(GammaValue))
        volumeNode = self.RunBuild(buildArgs)
        self.assertIsNotNone(volumeNode, "No volume node returned from build")

        SectionNode = volumeNode.find("Block/Section[@Number='%s']" % str(Section))
        self.assertIsNotNone(SectionNode, "No section node found")

        Filters = list(MatchingFilters([SectionNode], Channels, Filters))

        for fnode in Filters:
            hNode = fnode.GetHistogram()
            self.assertIsNotNone(hNode, "Filter should have histogram")

            ahNode = hNode.GetAutoLevelHint()
            self.assertIsNotNone(ahNode, "Histogram should have autolevelhint")

            if math.isnan(float(MinValue)):
                self.assertIsNone(ahNode.UserRequestedMinIntensityCutoff, "Min value should match the passed value")
            else:
                self.assertEqual(ahNode.UserRequestedMinIntensityCutoff, float(MinValue), "Min value should match the passed value")

            if math.isnan(float(MaxValue)):
                self.assertIsNone(ahNode.UserRequestedMaxIntensityCutoff, "Max value should match the passed value")
            else:
                self.assertEqual(ahNode.UserRequestedMaxIntensityCutoff, float(MaxValue), "Max value should match the passed value")

            if math.isnan(float(GammaValue)):
                self.assertIsNone(ahNode.UserRequestedGamma, "Gamma value should match the passed value")
            else:
                self.assertEqual(ahNode.UserRequestedGamma, float(GammaValue), "Gamma value should match the passed value")


    def RunSetFilterLocked(self, sectionListStr, Channels, Filters, Locked):

        buildArgs = self._CreateBuildArgs('SetFilterLock', '-Sections', sectionListStr, '-Channels',
                                           Channels, '-Filters', Filters,
                                            '-Locked', str(Locked))
        LockedVal = bool(int(Locked))
        volumeNode = self.RunBuild(buildArgs)

        sectionNumbers = NumberList(sectionListStr)
        Sections = list(MatchingSections(volumeNode.findall("Block/Section"), sectionNumbers))

        self.assertEqual(len(Sections), len(sectionNumbers), "Did not find all of the expected sections")

        Filters = list(MatchingFilters(Sections, Channels, Filters))

        for fnode in Filters:
            self.assertEqual(fnode.Locked, LockedVal, "Filter did not lock as expected")


    def RunShadingCorrection(self, ChannelPattern, CorrectionType=None, FilterPattern=None):
        if FilterPattern is None:
            FilterPattern = '(?![M|m]ask)'

        if CorrectionType is None:
            CorrectionType = 'brightfield'

        volumeNode = VolumeManager.Load(self.TestOutputPath)
        StartingFilter = volumeNode.find("Block/Section/Channel/Filter")
        self.assertIsNotNone(StartingFilter, "No starting filter node for shading correction")

        buildArgs = self._CreateBuildArgs('ShadeCorrect', '-Channels', ChannelPattern, '-Filters', FilterPattern, '-OutputFilter', 'ShadingCorrected', '-Correction', CorrectionType)
        volumeNode = self.RunBuild(buildArgs)

        ExpectedOutputFilter = 'ShadingCorrected' + StartingFilter.Name

        FilterNode = volumeNode.find("Block/Section/Channel/Filter[@Name='%s']" % ExpectedOutputFilter)
        self.assertIsNotNone(ExpectedOutputFilter, "No filter node produced for contrast adjustment")


    def RunHistogram(self, Filter=None, Downsample=4):
        if Filter is None:
            Filter = 'Raw8'

        # Adjust Contrast
        buildArgs = self._CreateBuildArgs('Histogram', '-Filters', Filter, '-Downsample', str(Downsample), '-InputTransform', 'Prune')
        volumeNode = self.RunBuild(buildArgs)

        HistogramNode = volumeNode.find("Block/Section/Channel/Filter[@Name='%s']/Histogram" % Filter)
        self.assertIsNotNone(HistogramNode, "No histogram node produced for histogram")

        return volumeNode

    def RunAdjustContrast(self, Filter=None, Gamma=None):
        if Filter is None:
            Filter = 'Raw8'

        # Adjust Contrast
        buildArgs = self._CreateBuildArgs('AdjustContrast', '-InputFilter', Filter, '-OutputFilter', 'Leveled', '-InputTransform', 'Prune')

        if not Gamma is None:
            buildArgs.extend(['-Gamma', str(Gamma)])

        volumeNode = self.RunBuild(buildArgs)

        FilterNode = volumeNode.find("Block/Section/Channel/Filter[@Name='%s']" % Filter)
        self.assertIsNotNone(FilterNode, "No filter node produced for contrast adjustment")

        return volumeNode

    def RunMosaic(self, Filter):
        if Filter is None:
            Filter = 'Leveled'
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('Mosaic', '-InputTransform', 'Prune', '-InputFilter', Filter, '-OutputTransform', 'Grid')
        volumeNode = self.RunBuild(buildArgs)

        TransformNode = volumeNode.find("Block/Section/Channel/Transform[@Name='Grid']")
        self.assertIsNotNone(TransformNode, "No final transform node produced by Mosaic pipeline")

        return volumeNode
    
    def _VerifyImageSetMatchesTransform(self, image_set_node, transform_name):
        self.assertEqual(ImageSetNode.InputTransform, transform_name, "InputTransform for ImageSet does not match transform used for assemble")
        self._CheckInputTransformChecksumCorrect(ImageSetNode, InputTransformName=transform_name)
        #Check that the InputTransform name and type match the requested transform

        AssembledImageNode = ImageSetNode.find("Level[@Downsample='%d']/Image" % (Level))
        self.assertIsNotNone(AssembledImageNode, "No Image node produced from assemble pipeline")
        
        self._CheckInputTransformChecksumCorrect(AssembledImageNode, InputTransformName=Transform)

    def RunAssemble(self, Filter=None, Transform=None, Levels=8):
        if Filter is None:
            Filter = "Leveled"
            
        if Transform is None:
            Transform = 'Grid'
        
        if not isinstance(Levels,str):
            if isinstance(Levels, list):
                LevelStr = ",".join(str(l) for l in Levels)
            else:
                LevelStr = str(Levels)
                Levels = [Levels]
        else:
            LevelStr = Levels
            Levels = NumberList(LevelStr)

        # Build Mosaics
        buildArgs = self._CreateBuildArgs('Assemble', '-Transform', Transform, '-Filters', Filter, '-Downsample', LevelStr, '-NoInterlace')
        volumeNode = self.RunBuild(buildArgs)

        #ChannelNode = volumeNode.find("Block/Section/Channel")
        
        ImageSetNodes = list(volumeNode.findall("Block/Section/Channel/Filter[@Name='%s']/ImageSet" % (Filter)))
        self.assertIsNotNone(ImageSetNodes, "ImageSet nodes not found")
        self.assertGreater(len(ImageSetNodes),0, "ImageSet nodes should be created by assemble unless this is a negative test of some sort")
        
        for ImageSetNode in ImageSetNodes: 
            self._CheckImageSetIsCorrect(ImageSetNode, Transform, Levels)

        return volumeNode
    
    
    def _CheckImageSetIsCorrect(self, image_set_node, transform, Levels):
        ''':param list Levels: Integer list of downsample levels expected'''
        
        self._CheckInputTransformIsCorrect(image_set_node, InputTransformName=transform)
        #Check that the InputTransform name and type match the requested transform

        for level in Levels:
            AssembledImageNode = image_set_node.find("Level[@Downsample='%d']/Image" % (level))
            self.assertIsNotNone(AssembledImageNode, "No Image node produced from assemble pipeline")
        
            self.assertTrue(os.path.exists(AssembledImageNode.FullPath), "Output file expected for image node after assemble runs, level %d" % (level))
        
        #self._CheckInputTransformIsCorrect(AssembledImageNode, InputTransformName=Transform)
    
    def _CheckInputTransformIsCorrect(self, InputTransformChecksumNode, InputTransformName):
        '''Check that the checksum for a transform matches the recorded input transform checksum for a node under a channel'''
        
        ChannelNode = InputTransformChecksumNode.FindParent('Channel')
        self.assertIsNotNone(ChannelNode, "Test requires the node be under a channel element")
        
        TransformNode = ChannelNode.GetTransform(InputTransformName)
        self.assertIsNotNone(TransformNode, "Could not locate transform with the correct name: %s" % (InputTransformName))
        
        self.assertEqual(InputTransformChecksumNode.InputTransform, TransformNode.Name, "Transform name does not match the transform.")
        self.assertEqual(InputTransformChecksumNode.InputTransformChecksum, TransformNode.Checksum, "Checksum does not match the transform")
        self.assertEqual(InputTransformChecksumNode.InputTransformType, TransformNode.Type, "Type does not match the transform")
        self.assertEqual(InputTransformChecksumNode.InputTransformCropBox, TransformNode.CropBox, "CropBox does not match the transform")
        
        self.assertTrue(InputTransformChecksumNode.IsInputTransformMatched(TransformNode), "IsInputTransformMatched should return true when the earlier tests in this function have passed")
        

    def RunMosaicReport(self, ContrastFilter=None, AssembleFilter=None, AssembleDownsample=8):
        if ContrastFilter is None:
            ContrastFilter = "Raw8"

        if AssembleFilter is None:
            AssembleFilter = "Leveled"

        buildArgs = self._CreateBuildArgs('MosaicReport', '-PruneFilter', 'Raw8', '-ContrastFilter', ContrastFilter, '-AssembleFilter', AssembleFilter, '-AssembleDownsample', str(AssembleDownsample))
        volumeNode = self.RunBuild(buildArgs)

        OutputHtml = glob.glob(os.path.join(self.TestOutputPath, '*.html'))
        self.assertTrue(len(OutputHtml) > 0)

        return volumeNode

    def RunCreateBlobFilter(self, Channels, Levels, Filter):
        if Channels is None:
            Channels = "*"

        if Filter is None:
            Filter = 'Leveled'

        # Build Mosaics
        buildArgs = self._CreateBuildArgs('CreateBlobFilter', '-Channels', Channels, '-InputFilter', Filter, '-Levels', Levels, '-OutputFilter', 'Blob')
        volumeNode = self.RunBuild(buildArgs)

        ChannelNode = volumeNode.find("Block/Section/Channel")

        AssembledImageNode = ChannelNode.find("Filter[@Name='Blob']/ImageSet/Level[@Downsample='%d']/Image" % 8)
        self.assertIsNotNone(AssembledImageNode, "No blob Image node produced from CreateBlobFilter pipeline")

        self.assertTrue(os.path.exists(AssembledImageNode.FullPath), "No file found for assembled image node")

        return volumeNode

    def RunAlignSections(self, Channels, Filters, Levels):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('AlignSections', '-NumAdjacentSections', '1', '-Filters', Filters, '-StosUseMasks', 'True', '-Downsample', str(Levels), '-Channels', Channels)
        volumeNode = self.RunBuild(buildArgs)

        PotentialStosMap = volumeNode.find("Block/StosMap[@Name='PotentialRegistrationChain']")
        self.assertIsNotNone(PotentialStosMap)

        FinalStosMap = volumeNode.find("Block/StosMap[@Name='FinalStosMap']")
        self.assertIsNotNone(FinalStosMap)

        StosBruteGroupNode = volumeNode.find("Block/StosGroup[@Name='StosBrute%d']" % Levels)
        self.assertIsNotNone(StosBruteGroupNode, "No Stos Group node produced")

        return volumeNode

    def RunRefineSectionAlignment(self, InputGroup, InputLevel, OutputGroup, OutputLevel, Filter):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('RefineSectionAlignment', '-InputGroup', InputGroup,
                                          '-InputDownsample', str(InputLevel),
                                          '-OutputGroup', OutputGroup,
                                          '-OutputDownsample', str(OutputLevel),
                                          '-Filter', 'Leveled',
                                          '-StosUseMasks', 'True')
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
        buildArgs = self._CreateBuildArgs('MosaicToVolume', '-InputTransform', 'Grid', '-OutputTransform', 'ChannelToVolume', '-Channels', '*')
        volumeNode = self.RunBuild(buildArgs)

        MosaicToVolumeTransformNode = volumeNode.find("Block/Section/Channel/Transform[@Name='ChannelToVolume']")
        self.assertIsNotNone(MosaicToVolumeTransformNode, "No mosaic to volume transform created")

        return volumeNode

    def RunAssembleMosaicToVolume(self, Channels, Filters=None, AssembleLevel=8):

        if Filters is None:
            Filters = "Leveled"
            
        Transform = 'ChannelToVolume'


        buildArgs = self._CreateBuildArgs('Assemble', '-ChannelPrefix', 'Registered_',
                                                               '-Channels', Channels,
                                                               '-Filters', Filters,
                                                               '-Downsample', str(AssembleLevel),
                                                               '-Transform', 'ChannelToVolume',
                                                               '-NoInterlace')
        volumeNode = self.RunBuild(buildArgs)

        FoundOutput = False
        for channelNode in volumeNode.findall("Block/Section/Channel"):
            if "Registered" in channelNode.Name:
                FoundOutput = True

        self.assertTrue(FoundOutput, "Output channel not created")
        return volumeNode

    def RunExportImages(self, Channels, Filters=None, AssembleLevel=1, Output=None):

        if Filters is None:
            Filters = "Leveled"

        if Output is None:
            Output = 'RegisteredOutput'

        # Build Mosaics
        imageOutputPath = os.path.join(self.TestOutputPath, Output)

        buildArgs = self._CreateBuildArgs('ExportImages', '-Channels', Channels,
                                                          '-Filters', Filters,
                                                          '-Downsample', str(AssembleLevel),
                                                          '-Output', imageOutputPath)
        volumeNode = self.RunBuild(buildArgs)

        OutputPngs = glob.glob(os.path.join(imageOutputPath, '*.png'))
        self.assertTrue(len(OutputPngs) > 0, "No exported images found in %s" % imageOutputPath)

        return volumeNode

    def RunCreateVikingXML(self, OutputFile, StosGroup=None, StosMap=None):

        if StosGroup is None:
            StosGroup = 'SliceToVolume1'

        if StosMap is None:
            StosMap = 'SliceToVolume'

        buildArgs = self._CreateBuildArgs('CreateVikingXML', '-StosGroup', StosGroup, '-StosMap', StosMap, '-OutputFile', OutputFile)

        if not self.TestOutputURL is None:
            buildArgs.extend(['-Host', self.TestOutputURL])

        volumeNode = self.RunBuild(buildArgs)

        self.assertTrue(os.path.exists(os.path.join(volumeNode.FullPath, OutputFile + ".VikingXML")), "No vikingxml file created")


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
        buildArgs = ['-debug', 'import', self.ImportedDataPath, '-volume', self.TestOutputPath]
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