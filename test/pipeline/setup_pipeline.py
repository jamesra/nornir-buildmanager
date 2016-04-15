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
import time
import datetime

import test.testbase


from nornir_buildmanager.VolumeManagerETree import *
from nornir_buildmanager.VolumeManagerHelpers import SearchCollection
import nornir_buildmanager.build as build
from nornir_buildmanager.validation import transforms
from nornir_imageregistration.files.mosaicfile import *
from nornir_buildmanager.argparsexml import NumberList
import nornir_shared.misc
import nornir_imageregistration.files
 


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
            
            
def SectionNumberList(SectionNodes=None):
    '''List of section numbers'''
    numbers = []
    for n in SectionNodes:
        numbers.append(n.Number) 
    
    return numbers


def FullPathsForNodes(node_list):
    list_full_paths = []
    for n in node_list:
        list_full_paths.append(n.FullPath)
        
    return list_full_paths


def BuildPathToModifiedDateMap(path_list):
    '''Given a list of paths, construct a dictionary which maps to a cached last modified date'''
    file_to_modified_time= {}
    for file_path in path_list:
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
        file_to_modified_time[file_path] = mtime
        
    return  file_to_modified_time


def EnumerateFilters(SectionNodes, Channels, Filters):
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
             
                
def EnumerateImageSets(testObj, volumeNode, Channels, Filter, RequireMasks=True):
    '''Used after assemble or blob create an imageset to ensure the correct levels exist'''
    
    sections = list(volumeNode.findall("Block/Section"))
    filters = EnumerateFilters(sections, Channels, Filter)
    
    for f in filters:
        if RequireMasks:
            testObj.assertTrue(f.HasMask, "Mask expected for filters")
            
        image_sets = list(f.findall('ImageSet'))
        testObj.assertIsNotNone(image_sets, "ImageSet node not found")
        testObj.assertEqual(len(image_sets),1, "Multiple ImageSet nodes found")
        yield image_sets[0]
        
def EnumerateTileSets(testObj, volumeNode, Channels, Filter):
    '''Used after assemble or blob create an imageset to ensure the correct levels exist'''
    
    sections = list(volumeNode.findall("Block/Section"))
    filters = EnumerateFilters(sections, Channels, Filter)
    
    for f in filters:
        tile_sets = list(f.findall('Tileset'))
        testObj.assertIsNotNone(tile_sets, "Tileset node not found")
        testObj.assertEqual(len(tile_sets),1, "Multiple Tileset nodes found")
        yield tile_sets[0]


def ConvertLevelsToList(Levels):
    if not isinstance(Levels, str):
        if not isinstance(Levels, list):
            Levels = [Levels]
    else:
        Levels = NumberList(Levels)
        
    return Levels


def ConvertLevelsToString(Levels):
    if not isinstance(Levels, str):
        if isinstance(Levels, list):
            LevelStr = ",".join(str(l) for l in Levels)
        else:
            LevelStr = str(Levels)
            Levels = [Levels]
    else:
        LevelStr = Levels
        Levels = NumberList(LevelStr)
        
    return LevelStr

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
        return os.path.join(self.TestInputPath, "PlatformRaw", self.Platform)
    
    
    @property
    def TestSetupCachePath(self):
        '''The directory where we can cache the setup phase of the tests.  Delete to obtain a clean run of all tests'''
        if 'TESTOUTPUTPATH' in os.environ:
            TestOutputDir = os.environ["TESTOUTPUTPATH"]
            return os.path.join(TestOutputDir, 'Cache', self.Platform, self.VolumePath)
        else:
            self.fail("TESTOUTPUTPATH environment variable should specify input data directory")

        return None
     
    @property
    def ImportedDataPath(self):

        if self.VolumePath and len(self.VolumePath) > 0:
            return os.path.join(self.PlatformFullPath, self.VolumePath)
        else:
            return self.PlatformFullPath

    def RunBuild(self, buildArgs):
        '''Run a build, ensure the output directory exists, and return the volume obj'''
        print("Run build : %s" % str(buildArgs))
        build.Execute(buildArgs)
        self.assertTrue(os.path.exists(self.TestOutputPath), "Test input was not copied")
        return self.LoadVolume()
        
    def LoadVolume(self):
        '''Load the volume meta data from disk''' 
        VolumeObj = VolumeManager.Load(self.TestOutputPath)
        self.assertIsNotNone(VolumeObj)
        self.assertTrue(os.path.exists(VolumeObj.FullPath))

        return VolumeObj

    def _CreateBuildArgs(self, pipeline=None, *args):

        pargs = [self.TestOutputPath, '-debug']

        if isinstance(pipeline, str):
            # pargs.append('-pipeline')
            pargs.append(pipeline)

        #pargs.extend([self.TestOutputPath])

        pargs.extend(args)

        return pargs

    def _CreateImportArgs(self, importer, importpath, *args):

        pargs = [self.TestOutputPath, '-debug', importer, importpath]

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

        
        self.assertTrue(transformNode.IsInputTransformMatched(InputTransform))

        # Check that our reported checksum and actual file checksums match
        self.ValidateTransformChecksum(InputTransform)

        # Check that
        self.assertTrue(self.PruneTransform.IsInputTransformMatched(self.StageTransform))

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
        if 'idoc' in self.Platform.lower():
            return self.RunIDocImport()
        elif 'pmg' in self.Platform.lower():
            return self.RunPMGImport()
        elif 'dm4' in self.Platform.lower():
            return self.RunDM4Import()
            
        raise NotImplementedError("Derived classes should point RunImport at a specific importer")

    def RunIDocImport(self):
        buildArgs = self._CreateImportArgs('ImportIDoc', self.ImportedDataPath)
        self.RunBuild(buildArgs)
        
    def RunPMGImport(self):
        buildArgs = self._CreateImportArgs('ImportPMG', self.ImportedDataPath)
        self.RunBuild(buildArgs)
        
    def RunDM4Import(self):
        buildArgs = self._CreateImportArgs('ImportDM4', self.ImportedDataPath)
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
        
        #Delete one prune data file, and make sure the associated .mosaic regenerates
        return volumeNode


    def RunSetPruneCutoff(self, Value, Section, Channels, Filters):

        buildArgs = self._CreateBuildArgs('SetPruneCutoff', '-Sections', Section, '-Channels', Channels, '-Filters', Filters, '-Value', str(Value))
        volumeNode = self.RunBuild(buildArgs)
        self.assertIsNotNone(volumeNode, "No volume node returned from build")

        SectionNode = volumeNode.find("Block/Section[@Number='%s']" % str(Section))
        self.assertIsNotNone(volumeNode, "No section node found")

        Filters = list(EnumerateFilters([SectionNode], Channels, Filters))

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

        Filters = list(EnumerateFilters([SectionNode], Channels, Filters))

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

        Filters = list(EnumerateFilters(Sections, Channels, Filters))

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


    def RunHistogram(self, Filter=None, Downsample=4, Transform=None):
        if Filter is None:
            Filter = 'Raw8'
        
        if Transform is None:
            Transform = "Prune"

        # Adjust Contrast
        buildArgs = self._CreateBuildArgs('Histogram', '-Filters', Filter, '-Downsample', str(Downsample), '-InputTransform', Transform)
        volumeNode = self.RunBuild(buildArgs)

        HistogramNode = volumeNode.find("Block/Section/Channel/Filter[@Name='%s']/Histogram" % Filter)
        self.assertIsNotNone(HistogramNode, "No histogram node produced for histogram")

        return volumeNode

    def RunAdjustContrast(self, Sections=None, Filter=None, Gamma=None, Transform=None):
        if Filter is None:
            Filter = 'Raw8'
            
        if Transform is None:
            Transform = 'Prune'

        # Adjust Contrast
        if Sections is None:
            buildArgs = self._CreateBuildArgs('AdjustContrast', '-InputFilter', Filter, '-OutputFilter', 'Leveled', '-InputTransform', Transform)
        else:
            buildArgs = self._CreateBuildArgs('AdjustContrast', '-Sections', str(Sections), '-InputFilter', Filter, '-OutputFilter', 'Leveled', '-InputTransform', Transform)

        if not Gamma is None:
            buildArgs.extend(['-Gamma', str(Gamma)])

        volumeNode = self.RunBuild(buildArgs)

        FilterNode = volumeNode.find("Block/Section/Channel/Filter[@Name='%s']" % Filter)
        self.assertIsNotNone(FilterNode, "No filter node produced for contrast adjustment")

        return volumeNode

    def RunMosaic(self, Filter, Transform=None):
        if Filter is None:
            Filter = 'Leveled'
        
        if Transform is None:
            Transform = 'Prune'
            
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('Mosaic', '-InputTransform', Transform, '-InputFilter', Filter, '-OutputTransform', 'Grid', '-Iterations', "3", '-Threshold', "1.0")
        volumeNode = self.RunBuild(buildArgs)

        TransformNode = volumeNode.find("Block/Section/Channel/Transform[@Name='Grid']")
        self.assertIsNotNone(TransformNode, "No final transform node produced by Mosaic pipeline")

        return volumeNode
        
    
    def _VerifyInputTransformIsCorrect(self, InputTransformChecksumNode, InputTransformName):
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
    
    
    def _VerifyImageSetMatchesTransform(self, image_set_node, transform_name):
        
        self.assertEqual(image_set_node.InputTransform, transform_name, "InputTransform for ImageSet does not match transform used for assemble")
        self._VerifyInputTransformIsCorrect(image_set_node, InputTransformName=transform_name)
        
    
    def _VerifyPyramidHasExpectedLevels(self, pyramid_node, expected_levels, ):
        '''Used to ensure that any node with level children nodes has the correct levels'''
        for level in expected_levels:
            LevelNode = pyramid_node.find("Level[@Downsample='%d']" % (level))
            self.assertIsNotNone(LevelNode, "No Level node at level %d" % (level))
            self.assertTrue(os.path.exists(LevelNode.FullPath), "Output directory expected for level node, level %d" % (level))
        
     
    
    def _VerifyImageSetHasExpectedLevels(self, image_set_node, expected_levels, ):
        for level in expected_levels:
            AssembledImageNode = image_set_node.find("Level[@Downsample='%d']/Image" % (level))
            self.assertIsNotNone(AssembledImageNode, "No Image node at level %d produced from assemble pipeline" % (level))
            self.assertTrue(os.path.exists(AssembledImageNode.FullPath), "Output file expected for image node after assemble runs, level %d" % (level))
        
        # self._VerifyInputTransformIsCorrect(AssembledImageNode, InputTransformName=Transform)
             
    def RunAssemble(self, Channels=None, Filter=None, TransformName=None, Levels=8):
        if Filter is None:
            Filter = "Leveled"
            
        if TransformName is None:
            TransformName = 'Grid'
        
        Levels = ConvertLevelsToList(Levels)
        LevelsStr = ConvertLevelsToString(Levels) 
        # Build Mosaics
        buildArgs = []
        if not Channels is None:
            buildArgs = self._CreateBuildArgs('Assemble', '-Channels', Channels, '-Transform', TransformName, '-Filters', Filter, '-Downsample', LevelsStr, '-NoInterlace')
        else:
            buildArgs = self._CreateBuildArgs('Assemble', '-Transform', TransformName, '-Filters', Filter, '-Downsample', LevelsStr, '-NoInterlace')
            
        volumeNode = self.RunBuild(buildArgs)
 
        for image_set_node in EnumerateImageSets(self, volumeNode, Channels, Filter, RequireMasks=True) : 
            self._VerifyImageSetHasExpectedLevels(image_set_node, Levels)
            self._VerifyImageSetMatchesTransform(image_set_node, TransformName)

        return volumeNode
    
    def RunAssembleTiles(self, Channels=None, Filter=None, TransformName=None, Levels=1, Shape=[512,512]):
        if Filter is None:
            Filter = "Leveled"
            
        if TransformName is None:
            TransformName = 'Grid'
        
        Levels = ConvertLevelsToList(Levels)
        LevelsStr = ConvertLevelsToString(Levels) 
        
        ShapeStr = ConvertLevelsToString(Shape)
        
        # Build Mosaics
        buildArgs = []
        if not Channels is None:
            buildArgs = self._CreateBuildArgs('AssembleTiles', '-Channels', Channels, '-Transform', TransformName, '-Filters', Filter, '-Downsample', LevelsStr, '-Shape', ShapeStr)
        else:
            buildArgs = self._CreateBuildArgs('AssembleTiles', '-Transform', TransformName, '-Filters', Filter, '-Downsample', LevelsStr, '-Shape', ShapeStr)
            
        volumeNode = self.RunBuild(buildArgs)
 
        for tile_set_node in EnumerateTileSets(self, volumeNode, Channels, Filter) : 
            self._VerifyPyramidHasExpectedLevels(tile_set_node, Levels) 

        return volumeNode
    

    def RunMosaicReport(self, ContrastFilter=None, AssembleFilter=None, AssembleDownsample=8, OutputFile=None):
        if ContrastFilter is None:
            ContrastFilter = "Raw8"

        if AssembleFilter is None:
            AssembleFilter = "Leveled"
        
        if OutputFile is None:
            OutputFile = 'MosaicReport'
         
        buildArgs = self._CreateBuildArgs('MosaicReport', '-PruneFilter', 'Raw8',
                                                          '-ContrastFilter', ContrastFilter,
                                                          '-AssembleFilter', AssembleFilter,
                                                          '-AssembleDownsample', str(AssembleDownsample),
                                                          '-Output', OutputFile)
        volumeNode = self.RunBuild(buildArgs)

        if OutputFile.endswith('.html'):
            OutputHtml = os.path.join(self.TestOutputPath, OutputFile)
        else:
            OutputHtml = os.path.join(self.TestOutputPath, OutputFile + '.html')
        
        self.assertTrue(os.path.exists(OutputHtml))

        return volumeNode

    def RunCreateBlobFilter(self, Channels, Levels, Filter):
        if Channels is None:
            Channels = "*"

        if Filter is None:
            Filter = 'Leveled'
            
        Levels = ConvertLevelsToList(Levels)
        LevelsStr = ConvertLevelsToString(Levels) 

        # Build Mosaics
        buildArgs = self._CreateBuildArgs('CreateBlobFilter', '-Channels', Channels, '-InputFilter', Filter, '-Levels', LevelsStr, '-OutputFilter', 'Blob')
        volumeNode = self.RunBuild(buildArgs)

        for image_set_node in EnumerateImageSets(self, volumeNode, Channels, Filter='Blob', RequireMasks=True) : 
            self._VerifyImageSetHasExpectedLevels(image_set_node, Levels)
            #self._VerifyImageSetMatchesTransform(image_set_node, TransformName)
            
        return volumeNode
    
    
    def _StosFileHasMasks(self,stosfileFullPath):
        stosfileObj = nornir_imageregistration.files.StosFile.Load(stosfileFullPath)
        return stosfileObj.HasMasks
    
    
    def _StosGroupHasMasks(self, stos_group_node):
        transformNodes = list(stos_group_node.findall("SectionMappings/Transform"))
        return self._StosFileHasMasks(transformNodes[0].FullPath)
    
    
    def VerifyStosTransformPipelineSharedTests(self, volumeNode, stos_map_name, stos_group_name, MasksRequired, buildArgs):
        '''Ensure every section has a transform and that the transform is not regenerated if the pipeline is run twice.'''
        stos_map_node = volumeNode.find("Block/StosMap[@Name='%s']" % (stos_map_name))
        self.assertIsNotNone(stos_map_node)
        
        stos_group_node = volumeNode.find("Block/StosGroup[@Name='%s']" % (stos_group_name))
        self.assertIsNotNone(stos_group_node)
        
        sectionNodes = volumeNode.findall('Block/Section')
        self.assertIsNotNone(sectionNodes)
        self.VerifySectionsHaveStosTransform(stos_group_node, stos_map_node.CenterSection, sectionNodes)
        
        #Run align again, make sure the last modified date is unchanged
        full_paths = FullPathsForNodes(stos_group_node.findall("SectionMappings/Transform"))
        transform_last_modified = BuildPathToModifiedDateMap(full_paths)
        
        #Make sure our output stos file has masks if they were called for
        self.assertEqual(self._StosFileHasMasks(full_paths[0]), MasksRequired, "%s does not match mask expectation, masks expected = %s" % (full_paths[0], '-UseMasks' in buildArgs))
          
        volumeNode = self.RunBuild(buildArgs)
        self.VerifyFilesLastModifiedDateUnchanged(transform_last_modified)
        

    def RunAlignSections(self, Channels, Filters, Levels, Center=None, UseMasks=True):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('AlignSections', '-NumAdjacentSections', '1', '-Filters', Filters, '-Downsample', str(Levels), '-Channels', Channels)
        if not Center is None:
            buildArgs = self._CreateBuildArgs('AlignSections', '-NumAdjacentSections', '1', '-Filters', Filters, '-Downsample', str(Levels), '-Channels', Channels, '-Center', str(Center))
            
        if UseMasks:
            buildArgs.append('-UseMasks')             
        
        volumeNode = self.RunBuild(buildArgs)
        self.assertIsNotNone(volumeNode)

        self.VerifyStosTransformPipelineSharedTests(volumeNode, stos_map_name='PotentialRegistrationChain', stos_group_name='%s%d' % ('StosBrute', Levels), MasksRequired='-UseMasks' in buildArgs, buildArgs=buildArgs)
    
        return volumeNode
    
    
    def VerifySectionsHaveStosTransform(self, stos_group_node, center_section, sectionNodes):
        '''Ensure that each section in the list is mapped by a transform in the stos group'''
        # Check that there are transforms 
        sectionNumbers = SectionNumberList(sectionNodes)
        self.assertGreater(len(sectionNumbers), 0)
        
        # Remove the center section
        sectionNumbers.remove(center_section)
        
        for section_number in sectionNumbers:
            transform = stos_group_node.find("SectionMappings/Transform[@MappedSectionNumber='%d']" % (section_number))
            self.assertIsNotNone(transform, "Missing transform mapping section %d" % (section_number))
            
    
    def VerifyFilesLastModifiedDateUnchanged(self, file_last_modified_map):
        '''Takes a dictionary of {file_path:  last_modified}.  Fails if a files modified time on disk does not match the value in the dictionary'''
        
        for (file_path, last_modified_reference) in file_last_modified_map.items():
            disk_modified_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
            self.assertEqual(last_modified_reference, disk_modified_time, "Last modified date for %s should not be different" % (file_path))
            
    
    def VerifyFilesLastModifiedDateChanged(self, file_last_modified_map):
        '''Takes a dictionary of {file_path:  last_modified}.  Fails if a files modified time on disk does not match the value in the dictionary'''
        
        for (file_path, last_modified_reference) in file_last_modified_map.items():
            disk_modified_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
            self.assertGreater(disk_modified_time, last_modified_reference, "Last modified date for %s is %s, should be later than %s" % (file_path, str(disk_modified_time), str(last_modified_reference)))
            
    
    def RunAssembleStosOverlays(self, Group, Downsample, StosMap):
        
        buildArgs = self._CreateBuildArgs('AssembleStosOverlays', '-StosGroup', Group, '-Downsample', str(Downsample), '-StosMap', StosMap)
        volumeNode = self.RunBuild(buildArgs)
                
        InputStosMap = volumeNode.find("Block/StosMap[@Name='%s']" % (StosMap))
        self.assertIsNotNone(InputStosMap)
        
        stos_group_node = volumeNode.find("Block/StosGroup[@Name='%s%d']" % (Group, Downsample))
        self.assertIsNotNone(stos_group_node)
        
        # Run the pipeline again and ensure that the overlay images are not recreated
        sectionNodes = volumeNode.findall('Block/Section')
        self.assertIsNotNone(sectionNodes)
        
        image_nodes = list(stos_group_node.findall('SectionMappings/Image'))
        self.assertGreater(len(image_nodes), 0, "Images should be produced by RunAssembleStosOverlays")
        
        #Check that the overlays are not regenerated on a rebuild
        full_paths = FullPathsForNodes(image_nodes)
        image_last_modified = BuildPathToModifiedDateMap(full_paths)
        
        volumeNode = self.RunBuild(buildArgs)
        self.VerifyFilesLastModifiedDateUnchanged(image_last_modified) 
         
        return volumeNode
    
    
    
    def RunSelectBestRegistrationChain(self, Group, Downsample, InputStosMap, OutputStosMap):
        
        buildArgs = self._CreateBuildArgs('SelectBestRegistrationChain', '-StosGroup', Group, '-Downsample', str(Downsample), '-InputStosMap', InputStosMap, '-OutputStosMap', OutputStosMap)
        volumeNode = self.RunBuild(buildArgs)
        
        InputStosMap = volumeNode.find("Block/StosMap[@Name='%s']" % (InputStosMap))
        self.assertIsNotNone(InputStosMap)

        OutputStosMap = volumeNode.find("Block/StosMap[@Name='%s']" % (OutputStosMap))
        self.assertIsNotNone(OutputStosMap)
        
        self.assertEqual(InputStosMap.CenterSection, OutputStosMap.CenterSection, "Center section should not change when selecting the best registration chain")
        
        return volumeNode
    

    def RunRefineSectionAlignment(self, InputGroup, InputLevel, OutputGroup, OutputLevel, Filter, UseMasks=True):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('RefineSectionAlignment', '-InputGroup', InputGroup,
                                          '-InputDownsample', str(InputLevel),
                                          '-OutputGroup', OutputGroup,
                                          '-OutputDownsample', str(OutputLevel),
                                          '-Filter', 'Leveled', 
                                          '-Iterations', "3",
                                          '-Threshold', "1.0")
        if UseMasks:
            buildArgs.append('-UseMasks')   
            
        volumeNode = self.RunBuild(buildArgs)

        stos_group_node = volumeNode.find("Block/StosGroup[@Name='%s%d']" % (OutputGroup, OutputLevel))
        self.assertIsNotNone(stos_group_node, "No %s%d Stos Group node produced" % (OutputGroup, OutputLevel))
          
        self.VerifyStosTransformPipelineSharedTests(volumeNode=volumeNode, stos_group_name='%s%d' % (OutputGroup, OutputLevel), stos_map_name='FinalStosMap',  MasksRequired='-UseMasks' in buildArgs, buildArgs=buildArgs)
         
        return volumeNode

    def RunScaleVolumeTransforms(self, InputGroup, InputLevel, OutputLevel=1):
        # Build Mosaics
        buildArgs = self._CreateBuildArgs('ScaleVolumeTransforms', '-InputGroup', InputGroup, '-InputDownsample', str(InputLevel), '-OutputDownsample', str(OutputLevel))
        volumeNode = self.RunBuild(buildArgs)

        StosGroupNode = volumeNode.find("Block/StosGroup[@Name='%s%d']" % (InputGroup, OutputLevel))
        self.assertIsNotNone(StosGroupNode, "No %s%d Stos Group node produced" % (InputGroup, OutputLevel))
        
        MasksRequired = self._StosGroupHasMasks(StosGroupNode)
        
        self.VerifyStosTransformPipelineSharedTests(volumeNode=volumeNode, stos_group_name='%s%d' % (InputGroup, OutputLevel), stos_map_name='FinalStosMap', MasksRequired=MasksRequired, buildArgs=buildArgs)
        return volumeNode

    def RunSliceToVolume(self, Level=1):
        # Build Mosaics
        group_name = 'SliceToVolume'
        buildArgs = self._CreateBuildArgs('SliceToVolume', '-InputDownsample', str(Level), '-InputGroup', 'Grid', '-OutputGroup', 'SliceToVolume')
        volumeNode = self.RunBuild(buildArgs)

        StosGroupNode = volumeNode.find("Block/StosGroup[@Name='%s%d']" % (group_name,Level))
        self.assertIsNotNone(StosGroupNode, "No SliceToVolume%d stos group node created" % Level)
        
        MasksRequired = self._StosGroupHasMasks(StosGroupNode)
        
        self.VerifyStosTransformPipelineSharedTests(volumeNode=volumeNode, stos_group_name='%s%d' % (group_name, Level), stos_map_name='FinalStosMap', MasksRequired=MasksRequired, buildArgs=buildArgs)
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
        
        filters = EnumerateFilters(volumeNode.findall("Block/Section"), Channels, Filters)
        NumImages = 0
        for f in filters:
            if f.Imageset.HasImage(AssembleLevel):
                NumImages += 1
                
        OutputPngs = glob.glob(os.path.join(imageOutputPath, '*.png'))
        self.assertEqual(len(OutputPngs), NumImages, "Missing exported images %s" % imageOutputPath)

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
        
    
    def __GetLevelNode(self, section_number=691, channel='TEM', filter='Leveled', level=1):
        
        volumeNode = self.LoadVolume()
        
        filterNode = volumeNode.find("Block/Section[@Number='%s']/Channel[@Name='%s']/Filter[@Name='%s']" % (section_number, channel, filter))                                     
        self.assertIsNotNone(filterNode, "Missing filter node")
        
        levelNode = filterNode.TilePyramid.GetLevel(level)
        self.assertIsNotNone(levelNode, "Missing level %d" % (level))
        
        return levelNode
        
    def RemoveTileFromPyramid(self, section_number=691, channel='TEM', filter='Leveled', level=1):
        '''Remove a single image from an image pyramid
        :return: Filename that was deleted
        '''
        
        levelNode = self.__GetLevelNode(section_number, channel, filter, level)
        self.assertIsNotNone(levelNode, "Missing level %d" % (level))
        
        # Choose a random tile and remove it
        pngFiles = glob.glob(os.path.join(levelNode.FullPath, '*.png'))
        
        chosenPngFile = pngFiles[0]
        
        os.remove(chosenPngFile)
        
        return chosenPngFile
    
    def RemoveAndRegenerateTile(self, RegenFunction, RegenKwargs, section_number, channel='TEM', filter='Leveled', level=1,):
        '''Remove a tile from an image pyramid level.  Run adjust contrast and ensure the tile is regenerated after RegenFunction is called'''
        removedTileFullPath = self.RemoveTileFromPyramid(section_number, channel, filter, level)
        
        RegenFunction(**RegenKwargs)
        
        self.assertTrue(os.path.exists(removedTileFullPath), "Deleted tile was not regenerated %s" % removedTileFullPath)
        
    def CopyManualStosFiles(self, ManualStosFullPath, StosGroupName):
        '''Copy all stos files from the manual stos directory into the StosGroup's manual directory.
        :param str ManualStosFullPath: Directory containing .stos files
        :param str StosGroupName: Stos group to add manual files to
        :return: list of transform nodes targeted by copied manual files
        '''
        
        volumeNode = self.LoadVolume()
        StosGroupNode = volumeNode.find("Block/StosGroup[@Name='%s']" % StosGroupName)
        self.assertIsNotNone(StosGroupNode)
        ManualStosDir = os.path.join(StosGroupNode.FullPath, 'Manual')

        # os.remove(ManualStosDir)
        stosTransformList = []
        for f in glob.glob(os.path.join(ManualStosFullPath, '*.stos')):
            shutil.copy(f, ManualStosDir)
            
            stosFilePath = os.path.basename(f)
            
            #Find the TransformNode in the stos group we expect to be replaced by this manual file
            stosTransform = StosGroupNode.find("SectionMappings/Transform[@Path='%s']" % stosFilePath)
            self.assertIsNotNone(stosTransform, "Stos transform file that is overriden by manual files does not exist: %s" % stosFilePath)
            
            stosTransformList.append(stosTransform)
            
        return stosTransformList


class CopySetupTestBase(PlatformTest):
    '''Copies data from Platform data directory to test output directory at setup'''

    def setUp(self):
        super(CopySetupTestBase, self).setUp()

        self.assertTrue(os.path.exists(self.PlatformFullPath))

        if os.path.exists(self.TestOutputPath):
            shutil.rmtree(self.TestOutputPath)

        shutil.copytree(self.ImportedDataPath, self.TestOutputPath)

class ImportOnlySetup(PlatformTest):
    '''Calls prepare on a PMG volume.  Used as a base class for more complex tests'''

    def setUp(self):
        super(ImportOnlySetup, self).setUp()

        CacheName = 'ImportOnlySetup'
        # Import the files
        if not self.TryLoadTestSetupFromCache(CacheName):
            self.RunImport()
            self.assertTrue(os.path.exists(self.TestOutputPath), "Test input was not copied")
            self.SaveTestSetupToCache(CacheName)
            
        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.TestOutputPath)
        self.assertIsNotNone(self.VolumeObj)

class PrepareSetup(PlatformTest):
    '''Calls prepare on a PMG volume.  Used as a base class for more complex tests'''
    def setUp(self):
        super(PrepareSetup, self).setUp()
        CacheName = 'PrepareSetup'
        
        if not self.TryLoadTestSetupFromCache(CacheName):
            self.RunImport()
            self.RunPrune()
            self.RunHistogram()        
            self.SaveTestSetupToCache(CacheName)

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.TestOutputPath)
        self.assertIsNotNone(self.VolumeObj)


class PrepareAndMosaicSetup(PlatformTest):
    '''Calls prepare and mosaic pipelines on a PMG volume.  Used as a base class for more complex tests'''

    def setUp(self):

        super(PrepareAndMosaicSetup, self).setUp()
        # Import the files
        CacheName = 'PrepareAndMosaicSetup'

        if not self.TryLoadTestSetupFromCache(CacheName):
            self.RunImportThroughMosaic()
            self.SaveTestSetupToCache(CacheName)

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.TestOutputPath)
        self.assertIsNotNone(self.VolumeObj)

class PrepareThroughAssembleSetup(PlatformTest):
    '''Calls prepare and mosaic pipelines on a PMG volume.  Used as a base class for more complex tests'''

    def setUp(self):

        super(PrepareThroughAssembleSetup, self).setUp()
        CacheName = 'PrepareThroughAssembleSetup'
        # Import the files
        if not self.TryLoadTestSetupFromCache(CacheName):
            self.RunImportThroughMosaicAssemble()
            self.SaveTestSetupToCache(CacheName)

        # Load the meta-data from the volumedata.xml file
        self.VolumeObj = VolumeManager.Load(self.TestOutputPath)
        self.assertIsNotNone(self.VolumeObj)

if __name__ == "__main__":
    # import syssys.argv = ['', 'Test.testName']
    unittest.main()
