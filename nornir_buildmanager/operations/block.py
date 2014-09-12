'''
Created on Jun 22, 2012

@author: Jamesan
'''


import copy
import itertools
import logging
import os
import random
import shutil
import subprocess
import math

from nornir_buildmanager import VolumeManagerETree, VolumeManagerHelpers
from nornir_buildmanager.metadatautils import *
from nornir_buildmanager.validation import transforms
from nornir_imageregistration import assemble, mosaic, volume
from nornir_imageregistration.files import stosfile, mosaicfile
from nornir_imageregistration.transforms import *
import nornir_imageregistration.stos_brute as stos_brute
from nornir_imageregistration.alignment_record import AlignmentRecord
import nornir_pools as pools
from nornir_shared import *
from nornir_shared.processoutputinterceptor import ProgressOutputInterceptor


import nornir_buildmanager.operations.helpers.stosgroupvolume as stosgroupvolume
import nornir_buildmanager.operations.helpers.mosaicvolume as mosaicvolume


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

                Pool = pools.GetGlobalProcessPool()

                cmd = 'convert -colorspace RGB ' + tempfilenameOne + ' ' + tempfilenameTwo + ' ' + tempfilenameOne + ' -combine -interlace PNG ' + OverlayFilename
                prettyoutput.Log(cmd)
                Pool.add_process(cmd, cmd + " && exit", shell=True)
                # subprocess.Popen(cmd + " && exit", shell=True)

                if self.DiffFilename is None:
                    DiffFilename = 'diff_' + OverlayFile.replace("temp", "", 1) + '.png'
                else:
                    DiffFilename = self.DiffFilename

                cmd = 'composite ' + tempfilenameOne + ' ' + tempfilenameTwo + ' -compose difference  -interlace PNG ' + DiffFilename
                prettyoutput.Log(cmd)

                Pool.add_process(cmd, cmd + " && exit", shell=True)

                if not self.WarpedFilename is None:
                    cmd = 'convert ' + tempfilenameTwo + " -interlace PNG " + self.WarpedFilename
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


def GetOrCreateNonStosSectionList(BlockNode, **kwargs):

    StosExemptNode = VolumeManagerETree.XElementWrapper(tag='NonStosSectionNumbers')
    (added, StosExemptNode) = BlockNode.UpdateOrAddChild(StosExemptNode)

    # Fetch the list of the exempt nodes from the element text
    ExemptString = StosExemptNode.text

    if(ExemptString is None or len(ExemptString) == 0):
        return []

    # OK, parse the exempt string to a different list
    NonStosSectionNumbers = [int(x) for x in ExemptString.split(',')]

    return NonStosSectionNumbers




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
    NonStosSectionNumbers = GetOrCreateNonStosSectionList(BlockNode)

    SectionNumberList = [SectionNumberKey(s) for s in SectionNodeList]

    StosSectionNumbers = []
    for sectionNumber in SectionNumberList:
        if not sectionNumber in NonStosSectionNumbers:
            StosSectionNumbers.append(sectionNumber)

    # Fetch the list of known bad sections, if it exists
    NonStosSectionNumbers = GetOrCreateNonStosSectionList(BlockNode)

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


def CreateSectionToSectionMapping(Parameters, BlockNode, Logger, **kwargs):
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

    NonStosSectionNumbers = GetOrCreateNonStosSectionList(BlockNode)
    if OutputMappingNode.ClearBannedControlMappings(NonStosSectionNumbers):
        SaveOutputMapping = True

    if UpdateStosMapWithRegistrationTree(OutputMappingNode, DefaultRT, Logger):
        SaveOutputMapping = True

#
#     for iSectionNode, SectionNode  in enumerate(SectionNodeList):
#         iStartingAdjacent = iSectionNode - NumAdjacentSections
#         iEndingAdjacent = iSectionNode + NumAdjacentSections
#
#         if(iStartingAdjacent < 0):
#             iStartingAdjacent = 0
#
#         if iEndingAdjacent >= len(SectionNodeList):
#             iEndingAdjacent = len(SectionNodeList) - 1
#
#         iAdjacentSections = list(range(iStartingAdjacent, iSectionNode))
#         iAdjacentSections.extend(range(iSectionNode + 1, iEndingAdjacent + 1))
#
#         AdjacentSections = list()
#
#         Logger.warn("Finding maps for " + str(SectionNode.Number))
#         SectionNumber = int(SectionNode.Number)
#
#         StosMapEntry = OutputMappingNode.find("Mapping[@Control='" + str(SectionNumber) + "']")
#
#         if SectionNumber in NonStosSectionNumbers:
#             Logger.warn("Skipping Banned Section: " + str(SectionNumber))
#             if not StosMapEntry is None:
#                 OutputMappingNode.remove(StosMapEntry)
#                 SaveOutputMapping = True
#         else:
#
#             for i in iAdjacentSections:
#
#                 AdjNodeNumber = int(SectionNodeList[i].Number)
#
#                 if SectionNumber - BlockMiddle == 0:
#                     ControlNumber = SectionNumber
#                     MappingNumber = AdjNodeNumber
#                 elif SectionNumber - BlockMiddle < 0:
#                     ControlNumber = max(SectionNumber, AdjNodeNumber)
#                     MappingNumber = min(SectionNumber, AdjNodeNumber)
#                 else:
#                     MappingNumber = max(SectionNumber, AdjNodeNumber)
#                     ControlNumber = min(SectionNumber, AdjNodeNumber)
#
#                 # Don't map the center section
#                 if MappingNumber == BlockMiddle:
#                     Logger.warn("Skipping Center Section: " + str(MappingNumber))
#                     continue
#
#                 # Figure out which section should be the control and which should be mapped
#                 if(SectionNumber - BlockMiddle == 0):
#                     AdjacentSections.append(MappingNumber)
#                     Logger.warn("Adding " + str(MappingNumber))
#                 elif(SectionNumber == ControlNumber):
#                     AdjacentSections.append(MappingNumber)
#                     Logger.warn("Adding " + str(MappingNumber))
#                 else:
#                     Logger.warn("Skipping " + str(MappingNumber))
#
#             # Create a node to store the stos mappings
#             if len(AdjacentSections) > 0:
#     #            AdjacentSectionString = ''.join(str(AdjacentSections))
#     #            AdjacentSectionString = AdjacentSectionString.strip('[')
#     #            AdjacentSectionString = AdjacentSectionString.strip(']')
#                 if StosMapEntry is None:
#                     StosMapEntry = VolumeManagerETree.MappingNode(SectionNode.Number, AdjacentSections)
#                     OutputMappingNode.append(StosMapEntry)
#                     SaveOutputMapping = True
#                 else:
#                     for a in AdjacentSections:
#                         if not a in StosMapEntry.Mapped:
#                             StosMapEntry.Mapped.append(a)
#                             SaveOutputMapping = True

    if SaveBlock:
        return BlockNode
    elif SaveOutputMapping:
        # Cannot save OutputMapping, it is not a container
        return BlockNode

    return None

def __CallNornirStosBrute(stosNode, Downsample, ControlImageNode, MappedImageNode, ControlMaskImageNode=None, MappedMaskImageNode=None, argstring=None, Logger=None):
    '''Call the stos-brute version from nornir-imageregistration'''

    alignment = None
    if not (ControlMaskImageNode is None or MappedMaskImageNode is None):
        alignment = stos_brute.SliceToSliceBruteForce(FixedImageInput=ControlImageNode.FullPath,
                                                      WarpedImageInput=MappedImageNode.FullPath,
                                                      FixedImageMaskPath=ControlMaskImageNode.FullPath,
                                                      WarpedImageMaskPath=MappedMaskImageNode.FullPath,
                                                      Cluster=False)

        stos = alignment.ToStos(ControlImageNode.FullPath,
                         MappedImageNode.FullPath,
                         ControlMaskImageNode.FullPath,
                         MappedMaskImageNode.FullPath,
                         PixelSpacing=Downsample)

        stos.Save(stosNode.FullPath)

    else:
        alignment = stos_brute.SliceToSliceBruteForce(FixedImageInput=ControlImageNode.FullPath,
                                                      WarpedImageInput=MappedImageNode.FullPath,
                                                      Cluster=False)

        stos = alignment.ToStos(ControlImageNode.FullPath,
                         MappedImageNode.FullPath,
                         PixelSpacing=Downsample)

        stos.Save(stosNode.FullPath, AddMasks=False)

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

def FilterToFilterBruteRegistration(StosGroup, ControlFilter, MappedFilter, OutputType, OutputPath, Logger=None, argstring=None):
    '''Create a transform node, populate, and generate the transform'''


    if Logger is None:
        Logger = logging.getLogger(__name__ + ".FilterToFilterBruteRegistration")
        
    stosNode = StosGroup.GetStosTransformNode(ControlFilter, MappedFilter)

    ControlImageNode = ControlFilter.GetOrCreateImage(StosGroup.Downsample)
    MappedImageNode = MappedFilter.GetOrCreateImage(StosGroup.Downsample)
    ControlMaskImageNode = ControlFilter.GetMaskImage(StosGroup.Downsample)
    MappedMaskImageNode = MappedFilter.GetMaskImage(StosGroup.Downsample)

    if not os.path.exists(ControlImageNode.FullPath):
        Logger.error("Control image missing" + ControlImageNode.FullPath)
        return None

    if not os.path.exists(MappedImageNode.FullPath):
        Logger.error("Mapped image missing" + MappedImageNode.FullPath)
        return None

    if stosNode is None:
        stosNode = StosGroup.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType, OutputPath)

        # We just created this, so remove any old files
        if os.path.exists(stosNode.FullPath):
            os.remove(stosNode.FullPath)

    else:
        if 'ControlImageChecksum' in stosNode.attrib:
            stosNode = transforms.RemoveOnMismatch(stosNode, 'ControlImageChecksum', ControlImageNode.Checksum)
            if stosNode is None:
                stosNode = StosGroup.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType, OutputPath)
        else:
            files.RemoveOutdatedFile(ControlImageNode.FullPath, stosNode.FullPath)
            if not ControlMaskImageNode is None:
                files.RemoveOutdatedFile(ControlMaskImageNode.FullPath, stosNode.FullPath)

        if 'MappedImageChecksum' in stosNode.attrib:
            stosNode = transforms.RemoveOnMismatch(stosNode, 'MappedImageChecksum', MappedImageNode.Checksum)
            if stosNode is None:
                stosNode = StosGroup.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType, OutputPath)
        else:
            files.RemoveOutdatedFile(MappedImageNode.FullPath, stosNode.FullPath)
            if not MappedMaskImageNode is None:
                files.RemoveOutdatedFile(MappedMaskImageNode.FullPath, stosNode.FullPath)

    # print OutputFileFullPath
    CmdRan = False
    if not os.path.exists(stosNode.FullPath):
        
        ManualStosFileFullPath = StosGroup.PathToManualTransform(stosNode.FullPath)
        if not ManualStosFileFullPath is None:
            prettyoutput.Log("Copy manual override stos file to output: " + os.path.basename(ManualStosFileFullPath))
            shutil.copy(ManualStosFileFullPath, stosNode.FullPath)
            CmdRan = True
        else: 
            __CallNornirStosBrute(stosNode, StosGroup.Downsample, ControlImageNode, MappedImageNode, ControlMaskImageNode, MappedMaskImageNode)
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
        stosNode.ControlImageChecksum = ControlImageNode.Checksum
        stosNode.MappedImageChecksum = MappedImageNode.Checksum

    if CmdRan:
        return stosNode

    return None


def __StosFilename(ControlFilter, MappedFilter):

    ControlSectionNode = ControlFilter.FindParent('Section')
    MappedSectionNode = MappedFilter.FindParent('Section')

    OutputFile = str(MappedSectionNode.Number) + '-' + str(ControlSectionNode.Number) + \
                             '_ctrl-' + ControlFilter.Parent.Name + "_" + ControlFilter.Name + \
                             '_map-' + MappedFilter.Parent.Name + "_" + MappedFilter.Name + '.stos'
    return OutputFile


def StosBrute(Parameters, VolumeNode, MappingNode, BlockNode, ChannelsRegEx, FiltersRegEx, Logger, **kwargs):
    '''Create an initial rotation and translation alignment for a pair of unregistered images'''

    Downsample = int(Parameters.get('Downsample', 32))
    OutputStosGroupName = kwargs.get('OutputGroup', 'Brute')
    OutputStosType = kwargs.get('Type', 'Brute')

    # Additional arguments for stos-brute
    argstring = misc.ArgumentsFromDict(Parameters)

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
            print "\tMap - " + MappedFilter.Parent.Name + "_" + MappedFilter.Name
             
            ControlFilterList = ControlSectionNode.MatchChannelFilterPattern(ChannelsRegEx, FiltersRegEx)
            for ControlFilter in ControlFilterList:
                print "\tCtrl - " + ControlFilter.Parent.Name + "_" + ControlFilter.Name

                # ControlImageSetNode = VolumeManagerETree.ImageNode.wrap(ControlImageSetNode)
                OutputFile = __StosFilename(ControlFilter, MappedFilter)
                
                (added, stos_mapping_node) = StosGroupNode.GetOrCreateSectionMapping(MappedSection)
                if added:
                    yield stos_mapping_node.Parent

                stosNode = FilterToFilterBruteRegistration(StosGroup=StosGroupNode,
                                                ControlFilter=ControlFilter,
                                                MappedFilter=MappedFilter,
                                                OutputType=OutputStosType,
                                                OutputPath=OutputFile)

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


def UpdateStosImagePaths(StosTransformPath, ControlImageFullPath, MappedImageFullPath, ControlImageMaskFullPath=None, MappedImageMaskFullPath=None):

    # ir-stom's -slice_dirs argument is broken for masks, so we have to patch the stos file before use
    InputStos = stosfile.StosFile.Load(StosTransformPath)

    InputStos.ControlImageFullPath = ControlImageFullPath
    InputStos.MappedImageFullPath = MappedImageFullPath

    if not InputStos.ControlMaskName is None:
        InputStos.ControlMaskFullPath = ControlImageMaskFullPath

    if not InputStos.MappedMaskName is None:
        InputStos.MappedMaskFullPath = MappedImageMaskFullPath

    InputStos.Save(StosTransformPath)


def FixStosFilePaths(ControlFilter, MappedFilter, StosTransformNode, Downsample, StosFilePath=None):

    if StosFilePath is None:
        StosFilePath = StosTransformNode.FullPath

    if ControlFilter.GetImageMask(Downsample) is None or MappedFilter.GetImageMask(Downsample) is None:
        UpdateStosImagePaths(StosFilePath,
                         ControlFilter.GetImage(Downsample).FullPath,
                         MappedFilter.GetImage(Downsample).FullPath)
    else:
        UpdateStosImagePaths(StosFilePath,
                         ControlFilter.GetImage(Downsample).FullPath,
                         MappedFilter.GetImage(Downsample).FullPath,
                         ControlFilter.GetImageMask(Downsample).FullPath,
                         MappedFilter.GetImageMask(Downsample).FullPath)


def SectionToVolumeImage(Parameters, TransformNode, Logger, CropUndefined=True, **kwargs):
    '''Executre ir-stom on a provided .stos file'''

    GroupNode = TransformNode.FindParent("StosGroup")
    SaveRequired = False

    SectionMappingNode = TransformNode.FindParent('SectionMappings')

    FilePrefix = str(SectionMappingNode.MappedSectionNumber) + '-' + TransformNode.ControlSectionNumber + '_'
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

                    FilePrefix = str(SectionMappingNode.MappedSectionNumber) + '-' + StosTransformNode.ControlSectionNumber + '_'

                    stosImages = StosImageNodes(StosTransformNode, GroupNode.Downsample)

                    if stosImages.ControlImageNode is None or stosImages.MappedImageNode is None:
                        continue
                    
                    if created_overlay:
                        OverlayImageNode.SetTransform(StosTransformNode)
                    else:
                        if not OverlayImageNode.RemoveIfTransformMismatched(StosTransformNode):
                            files.RemoveOutdatedFile(StosTransformNode.FullPath, OverlayImageNode.FullPath)
                            files.RemoveOutdatedFile(stosImages.ControlImageNode.FullPath, OverlayImageNode.FullPath)
                            files.RemoveOutdatedFile(stosImages.MappedImageNode.FullPath, OverlayImageNode.FullPath)
                        
                    if created_diff:
                        DiffImageNode.SetTransform(StosTransformNode)
                    else:
                        DiffImageNode.RemoveIfTransformMismatched(StosTransformNode)
                        
                    if created_warped:
                        WarpedImageNode.SetTransform(StosTransformNode)
                    else:
                        WarpedImageNode.RemoveIfTransformMismatched(StosTransformNode)

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
                        processoutputinterceptor.ProcessOutputInterceptor.Intercept(StomPreviewOutputInterceptor(NewP,
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

        Pool = pools.GetGlobalProcessPool()
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
    Pool = pools.GetGlobalProcessPool()
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


# def FindTransformsForMapping(GroupNode, ControlSection, MappedSection):
#    '''Locate the transform within GroupNode mapping ControlSection to MappedSection'''
#
#    SectionMappingNode = GroupNode.GetSectionMapping(MappedSection)
#
#    TransformXPathTemplate = "SectionMappings[@MappedSectionNumber='%(MappedSection)d']/Transform[@ControlSectionNumber='%(ControlSection)d']"
#    # Find the inputTransformNode in the InputGroupNode
#    TransformXPath = TransformXPathTemplate % {'MappedSection' : MappedSection,
#                                               'ControlSection' : ControlSection}
#
#    InputTransformNode = GroupNode.find(TransformXPath)
#    return InputTransformNode
 

def __GetInputStosFileForRegistration(StosGroupNode, InputTransformNode, OutputDownsample, ControlFilter, MappedFilter):

    # Begin selecting the input transform for registration
    AutomaticInputDir = os.path.join(StosGroupNode.FullPath, 'Automatic')
    if not os.path.exists(AutomaticInputDir):
        os.makedirs(AutomaticInputDir)

    ManualInputDir = os.path.join(StosGroupNode.FullPath, 'Manual')
    if not os.path.exists(ManualInputDir):
        os.makedirs(ManualInputDir)

    # Copy the input stos or converted stos to the input directory
    AutomaticInputStosFullPath = os.path.join(AutomaticInputDir, InputTransformNode.Path)
    ManualInputStosFullPath = os.path.join(ManualInputDir, __StosFilename(ControlFilter, MappedFilter))

    InputStosFullPath = __SelectAutomaticOrManualStosFilePath(AutomaticInputStosFullPath=AutomaticInputStosFullPath, ManualInputStosFullPath=ManualInputStosFullPath)
    InputChecksum = None
    if InputStosFullPath == AutomaticInputStosFullPath:
        __GenerateStosFile(InputTransformNode, AutomaticInputStosFullPath, OutputDownsample, ControlFilter, MappedFilter)
        InputChecksum = InputTransformNode.Checksum
    else:
        InputChecksum = stosfile.StosFile.LoadChecksum(InputStosFullPath)

    return (InputStosFullPath, InputChecksum)


def __GenerateStosFile(InputTransformNode, OutputTransformPath, OutputDownsample, ControlFilter, MappedFilter):
    '''Generates a new stos file using the specified filters and scales the transform to match the
       requested downsample as needed.
       returns true if a new stos file was generated'''

    # Replace the automatic files if they are outdated.
    # We should not be trying to create output if we have no input
    assert(os.path.exists(InputTransformNode.FullPath))

    files.RemoveOutdatedFile(InputTransformNode.FullPath, OutputTransformPath)
    if not os.path.exists(OutputTransformPath):
        StosGroupNode = InputTransformNode.FindParent('StosGroup')
        InputDownsample = StosGroupNode.Downsample
        try:
            InputStos = stosfile.StosFile.Load(InputTransformNode.FullPath)
        except ValueError:
            return False
             
        ControlImage = ControlFilter.GetOrCreateImage(OutputDownsample)
        if ControlImage is None:
            raise Exception("No control image available for stos file generation: %s" % InputTransformNode.FullPath)

        MappedImage = MappedFilter.GetOrCreateImage(OutputDownsample)
        if MappedImage is None:
            raise Exception("No mapped image available for stos file generation: %s" % InputTransformNode.FullPath)

        if not (InputStos.ControlImagePath == ControlImage.FullPath and
           InputStos.MappedImagePath == MappedImage.FullPath and
           OutputDownsample == InputDownsample):

            ModifiedInputStos = InputStos.ChangeStosGridPixelSpacing(oldspacing=InputDownsample,
                                           newspacing=OutputDownsample,
                                           ControlImageFullPath=ControlImage.FullPath,
                                           MappedImageFullPath=MappedImage.FullPath)

            ModifiedInputStos.Save(OutputTransformPath)
        else:
            shutil.copyfile(InputTransformNode.FullPath, OutputTransformPath)

        return True

    return False


def __SelectAutomaticOrManualStosFilePath(AutomaticInputStosFullPath, ManualInputStosFullPath):
    ''' Use the manual stos file if it exists, prevent any cleanup from occurring on the manual file '''

    if not os.path.exists(AutomaticInputStosFullPath):
        if os.path.exists(ManualInputStosFullPath):
            return ManualInputStosFullPath

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


                                                                              

def StosGrid(Parameters, MappingNode, InputGroupNode, Downsample=32, ControlFilterPattern=None, MappedFilterPattern=None, OutputStosGroup=None, Type=None, **kwargs):

    Logger = logging.getLogger(__name__ + '.StosGrid')

    BlockNode = InputGroupNode.FindParent('Block')

    if(OutputStosGroup is None):
        OutputStosGroup = 'Grid'
        
    OutputStosGroupName = OutputStosGroup

    if(Type is None):
        Type = 'Grid'

    MappedSectionList = MappingNode.Mapped

    MappedSectionList.sort()

    SaveBlockNode = False
    SaveGroupNode = False
    
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

            InputStosGroupNode = InputSectionMappingNode.FindParent('StosGroup')
            InputDownsample = int(InputStosGroupNode.Downsample)
            InputImageXPathTemplate = "Channel/Filter/Image[@Name='%(ImageName)s']/Level[@Downsample='%(OutputDownsample)d']"
            
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

            OutputFile = __StosFilename(ControlFilter, MappedFilter)
            OutputStosFullPath = os.path.join(OutputStosGroupNode.FullPath, OutputFile)
            stosNode = OutputStosGroupNode.GetStosTransformNode(ControlFilter, MappedFilter)
            if stosNode is None:
                stosNode = OutputStosGroupNode.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType="grid", OutputPath=OutputFile)

            (InputStosFullPath, InputStosFileChecksum) = __GetInputStosFileForRegistration(StosGroupNode=OutputStosGroupNode,
                                                                    InputTransformNode=InputTransformNode,
                                                                    ControlFilter=ControlFilter,
                                                                    MappedFilter=MappedFilter,
                                                                    OutputDownsample=OutputDownsample)

            if not os.path.exists(InputStosFullPath):
                # Hmm... no input.  This is worth reporting and moving on
                Logger.error("ir-stos-grid did not produce output for " + InputStosFullPath)
                InputGroupNode.remove(InputTransformNode)
                continue

            # If the manual or automatic stos file is newer than the output, remove the output
            #files.RemoveOutdatedFile(InputTransformNode.FullPath, OutputStosFullPath): 

            # Remove our output if it was generated from an input transform with a different checksum
            if os.path.exists(OutputStosFullPath):
                # stosNode = OutputSectionMappingNode.GetChildByAttrib('Transform', 'ControlSectionNumber', InputTransformNode.ControlSectionNumber)
                if not stosNode is None:
                    if 'InputTransformChecksum' in stosNode.attrib:
                        if(InputStosFileChecksum != stosNode.InputTransformChecksum):
                            os.remove(OutputStosFullPath)

                            # Remove old stos meta-data and create from scratch to avoid stale data.
                            OutputSectionMappingNode.remove(stosNode)
                            stosNode = OutputStosGroupNode.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType="grid", OutputPath=OutputFile)

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


def BuildSliceToVolumeTransforms(StosMapNode, StosGroupNode, OutputMap, OutputGroup, **kwargs):
    '''Build a slice-to-volume transform for each section referenced in the StosMap'''

    BlockNode = StosGroupNode.Parent
    InputStosGroupNode = StosGroupNode

    rt = __StosMapToRegistrationTree(StosMapNode)

    if len(rt.RootNodes) == 0:
        return

    (AddedGroupNode, OutputGroupNode) = BlockNode.GetOrCreateStosGroup(OutputGroup, InputStosGroupNode.Downsample)
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
        for saveNode in SliceToVolumeFromRegistrationTreeNode(rt, Node, InputGroupNode=InputStosGroupNode, OutputGroupNode=OutputGroupNode, ControlToVolumeTransform=None):
            yield saveNode

    # TranslateVolumeToZeroOrigin(OutputGroupNode)
    # Do not use TranslateVolumeToZeroOrigin here because the center of the volume image does not get shifted with the rest of the sections. That is a problem.  We should probably create an identity transform for the root nodes in
    # the registration tree
    

def SliceToVolumeFromRegistrationTreeNode(rt, Node, InputGroupNode, OutputGroupNode, ControlToVolumeTransform=None):
    ControlSection = Node.SectionNumber

    Logger = logging.getLogger(__name__ + '.SliceToVolumeFromRegistrationTreeNode')

    for MappedSectionNode in Node.Children:
        mappedSectionNumber = MappedSectionNode.SectionNumber
        mappedNode = rt.Nodes[mappedSectionNumber]

        logStr = "%s <- %s" % (str(ControlSection),  str(mappedSectionNumber))

        (MappingAdded, OutputSectionMappingsNode) = OutputGroupNode.GetOrCreateSectionMapping(mappedSectionNumber)
        if MappingAdded:
            yield OutputGroupNode
        
        MappedToControlTransforms = InputGroupNode.TransformsForMapping(mappedSectionNumber, ControlSection)
        
        if MappedToControlTransforms is None or len(MappedToControlTransforms) == 0:
            Logger.error(" %s : No transform found:" % (logStr))
            continue
         
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
                (OutputTransformAdded, OutputTransform) = OutputSectionMappingsNode.UpdateOrAddChildByAttrib(OutputTransform, 'MappedSectionNumber')
                OutputTransform.Name = str(mappedSectionNumber) + '-' + str(ControlSection)
                OutputTransform.Path = OutputTransform.Name + '.stos'
                OutputTransform.SetTransform(MappedToControlTransform)
                
                #Remove any residual transform file just in case
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
                        MToVStos = stosfile.AddStosTransforms(MappedToControlTransform.FullPath, ControlToVolumeTransform.FullPath)
                        MToVStos.Save(OutputTransform.FullPath)

                        OutputTransform.ControlToVolumeTransformChecksum = ControlToVolumeTransform.Checksum
                        OutputTransform.ResetChecksum()
                        OutputTransform.SetTransform(MappedToControlTransform)
                        # OutputTransform.Checksum = stosfile.StosFile.LoadChecksum(OutputTransform.FullPath)
                    except ValueError:
                        # Probably an invalid transform.  Skip it
                        OutputSectionMappingsNode.remove(OutputTransform)
                        OutputTransform = None
                        pass
                    yield OutputSectionMappingsNode
                else:
                    Logger.info(" %s: is still valid" % (logStr))

            for retval in SliceToVolumeFromRegistrationTreeNode(rt, mappedNode, InputGroupNode, OutputGroupNode, ControlToVolumeTransform=OutputTransform):
                yield retval


def RegistrationTreeFromStosMapNode(StosMapNode):
    rt = registrationtree.RegistrationTree()

    for mappingNode in StosMapNode.findall('Mapping'):
        for mappedSection in mappingNode.Mapped:
            rt.AddPair(mappingNode.Control, mappedSection)

    return rt


def __MappedFilterForTransform(transform_node):
    return __GetFilter(transform_node,
                                transform_node.MappedSectionNumber,
                                transform_node.MappedChannelName,
                                transform_node.MappedFilterName)

def __ControlFilterForTransform(transform_node):
    return __GetFilter(transform_node,
                                transform_node.ControlSectionNumber,
                                transform_node.ControlChannelName,
                                transform_node.ControlFilterName)

def __GetFilter(transform_node, section, channel, filter):
    BlockNode = transform_node.FindParent(ParentTag='Block')
    sectionNode = BlockNode.GetSection(section)
    channelNode = sectionNode.GetChannel(channel)
    filterNode = channelNode.GetFilter(filter)
    return filterNode


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
        
     
    # TODO: Skip transforms using filters which no longer exist.  Should live in a seperate function.
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


def ScaleStosGroup(InputStosGroupNode, OutputDownsample, OutputGroupName, **kwargs):

    '''Take a stos group node, scale the transforms, and save in new stosgroup
    
       TODO: This function used to create stos transforms between different filters to.  Port that to a seperate function
    '''

    ControlChannelPattern = kwargs.get("ControlChannelPattern", None)
    ControlFilterPattern = kwargs.get("ControlFilterPattern", None)
    MappedChannelPattern = kwargs.get("MappedChannelPattern", None)
    MappedFilterPattern = kwargs.get("MappedFilterPattern", None)

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
                ControlFilter = __ControlFilterForTransform(InputTransformNode)
                MappedFilter = __MappedFilterForTransform(InputTransformNode)
            except AttributeError as e:
                logger = logging.getLogger("ScaleStosGroup")
                logger.error("ScaleStosGroup missing filter for InputTransformNode " + InputTransformNode.FullPath)
                continue

            # for (ControlFilter, MappedFilter) in itertools.product(ControlFilters, MappedFilters):

            (stosNode_added, stosNode) = OutputGroupNode.GetOrCreateStosTransformNode(ControlFilter,
                                                             MappedFilter,
                                                             OutputType=InputTransformNode.Type,
                                                             OutputPath=__StosFilename(ControlFilter, MappedFilter))
            
            if not stosNode_added:
                if not stosNode.IsInputTransformMatched(InputTransformNode):
                    if os.path.exists(stosNode.FullPath):
                        os.remove(stosNode.FullPath)
            else:
                #Remove an old file if we had to generate the meta-data
                if os.path.exists(stosNode.FullPath):
                    os.remove(stosNode.FullPath)
                    
            if not os.path.exists(stosNode.FullPath):
                stosGenerated = __GenerateStosFile(InputTransformNode,
                                                                stosNode.FullPath,
                                                                OutputDownsample,
                                                                ControlFilter,
                                                                MappedFilter)

                if stosGenerated:
                    stosNode.ResetChecksum()
                    stosNode.SetTransform(InputTransformNode)
                else:
                    OutputGroupNode.Remove(stosNode)

                yield OutputGroupNode 
 

# def ScaleStosGroup(InputStosGroupNode, OutputDownsample, OutputGroupName, **kwargs):
#
#     '''Take a stos group node, scale the transforms, and save in new stosgroup'''
#
#     ControlChannelPattern = kwargs.get("ControlChannelPattern", None)
#     ControlFilterPattern = kwargs.get("ControlFilterPattern", None)
#     MappedChannelPattern = kwargs.get("MappedChannelPattern", None)
#     MappedFilterPattern = kwargs.get("MappedFilterPattern", None)
#
#     GroupParent = InputStosGroupNode.Parent
#
#     OutputGroupNode = VolumeManagerETree.StosGroupNode(OutputGroupName, OutputDownsample)
#     (SaveBlockNode, OutputGroupNode) = GroupParent.UpdateOrAddChildByAttrib(OutputGroupNode)
#
#     if not os.path.exists(OutputGroupNode.FullPath):
#             os.makedirs(OutputGroupNode.FullPath)
#
#     for inputSectionMapping in InputStosGroupNode.SectionMappings:
#
#         OutputSectionMapping = OutputGroupNode.GetOrCreateSectionMapping(inputSectionMapping.MappedSectionNumber)
#
#         InputTransformNodes = inputSectionMapping.findall('Transform')
#
#         for InputTransformNode in InputTransformNodes:
#
#             ControlFilters = __ControlFiltersForTransform(InputTransformNode, ControlChannelPattern, ControlFilterPattern)
#             MappedFilters = __MappedFiltersForTransform(InputTransformNode, MappedChannelPattern, MappedFilterPattern)
#
#             for (ControlFilter, MappedFilter) in itertools.product(ControlFilters, MappedFilters):
#
#                 stosNode = OutputGroupNode.CreateStosTransformNode(ControlFilter,
#                                                                  MappedFilter,
#                                                                  OutputType=InputTransformNode.Type,
#                                                                  OutputPath=__StosFilename(ControlFilter, MappedFilter))
#
#                 __RemoveStosFileIfOutdated(stosNode, InputTransformNode)
#
#                 if not os.path.exists(stosNode.FullPath):
#                     stosGenerated = __GenerateStosFile(InputTransformNode,
#                                                                     stosNode.FullPath,
#                                                                     OutputDownsample,
#                                                                     ControlFilter,
#                                                                     MappedFilter)
#
#                     if stosGenerated:
#                         stosNode.InputTransformChecksum = InputTransformNode.Checksum
#                         stosNode.Checksum = stosfile.StosFile.LoadChecksum(stosNode.FullPath)
#                     else:
#                         OutputGroupNode.Remove(stosNode)
#
#                 SaveBlockNode = SaveBlockNode or stosGenerated
#
#     if SaveBlockNode:
#         return GroupParent
#     return None


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
        #OutputTransformNode.SetTransform(StosTransformNode)
        OutputTransformNode.InputMosaicTransformChecksum = TransformNode.Checksum 
        # OutputTransformNode.Checksum = TransformNode.Checksum
        
    else:
        #files.RemoveOutdatedFile(StosTransformNode.FullPath, OutputTransformNode.FullPath)

        StosGroupNode = StosTransformNode.FindParent('StosGroup')

        SToV = stosfile.StosFile.Load(StosTransformNode.FullPath)
        # Make sure we are not using a downsampled transform
        SToV = SToV.ChangeStosGridPixelSpacing(StosGroupNode.Downsample, 1.0, create_copy=False)
        StoVTransform = factory.LoadTransform(SToV.Transform)

        MosaicTransform = mosaic.Mosaic.LoadFromMosaicFile(TransformNode.FullPath)
        assert(MosaicTransform.FixedBoundingBox[0] == 0 and MosaicTransform.FixedBoundingBox[1] == 0)
        # MosaicTransform.TranslateToZeroOrigin() 
        Tasks = []
         
        UsePool = True
        if UsePool:
            #This is a parallel operation, but the Python GIL is so slow using threads is slower.
            Pool = pools.GetLocalMachinePool()
    
            for imagename, MosaicToSectionTransform in MosaicTransform.ImageToTransform.iteritems():
                task = Pool.add_task(imagename, triangulation.AddTransforms, StoVTransform, MosaicToSectionTransform)
                task.imagename = imagename
                if hasattr(MosaicToSectionTransform, 'gridWidth'):
                    task.dimX = MosaicToSectionTransform.gridWidth
                if hasattr(MosaicToSectionTransform, 'gridHeight'):
                    task.dimY = MosaicToSectionTransform.gridHeight
    
                Tasks.append(task)
    
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
        
        #Always copy so our offset calculation is based on untranslated transforms
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
    
    #Create a new node for the translated mosaic if needed and save it
    
    mosaicToVolume.Save()

    return


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
