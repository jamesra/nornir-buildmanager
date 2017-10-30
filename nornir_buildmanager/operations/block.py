'''
Created on Jun 22, 2012

@author: Jamesan
'''


import copy
import logging
import math
import random
import shutil
import subprocess

from nornir_buildmanager import VolumeManagerETree, VolumeManagerHelpers
from nornir_buildmanager.metadatautils import *
from nornir_buildmanager.validation import transforms
from nornir_imageregistration import assemble, mosaic
from nornir_imageregistration.files import stosfile
from nornir_imageregistration.transforms import *
from nornir_shared import prettyoutput, files, misc
from nornir_shared.processoutputinterceptor import ProgressOutputInterceptor, ProcessOutputInterceptor

import nornir_buildmanager.operations.helpers.mosaicvolume as mosaicvolume 
import nornir_buildmanager.operations.helpers.stosgroupvolume as stosgroupvolume
import nornir_imageregistration.stos_brute as stos_brute
import nornir_pools


class StomPreviewOutputInterceptor(ProgressOutputInterceptor):

    def __init__(self, proc, processData=None, OverlayFilename=None, DiffFilename=None, WarpedFilename=None):
        self.Proc = proc
        self.ProcessData = processData
        self.Output = list()  # List of output lines
        self.LastLoadedFile = None  # Last file loaded by stom, used to rename the output
        self.stosfilename = None

        self.OverlayFilename = OverlayFilename
        self.DiffFilename = DiffFilename
        self.WarpedFilename = WarpedFilename
        return

    def Parse(self, line):
        '''Parse a line of output from stom so we can figure out how to correctly name the output files.
           sample input:
            Tool Percentage: 5.000000e-002
            loading 0009_ShadingCorrected-dapi_blob_1.png
            saving BruteResults/008.tif
            Tool Percentage: 5.000000e-002
            loading 0010_ShadingCorrected-dapi_blob_1.png
            saving BruteResults/009.tif
            Tool Percentage: 5.000000e-002'''

        # Line is called with None when the process has terminated which means it is safe to rename the created files
        if line is not None:
            # Let base class handle a progress percentage message
            ProgressOutputInterceptor.Parse(self, line)
            prettyoutput.Log(line)
            self.Output.append(line)
        else:
            outputfiles = list()
            '''Create a cmd for image magick to merge the images'''


            for line in self.Output:
                '''Processes a single line of output from the provided process and updates status as needed'''
                try:
                    line = line.lower()
                    if(line.find("loading") >= 0):
                        parts = line.split()
                        [name, ext] = os.path.splitext(parts[1])
                        if(self.stosfilename is None):
                            self.stosfilename = name
                        else:
                            self.LastLoadedFile = name

                    elif(line.find("saving") >= 0):
                        parts = line.split()
                        outputFile = parts[1]
                        # Figure out if the output file has a different path
                        path = os.path.dirname(outputFile)

                        [name, ext] = os.path.splitext(outputFile)
                        if(ext is None):
                            ext = '.tif'

                        if(len(ext) <= 0):
                            ext = '.tif'

                        outputfiles.append(outputFile)
                        # prettyoutput.Log("Renaming " + outputFile + " to " + os.path.join(path, self.LastLoadedFile + ext))

                        # shutil.move(outputFile, os.path.join(path, self.LastLoadedFile + ext))
                except:
                    pass

            if(len(outputfiles) == 2):

                [OverlayFile, ext] = os.path.splitext(self.stosfilename)
                path = os.path.dirname(outputfiles[0])
                [temp, ext] = os.path.splitext(outputfiles[0])

                # Rename the files so we can continue without waiting for convert
                while(True):
                    r = random.randrange(2, 100000, 1)
                    tempfilenameOne = os.path.join(path, str(r) + ext)
                    tempfilenameTwo = os.path.join(path, str(r + 1) + ext)

                    while os.path.exists(tempfilenameOne) or os.path.exists(tempfilenameTwo):
                        r = random.randrange(2, 100000, 1)
                        tempfilenameOne = os.path.join(path, str(r) + ext)
                        tempfilenameTwo = os.path.join(path, str(r + 1) + ext)

                    if (not os.path.exists(tempfilenameOne)) and (not os.path.exists(tempfilenameTwo)):
                        prettyoutput.Log("Renaming " + outputfiles[0] + " to " + tempfilenameOne)
                        shutil.move(outputfiles[0], tempfilenameOne)

                        prettyoutput.Log("Renaming " + outputfiles[1] + " to " + tempfilenameTwo)
                        shutil.move(outputfiles[1], tempfilenameTwo)
                        break

                if self.OverlayFilename is None:
                    OverlayFilename = 'overlay_' + OverlayFile.replace("temp", "", 1) + '.png'
                else:
                    OverlayFilename = self.OverlayFilename

                Pool = nornir_pools.GetGlobalProcessPool()

                cmd = 'magick convert -colorspace RGB ' + tempfilenameOne + ' ' + tempfilenameTwo + ' ' + tempfilenameOne + ' -combine -interlace PNG ' + OverlayFilename
                prettyoutput.Log(cmd)
                Pool.add_process(cmd, cmd + " && exit", shell=True)
                # subprocess.Popen(cmd + " && exit", shell=True)

                if self.DiffFilename is None:
                    DiffFilename = 'diff_' + OverlayFile.replace("temp", "", 1) + '.png'
                else:
                    DiffFilename = self.DiffFilename

                cmd = 'magick composite ' + tempfilenameOne + ' ' + tempfilenameTwo + ' -compose difference  -interlace PNG ' + DiffFilename
                prettyoutput.Log(cmd)

                Pool.add_process(cmd, cmd + " && exit", shell=True)

                if not self.WarpedFilename is None:
                    cmd = 'magick convert ' + tempfilenameTwo + " -interlace PNG " + self.WarpedFilename
                    Pool.add_process(cmd, cmd + " && exit", shell=True)

                # subprocess.call(cmd + " && exit", shell=True)
            else:
                prettyoutput.Log("Unexpected number of images output from ir-stom, expected 2: " + str(outputfiles))

        return


def SectionNumberKey(SectionNodeA):
    '''Sort section nodes by number'''
    return int(SectionNodeA.get('Number', None))


def SectionNumberCompare(SectionNodeA, SectionNodeB):
    '''Sort section nodes by number'''
    return cmp(int(SectionNodeA.get('Number', None)), int(SectionNodeB.get('Number', None)))


def _GetCenterSection(Parameters, MappingNode=None):
    '''Returns the number of the center section from the Block Node if possible, otherwise it checks the parameters.  Returns None if unspecified'''

    CenterSection = MappingNode.CenterSection
    if not CenterSection is None:
        return MappingNode.CenterSection

    CenterSection = Parameters.get('CenterSection', None)
    try:
        CenterSection = int(CenterSection)
    except:
        CenterSection = None

    return CenterSection


def _CreateDefaultRegistrationTree(BlockNode, CenterSectionNumber, NumAdjacentSections, Logger=None):
    '''Return a list of integers containing known good sections
    :param BlockNode BlockNode: Block meta-data
    :param int CenterSectionNumber: Section number to place root of registration tree at
    :param int NumAdjacentSections: Number of adjacent sections to attempt registration with'''

    SectionNodeList = list(BlockNode.findall('Section'))
    SectionNodeList.sort(key=SectionNumberKey)

    # Fetch the list of known bad sections, if it exists
    NonStosSectionNumbers = BlockNode.NonStosSectionNumbers

    SectionNumberList = [SectionNumberKey(s) for s in SectionNodeList]

    StosSectionNumbers = []
    for sectionNumber in SectionNumberList:
        if not sectionNumber in NonStosSectionNumbers:
            StosSectionNumbers.append(sectionNumber)
 
    RT = registrationtree.RegistrationTree.CreateRegistrationTree(StosSectionNumbers, adjacentThreshold=NumAdjacentSections, center=CenterSectionNumber)
    RT.AddNonControlSections(NonStosSectionNumbers)

    return RT


def UpdateStosMapWithRegistrationTree(StosMap, RT, Logger):
    '''Adds any mappings missing in the StosMap with those from the registration tree'''

    # Part one, add all RT mappings to the existing nodes
    Modified = False
    mappings = list(StosMap.Mappings)
    for mapping in mappings:
        control = mapping.Control

        rt_node = RT.Nodes.get(control, None)
        if not rt_node:
            Logger.info("Removing mapping missing from registration tree: " + str(mapping))
            StosMap.remove(mapping)
            Modified = True
            continue

        known_mappings = mapping.Mapped
        for rt_mapped in rt_node.Children:

            if not rt_mapped.SectionNumber in known_mappings:
                Modified = True
                mapping.AddMapping(rt_mapped.SectionNumber)

    # Part two, create nodes existing in the RT but not the StosMap
    for rt_node in RT.Nodes.values():

        if len(rt_node.Children) == 0:
            continue

        known_mappings = StosMap.GetMappingsForControl(rt_node.SectionNumber)
        mappingNode = None
        if len(known_mappings) == 0:
            # Create a mapping
            mappingNode = VolumeManagerETree.MappingNode(rt_node.SectionNumber, None)
            StosMap.append(mappingNode)
            Modified = True
        else:
            mappingNode = known_mappings[0]

        for rt_mapped in rt_node.Children:
            if not rt_mapped.SectionNumber in mappingNode.Mapped:
                mappingNode.AddMapping(rt_mapped.SectionNumber)
                Logger.info("\tAdded %d <- %d" % (rt_node.SectionNumber, rt_mapped.SectionNumber))
                Modified = True

    return Modified


def CreateSectionToSectionMapping(Parameters, BlockNode, ChannelsRegEx, FiltersRegEx, Logger, **kwargs):
    '''Figure out which sections should be registered to each other
        @BlockNode'''
    NumAdjacentSections = int(Parameters.get('NumAdjacentSections', '1'))
    StosMapName = Parameters.get('OutputStosMapName', 'PotentialRegistrationChain')

    StosMapType = StosMapName + misc.GenNameFromDict(Parameters)

    SaveBlock = False
    SaveOutputMapping = False
    # Create a node to store the stos mappings
    OutputMappingNode = VolumeManagerETree.XElementWrapper(tag='StosMap', Name=StosMapName, Type=StosMapType)
    (SaveBlock, OutputMappingNode) = BlockNode.UpdateOrAddChildByAttrib(OutputMappingNode)

    SectionNodeList = list(BlockNode.findall('Section'))
      
    SectionNodeList.sort(key=SectionNumberKey)

    CenterSectionNumber = _GetCenterSection(Parameters, OutputMappingNode)
    DefaultRT = _CreateDefaultRegistrationTree(BlockNode, CenterSectionNumber, NumAdjacentSections, Logger)

    if(DefaultRT.IsEmpty):
        return None

    if OutputMappingNode.CenterSection is None:
        OutputMappingNode.CenterSection = DefaultRT.RootNodes.values()[0].SectionNumber

    NonStosSectionNumbers = BlockNode.NonStosSectionNumbers
    
    # Add sections which do not have the correct channels or filters to the non-stos section list.  These will not be used as control sections
    MissingChannelOrFilterSections = filter(lambda s: False == s.MatchChannelFilterPattern(ChannelsRegEx, FiltersRegEx), SectionNodeList)
    MissingChannelOrFilterSectionNumbers = map(lambda s: s.SectionNumber, MissingChannelOrFilterSections)
    
    NonStosSectionNumbers += MissingChannelOrFilterSectionNumbers
        
    if OutputMappingNode.ClearBannedControlMappings(NonStosSectionNumbers):
        SaveOutputMapping = True

    if UpdateStosMapWithRegistrationTree(OutputMappingNode, DefaultRT, Logger):
        SaveOutputMapping = True
        
    if SaveBlock:
        return BlockNode
    elif SaveOutputMapping:
        # Cannot save OutputMapping, it is not a container
        return BlockNode

    return None

def __CallNornirStosBrute(stosNode, Downsample, ControlImageFullPath, MappedImageFullPath, ControlMaskImageFullPath=None, MappedMaskImageFullPath=None, AngleSearchRange=None, argstring=None, Logger=None):
    '''Call the stos-brute version from nornir-imageregistration'''

    alignment = stos_brute.SliceToSliceBruteForce(FixedImageInput=ControlImageFullPath,
                                                  WarpedImageInput=MappedImageFullPath,
                                                  FixedImageMaskPath=ControlMaskImageFullPath,
                                                  WarpedImageMaskPath=MappedMaskImageFullPath,
                                                  AngleSearchRange=AngleSearchRange,
                                                  Cluster=False)

    stos = alignment.ToStos(ControlImageFullPath,
                     MappedImageFullPath,
                     ControlMaskImageFullPath,
                     MappedMaskImageFullPath,
                     PixelSpacing=Downsample)
    
    stos.Save(stosNode.FullPath)

    return

def __CallIrToolsStosBrute(stosNode, ControlImageNode, MappedImageNode, ControlMaskImageNode=None, MappedMaskImageNode=None, argstring=None, Logger=None):
    if argstring is None:
        argstring = ""

    StosBruteTemplate = 'ir-stos-brute ' + argstring + '-save %(OutputFile)s -load %(ControlImage)s %(MovingImage)s -mask %(ControlMask)s %(MovingMask)s'
    StosBruteTemplateNoMask = 'ir-stos-brute ' + argstring + '-save %(OutputFile)s -load %(ControlImage)s %(MovingImage)s '

    cmd = None
    if not (ControlMaskImageNode is None or MappedMaskImageNode is None):
        cmd = StosBruteTemplate % {'OutputFile' : stosNode.FullPath,
                               'ControlImage' : ControlImageNode.FullPath,
                               'MovingImage' : MappedImageNode.FullPath,
                               'ControlMask' : ControlMaskImageNode.FullPath,
                               'MovingMask' : MappedMaskImageNode.FullPath}
    else:
        cmd = StosBruteTemplateNoMask % {'OutputFile' : stosNode.FullPath,
                               'ControlImage' : ControlImageNode.FullPath,
                               'MovingImage' : MappedImageNode.FullPath }

    prettyoutput.Log(cmd)
    subprocess.call(cmd + " && exit", shell=True)

    CmdRan = True

    if not os.path.exists(stosNode.FullPath):
        Logger.error("Stos brute did not produce useable output\n" + cmd)
        return None
    

def GetOrCreateRegistrationImageNodes(filter_node, Downsample, GetMask, Logger=None):
    '''
    :param object filter_node: Filter meta-data to get images for
    :param int Downsample: Resolution of the image node to fetch or create
    :param bool GetMask: True if the mask node should be returned
    :return: Tuple of (image_node, mask_node) for the filter at the given downsample level
    '''
    if Logger is None:
        Logger = logging.getLogger(__name__ + ".FilterToFilterBruteRegistration")
        
    image_node = filter_node.GetOrCreateImage(Downsample)
    if image_node is None:
        Logger.error("Image metadata missing %s" % filter_node.FullPath)
        return (None, None)
    
    if not os.path.exists(image_node.FullPath):
        Logger.error("Image image file missing %s" % image_node.FullPath)
        return (None, None)
    
    mask_image_node = None
    if GetMask:
        mask_image_node = filter_node.GetOrCreateMaskImage(Downsample)
        if mask_image_node is None:
            Logger.error("Mask image metadata missing %s" % filter_node.FullPath)
            return (None, None)
        
        if not os.path.exists(mask_image_node.FullPath):
            Logger.error("Mask image file missing %s" % mask_image_node.FullPath)
            return (None, None)
        
    return (image_node, mask_image_node)
        

def FilterToFilterBruteRegistration(StosGroup, ControlFilter, MappedFilter, OutputType, OutputPath, UseMasks, AngleSearchRange=None, Logger=None, argstring=None):
    '''Create a transform node, populate, and generate the transform'''

    if Logger is None:
        Logger = logging.getLogger(__name__ + ".FilterToFilterBruteRegistration")

    stosNode = StosGroup.GetStosTransformNode(ControlFilter, MappedFilter)
    if stosNode:
        if StosGroup.AreStosInputImagesOutdated(stosNode, ControlFilter, MappedFilter, MaskRequired=UseMasks):
            stosNode.Clean("Input Images are Outdated")
            stosNode = None
        else:
            #Check if the manual stos file exists and is different than the output file        
            ManualStosFileFullPath = StosGroup.PathToManualTransform(stosNode.FullPath)
            ManualFileExists = os.path.exists(ManualStosFileFullPath)
            ManualInputChecksum = None
            if ManualFileExists:
                if 'InputTransformChecksum' in stosNode.attrib:
                    ManualInputChecksum = stosfile.StosFile.LoadChecksum(ManualStosFileFullPath)
                    stosNode = transforms.RemoveOnMismatch(stosNode, 'InputTransformChecksum', ManualInputChecksum)
                else:
                    stosNode.Clean("No input checksum to test manual stos file against. Replacing with new manual input")
                    stosNode = None

            if not ManualFileExists:
                if 'InputTransformChecksum' in stosNode.attrib:
                    stosNode.Clean("Manual file used to create transform but the manual file has been removed")
                    stosNode = None

    #Get or create the input images
    (ControlImageNode, ControlMaskImageNode) = GetOrCreateRegistrationImageNodes(ControlFilter, StosGroup.Downsample, GetMask=UseMasks, Logger=Logger)
    (MappedImageNode, MappedMaskImageNode) = GetOrCreateRegistrationImageNodes(MappedFilter, StosGroup.Downsample, GetMask=UseMasks, Logger=Logger)

    if stosNode is None:
        stosNode = StosGroup.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType, OutputPath)

        # We just created this, so remove any old files
        if os.path.exists(stosNode.FullPath):
            os.remove(stosNode.FullPath) 

    # print OutputFileFullPath
    CmdRan = False
    if not os.path.exists(stosNode.FullPath):
        ManualStosFileFullPath = StosGroup.PathToManualTransform(stosNode.FullPath)
        if ManualStosFileFullPath:
            prettyoutput.Log("Copy manual override stos file to output: " + os.path.basename(ManualStosFileFullPath))
            shutil.copy(ManualStosFileFullPath, stosNode.FullPath)
            # Ensure we add or remove masks according to the parameters
            SetStosFileMasks(stosNode.FullPath, ControlFilter, MappedFilter, UseMasks, StosGroup.Downsample)
            stosNode.InputTransformChecksum = ManualInputChecksum
        elif not (ControlMaskImageNode is None and MappedMaskImageNode is None):
            __CallNornirStosBrute(stosNode, StosGroup.Downsample, ControlImageNode.FullPath, MappedImageNode.FullPath, ControlMaskImageNode.FullPath, MappedMaskImageNode.FullPath, AngleSearchRange=AngleSearchRange)
        else:
            __CallNornirStosBrute(stosNode, StosGroup.Downsample, ControlImageNode.FullPath, MappedImageNode.FullPath, AngleSearchRange=AngleSearchRange)

        CmdRan = True
            # __CallIrToolsStosBrute(stosNode, ControlImageNode, MappedImageNode, ControlMaskImageNode, MappedMaskImageNode, argstring, Logger)

            # Rescale stos file to full-res
            # stosFile = stosfile.StosFile.Load(stosNode.FullPath)
            # stosFile.Scale(StosGroup.Downsample)
            # stosFile.Save(stosNode.FullPath)

            # Load and save the stos file to ensure the transform doesn't have the original Ir-Tools floating point string representation which
            # have identical values but different checksums from the Python stos file objects %g representation

            # stosNode.Checksum = stosfile.StosFile.LoadChecksum(stosNode.FullPath)
        stosNode.ResetChecksum()
        StosGroup.AddChecksumsToStos(stosNode, ControlFilter, MappedFilter)

    if CmdRan:
        return stosNode

    return None




def StosBrute(Parameters, VolumeNode, MappingNode, BlockNode, ChannelsRegEx, FiltersRegEx, Logger, **kwargs):
    '''Create an initial rotation and translation alignment for a pair of unregistered images'''

    Downsample = int(Parameters.get('Downsample', 32))
    OutputStosGroupName = kwargs.get('OutputGroup', 'Brute')
    OutputStosType = kwargs.get('Type', 'Brute')
    AngleSearchRange = kwargs.get('AngleSearchRange', None)
    
    # Argparse value for 
    if(AngleSearchRange == "None"): 
        AngleSearchRange = None

    # Additional arguments for stos-brute
    argstring = misc.ArgumentsFromDict(Parameters)
    
    UseMasks = Parameters.get("UseMasks", False)

    ControlNumber = MappingNode.Control
    AdjacentSections = MappingNode.Mapped

    # Find the nodes for the control and mapped sections
    ControlSectionNode = BlockNode.GetSection(ControlNumber)
    if ControlSectionNode is None:
        Logger.error("Missing control section node for # " + str(ControlNumber))
        return

    if not os.path.exists(OutputStosGroupName):
        os.makedirs(OutputStosGroupName)
 
    (added, StosGroupNode) = BlockNode.GetOrCreateStosGroup(OutputStosGroupName, downsample=Downsample) 
    StosGroupNode.CreateDirectories()
    if added:
        yield BlockNode

    if not os.path.exists(StosGroupNode.FullPath):
        os.makedirs(StosGroupNode.FullPath)

    for MappedSection in AdjacentSections:
        MappedSectionNode = BlockNode.GetSection(MappedSection)

        if(MappedSectionNode is None):
            prettyoutput.LogErr("Could not find expected section for StosBrute: " + str(MappedSection))
            continue

        # Figure out all the combinations of assembled images between the two section and test them
        MappedFilterList = MappedSectionNode.MatchChannelFilterPattern(ChannelsRegEx, FiltersRegEx)

        if 'Downsample' in Parameters:
            del Parameters['Downsample']

        for MappedFilter in MappedFilterList:
            print("\tMap - " + MappedFilter.FullPath)
             
            ControlFilterList = ControlSectionNode.MatchChannelFilterPattern(ChannelsRegEx, FiltersRegEx)
            for ControlFilter in ControlFilterList:
                print("\tCtrl - " + ControlFilter.FullPath)

                # ControlImageSetNode = VolumeManagerETree.ImageNode.wrap(ControlImageSetNode)
                OutputFile = VolumeManagerETree.StosGroupNode.GenerateStosFilename(ControlFilter, MappedFilter)
                
                (added, stos_mapping_node) = StosGroupNode.GetOrCreateSectionMapping(MappedSection)
                if added:
                    yield stos_mapping_node.Parent

                stosNode = FilterToFilterBruteRegistration(StosGroup=StosGroupNode,
                                                ControlFilter=ControlFilter,
                                                MappedFilter=MappedFilter,
                                                OutputType=OutputStosType,
                                                OutputPath=OutputFile,
                                                UseMasks=UseMasks,
                                                AngleSearchRange=AngleSearchRange)

                if not stosNode is None:
                    yield stosNode.Parent
 

def GetImage(BlockNode, SectionNumber, Channel, Filter, Downsample):

    sectionNode = BlockNode.GetSection(SectionNumber)
    if sectionNode is None:
        return (None, None)
    
    channelNode = sectionNode.GetChannel(Channel)
    if channelNode is None:
        return (None, None)
    
    filterNode = channelNode.GetFilter(Filter)
    if filterNode is None:
        return (None, None)

    return (filterNode.GetOrCreateImage(Downsample), filterNode.GetMaskImage(Downsample))

def StosImageNodes(StosTransformNode, Downsample):

    class output:

        def __init__(self):
            pass

    output = output()

    BlockNode = StosTransformNode.FindParent('Block')

    (output.ControlImageNode, output.ControlImageMaskNode) = GetImage(BlockNode, SectionNumber=StosTransformNode.ControlSectionNumber,
                                                                                 Channel=StosTransformNode.ControlChannelName,
                                                                                 Filter=StosTransformNode.ControlFilterName,
                                                                                 Downsample=Downsample)

    (output.MappedImageNode, output.MappedImageMaskNode) = GetImage(BlockNode, SectionNumber=StosTransformNode.MappedSectionNumber,
                                                                                 Channel=StosTransformNode.MappedChannelName,
                                                                                 Filter=StosTransformNode.MappedFilterName,
                                                                                 Downsample=Downsample)

    return output

def ValidateSectionMappingPipeline(Parameters, Logger, section_mapping_node, **kwargs):
    return ValidateSectionMapping(section_mapping_node, Logger)
    
def ValidateSectionMapping(section_mapping_node, Logger):
    save_node = False;
    save_node |= section_mapping_node.CleanIfInvalid();
    for t in section_mapping_node.Transforms:
        save_node |= ValidateSectionMappingTransform(t, Logger) is not None
    
    for img in section_mapping_node.Images:
        save_node |= img.CleanIfInvalid()
        
    if save_node:
        return section_mapping_node;
    
    return None

def ValidateSectionMappingTransformPipeline(Parameters, Logger, stos_transform_node, **kwargs):
    return ValidateSectionMappingTransform(stos_transform_node, Logger)
        
def ValidateSectionMappingTransform(stos_transform_node, Logger):
    
    parent = stos_transform_node.Parent 
    stos_group = stos_transform_node.FindParent('StosGroup')
    downsample = int(stos_group.Downsample)
    (mapped_filter, mapped_mask_filter) = __MappedFilterForTransform(stos_transform_node)
    (control_filter, control_mask_filter) = __ControlFilterForTransform(stos_transform_node)
    
    if mapped_filter is None or control_filter is None:
        Logger.warn("Removed stos file for missing filters: %s" % stos_transform_node.FullPath) 
        parent.remove(stos_transform_node)
        return parent
    
    # Could be a generated transform not pointing at actual images, move on if the input image does not exist at that level 
    if control_filter.GetImage(downsample) is None:
        return None
    if mapped_filter.GetImage(downsample) is None:
        return None
    
    if FixStosFilePaths(control_filter, mapped_filter, stos_transform_node, downsample):
        Logger.warn("Updated stos images: %s" % stos_transform_node.FullPath)
        return parent
    
    return None


def UpdateStosImagePaths(StosTransformPath, ControlImageFullPath, MappedImageFullPath, ControlImageMaskFullPath=None, MappedImageMaskFullPath=None):
    '''
    Replace the paths of the stos file with the passed parameters
    :return: True if the stos file was updated
    '''

    # ir-stom's -slice_dirs argument is broken for masks, so we have to patch the stos file before use
    InputStos = stosfile.StosFile.Load(StosTransformPath)
    
    NeedsUpdate = InputStos.ControlImageFullPath != ControlImageFullPath or \
                  InputStos.MappedImageFullPath != MappedImageFullPath or \
                  InputStos.ControlMaskFullPath != ControlImageMaskFullPath or \
                  InputStos.MappedMaskFullPath != MappedImageMaskFullPath
    
    if NeedsUpdate:
        InputStos.ControlImageFullPath = ControlImageFullPath
        InputStos.MappedImageFullPath = MappedImageFullPath
    
        if not InputStos.ControlMaskName is None:
            InputStos.ControlMaskFullPath = ControlImageMaskFullPath
    
        if not InputStos.MappedMaskName is None:
            InputStos.MappedMaskFullPath = MappedImageMaskFullPath

        InputStos.Save(StosTransformPath)
        
    return NeedsUpdate


def FixStosFilePaths(ControlFilter, MappedFilter, StosTransformNode, Downsample, StosFilePath=None):
    '''Check if the stos file uses appropriate images for the passed filters'''

    if StosFilePath is None:
        StosFilePath = StosTransformNode.FullPath

    if ControlFilter.GetMaskImage(Downsample) is None or MappedFilter.GetMaskImage(Downsample) is None:
        return UpdateStosImagePaths(StosFilePath,
                         ControlFilter.GetImage(Downsample).FullPath,
                         MappedFilter.GetImage(Downsample).FullPath)
    else:
        return UpdateStosImagePaths(StosFilePath,
                         ControlFilter.GetImage(Downsample).FullPath,
                         MappedFilter.GetImage(Downsample).FullPath,
                         ControlFilter.GetMaskImage(Downsample).FullPath,
                         MappedFilter.GetMaskImage(Downsample).FullPath)


def SectionToVolumeImage(Parameters, TransformNode, Logger, CropUndefined=True, **kwargs):
    '''Executre ir-stom on a provided .stos file'''

    GroupNode = TransformNode.FindParent("StosGroup")
    SaveRequired = False

    SectionMappingNode = TransformNode.FindParent('SectionMappings')

    FilePrefix = str(SectionMappingNode.MappedSectionNumber) + '-' + str(TransformNode.ControlSectionNumber) + '_'
    WarpedOutputFilename = FilePrefix + 'warped_' + GroupNode.Name + "_" + TransformNode.Type + '.png'
    WarpedOutputFileFullPath = os.path.join(GroupNode.FullPath, WarpedOutputFilename)

    # Create a node in the XML records

    (created, WarpedImageNode) = GetOrCreateImageNodeHelper(SectionMappingNode, WarpedOutputFileFullPath)
    WarpedImageNode.Type = 'Warped_' + TransformNode.Type

    stosImages = StosImageNodes(TransformNode, GroupNode.Downsample)

    # Compare the .stos file creation date to the output

    WarpedImageNode = transforms.RemoveOnMismatch(WarpedImageNode, 'InputTransformChecksum', TransformNode.Checksum)

    if(not WarpedImageNode is None):
        files.RemoveOutdatedFile(stosImages.ControlImageNode.FullPath, WarpedImageNode.FullPath)
        files.RemoveOutdatedFile(stosImages.MappedImageNode.FullPath, WarpedImageNode.FullPath)
    else:
        (created, WarpedImageNode) = GetOrCreateImageNodeHelper(SectionMappingNode, WarpedOutputFileFullPath)
        WarpedImageNode.Type = 'Warped_' + TransformNode.Type

    if not os.path.exists(WarpedImageNode.FullPath):
        SaveRequired = True
        WarpedImageNode.InputTransformChecksum = TransformNode.Checksum
        assemble.TransformStos(TransformNode.FullPath, OutputFilename=WarpedImageNode.FullPath, CropUndefined=CropUndefined)
        prettyoutput.Log("Saving image: " + WarpedImageNode.FullPath)

    if SaveRequired:
        return GroupNode
    else:
        return None


def AssembleStosOverlays(Parameters, StosMapNode, GroupNode, Logger, **kwargs):
    '''Executre ir-stom on a provided .stos file'''

    oldDir = os.getcwd()
    TransformXPathTemplate = "SectionMappings[@MappedSectionNumber='%(MappedSection)d']/Transform[@ControlSectionNumber='%(ControlSection)d']"

    SaveRequired = False
    BlockNode = StosMapNode.FindParent('Block')

    try:
        for MappingNode in StosMapNode.findall('Mapping'):
            MappedSectionList = MappingNode.Mapped

            for MappedSection in MappedSectionList:
                # Find the inputTransformNode in the InputGroupNode
                # TransformXPath = TransformXPathTemplate % {'MappedSection' : MappedSection,
                #                                           'ControlSection' : MappingNode.Control}

                # StosTransformNode = GroupNode.find(TransformXPath)
                StosTransformNodes = GroupNode.TransformsForMapping(MappedSection, MappingNode.Control)
                if StosTransformNodes is None:
                    Logger.warn("No transform found for mapping: " + str(MappedSection) + " -> " + str(MappingNode.Control))
                    continue

                for StosTransformNode in StosTransformNodes:
                    SectionMappingNode = StosTransformNode.FindParent('SectionMappings')
                    [TransformBaseFilename, ext] = os.path.splitext(StosTransformNode.Path)
                    OverlayOutputFilename = 'overlay_' + TransformBaseFilename + '.png'
                    DiffOutputFilename = 'diff_' + TransformBaseFilename + '.png'
                    WarpedOutputFilename = 'warped_' + TransformBaseFilename + '.png'

                    OverlayOutputFileFullPath = os.path.join(GroupNode.FullPath, OverlayOutputFilename)
                    DiffOutputFileFullPath = os.path.join(GroupNode.FullPath, DiffOutputFilename)
                    WarpedOutputFileFullPath = os.path.join(GroupNode.FullPath, WarpedOutputFilename)

                    os.chdir(GroupNode.FullPath)

                    if not os.path.exists('Temp'):
                        os.makedirs('Temp')

                    # Create a node in the XML records
                    (created_overlay, OverlayImageNode) = GetOrCreateImageNodeHelper(SectionMappingNode, OverlayOutputFileFullPath)
                    OverlayImageNode.Type = 'Overlay_' + StosTransformNode.Type
                    (created_diff, DiffImageNode) = GetOrCreateImageNodeHelper(SectionMappingNode, DiffOutputFileFullPath)
                    DiffImageNode.Type = 'Diff_' + StosTransformNode.Type
                    (created_warped, WarpedImageNode) = GetOrCreateImageNodeHelper(SectionMappingNode, WarpedOutputFileFullPath)
                    WarpedImageNode.Type = 'Warped_' + StosTransformNode.Type

                    FilePrefix = str(SectionMappingNode.MappedSectionNumber) + '-' + str(StosTransformNode.ControlSectionNumber) + '_'

                    stosImages = StosImageNodes(StosTransformNode, GroupNode.Downsample)

                    if stosImages.ControlImageNode is None or stosImages.MappedImageNode is None:
                        continue
                    
                    if created_overlay:
                        OverlayImageNode.SetTransform(StosTransformNode)
                    else:
                        if not OverlayImageNode.CleanIfInputTransformMismatched(StosTransformNode):
                            files.RemoveOutdatedFile(StosTransformNode.FullPath, OverlayImageNode.FullPath)
                            files.RemoveOutdatedFile(stosImages.ControlImageNode.FullPath, OverlayImageNode.FullPath)
                            files.RemoveOutdatedFile(stosImages.MappedImageNode.FullPath, OverlayImageNode.FullPath)
                        
                    if created_diff:
                        DiffImageNode.SetTransform(StosTransformNode)
                    else:
                        DiffImageNode.CleanIfInputTransformMismatched(StosTransformNode)
                        
                    if created_warped:
                        WarpedImageNode.SetTransform(StosTransformNode)
                    else:
                        WarpedImageNode.CleanIfInputTransformMismatched(StosTransformNode)

                    # Compare the .stos file creation date to the output
                     
                    
                    #===========================================================
                    # if hasattr(OverlayImageNode, 'InputTransformChecksum'):
                    #     transforms.RemoveOnMismatch(OverlayImageNode, 'InputTransformChecksum', StosTransformNode.Checksum)
                    # if hasattr(DiffImageNode, 'InputTransformChecksum'):
                    #     transforms.RemoveOnMismatch(DiffImageNode, 'InputTransformChecksum', StosTransformNode.Checksum)
                    # if hasattr(WarpedImageNode, 'InputTransformChecksum'):
                    #     transforms.RemoveOnMismatch(WarpedImageNode, 'InputTransformChecksum', StosTransformNode.Checksum)
                    #===========================================================                   

                    if not (os.path.exists(OverlayImageNode.FullPath) and os.path.exists(DiffImageNode.FullPath)):

                        # ir-stom's -slice_dirs argument is broken for masks, so we have to patch the stos file before use
                        if stosImages.ControlImageMaskNode is None or stosImages.MappedImageMaskNode is None:
                            UpdateStosImagePaths(StosTransformNode.FullPath,
                                             stosImages.ControlImageNode.FullPath,
                                             stosImages.MappedImageNode.FullPath,)
                        else:
                            UpdateStosImagePaths(StosTransformNode.FullPath,
                                             stosImages.ControlImageNode.FullPath,
                                             stosImages.MappedImageNode.FullPath,
                                             stosImages.ControlImageMaskNode.FullPath,
                                             stosImages.MappedImageMaskNode.FullPath)

                        stomtemplate = 'ir-stom -load %(InputFile)s -save Temp/ ' + misc.ArgumentsFromDict(Parameters)

                        cmd = stomtemplate % {'InputFile' : StosTransformNode.FullPath}

                        NewP = subprocess.Popen(cmd + " && exit", shell=True, stdout=subprocess.PIPE)
                        ProcessOutputInterceptor.Intercept(StomPreviewOutputInterceptor(NewP,
                                                                                                                  OverlayFilename=OverlayImageNode.FullPath,
                                                                                                                   DiffFilename=DiffImageNode.FullPath,
                                                                                                                   WarpedFilename=WarpedImageNode.FullPath))

                        SaveRequired = True

                        OverlayImageNode.SetTransform(StosTransformNode)
                        DiffImageNode.SetTransform(StosTransformNode)
                        WarpedImageNode.SetTransform(StosTransformNode)

            # Figure out where our output should live...

            # try:
                # shutil.rmtree('Temp')
            # except:
                # pass

        Pool = nornir_pools.GetGlobalProcessPool()
        Pool.wait_completion()
    finally:
        if os.path.exists('Temp'):
            shutil.rmtree('Temp')

        os.chdir(oldDir)

    if SaveRequired:
        return BlockNode
    else:
        return GroupNode

def SelectBestRegistrationChain(Parameters, InputGroupNode, StosMapNode, OutputStosMapName, Logger, **kwargs):
    '''Figure out which sections should be registered to each other'''

    # SectionMappingsNode
    Pool = nornir_pools.GetGlobalProcessPool()
    Pool.wait_completion()

    # Assess all of the images
    ComparisonImageType = kwargs.get('ComparisonImageType', 'Diff_Brute')
    ImageSearchXPathTemplate = "Image[@InputTransformChecksum='%(InputTransformChecksum)s']"

    # OutputStosMapName = kwargs.get('OutputStosMapName', 'FinalStosMap')

    BlockNode = InputGroupNode.FindParent('Block')

    # OK, we have the best mapping. Add it to our registration chain.
    # Create a node to store the stos mappings
    OutputStosMapNode = VolumeManagerETree.StosMapNode(Name=OutputStosMapName, Type=OutputStosMapName, CenterSection=str(StosMapNode.CenterSection))
    (added, OutputStosMapNode) = BlockNode.UpdateOrAddChildByAttrib(OutputStosMapNode)

    OutputStosGroupNode = VolumeManagerETree.XElementWrapper(tag='StosGroup', attrib=InputGroupNode.attrib)
    (added, OutputStosGroupNode) = BlockNode.UpdateOrAddChildByAttrib(OutputStosGroupNode, 'Path')

    # Look at all of the mappings and create a list of potential control sections for each mapped section
    # Mappings = list(StosMapNode.findall('Mapping'))

    MappedToControlCandidateList = StosMapNode.MappedToControls()

#    for mappingNode in Mappings:
#        for mappedSection in mappingNode.Mapped:
#            if mappedSection in MappedToControlCandidateList:
#                MappedToControlCandidateList[mappedSection].append(mappingNode.Control)
#            else:
#                MappedToControlCandidateList[mappedSection] = [mappingNode.Control]

    # OK, fetch the mapped section numbers and lets work through them in order
    mappedSectionNumbers = MappedToControlCandidateList.keys()
    mappedSectionNumbers.sort()

    # If a section is used as a control, then prefer it when generat
    for mappedSection in mappedSectionNumbers:

        potentialControls = MappedToControlCandidateList[mappedSection]
        if len(potentialControls) == 0:
            # No need to test, copy over the transform
            Logger.error(str(mappedSection) + " -> ? No control section candidates found")
            continue

        knownControlSections = list(OutputStosMapNode.FindAllControlsForMapped(mappedSection))
        if len(knownControlSections) == len(potentialControls):
            Logger.info(str(mappedSection) + " -> " + str(knownControlSections) + " was previously mapped, skipping")
            continue

        # Examine each stos image if it exists and determine the best match
        WinningTransform = None

        InputSectionMappingNode = InputGroupNode.GetSectionMapping(mappedSection)
        if InputSectionMappingNode is None:
            Logger.error(str(mappedSection) + " -> ? No SectionMapping data found")
            continue

        PotentialTransforms = []
        for controlSection in potentialControls:
            t = InputSectionMappingNode.TransformsToSection(controlSection)
            PotentialTransforms.extend(t)

        if len(PotentialTransforms) == 1:
            # No need to test, copy over the transform
            WinningTransform = PotentialTransforms[0]
        else:
            TaskList = []
            for Transform in PotentialTransforms:
                try:
                    ImageSearchXPath = ImageSearchXPathTemplate % {'InputTransformChecksum' : Transform.Checksum}
                    ImageNode = InputSectionMappingNode.find(ImageSearchXPath)

                    if ImageNode is None:
                        Logger.error(str(mappedSection) + ' -> ' + str(controlSection))
                        Logger.error("No image node found for transform")
                        Logger.error("Checksum: " + Transform.Checksum)
                        continue

                    identifyCmd = 'identify -format %[mean] ' + ImageNode.FullPath

                    task = Pool.add_process(ImageNode.attrib['Path'], identifyCmd + " && exit", shell=True)
                    task.TransformNode = Transform
                    TaskList.append(task)
                    Logger.info("Evaluating " + str(mappedSection) + ' -> ' + str(controlSection))

                except Exception as e:
                    Logger.error("Could not evalutate mapping " + str(mappedSection) + ' -> ' + str(controlSection))

            BestMean = None

            for t in TaskList:
                try:
                    MeanStr = t.wait_return()
                    MeanVal = float(MeanStr)
                    if BestMean is None:
                        WinningTransform = t.TransformNode
                        BestMean = MeanVal
                    elif BestMean > float(MeanVal):
                        WinningTransform = t.TransformNode
                        BestMean = MeanVal
                except:
                    pass

        if WinningTransform is None:
            Logger.error("Winning transform is none, section #" + str(mappedSection))
            continue

        OutputStosMapNode.AddMapping(WinningTransform.ControlSectionNumber, mappedSection)

        OutputSectionMappingNode = VolumeManagerETree.SectionMappingsNode(attrib=InputSectionMappingNode.attrib)
        (added, OutputSectionMappingNode) = OutputStosGroupNode.UpdateOrAddChildByAttrib(OutputSectionMappingNode, 'MappedSectionNumber')

        (added, OutputTransformNode) = OutputSectionMappingNode.UpdateOrAddChildByAttrib(WinningTransform, 'Path')
        OutputTransformNode.attrib = copy.deepcopy(WinningTransform.attrib)

        if controlSection is None:
            Logger.info("No mapping found for " + str(mappedSection))
        else:
            Logger.info("Created mapping " + str(mappedSection) + ' -> ' + str(controlSection))

    return BlockNode


def __GetOrCreateInputStosFileForRegistration(StosGroupNode, InputTransformNode, OutputDownsample, ControlFilter, MappedFilter, UseMasks):
    '''
    :return: If a manual override stos file exists we return the manual file.  If it does not exist we scale the input transform to the desired size
    '''
    # Begin selecting the input transform for registration
    AutomaticInputDir = os.path.join(StosGroupNode.FullPath, 'Automatic')
    if not os.path.exists(AutomaticInputDir):
        os.makedirs(AutomaticInputDir)

    ExpectedStosFileName = VolumeManagerETree.StosGroupNode.GenerateStosFilename(ControlFilter, MappedFilter)

    # Copy the input stos or converted stos to the input directory
    AutomaticInputStosFullPath = os.path.join(AutomaticInputDir, InputTransformNode.Path)
    ManualInputStosFullPath = StosGroupNode.PathToManualTransform(ExpectedStosFileName)

    InputStosFullPath = __SelectAutomaticOrManualStosFilePath(AutomaticInputStosFullPath=AutomaticInputStosFullPath, ManualInputStosFullPath=ManualInputStosFullPath)
    InputChecksum = None
    if InputStosFullPath == AutomaticInputStosFullPath:
        __GenerateStosFileIfOutdated(InputTransformNode, AutomaticInputStosFullPath, OutputDownsample, ControlFilter, MappedFilter, UseMasks)
        InputChecksum = InputTransformNode.Checksum
    else:
        InputChecksum = stosfile.StosFile.LoadChecksum(InputStosFullPath)

    return (InputStosFullPath, InputChecksum)


def SetStosFileMasks(stosFullPath, ControlFilter, MappedFilter, UseMasks, Downsample):
    '''
    Ensure the stos file has masks
    ''' 
    
    OutputStos = stosfile.StosFile.Load(stosFullPath)
    if OutputStos.HasMasks == UseMasks:
        return 
    else:
        if not UseMasks:
            if OutputStos.HasMasks:
                OutputStos.ClearMasks()
                OutputStos.Save(stosFullPath, AddMasks=False)
            
            return 
        else:
            ControlMaskImageFullPath = ControlFilter.MaskImageset.GetOrPredictImageFullPath(Downsample)
            MappedMaskImageFullPath = MappedFilter.MaskImageset.GetOrPredictImageFullPath(Downsample)
            
            OutputStos.ControlMaskFullPath = ControlMaskImageFullPath
            OutputStos.MappedMaskFullPath = MappedMaskImageFullPath
            OutputStos.Save(stosFullPath)
            return 
        
    
    


def IsStosNodeOutdated(InputTransformNode, OutputTransformNode, ControlFilter, MappedFilter, UseMasks, OutputDownsample):
    '''
    :param bool UseMasks: True if masks should be included.  None if we should use masks if they exist in the input stos transform
    :Return: true if the output stos transform is stale
    '''
    
    if not os.path.exists(OutputTransformNode.FullPath):
        return True
    
    if OutputTransformNode is None:
        return True
    
    OutputStosGroup = OutputTransformNode.GetParent('StosGroup') 
    
    InputStos = stosfile.StosFile.Load(InputTransformNode.FullPath)
    if UseMasks is None:
        UseMasks = InputStos.HasMasks
        
    if 'InputTransformChecksum' in OutputTransformNode.attrib:
        if not transforms.IsValueMatched(OutputTransformNode, 'InputTransformChecksum', InputTransformNode.Checksum):
            return True
        
    if OutputStosGroup.AreStosInputImagesOutdated(OutputTransformNode, ControlFilter, MappedFilter, MasksRequired=UseMasks):
        return True
    
    
    if UseMasks is None:
        InputStos = stosfile.StosFile.Load(InputTransformNode.FullPath)
        UseMasks = InputStos.HasMasks
        
    OutputStos = stosfile.StosFile.Load(OutputTransformNode.FullPath)
    if OutputStos.HasMasks != UseMasks:
        return True 
    
    ControlImageFullPath = ControlFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    MappedImageFullPath = MappedFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    
    ControlMaskImageFullPath = None
    MappedMaskImageFullPath = None
    if UseMasks:
        ControlMaskImageFullPath = ControlFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
        MappedMaskImageFullPath = MappedFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
    
    return not (OutputStos.ControlImagePath == ControlImageFullPath and
                OutputStos.MappedImagePath == MappedImageFullPath and
                OutputStos.ControlMaskFullPath == ControlMaskImageFullPath and
                OutputStos.MappedMaskFullPath == MappedMaskImageFullPath and
                OutputStos.HasMasks == UseMasks)
    

def IsStosFileOutdated(InputTransformNode, OutputTransformPath, OutputDownsample, ControlFilter, MappedFilter, UseMasks):
    '''
    :param bool UseMasks: True if masks should be included.  None if we should use masks if they exist in the input stos transform
    :return: True if any part of the stos file is out of date compared to the input stos file
    '''
    
    # Replace the automatic files if they are outdated.
    files.RemoveOutdatedFile(InputTransformNode.FullPath, OutputTransformPath)
    
    if not os.path.exists(OutputTransformPath):
        return True 
     
    if UseMasks is None:
        InputStos = stosfile.StosFile.Load(InputTransformNode.FullPath)
        UseMasks = InputStos.HasMasks
        
    OutputStos = stosfile.StosFile.Load(InputTransformNode.FullPath)
    if not OutputStos.HasMasks == UseMasks:
        return False
    
    ControlImageFullPath = ControlFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    MappedImageFullPath = MappedFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    
    if not (OutputStos.ControlImagePath == ControlImageFullPath and OutputStos.MappedImagePath == MappedImageFullPath):
        return False 
    
    ControlMaskImageFullPath = None
    MappedMaskImageFullPath = None
    if UseMasks:
        ControlMaskImageFullPath = ControlFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
        MappedMaskImageFullPath = MappedFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
        
        if not OutputStos.ControlMaskFullPath == ControlMaskImageFullPath and OutputStos.MappedMaskFullPath == MappedMaskImageFullPath:
            return False 
    
    return True
    


def __GenerateStosFileIfOutdated(InputTransformNode, OutputTransformPath, OutputDownsample, ControlFilter, MappedFilter, UseMasks):
    '''Only generates a stos file if the Output stos path has an earlier last modified time compared to the input
    :param bool UseMasks: True if masks should be included.  None if we should copy setting from input stos transform
    :return: True if a file was generated, False if the output already existed and was valid
    '''
    
    # We should not be trying to create output if we have no input
    assert(os.path.exists(InputTransformNode.FullPath))
    if IsStosFileOutdated(InputTransformNode, OutputTransformPath, OutputDownsample, ControlFilter, MappedFilter, UseMasks):
        if os.path.exists(OutputTransformPath):
            os.remove(OutputTransformPath)
    
    if not os.path.exists(OutputTransformPath):
        __GenerateStosFile(InputTransformNode, OutputTransformPath, OutputDownsample, ControlFilter, MappedFilter, UseMasks)
        return True
    
    return False


def __PredictStosImagePaths(filter_node, Downsample):
    '''
    .stos files embed file names.  However if we scale .stos files past the point where they have an assembled image we need to put the filename we would expect 
    without actually creating meta-data for a non-existent image level 
    '''
    
    imageFullPath = filter_node.PredictImageFullPath(Downsample)
    
    if filter_node.HasMask():
        maskFullPath = filter_node.PredictMaskFullPath(Downsample)

    return (imageFullPath, maskFullPath)


def __GenerateStosFile(InputTransformNode, OutputTransformPath, OutputDownsample, ControlFilter, MappedFilter, UseMasks):
    '''Generates a new stos file using the specified filters and scales the transform to match the
       requested downsample as needed.
       :param bool UseMasks: True if masks should be included.  None if we should copy setting from input stos transform
       :rtype: bool
       :return: True if a new stos file was generated
    '''
    
    StosGroupNode = InputTransformNode.FindParent('StosGroup')
    InputDownsample = StosGroupNode.Downsample

    InputStos = stosfile.StosFile.Load(InputTransformNode.FullPath)
    if UseMasks is None:
        UseMasks = InputStos.HasMasks
     
    ControlImageFullPath = ControlFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    MappedImageFullPath = MappedFilter.Imageset.GetOrPredictImageFullPath(OutputDownsample)
    
    ControlMaskImageFullPath = None
    MappedMaskImageFullPath = None
    if UseMasks:
        ControlMaskImageFullPath = ControlFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)
        MappedMaskImageFullPath = MappedFilter.MaskImageset.GetOrPredictImageFullPath(OutputDownsample)

    # If all the core details are the same we can save time by copying the data instead
    if not (InputStos.ControlImagePath == ControlImageFullPath and
        InputStos.MappedImagePath == MappedImageFullPath and
        InputStos.ControlMaskFullPath == ControlMaskImageFullPath and
        InputStos.MappedMaskFullPath == MappedMaskImageFullPath and
        OutputDownsample == InputDownsample and
        InputStos.HasMasks == UseMasks): 
        
        ModifiedInputStos = InputStos.ChangeStosGridPixelSpacing(oldspacing=InputDownsample,
                                       newspacing=OutputDownsample,
                                       ControlImageFullPath=ControlImageFullPath,
                                       MappedImageFullPath=MappedImageFullPath,
                                       ControlMaskFullPath=ControlMaskImageFullPath,
                                       MappedMaskFullPath=MappedMaskImageFullPath)

        ModifiedInputStos.Save(OutputTransformPath)
    else:
        shutil.copyfile(InputTransformNode.FullPath, OutputTransformPath)

    return True
 

def __SelectAutomaticOrManualStosFilePath(AutomaticInputStosFullPath, ManualInputStosFullPath):
    ''' Use the manual stos file if it exists, prevent any cleanup from occurring on the manual file '''

    #If we know there is no manual file, then use the automatic file
    if not ManualInputStosFullPath:
        return AutomaticInputStosFullPath

    #If we haven't generated an automatic file and a manual file exists, use the manual file.  Delete the automatic if it also exists.
    InputStosFullPath = AutomaticInputStosFullPath
    if os.path.exists(ManualInputStosFullPath):
        InputStosFullPath = ManualInputStosFullPath
        # Files.RemoveOutdatedFile(ManualInputStosFullPath, OutputStosFullPath)
        if os.path.exists(AutomaticInputStosFullPath):
            # Clean up the automatic input if we have a manual override
            os.remove(AutomaticInputStosFullPath)
    # else:
        # The local copy may have a different downsample level, in which case the checksums based on the transform would always be different
        # As a result we need to use the meta-data checksum records and not the automatically generated file.
        # In this case we should delete the automatic file and let it regenerate to be sure it is always fresh when the script executes
        # if os.path.exists(AutomaticInputStosFullPath):
            # os.remove(AutomaticInputStosFullPath)

    return InputStosFullPath

def StosGrid(Parameters, MappingNode, InputGroupNode, UseMasks, Downsample=32, ControlFilterPattern=None, MappedFilterPattern=None, OutputStosGroup=None, Type=None, **kwargs):

    Logger = logging.getLogger(__name__ + '.StosGrid')

    BlockNode = InputGroupNode.FindParent('Block')

    if(OutputStosGroup is None):
        OutputStosGroup = 'Grid'

    OutputStosGroupName = OutputStosGroup

    if(Type is None):
        Type = 'Grid'

    MappedSectionList = MappingNode.Mapped

    MappedSectionList.sort()

    (added, OutputStosGroupNode) = BlockNode.GetOrCreateStosGroup(OutputStosGroupName, Downsample)
    OutputStosGroupNode.CreateDirectories()

    if added:
        yield BlockNode

    for MappedSection in MappedSectionList:
        # Find the inputTransformNode in the InputGroupNode
        InputTransformNodes = InputGroupNode.TransformsForMapping(MappedSection, MappingNode.Control)
        if(InputTransformNodes is None or len(InputTransformNodes) == 0):
            Logger.warning("No transform found for mapping " + str(MappedSection) + " -> " + str(MappingNode.Control))
            continue

        for InputTransformNode in InputTransformNodes:
            OutputDownsample = Downsample

            InputSectionMappingNode = InputTransformNode.FindParent('SectionMappings')
            OutputSectionMappingNode = VolumeManagerETree.XElementWrapper('SectionMappings', InputSectionMappingNode.attrib)
            (added, OutputSectionMappingNode) = OutputStosGroupNode.UpdateOrAddChildByAttrib(OutputSectionMappingNode, 'MappedSectionNumber')
            if added:
                yield OutputStosGroupNode

            ControlFilter = __GetFirstMatchingFilter(BlockNode,
                                                     InputTransformNode.ControlSectionNumber,
                                                     InputTransformNode.ControlChannelName,
                                                     ControlFilterPattern)

            MappedFilter = __GetFirstMatchingFilter(BlockNode,
                                                     InputTransformNode.MappedSectionNumber,
                                                     InputTransformNode.MappedChannelName,
                                                     MappedFilterPattern)

            if ControlFilter is None:
                Logger.warning("No control filter, skipping refinement")
                OutputSectionMappingNode.Clean("No control filter found in stos grid")
                yield OutputStosGroupNode 
                continue

            if MappedFilter is None:
                Logger.warning("No mapped filter, skipping refinement")
                OutputSectionMappingNode.Clean("No mapped filter found in stos grid")
                yield OutputStosGroupNode 
                continue

            (ControlImageNode, ControlMaskImageNode) = GetOrCreateRegistrationImageNodes(ControlFilter, OutputDownsample, GetMask=UseMasks, Logger=Logger)
            (MappedImageNode, MappedMaskImageNode) = GetOrCreateRegistrationImageNodes(MappedFilter, OutputDownsample, GetMask=UseMasks, Logger=Logger)

            OutputFile = VolumeManagerETree.StosGroupNode.GenerateStosFilename(ControlFilter, MappedFilter)
            OutputStosFullPath = os.path.join(OutputStosGroupNode.FullPath, OutputFile)
            stosNode = OutputStosGroupNode.GetStosTransformNode(ControlFilter, MappedFilter)
            if stosNode is None:
                stosNode = OutputStosGroupNode.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType=Type, OutputPath=OutputFile)

            (InputStosFullPath, InputStosFileChecksum) = __GetOrCreateInputStosFileForRegistration(StosGroupNode=OutputStosGroupNode,
                                                                    InputTransformNode=InputTransformNode,
                                                                    ControlFilter=ControlFilter,
                                                                    MappedFilter=MappedFilter,
                                                                    OutputDownsample=OutputDownsample,
                                                                    UseMasks=UseMasks)

            if not os.path.exists(InputStosFullPath):
                # Hmm... no input.  This is worth reporting and moving on
                Logger.error("ir-stos-grid did not produce output for " + InputStosFullPath)
                InputGroupNode.remove(InputTransformNode)
                continue

            # If the manual or automatic stos file is newer than the output, remove the output
            # files.RemoveOutdatedFile(InputTransformNode.FullPath, OutputStosFullPath): 

            # Remove our output if it was generated from an input transform with a different checksum
            if os.path.exists(OutputStosFullPath):
                # stosNode = OutputSectionMappingNode.GetChildByAttrib('Transform', 'ControlSectionNumber', InputTransformNode.ControlSectionNumber)
                if not stosNode is None:
                    if OutputStosGroupNode.AreStosInputImagesOutdated(stosNode, ControlFilter, MappedFilter, MaskRequired=UseMasks):
                        stosNode.Clean("Input images outdated for %s" % (stosNode.FullPath))
                        stosNode = None
                    elif 'InputTransformChecksum' in stosNode.attrib:
                        stosNode = transforms.RemoveOnMismatch(stosNode, 'InputTransformChecksum', InputStosFileChecksum)
                        # if(InputStosFileChecksum != stosNode.InputTransformChecksum):
                            # os.remove(OutputStosFullPath)

                            # Remove old stos meta-data and create from scratch to avoid stale data.
                            # OutputSectionMappingNode.remove(stosNode)
                if stosNode is None:
                    stosNode = OutputStosGroupNode.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType=Type, OutputPath=OutputFile)

#                    else:
#                        os.remove(OutputStosFullPath)

            # Replace the automatic files if they are outdated.
            # GenerateStosFile(InputTransformNode, AutomaticInputStosFullPath, OutputDownsample, ControlFilter, MappedFilter)

    #        FixStosFilePaths(ControlFilter, MappedFilter, InputTransformNode, OutputDownsample, StosFilePath=InputStosFullPath)
            if not os.path.exists(OutputStosFullPath):
                ManualStosFileFullPath = OutputStosGroupNode.PathToManualTransform(stosNode.FullPath)
                if ManualStosFileFullPath is None:
                    argstring = misc.ArgumentsFromDict(Parameters)
                    StosGridTemplate = 'ir-stos-grid -save %(OutputStosFullPath)s -load %(InputStosFullPath)s ' + argstring

                    cmd = StosGridTemplate % {'OutputStosFullPath' : OutputStosFullPath,
                                                   'InputStosFullPath' : InputStosFullPath}

                    prettyoutput.Log(cmd)
                    subprocess.call(cmd + " && exit", shell=True)

                    if not os.path.exists(OutputStosFullPath):
                        Logger.error("ir-stos-grid did not produce output for " + InputStosFullPath)
                        OutputSectionMappingNode.remove(stosNode)
                        stosNode = None
                        yield OutputSectionMappingNode
                        continue
                    else:
                        if not stosfile.StosFile.IsValid(OutputStosFullPath):
                            os.remove(OutputStosFullPath)
                            OutputSectionMappingNode.remove(stosNode)
                            stosNode = None
                            prettyoutput.Log("Transform generated by refine was unable to be loaded. Deleting.  Check input transform: " + OutputStosFullPath)
                            yield OutputSectionMappingNode
                            continue
                else:
                    prettyoutput.Log("Copy manual override stos file to output: " + os.path.basename(ManualStosFileFullPath))
                    shutil.copy(ManualStosFileFullPath, OutputStosFullPath)
                    
                    # Ensure we add or remove masks according to the parameters
                    SetStosFileMasks(OutputStosFullPath, ControlFilter, MappedFilter, UseMasks, OutputStosGroupNode.Downsample)

                stosNode.Path = OutputFile

                if os.path.exists(OutputStosFullPath):
                    stosNode.ResetChecksum()
                    stosNode.SetTransform(InputTransformNode)
                    stosNode.InputTransformChecksum = InputStosFileChecksum
                
                yield OutputSectionMappingNode
                 

def __StosMapToRegistrationTree(StosMapNode):
    '''Convert a collection of stos mappings into a tree.  The tree describes which transforms must be used to map points between sections'''

    rt = registrationtree.RegistrationTree()

    for mappingNode in StosMapNode.Mappings:
        for mappedSection in mappingNode.Mapped:
            rt.AddPair(mappingNode.Control, mappedSection)

    return rt


def __RegistrationTreeToStosMap(rt, StosMapName):
    '''Create a stos map where every mapping transforms to the root of the tree'''

    OutputStosMap = VolumeManagerETree.StosMapNode(StosMapName)

    for sectionNumber in rt.RootNodes.keys():
        rootNode = rt.RootNodes[sectionNumber]
        __AddRegistrationTreeNodeToStosMap(OutputStosMap, rt, rootNode.SectionNumber)

    return OutputStosMap

      
def __AddRegistrationTreeNodeToStosMap(StosMapNode, rt, controlSectionNumber, mappedSectionNumber=None):
    '''recursively adds registration tree nodes to the stos map'''
 
    if mappedSectionNumber is None:
        mappedSectionNumber = controlSectionNumber
    elif isinstance(mappedSectionNumber, registrationtree.RegistrationTreeNode):
        mappedSectionNumber = mappedSectionNumber.SectionNumber
        
    print("Adding " + str(mappedSectionNumber))

    rtNode = None
    if mappedSectionNumber in rt.Nodes:
        rtNode = rt.Nodes[mappedSectionNumber]
    else:
        return

    #Can loop forever here if a section is mapped twice*/
    for mapped in rtNode.Children:
        StosMapNode.AddMapping(controlSectionNumber, mapped.SectionNumber)

        if mapped.SectionNumber in rt.Nodes:
            __AddRegistrationTreeNodeToStosMap(StosMapNode, rt, controlSectionNumber, mapped.SectionNumber)


def TranslateVolumeToZeroOrigin(StosGroupNode, **kwargs):

    vol = stosgroupvolume.StosGroupVolume.Load(StosGroupNode)

    vol.TranslateToZeroOrigin()

    SavedStosGroupNode = vol.Save()

    return SavedStosGroupNode


def BuildSliceToVolumeTransforms(StosMapNode, StosGroupNode, OutputMap, OutputGroupName, Downsample, Enrich, Tolerance, **kwargs):
    '''Build a slice-to-volume transform for each section referenced in the StosMap

    :param str OutputMap: Name of the StosMap to create, defaults to StosGroupNode name if None
    :param bool Enrich: True if additional control points should be added if the transformed centroids of delaunay triangles are too far from expected position
    :param float Tolerance: The maximum distance the transformed and actual centroids can be before an additional control point is added at the centroid
    '''

    BlockNode = StosGroupNode.Parent
    InputStosGroupNode = StosGroupNode

    if not OutputMap:
        OutputMap = OutputGroupName

    OutputGroupFullname = '%s%d' % (OutputGroupName, Downsample)

    if not Enrich:
        Tolerance = None
    else:
        #Scale the tolerance for the downsample level
        Tolerance = Tolerance / float(Downsample)

    rt = __StosMapToRegistrationTree(StosMapNode)

    if len(rt.RootNodes) == 0:
        return

    (AddedGroupNode, OutputGroupNode) = BlockNode.GetOrCreateStosGroup(OutputGroupFullname, InputStosGroupNode.Downsample)
    if AddedGroupNode:
        yield BlockNode
    
    # build the stos map again if it exists
    BlockNode.RemoveStosMap(map_name=OutputMap) 
    
    OutputStosMap = __RegistrationTreeToStosMap(rt, OutputMap)
    (AddedStosMap, OutputStosMap) = BlockNode.UpdateOrAddChildByAttrib(OutputStosMap)
    OutputStosMap.CenterSection = StosMapNode.CenterSection

    if AddedStosMap:
        yield BlockNode
        
    for sectionNumber in rt.RootNodes:
        Node = rt.Nodes[sectionNumber]
        for saveNode in SliceToVolumeFromRegistrationTreeNode(rt, Node, InputGroupNode=InputStosGroupNode, OutputGroupNode=OutputGroupNode, EnrichTolerance=Tolerance, ControlToVolumeTransform=None):
            yield saveNode

    # TranslateVolumeToZeroOrigin(OutputGroupNode)
    # Do not use TranslateVolumeToZeroOrigin here because the center of the volume image does not get shifted with the rest of the sections. That is a problem.  We should probably create an identity transform for the root nodes in
    # the registration tree
    

def SliceToVolumeFromRegistrationTreeNode(rt, Node, InputGroupNode, OutputGroupNode, EnrichTolerance, ControlToVolumeTransform=None):
    ControlSection = Node.SectionNumber

    Logger = logging.getLogger(__name__ + '.SliceToVolumeFromRegistrationTreeNode')

    for MappedSectionNode in Node.Children:
        mappedSectionNumber = MappedSectionNode.SectionNumber
        mappedNode = rt.Nodes[mappedSectionNumber]

        logStr = "%s <- %s" % (str(ControlSection), str(mappedSectionNumber))

        (MappingAdded, OutputSectionMappingsNode) = OutputGroupNode.GetOrCreateSectionMapping(mappedSectionNumber)
        if MappingAdded:
            yield OutputGroupNode

        MappedToControlTransforms = InputGroupNode.TransformsForMapping(mappedSectionNumber, ControlSection)

        if MappedToControlTransforms is None or len(MappedToControlTransforms) == 0:
            Logger.error(" %s : No transform found:" % (logStr))
            continue

        # In theory each iteration of this loop could be run in a seperate thread.  Useful when center is in center of volume.
        for MappedToControlTransform in MappedToControlTransforms:

            ControlSectionNumber = None
            ControlChannelName = None
            ControlFilterName = None

            if ControlToVolumeTransform is None:
                ControlSectionNumber = MappedToControlTransform.ControlSectionNumber
                ControlChannelName = MappedToControlTransform.ControlChannelName
                ControlFilterName = MappedToControlTransform.ControlFilterName
            else:
                ControlSectionNumber = ControlToVolumeTransform.ControlSectionNumber
                ControlChannelName = ControlToVolumeTransform.ControlChannelName
                ControlFilterName = ControlToVolumeTransform.ControlFilterName

            OutputTransform = OutputSectionMappingsNode.FindStosTransform(ControlSectionNumber=ControlSectionNumber,
                                                                               ControlChannelName=ControlChannelName,
                                                                               ControlFilterName=ControlFilterName,
                                                                               MappedSectionNumber=MappedToControlTransform.MappedSectionNumber,
                                                                               MappedChannelName=MappedToControlTransform.MappedChannelName,
                                                                               MappedFilterName=MappedToControlTransform.MappedFilterName)

            if OutputTransform is None:
                OutputTransform = copy.deepcopy(MappedToControlTransform)
                OutputTransform.Name = str(mappedSectionNumber) + '-' + str(ControlSection)
                OutputTransform.Path = OutputTransform.Name + '.stos'
                OutputTransformAdded = OutputSectionMappingsNode.AddOrUpdateTransform(OutputTransform)
                OutputTransform.SetTransform(MappedToControlTransform)

                # Remove any residual transform file just in case
                if os.path.exists(OutputTransform.FullPath):
                    os.remove(OutputTransform.FullPath)

            if not ControlToVolumeTransform is None:
                OutputTransform.Path = str(mappedSectionNumber) + '-' + str(ControlToVolumeTransform.ControlSectionNumber) + '.stos'

            if not OutputTransform.IsInputTransformMatched(MappedToControlTransform):
                Logger.info(" %s: Removed outdated transform %s" % (logStr, OutputTransform.Path))
                if os.path.exists(OutputTransform.FullPath):
                    os.remove(OutputTransform.FullPath)



            #===================================================================
            # if not hasattr(OutputTransform, 'InputTransformChecksum'):
            #     if os.path.exists(OutputTransform.FullPath):
            #         os.remove(OutputTransform.FullPath)
            # else:
            #     if not MappedToControlTransform.Checksum == OutputTransform.InputTransformChecksum:
            #         if os.path.exists(OutputTransform.FullPath):
            #             os.remove(OutputTransform.FullPath)
            #===================================================================


            if ControlToVolumeTransform is None:
                # This maps directly to the origin, add it to the output stos group
                # Files.RemoveOutdatedFile(MappedToControlTransform.FullPath, OutputTransform.FullPath )

                if not os.path.exists(OutputTransform.FullPath):
                    Logger.info(" %s: Copy mapped to volume center stos transform %s" % (logStr, OutputTransform.Path))
                    shutil.copy(MappedToControlTransform.FullPath, OutputTransform.FullPath)
                    # OutputTransform.Checksum = MappedToControlTransform.Checksum
                    OutputTransform.SetTransform(MappedToControlTransform)
                    
                    yield OutputSectionMappingsNode

            else:
                OutputTransform.ControlSectionNumber = ControlToVolumeTransform.ControlSectionNumber
                OutputTransform.ControlChannelName = ControlToVolumeTransform.ControlChannelName
                OutputTransform.ControlFilterName = ControlToVolumeTransform.ControlFilterName

                if hasattr(OutputTransform, "ControlToVolumeTransformChecksum"):
                    if not OutputTransform.ControlToVolumeTransformChecksum == ControlToVolumeTransform.Checksum:
                        Logger.info(" %s: ControlToVolumeTransformChecksum mismatch, removing" % (logStr))
                        if os.path.exists(OutputTransform.FullPath):
                            os.remove(OutputTransform.FullPath)
                elif os.path.exists(OutputTransform.FullPath):
                    os.remove(OutputTransform.FullPath)
 
                if not os.path.exists(OutputTransform.FullPath):
                    try:
                        Logger.info(" %s: Adding transforms" % (logStr))
                        prettyoutput.Log(logStr)
                        MToVStos = stosfile.AddStosTransforms(MappedToControlTransform.FullPath, ControlToVolumeTransform.FullPath, EnrichTolerance=EnrichTolerance)
                        MToVStos.Save(OutputTransform.FullPath)

                        OutputTransform.ControlToVolumeTransformChecksum = ControlToVolumeTransform.Checksum
                        OutputTransform.ResetChecksum()
                        OutputTransform.SetTransform(MappedToControlTransform)
                        # OutputTransform.Checksum = stosfile.StosFile.LoadChecksum(OutputTransform.FullPath)
                    except ValueError:
                        # Probably an invalid transform.  Skip it
                        OutputTransform.Clean()
                        OutputTransform = None
                        pass
                    yield OutputSectionMappingsNode
                else:
                    Logger.info(" %s: is still valid" % (logStr))

            for retval in SliceToVolumeFromRegistrationTreeNode(rt, mappedNode, InputGroupNode, OutputGroupNode, EnrichTolerance=EnrichTolerance, ControlToVolumeTransform=OutputTransform):
                yield retval


def RegistrationTreeFromStosMapNode(StosMapNode):
    rt = registrationtree.RegistrationTree()

    for mappingNode in StosMapNode.findall('Mapping'):
        for mappedSection in mappingNode.Mapped:
            rt.AddPair(mappingNode.Control, mappedSection)

    return rt


def __MappedFilterForTransform(transform_node):
    return __GetFilterAndMaskFilter(transform_node,
                                transform_node.MappedSectionNumber,
                                transform_node.MappedChannelName,
                                transform_node.MappedFilterName)

def __ControlFilterForTransform(transform_node):
    return __GetFilterAndMaskFilter(transform_node,
                                transform_node.ControlSectionNumber,
                                transform_node.ControlChannelName,
                                transform_node.ControlFilterName)

def __GetFilter(transform_node, section, channel, filter_name):
    BlockNode = transform_node.FindParent(ParentTag='Block')
    if BlockNode is None:
        return None
    sectionNode = BlockNode.GetSection(section)
    if sectionNode is None:
        return None
    channelNode = sectionNode.GetChannel(channel)
    if channelNode is None:
        return None
    
    filterNode = channelNode.GetFilter(filter_name)
    return filterNode

def __GetFilterAndMaskFilter(transform_node, section, channel, filter_name):
    BlockNode = transform_node.FindParent(ParentTag='Block')
    if BlockNode is None:
        return None
    
    sectionNode = BlockNode.GetSection(section)
    if(sectionNode is None):
        return (None, None)
    
    channelNode = sectionNode.GetChannel(channel)
    if channelNode is None:
        return (None, None)
    
    filterNode = channelNode.GetFilter(filter_name)
    if filterNode is None:
        return (None, None)
    
    mask_filterNode = filterNode.GetMaskFilter()
    return (filterNode, mask_filterNode)


def __GetFirstMatchingFilter(block_node, section_number, channel_name, filter_pattern):
    '''Return the first filter in the section matching the pattern, or None if no filter exists'''
    section_node = block_node.GetSection(section_number)
     
    if section_node is None:
        Logger = logging.getLogger(__name__ + '.__GetFirstFilter')
        Logger.warning("Section %s is missing" % (section_number))
        return None
    
    channel_node = section_node.GetChannel(channel_name)
    if channel_node is None:
        Logger = logging.getLogger(__name__ + '.__GetFirstFilter')
        Logger.warning("Channel %s.%s is missing, skipping grid refinement" % (section_number, channel_node))
        return None
        
     
    # TODO: Skip transforms using filters which no longer exist.  Should live in a separate function.
    filter_matches = VolumeManagerHelpers.SearchCollection(channel_node.Filters,
                                                          'Name', filter_pattern,
                                                          CaseSensitive=True)
    
    if filter_matches is None or len(filter_matches) == 0:
        Logger = logging.getLogger(__name__ + '.__GetFirstFilter')
        Logger.warning("No %s.%s filters match pattern %s" % (section_number, channel_node, filter_pattern))
        return None
                                                          
    return filter_matches[0]

# def __MatchMappedFiltersForTransform(InputTransformNode, channelPattern=None, filterPattern=None):
#
#     if(filterPattern is None):
#         filterPattern = InputTransformNode.MappedFilterName
#
#     if(channelPattern is None):
#         channelPattern = InputTransformNode.MappedChannelName
#
#     sectionNumber = InputTransformNode.MappedSectionNumber
#     BlockNode = InputTransformNode.FindParent(ParentTag='Block')
#     sectionNode = BlockNode.GetSection(sectionNumber)
#     return sectionNode.MatchChannelFilterPattern(channelPattern, filterPattern)
#
#
# def __MatchControlFiltersForTransform(InputTransformNode, channelPattern=None, filterPattern=None):
#
#     if(filterPattern is None):
#         filterPattern = InputTransformNode.ControlFilterName
#
#     if(channelPattern is None):
#         channelPattern = InputTransformNode.ControlChannelName
#
#     sectionNumber = InputTransformNode.ControlSectionNumber
#     BlockNode = InputTransformNode.FindParent(ParentTag='Block')
#     sectionNode = BlockNode.GetSection(sectionNumber)
#     return sectionNode.MatchChannelFilterPattern(channelPattern, filterPattern)



def ScaleStosGroup(InputStosGroupNode, OutputDownsample, OutputGroupName, UseMasks, **kwargs):

    '''Take a stos group node, scale the transforms, and save in new stosgroup
    
       TODO: This function used to create stos transforms between different filters to.  Port that to a separate function
    '''
    GroupParent = InputStosGroupNode.Parent

    OutputGroupNode = VolumeManagerETree.StosGroupNode(OutputGroupName, OutputDownsample)
    (SaveBlockNode, OutputGroupNode) = GroupParent.UpdateOrAddChildByAttrib(OutputGroupNode)
     
    if not os.path.exists(OutputGroupNode.FullPath):
        os.makedirs(OutputGroupNode.FullPath)
        
    if SaveBlockNode:
        yield GroupParent

    for inputSectionMapping in InputStosGroupNode.SectionMappings:

        (SectionMappingNodeAdded, OutputSectionMapping) = OutputGroupNode.GetOrCreateSectionMapping(inputSectionMapping.MappedSectionNumber)
        if SectionMappingNodeAdded:
            yield OutputGroupNode

        InputTransformNodes = inputSectionMapping.findall('Transform')

        for InputTransformNode in InputTransformNodes:

            if not os.path.exists(InputTransformNode.FullPath):
                continue

            # ControlFilters = __ControlFiltersForTransform(InputTransformNode, ControlChannelPattern, ControlFilterPattern)
            # MappedFilters = __MappedFiltersForTransform(InputTransformNode, MappedChannelPattern, MappedFilterPattern)
            try:
                (ControlFilter, ControlMaskFilter) = __ControlFilterForTransform(InputTransformNode)
                (MappedFilter, MappedMaskFilter) = __MappedFilterForTransform(InputTransformNode)
            except AttributeError as e:
                logger = logging.getLogger("ScaleStosGroup")
                logger.error("ScaleStosGroup missing filter for InputTransformNode " + InputTransformNode.FullPath)
                continue
            
            if ControlFilter is None or MappedFilter is None:
                logger = logging.getLogger("ScaleStosGroup")
                logger.error("ScaleStosGroup missing filter for InputTransformNode " + InputTransformNode.FullPath)
                continue
            # for (ControlFilter, MappedFilter) in itertools.product(ControlFilters, MappedFilters):

            (stosNode_added, stosNode) = OutputGroupNode.GetOrCreateStosTransformNode(ControlFilter,
                                                             MappedFilter,
                                                             OutputType=InputTransformNode.Type,
                                                             OutputPath=VolumeManagerETree.StosGroupNode.GenerateStosFilename(ControlFilter, MappedFilter))
            
            if not stosNode_added:
                if not stosNode.IsInputTransformMatched(InputTransformNode):
                    if os.path.exists(stosNode.FullPath):
                        os.remove(stosNode.FullPath)
            else:
                # Remove an old file if we had to generate the meta-data
                if os.path.exists(stosNode.FullPath):
                    os.remove(stosNode.FullPath)
                    
            if not os.path.exists(stosNode.FullPath):
                stosGenerated = __GenerateStosFile(InputTransformNode,
                                                    stosNode.FullPath,
                                                    OutputDownsample,
                                                    ControlFilter,
                                                    MappedFilter,
                                                    UseMasks=None)

                if stosGenerated:
                    stosNode.ResetChecksum()
                    stosNode.SetTransform(InputTransformNode)
                else:
                    OutputGroupNode.Remove(stosNode)

                yield OutputGroupNode 

def __RemoveStosFileIfOutdated(OutputStosNode, InputStosNode):
    '''Removes the .stos file from the file system but leaves the meta data alone for reuse.
       Always removes the file if the meta-data does not have an InputTransformChecksum property'''

    if hasattr(OutputStosNode, "InputTransformChecksum"):
        if not transforms.IsValueMatched(OutputNode=OutputStosNode,
                                    OutputAttribute="InputTransformChecksum",
                                    TargetValue=InputStosNode.Checksum):
            if os.path.exists(OutputStosNode.FullPath):
                os.remove(OutputStosNode.FullPath)
                return True
        else:
            # InputTransformChecksum is equal
            return False

    elif os.path.exists(OutputStosNode.FullPath):
        os.remove(OutputStosNode.FullPath)
        return True

    return False
 
    

def _GetStosToMosaicTransform(StosTransformNode, TransformNode, OutputTransformName):
    OutputTransformNode = TransformNode.Parent.GetTransform(OutputTransformName)
    added = False
    if OutputTransformNode is None:
        # Create transform node for the output
        OutputTransformNode = VolumeManagerETree.TransformNode(Name=OutputTransformName, Type="MosaicToVolume", Path=OutputTransformName + '.mosaic')
        TransformNode.Parent.AddChild(OutputTransformNode)
        added = True
        
    return (added, OutputTransformNode)


def _ApplyStosToMosaicTransform(StosTransformNode, TransformNode, OutputTransformName, Logger, **kwargs):

    MappedFilterNode = TransformNode.FindParent('Filter')

    (added_output_transform, OutputTransformNode) = _GetStosToMosaicTransform(StosTransformNode, TransformNode, OutputTransformName)
    
    if added_output_transform: 
        OutputTransformNode.SetTransform(StosTransformNode) 
        OutputTransformNode.InputMosaicTransformChecksum = TransformNode.Checksum
     
    if not StosTransformNode is None:
        if not (OutputTransformNode.IsInputTransformMatched(StosTransformNode) and OutputTransformNode.InputMosaicTransformChecksum == TransformNode.Checksum):
            if os.path.exists(OutputTransformNode.FullPath):
                os.remove(OutputTransformNode.FullPath)
    else:
        if not OutputTransformNode.InputMosaicTransformChecksum == TransformNode.Checksum:
            if os.path.exists(OutputTransformNode.FullPath):
                os.remove(OutputTransformNode.FullPath)
        
    if os.path.exists(OutputTransformNode.FullPath):
        return OutputTransformNode
 
        
    if StosTransformNode is None:
        # No transform, copy transform directly

        # Create transform node for the output
        shutil.copyfile(TransformNode.FullPath, OutputTransformNode.FullPath)
        # OutputTransformNode.SetTransform(StosTransformNode)
        OutputTransformNode.InputMosaicTransformChecksum = TransformNode.Checksum 
        # OutputTransformNode.Checksum = TransformNode.Checksum
        
    else:
        # files.RemoveOutdatedFile(StosTransformNode.FullPath, OutputTransformNode.FullPath)

        StosGroupNode = StosTransformNode.FindParent('StosGroup')

        SToV = stosfile.StosFile.Load(StosTransformNode.FullPath)
        # Make sure we are not using a downsampled transform
        SToV = SToV.ChangeStosGridPixelSpacing(StosGroupNode.Downsample, 1.0,
                                               SToV.ControlImageFullPath,
                                               SToV.MappedImageFullPath,
                                               SToV.ControlMaskFullPath,
                                               SToV.MappedMaskFullPath,
                                               create_copy=False)
        StoVTransform = factory.LoadTransform(SToV.Transform)

        MosaicTransform = mosaic.Mosaic.LoadFromMosaicFile(TransformNode.FullPath)
        assert(MosaicTransform.FixedBoundingBox.BottomLeft[0] == 0 and MosaicTransform.FixedBoundingBox.BottomLeft[1] == 0)
        # MosaicTransform.TranslateToZeroOrigin() 
        Tasks = []
         
        UsePool = True
        if UsePool:
            # This is a parallel operation, but the Python GIL is so slow using threads is slower.
            Pool = nornir_pools.GetLocalMachinePool()
    
            for imagename, MosaicToSectionTransform in MosaicTransform.ImageToTransform.iteritems():
                task = Pool.add_task(imagename, triangulation.AddTransforms, StoVTransform, MosaicToSectionTransform)
                task.imagename = imagename
                if hasattr(MosaicToSectionTransform, 'gridWidth'):
                    task.dimX = MosaicToSectionTransform.gridWidth
                if hasattr(MosaicToSectionTransform, 'gridHeight'):
                    task.dimY = MosaicToSectionTransform.gridHeight
    
                Tasks.append(task)
                
            Pool.wait_completion()
    
            for task in Tasks:
                try:
                    MosaicToVolume = task.wait_return()
                    MosaicTransform.ImageToTransform[task.imagename] = MosaicToVolume
                except:
                    Logger.warn("Exception transforming tile. Skipping %s" % task.imagename)
                    pass
        else:
            for imagename, MosaicToSectionTransform in MosaicTransform.ImageToTransform.iteritems():
                MosaicToVolume = StoVTransform.AddTransform(MosaicToSectionTransform)
                MosaicTransform.ImageToTransform[imagename] = MosaicToVolume
                

        if len(MosaicTransform.ImageToTransform) > 0:
            OutputMosaicFile = MosaicTransform.ToMosaicFile()
            OutputMosaicFile.Save(OutputTransformNode.FullPath)
            
            OutputTransformNode.ResetChecksum()
            OutputTransformNode.SetTransform(StosTransformNode)
            OutputTransformNode.InputMosaicTransformChecksum = TransformNode.Checksum 

    return OutputTransformNode


def BuildMosaicToVolumeTransforms(StosMapNode, StosGroupNode, BlockNode, ChannelsRegEx, InputTransformName, OutputTransformName, Logger, **kwargs):
    '''Create a .mosaic file that translates a section directly into the volume.  Two .mosaics are created, a _untraslated version which may have a negative origin
       and a version with the requested OutputTransformName which will have an origin at zero
    '''
    Channels = BlockNode.findall('Section/Channel')

    MatchingChannelNodes = VolumeManagerHelpers.SearchCollection(Channels, 'Name', ChannelsRegEx)

    StosMosaicTransforms = []
    
    UntranslatedOutputTransformName = OutputTransformName + "_Untranslated"

    for channelNode in MatchingChannelNodes:
        transformNode = channelNode.GetChildByAttrib('Transform', 'Name', InputTransformName)

        if transformNode is None:
            continue

        NodeToSave = BuildChannelMosaicToVolumeTransform(StosMapNode, StosGroupNode, transformNode, UntranslatedOutputTransformName, Logger, **kwargs)
        if not NodeToSave is None:
            yield NodeToSave

        OutputTransformNode = channelNode.GetChildByAttrib('Transform', 'Name', UntranslatedOutputTransformName)
        if not OutputTransformNode is None:
            StosMosaicTransforms.append(OutputTransformNode)

    if len(StosMosaicTransforms) == 0:
        return

    __MoveMosaicsToZeroOrigin(StosMosaicTransforms, OutputTransformName)

    yield BlockNode

def __MoveMosaicsToZeroOrigin(StosMosaicTransforms, OutputStosMosaicTransformName):
    '''Given a set of transforms, ensure they are all translated so that none have a negative coordinate for the origin.
       :param list StosMosaicTransforms: [StosTransformNode]
       :param list OutputStosMosaicTransforms: list of names for output nodes
       '''
    
    output_transform_list = []
    
    for input_transform_node in StosMosaicTransforms:
        channel_node = input_transform_node.Parent
        output_transform_node = channel_node.GetTransform(OutputStosMosaicTransformName)
        if output_transform_node is None:
            output_transform_node = copy.deepcopy(input_transform_node)
            output_transform_node.Name = OutputStosMosaicTransformName
            output_transform_node.Path = OutputStosMosaicTransformName + '.mosaic'
            
            
            channel_node.AddChild(output_transform_node)
        else:
            if not output_transform_node.IsInputTransformMatched(input_transform_node):
                if os.path.exists(output_transform_node.FullPath):
                    os.remove(output_transform_node.FullPath)    
         
        output_transform_list.append(output_transform_node)
        
        # Always copy so our offset calculation is based on untranslated transforms
        output_transform_node.SetTransform(input_transform_node)
        shutil.copy(input_transform_node.FullPath, output_transform_node.FullPath)              
            
    mosaicToVolume = mosaicvolume.MosaicVolume.Load(output_transform_list)

    # Translate needs to accound for the fact that the mosaics need an origin of 0,0 for assemble to work.  We also need to figure out the largest image dimension
    # and set the CropBox property so each image is the same size after assemble is used.
    if mosaicToVolume.IsOriginAtZero():
        return None

    mosaicToVolume.TranslateToZeroOrigin()

    (minY, minX, maxY, maxX) = mosaicToVolume.VolumeBounds

    maxX = int(math.ceil(maxX))
    maxY = int(math.ceil(maxY))

    # Failing these asserts means the translate to zero origin function is not actually translating to a zero origin
    assert(minX >= 0)
    assert(minY >= 0)

    for transform in output_transform_list:
        transform.CropBox = (maxX, maxY)
    
    # Create a new node for the translated mosaic if needed and save it
    
    mosaicToVolume.Save()

    return


def FetchVolumeTransforms(StosMapNode, ChannelsRegEx, TransformRegEx):
    BlockNode = StosMapNode.FindParent('Block')
    Channels = BlockNode.findall('Section/Channel')
    MatchingChannelNodes = VolumeManagerHelpers.SearchCollection(Channels, 'Name', ChannelsRegEx)
    
    MatchingTransformNodes = VolumeManagerHelpers.SearchCollection(MatchingChannelNodes, 'Name', TransformRegEx)
     
    StosMosaicTransforms = []
    for TransformNode in MatchingTransformNodes:
        sectionNode = TransformNode.FindParent('Section')
        if sectionNode is None:
            continue
        
        if not StosMapNode.SectionInMap(sectionNode.Number):
            continue
            
        StosMosaicTransforms.append(TransformNode) 
    
    return StosMosaicTransforms


def ReportVolumeBounds(StosMapNode, ChannelsRegEx, TransformName, Logger, **kwargs): 
   
    StosMosaicTransformNodes = FetchVolumeTransforms(StosMapNode, ChannelsRegEx, TransformName)
    
    StosMosaicTransforms = map(lambda tnode: tnode.FullPath, StosMosaicTransformNodes)
    
    mosaicToVolume = mosaicvolume.MosaicVolume.Load(StosMosaicTransforms)
    
    return str(mosaicToVolume.VolumeBounds)
    

def BuildChannelMosaicToVolumeTransform(StosMapNode, StosGroupNode, TransformNode, OutputTransformName, Logger, **kwargs):
    '''Build a slice-to-volume transform for each section referenced in the StosMap'''

    MosaicTransformParent = TransformNode.Parent

    SaveTransformParent = False

    MappedChannelNode = TransformNode.FindParent('Channel')

    SectionNode = TransformNode.FindParent('Section')
    if SectionNode is None:
        Logger.error("No section found for transform: " + str(TransformNode))
        return None

    MappedSectionNumber = SectionNode.Number

    ControlSectionNumbers = list(StosMapNode.FindAllControlsForMapped(MappedSectionNumber))
    if len(ControlSectionNumbers) == 0:
        if StosMapNode.CenterSection != MappedSectionNumber:
            Logger.info("No SectionMappings found for section: " + str(MappedSectionNumber))

    Logger.info("%d -> %s" % (MappedSectionNumber, str(ControlSectionNumbers)))

    SectionMappingNode = StosGroupNode.GetSectionMapping(MappedSectionNumber)
    if SectionMappingNode is None:
        stosMosaicTransform = _ApplyStosToMosaicTransform(None, TransformNode, OutputTransformName, Logger, **kwargs)
        if not stosMosaicTransform is None:
            SaveTransformParent = True
        
    else:
        for stostransform in SectionMappingNode.Transforms:
            if not stostransform.MappedChannelName == MappedChannelNode.Name:
                continue

            if not int(stostransform.ControlSectionNumber) in ControlSectionNumbers:
                continue

            stosMosaicTransform = _ApplyStosToMosaicTransform(stostransform, TransformNode, OutputTransformName, Logger, **kwargs)
            if not stosMosaicTransform is None:
                SaveTransformParent = True

#         mosaicToVolume = mosaicvolume.MosaicVolume.Load(StosMosaicTransforms)
#         mosaicToVolume.TranslateToZeroOrigin()
#         mosaicToVolume.Save()



        #
#     SliceToVolumeTransform = FindTransformForMapping(StosGroupNode, ControlSectionNumber, MappedSectionNumber)
#     if SliceToVolumeTransform is None:
#         Logger.error("No SliceToVolumeTransform found for: " + str(MappedSectionNumber) + " -> " + ControlSectionNumber.Control)
#         return
#
#     files.RemoveOutdatedFile(SliceToVolumeTransform.FullPath, OutputTransformNode.FullPath)
#
#     if os.path.exists(OutputTransformNode.FullPath):
#         return
    if SaveTransformParent:
        return MosaicTransformParent

    return None

if __name__ == '__main__':
    pass
