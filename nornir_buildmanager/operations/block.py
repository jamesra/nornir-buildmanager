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

from nornir_buildmanager import VolumeManagerETree, VolumeManagerHelpers
from nornir_buildmanager.metadatautils import *
from nornir_buildmanager.validation import transforms
from nornir_imageregistration import assemble
from nornir_imageregistration.io import stosfile, mosaicfile
from nornir_imageregistration.transforms import *
import nornir_pools as pools
from nornir_shared import *
from nornir_shared.processoutputinterceptor import ProgressOutputInterceptor


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
                Pool.add_task(cmd, cmd + " && exit", shell=True)
                # subprocess.Popen(cmd + " && exit", shell=True)

                if self.DiffFilename is None:
                    DiffFilename = 'diff_' + OverlayFile.replace("temp", "", 1) + '.png'
                else:
                    DiffFilename = self.DiffFilename

                cmd = 'composite ' + tempfilenameOne + ' ' + tempfilenameTwo + ' -compose difference  -interlace PNG ' + DiffFilename
                prettyoutput.Log(cmd)

                Pool.add_task(cmd, cmd + " && exit", shell=True)

                if not self.WarpedFilename is None:
                    cmd = 'convert ' + tempfilenameTwo + " -interlace PNG " + self.WarpedFilename
                    Pool.add_task(cmd, cmd + " && exit", shell=True)

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


def CreateSectionToSectionMapping(Parameters, BlockNode, Logger, **kwargs):
    '''Figure out which sections should be registered to each other
        @BlockNode'''
    NumAdjacentSections = int(Parameters.get('NumAdjacentSections', '1'))
    StosMapName = Parameters.get('OutputStosMapName', 'PotentialRegistrationChain')

    BlockMiddle = Parameters.get('CenterSection', None)

    try:
        BlockMiddle = int(BlockMiddle)
    except:
        BlockMiddle = None

    StosMapType = StosMapName + misc.GenNameFromDict(Parameters)

    SaveBlock = False
    SaveOutputMapping = False
    # Create a node to store the stos mappings
    OutputMappingNode = VolumeManagerETree.XElementWrapper(tag='StosMap', Name=StosMapName, Type=StosMapType)
    (SaveBlock, OutputMappingNode) = BlockNode.UpdateOrAddChildByAttrib(OutputMappingNode)

    if not SaveBlock and BlockMiddle is None:
        BlockMiddle = OutputMappingNode.CenterSection

    SectionNodeList = list(BlockNode.findall('Section'))
    SectionNodeList.sort(key=SectionNumberKey)

    NonStosSectionNumbers = GetOrCreateNonStosSectionList(BlockNode)

    # Fetch the list of known bad sections, if it exists

    if BlockMiddle is None:
        MaxSectionNumber = int(SectionNodeList[-1].Number)
        MinSectionNumber = int(SectionNodeList[0].Number)
        SectionNumberRange = MaxSectionNumber - MinSectionNumber
        BlockMiddle = (SectionNumberRange / 2) + MinSectionNumber

    OutputMappingNode.attrib['CenterSection'] = str(BlockMiddle)

    for iSectionNode, SectionNode  in enumerate(SectionNodeList):
        iStartingAdjacent = iSectionNode - NumAdjacentSections
        iEndingAdjacent = iSectionNode + NumAdjacentSections

        if(iStartingAdjacent < 0):
            iStartingAdjacent = 0

        if iEndingAdjacent >= len(SectionNodeList):
            iEndingAdjacent = len(SectionNodeList) - 1

        iAdjacentSections = list(range(iStartingAdjacent, iSectionNode))
        iAdjacentSections.extend(range(iSectionNode + 1, iEndingAdjacent + 1))

        AdjacentSections = list()

        Logger.warn("Finding maps for " + str(SectionNode.Number))
        SectionNumber = int(SectionNode.Number)

        StosMapEntry = OutputMappingNode.find("Mapping[@Control='" + str(SectionNumber) + "']")

        if SectionNumber in NonStosSectionNumbers:
            Logger.warn("Skipping Banned Section: " + str(SectionNumber))
            if not StosMapEntry is None:
                OutputMappingNode.remove(StosMapEntry)
                SaveOutputMapping = True
        else:

            for i in iAdjacentSections:

                AdjNodeNumber = int(SectionNodeList[i].Number)

                if SectionNumber - BlockMiddle == 0:
                    ControlNumber = SectionNumber
                    MappingNumber = AdjNodeNumber
                elif SectionNumber - BlockMiddle < 0:
                    ControlNumber = max(SectionNumber, AdjNodeNumber)
                    MappingNumber = min(SectionNumber, AdjNodeNumber)
                else:
                    MappingNumber = max(SectionNumber, AdjNodeNumber)
                    ControlNumber = min(SectionNumber, AdjNodeNumber)

                # Don't map the center section
                if MappingNumber == BlockMiddle:
                    Logger.warn("Skipping Center Section: " + str(MappingNumber))
                    continue

                # Figure out which section should be the control and which should be mapped
                if(SectionNumber - BlockMiddle == 0):
                    AdjacentSections.append(MappingNumber)
                    Logger.warn("Adding " + str(MappingNumber))
                elif(SectionNumber == ControlNumber):
                    AdjacentSections.append(MappingNumber)
                    Logger.warn("Adding " + str(MappingNumber))
                else:
                    Logger.warn("Skipping " + str(MappingNumber))

            # Create a node to store the stos mappings
            if len(AdjacentSections) > 0:
    #            AdjacentSectionString = ''.join(str(AdjacentSections))
    #            AdjacentSectionString = AdjacentSectionString.strip('[')
    #            AdjacentSectionString = AdjacentSectionString.strip(']')
                if StosMapEntry is None:
                    StosMapEntry = VolumeManagerETree.MappingNode(SectionNode.Number, AdjacentSections)
                    OutputMappingNode.append(StosMapEntry)
                    SaveOutputMapping = True
                else:
                    for a in AdjacentSections:
                        if not a in StosMapEntry.Mapped:
                            StosMapEntry.Mapped.append(a)
                            SaveOutputMapping = True

    if SaveBlock:
        return BlockNode
    elif SaveOutputMapping:
        # Cannot save OutputMapping, it is not a container
        return BlockNode

    return None


def FilterToFilterBruteRegistration(StosGroup, ControlFilter, MappedFilter, OutputType, OutputPath, Logger=None, argstring=None):
    '''Create a transform node, populate, and generate the transform'''
    if argstring is None:
        argstring = ""

    if Logger is None:
        Logger = logging.getLogger("FilterToFilterBruteRegistration")

    StosBruteTemplate = 'ir-stos-brute ' + argstring + '-save %(OutputFile)s -load %(ControlImage)s %(MovingImage)s -mask %(ControlMask)s %(MovingMask)s'
    StosBruteTemplateNoMask = 'ir-stos-brute ' + argstring + '-save %(OutputFile)s -load %(ControlImage)s %(MovingImage)s '

    stosNode = StosGroup.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType, OutputPath)

    OutputFileFullPath = stosNode.FullPath

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

    if 'ControlImageChecksum' in stosNode.attrib:
        stosNode = transforms.RemoveOnMismatch(stosNode, 'ControlImageChecksum', ControlImageNode.Checksum)
        if stosNode is None:
            stosNode = StosGroup.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType, OutputPath)
    else:
        files.RemoveOutdatedFile(ControlImageNode.FullPath, OutputFileFullPath)
        if not ControlMaskImageNode is None: 
            files.RemoveOutdatedFile(ControlMaskImageNode.FullPath, OutputFileFullPath)
    
    if 'MappedImageChecksum' in stosNode.attrib:
        stosNode = transforms.RemoveOnMismatch(stosNode, 'MappedImageChecksum', MappedImageNode.Checksum)
        if stosNode is None:
            stosNode = StosGroup.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType, OutputPath)
    else:
        files.RemoveOutdatedFile(MappedImageNode.FullPath, OutputFileFullPath)
        if not MappedMaskImageNode is None:
            files.RemoveOutdatedFile(MappedMaskImageNode.FullPath, OutputFileFullPath)
     
    # print OutputFileFullPath
    CmdRan = False
    if not os.path.exists(stosNode.FullPath):
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

        # Rescale stos file to full-res
        # stosFile = stosfile.StosFile.Load(stosNode.FullPath)
        # stosFile.Scale(StosGroup.Downsample)
        # stosFile.Save(stosNode.FullPath)

        # Load and save the stos file to ensure the transform doesn't have the original Ir-Tools floating point string representation which
        # have identical values but different checksums from the Python stos file objects %g representation

        stosNode.Checksum = stosfile.StosFile.LoadChecksum(stosNode.FullPath)
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

    SaveGroup = False

    # GetOrCreate the group for these stos files
    StosGroupNode = VolumeManagerETree.StosGroupNode(OutputStosGroupName, Downsample=Downsample)
    (SaveBlock, StosGroupNode) = BlockNode.UpdateOrAddChildByAttrib(StosGroupNode)

    StosGroupNode.Downsample = Downsample

    if not os.path.exists(StosGroupNode.FullPath):
        os.makedirs(StosGroupNode.FullPath)

    for MappedSection in AdjacentSections:
        MappedSectionNode = BlockNode.GetSection(MappedSection)

        if(MappedSectionNode is None):
            prettyoutput.LogErr("Could not find expected section for StosBrute: " + str(MappedSection))


        # Figure out all the combinations of assembled images between the two section and test them
        MappedFilterList = MappedSectionNode.MatchChannelFilterPattern(ChannelsRegEx, FiltersRegEx)

        if 'Downsample' in Parameters:
            del Parameters['Downsample']

        for MappedFilter in MappedFilterList:
            print "\tMap - " + MappedFilter.Parent.Name + "_" + MappedFilter.Name
            MappedImageNode = MappedFilter.GetImage(Downsample)
            MappedMaskImageNode = MappedFilter.GetMaskImage(Downsample)

            ControlFilterList = ControlSectionNode.MatchChannelFilterPattern(ChannelsRegEx, FiltersRegEx)
            for ControlFilter in ControlFilterList:
                print "\tCtrl - " + ControlFilter.Parent.Name + "_" + ControlFilter.Name

               # ControlImageSetNode = VolumeManagerETree.ImageNode.wrap(ControlImageSetNode)
                OutputFile = __StosFilename(ControlFilter, MappedFilter)

                stosNode = FilterToFilterBruteRegistration(StosGroup=StosGroupNode,
                                                ControlFilter=ControlFilter,
                                                MappedFilter=MappedFilter,
                                                OutputType=OutputStosType,
                                                OutputPath=OutputFile)

                if not stosNode is None:
                    SaveGroup = True

    if SaveBlock:
        return BlockNode
    elif SaveGroup:
        return StosGroupNode
    else:
        return None

def GetImage(BlockNode, SectionNumber, Channel, Filter, Downsample):

    sectionNode = BlockNode.GetSection(SectionNumber)
    channelNode = sectionNode.GetChannel(Channel)
    filterNode = channelNode.GetFilter(Filter)

    if sectionNode is None or channelNode is None or filterNode is None:
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

    WarpedImageNode = CreateImageNodeHelper(SectionMappingNode, WarpedOutputFileFullPath)
    WarpedImageNode.Type = 'Warped_' + TransformNode.Type

    stosImages = StosImageNodes(TransformNode, GroupNode.Downsample)

    # Compare the .stos file creation date to the output

    WarpedImageNode = transforms.RemoveOnMismatch(WarpedImageNode, 'InputTransformChecksum', TransformNode.Checksum)
    
    if(not WarpedImageNode is None):
        files.RemoveOutdatedFile(stosImages.ControlImageNode.FullPath, WarpedImageNode.FullPath)
        files.RemoveOutdatedFile(stosImages.MappedImageNode.FullPath, WarpedImageNode.FullPath)
    else:
        WarpedImageNode = CreateImageNodeHelper(SectionMappingNode, WarpedOutputFileFullPath)
        WarpedImageNode.Type = 'Warped_' + TransformNode.Type

    if not os.path.exists(WarpedImageNode.FullPath):
        SaveRequired = True
        assemble.TransformStos(TransformNode.FullPath, OutputFilename=WarpedImageNode.FullPath, CropUndefined=CropUndefined)
        prettyoutput.Log("Saving image: " + WarpedImageNode.FullPath)
        WarpedImageNode.InputTransformChecksum = TransformNode.Checksum

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
                    OverlayImageNode = CreateImageNodeHelper(SectionMappingNode, OverlayOutputFileFullPath)
                    OverlayImageNode.Type = 'Overlay_' + StosTransformNode.Type
                    DiffImageNode = CreateImageNodeHelper(SectionMappingNode, DiffOutputFileFullPath)
                    DiffImageNode.Type = 'Diff_' + StosTransformNode.Type
                    WarpedImageNode = CreateImageNodeHelper(SectionMappingNode, WarpedOutputFileFullPath)
                    WarpedImageNode.Type = 'Warped_' + StosTransformNode.Type

                    FilePrefix = str(SectionMappingNode.MappedSectionNumber) + '-' + StosTransformNode.ControlSectionNumber + '_'

                    stosImages = StosImageNodes(StosTransformNode, GroupNode.Downsample)

                    if stosImages.ControlImageNode is None or stosImages.MappedImageNode is None:
                        continue

                    # Compare the .stos file creation date to the output
                    if hasattr(OverlayImageNode, 'InputTransformChecksum'):
                        transforms.RemoveOnMismatch(OverlayImageNode, 'InputTransformChecksum', StosTransformNode.Checksum)
                    if hasattr(DiffImageNode, 'InputTransformChecksum'):
                        transforms.RemoveOnMismatch(DiffImageNode, 'InputTransformChecksum', StosTransformNode.Checksum)
                    if hasattr(WarpedImageNode, 'InputTransformChecksum'):
                        transforms.RemoveOnMismatch(WarpedImageNode, 'InputTransformChecksum', StosTransformNode.Checksum)

                    files.RemoveOutdatedFile(StosTransformNode.FullPath, OverlayImageNode.FullPath)
                    files.RemoveOutdatedFile(stosImages.ControlImageNode.FullPath, OverlayImageNode.FullPath)
                    files.RemoveOutdatedFile(stosImages.MappedImageNode.FullPath, OverlayImageNode.FullPath)

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

                    OverlayImageNode.InputTransformChecksum = StosTransformNode.Checksum
                    DiffImageNode.InputTransformChecksum = StosTransformNode.Checksum
                    WarpedImageNode.InputTransformChecksum = StosTransformNode.Checksum

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

def SelectBestRegistrationChain(Parameters, InputGroupNode, StosMapNode, Logger, **kwargs):
    '''Figure out which sections should be registered to each other'''

    # SectionMappingsNode
    Pool = pools.GetGlobalProcessPool()
    Pool.wait_completion()

    # Assess all of the images
    ComparisonImageType = kwargs.get('ComparisonImageType', 'Diff_Brute')
    ImageSearchXPathTemplate = "Image[@InputTransformChecksum='%(InputTransformChecksum)s']"

    InputStosMapName = kwargs.get('InputStosMapName', 'PotentialRegistrationChain')
    OutputStosMapName = kwargs.get('OutputStosMapName', 'FinalStosMap')

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

        knownControlSection = OutputStosMapNode.FindControlForMapped(mappedSection)
        if not knownControlSection is None:
            Logger.info(str(mappedSection) + " -> " + str(knownControlSection) + " was previously mapped, skipping")
            continue

        # Examine each stos image if it exists and determine the best match
        WinningTransform = None

        InputSectionMappingNode = InputGroupNode.GetSectionMapping(mappedSection)
        if InputSectionMappingNode is None:
            Logger.error(str(mappedSection) + " -> ? No SectionMapping data found")
            continue

        potentialControls = MappedToControlCandidateList[mappedSection]
        if len(potentialControls) == 0:
            # No need to test, copy over the transform
            Logger.error(str(mappedSection) + " -> ? No control section candidates found")
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

                    task = Pool.add_task(ImageNode.attrib['Path'], identifyCmd + " && exit", shell=True)
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

def __FindManualStosFile(StosGroupNode, InputTransformNode):
    '''Check the manual directory for the existence of a manually created file we should insert into this group.
       Returns the path to the file if it exists, otherwise None'''

    ManualInputDir = os.path.join(StosGroupNode.FullPath, 'Manual')
    if not os.path.exists(ManualInputDir):
        os.makedirs(ManualInputDir)

    # Copy the input stos or converted stos to the input directory
    ManualInputStosFullPath = os.path.join(ManualInputDir, InputTransformNode.Path)

    if os.path.exists(ManualInputStosFullPath):
        return ManualInputStosFullPath

    return None

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
    #We should not be trying to create output if we have no input
    assert(os.path.exists(InputTransformNode.FullPath) )

    files.RemoveOutdatedFile(InputTransformNode.FullPath, OutputTransformPath)
    if not os.path.exists(OutputTransformPath):
        StosGroupNode = InputTransformNode.FindParent('StosGroup')
        InputDownsample = StosGroupNode.Downsample
        InputStos = stosfile.StosFile.Load(InputTransformNode.FullPath)

        ControlImage = ControlFilter.GetOrCreateImage(OutputDownsample)
        MappedImage = MappedFilter.GetOrCreateImage(OutputDownsample)

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

    Logger = kwargs.get('Logger', logging.getLogger('StosGrid'))

    BlockNode = InputGroupNode.FindParent('Block')

    if(OutputStosGroup is None):
        OutputStosGroup = 'Grid'

    if(Type is None):
        Type = 'Grid'

    MappedSectionList = MappingNode.Mapped

    MappedSectionList.sort()

    SaveBlockNode = False
    SaveGroupNode = False

    for MappedSection in MappedSectionList:
        # Find the inputTransformNode in the InputGroupNode
        InputTransformNodes = InputGroupNode.TransformsForMapping(MappedSection, MappingNode.Control)
        if(InputTransformNodes is None or len(InputTransformNodes) == 0):
            Logger.warning("No transform found for mapping " + str(MappedSection) + " -> " + str(MappingNode.Control))
            continue

        for InputTransformNode in InputTransformNodes:
            OutputDownsample = Downsample

            InputSectionMappingNode = InputTransformNode.FindParent('SectionMappings')

            InputStosGroupNode = InputSectionMappingNode.FindParent('StosGroup')
            InputDownsample = int(InputStosGroupNode.Downsample)

            OutputStosGroupName = OutputStosGroup
            InputImageXPathTemplate = "Channel/Filter/Image[@Name='%(ImageName)s']/Level[@Downsample='%(OutputDownsample)d']"

            ControlNumber = InputTransformNode.ControlSectionNumber
            MappedNumber = InputSectionMappingNode.MappedSectionNumber

            ControlFilter = VolumeManagerHelpers.SearchCollection(BlockNode.GetSection(ControlNumber).GetChannel(InputTransformNode.ControlChannelName).Filters,
                                                                      'Name', ControlFilterPattern)[0]
            MappedFilter = VolumeManagerHelpers.SearchCollection(BlockNode.GetSection(MappedNumber).GetChannel(InputTransformNode.MappedChannelName).Filters,
                                                                      'Name', MappedFilterPattern)[0]

            # GetOrCreate the group for these stos files
            StosGroupNode = VolumeManagerETree.XContainerElementWrapper('StosGroup', OutputStosGroupName, OutputStosGroupName, {'Downsample' : str(OutputDownsample)})
            (added, StosGroupNode) = BlockNode.UpdateOrAddChildByAttrib(StosGroupNode)

            if added:
                SaveBlockNode = True

            if not os.path.exists(StosGroupNode.FullPath):
                os.makedirs(StosGroupNode.FullPath)

            OutputFile = __StosFilename(ControlFilter, MappedFilter)
            OutputStosFullPath = os.path.join(StosGroupNode.FullPath, OutputFile)
            stosNode = StosGroupNode.CreateStosTransformNode(ControlFilter, MappedFilter, OutputType="grid", OutputPath=OutputFile)

            ManualStosFileFullPath = __FindManualStosFile(StosGroupNode=StosGroupNode, InputTransformNode=stosNode)

            (InputStosFullPath, InputStosFileChecksum) = __GetInputStosFileForRegistration(StosGroupNode=StosGroupNode,
                                                                    InputTransformNode=InputTransformNode,
                                                                    ControlFilter=ControlFilter,
                                                                    MappedFilter=MappedFilter,
                                                                    OutputDownsample=OutputDownsample)
            
            if not os.path.exists(InputStosFullPath):
                #Hmm... no input.  This is worth reporting and moving on
                Logger.error("ir-stos-grid did not produce output for " + InputStosFullPath)
                InputGroupNode.remove(InputTransformNode)
                continue

            
            OutputSectionMappingNode = VolumeManagerETree.XElementWrapper('SectionMappings', InputSectionMappingNode.attrib)
            (added, OutputSectionMappingNode) = StosGroupNode.UpdateOrAddChildByAttrib(OutputSectionMappingNode, 'MappedSectionNumber')
            if added:
                SaveGroupNode = True
                
            #If the manual or automatic stos file is newer than the output, remove the output
            if files.RemoveOutdatedFile(InputTransformNode.FullPath, OutputStosFullPath):
                SaveGroupNode = True 
            
            # Remove our output if it was generated from an input transform with a different checksum
            if os.path.exists(OutputStosFullPath):
                stosNode = OutputSectionMappingNode.GetChildByAttrib('Transform', 'ControlSectionNumber', InputTransformNode.ControlSectionNumber)
                if not stosNode is None:
                    if 'InputTransformChecksum' in stosNode.attrib:
                        if(InputStosFileChecksum != stosNode.InputTransformChecksum):
                            os.remove(OutputStosFullPath)
                            SaveGroupNode = True
#                    else:
#                        os.remove(OutputStosFullPath)
#                        SaveGroupNode = True

            # Replace the automatic files if they are outdated.
            # GenerateStosFile(InputTransformNode, AutomaticInputStosFullPath, OutputDownsample, ControlFilter, MappedFilter)

    #        FixStosFilePaths(ControlFilter, MappedFilter, InputTransformNode, OutputDownsample, StosFilePath=InputStosFullPath)
            if not os.path.exists(OutputStosFullPath):

                ManualStosFileFullPath = __FindManualStosFile(StosGroupNode=StosGroupNode, InputTransformNode=stosNode)
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
                        continue
                    else:
                        SaveGroupNode = True
                else:
                    prettyoutput.Log("Copy manual override stos file to output: " + os.path.basename(ManualStosFileFullPath))
                    shutil.copy(ManualStosFileFullPath, OutputStosFullPath)

                stosNode.Path = OutputFile
            
            if os.path.exists(OutputStosFullPath):    
                stosNode.Checksum = stosfile.StosFile.LoadChecksum(stosNode.FullPath)
                stosNode.InputTransformChecksum = InputStosFileChecksum

    if SaveBlockNode:
        return BlockNode
    if SaveGroupNode:
        return StosGroupNode

    return None

def __AddRegistrationTreeNodeToStosMap(StosMapNode, rt, controlSectionNumber, mappedSectionNumber=None):
    '''recursively adds registration tree nodes to the stos map'''

    if mappedSectionNumber is None:
        mappedSectionNumber = controlSectionNumber

    rtNode = None
    if mappedSectionNumber in rt.Nodes:
        rtNode = rt.Nodes[mappedSectionNumber]
    else:
        return

    for mapped in rtNode.Children:
        StosMapNode.AddMapping(controlSectionNumber, mapped)

        if mapped in rt.Nodes:
            __AddRegistrationTreeNodeToStosMap(StosMapNode, rt, controlSectionNumber, mapped)


def __RegistrationTreeToStosMap(rt, StosMapName):

    OutputStosMap = VolumeManagerETree.StosMapNode(StosMapName)

    for sectionNumber in rt.RootNodes:
        rootNode = rt.RootNodes[sectionNumber]
        __AddRegistrationTreeNodeToStosMap(OutputStosMap, rt, rootNode.SectionNumber)

    return OutputStosMap
 

def SliceToVolumeFromRegistrationTreeNode(rt, Node, InputGroupNode, OutputGroupNode, ControlToVolumeTransform=None):
    ControlSection = Node.SectionNumber

    Logger = logging.getLogger('SliceToVolume')

#    ControlToVolumeTransform = None:
#    if VolumeOriginSectionNumber is None or VolumeOriginSectionNumber != ControlSection:
#        ControlToVolume = FindTransformForMapping(OutputGroupNode, VolumeOriginSectionNumber, ControlSection)
#        if ControlToVolume is None:
#            Logger.error("No transform found to origin of volume: " + str(ControlSection) + ' -> ' + str(VolumeOriginSectionNumber))
#            return

    for mappedSectionNumber in Node.Children:
        mappedNode = rt.Nodes[mappedSectionNumber]

        print str(ControlSection) + " <- " + str(mappedSectionNumber)

        OutputSectionMappingsNode = OutputGroupNode.GetOrCreateSectionMapping(mappedSectionNumber)

        MappedToControlTransforms = InputGroupNode.TransformsForMapping(mappedSectionNumber, ControlSection)

        if MappedToControlTransforms is None or len(MappedToControlTransforms) == 0:
            Logger.error("No transform found: " + str(ControlSection) + ' -> ' + str(mappedSectionNumber))
            continue

        for MappedToControlTransform in MappedToControlTransforms:

            OutputTransform = copy.deepcopy(MappedToControlTransform)

            (OutputTransformAdded, OutputTransform) = OutputSectionMappingsNode.UpdateOrAddChildByAttrib(OutputTransform, 'MappedSectionNumber')
            OutputTransform.Path = str(mappedSectionNumber) + '-' + str(ControlSection) + '.stos'

            if not ControlToVolumeTransform is None:
                OutputTransform.Path = str(mappedSectionNumber) + '-' + str(ControlToVolumeTransform.ControlSectionNumber) + '.stos'

            if not hasattr(OutputTransform, 'InputTransformChecksum'):
                if os.path.exists(OutputTransform.FullPath):
                    os.remove(OutputTransform.FullPath)
            else:
                if not MappedToControlTransform.Checksum == OutputTransform.InputTransformChecksum:
                    if os.path.exists(OutputTransform.FullPath):
                        os.remove(OutputTransform.FullPath)


            if ControlToVolumeTransform is None:
                # This maps directly to the origin, add it to the output stos group
                # Files.RemoveOutdatedFile(MappedToControlTransform.FullPath, OutputTransform.FullPath )

                if not os.path.exists(OutputTransform.FullPath):
                    shutil.copy(MappedToControlTransform.FullPath, OutputTransform.FullPath)
                    OutputTransform.Checksum = MappedToControlTransform.Checksum
                    OutputTransform.InputTransformChecksum = MappedToControlTransform.Checksum

            else:
                OutputTransform.ControlSectionNumber = ControlToVolumeTransform.ControlSectionNumber
                OutputTransform.ControlChannelName = ControlToVolumeTransform.ControlChannelName
                OutputTransform.ControlFilterName = ControlToVolumeTransform.ControlFilterName

                print  OutputTransform.Path

                if hasattr(OutputTransform, "ControlToVolumeTransformChecksum"):
                    if not OutputTransform.ControlToVolumeTransformChecksum == ControlToVolumeTransform.Checksum:
                        if os.path.exists(OutputTransform.FullPath):
                            os.remove(OutputTransform.FullPath)
                elif os.path.exists(OutputTransform.FullPath):
                            os.remove(OutputTransform.FullPath)

                # Files.RemoveOutdatedFile(MappedToControlTransform.FullPath, OutputTransform.FullPath )
                # Files.RemoveOutdatedFile(ControlToVolumeTransform.FullPath, OutputTransform.FullPath )

                if not os.path.exists(OutputTransform.FullPath):
                    MToVStos = stosfile.AddStosTransforms(MappedToControlTransform.FullPath, ControlToVolumeTransform.FullPath)
                    MToVStos.Save(OutputTransform.FullPath)


                    OutputTransform.ControlToVolumeTransformChecksum = ControlToVolumeTransform.Checksum
                    OutputTransform.InputTransformChecksum = MappedToControlTransform.Checksum

            SliceToVolumeFromRegistrationTreeNode(rt, mappedNode, InputGroupNode, OutputGroupNode, ControlToVolumeTransform=OutputTransform)

    return OutputGroupNode


def RegistrationTreeFromStosMapNode(StosMapNode):
    rt = registrationtree.RegistrationTree()

    for mappingNode in StosMapNode.findall('Mapping'):
        for mappedSection in mappingNode.Mapped:
            rt.AddPair(mappingNode.Control, mappedSection)

    return rt


def __MappedFiltersForTransform(InputTransformNode, channelPattern=None, filterPattern=None):

    if(filterPattern is None):
        filterPattern = InputTransformNode.MappedFilterName

    if(channelPattern is None):
        channelPattern = InputTransformNode.MappedChannelName

    sectionNumber = InputTransformNode.MappedSectionNumber
    BlockNode = InputTransformNode.FindParent(ParentTag='Block')
    sectionNode = BlockNode.GetSection(sectionNumber)
    return sectionNode.MatchChannelFilterPattern(channelPattern, filterPattern)


def __ControlFiltersForTransform(InputTransformNode, channelPattern=None, filterPattern=None):

    if(filterPattern is None):
        filterPattern = InputTransformNode.ControlFilterName

    if(channelPattern is None):
        channelPattern = InputTransformNode.ControlChannelName

    sectionNumber = InputTransformNode.ControlSectionNumber
    BlockNode = InputTransformNode.FindParent(ParentTag='Block')
    sectionNode = BlockNode.GetSection(sectionNumber)
    return sectionNode.MatchChannelFilterPattern(channelPattern, filterPattern)


def ScaleStosGroup(InputStosGroupNode, OutputDownsample, OutputGroupName, **kwargs):

    '''Take a stos group node, scale the transforms, and save in new stosgroup'''

    ControlChannelPattern = kwargs.get("ControlChannelPattern", None)
    ControlFilterPattern = kwargs.get("ControlFilterPattern", None)
    MappedChannelPattern = kwargs.get("MappedChannelPattern", None)
    MappedFilterPattern = kwargs.get("MappedFilterPattern", None)

    GroupParent = InputStosGroupNode.Parent

    OutputGroupNode = VolumeManagerETree.StosGroupNode(OutputGroupName, OutputDownsample)
    (SaveBlockNode, OutputGroupNode) = GroupParent.UpdateOrAddChildByAttrib(OutputGroupNode)

    if not os.path.exists(OutputGroupNode.FullPath):
            os.makedirs(OutputGroupNode.FullPath)

    for inputSectionMapping in InputStosGroupNode.SectionMappings:

        OutputSectionMapping = OutputGroupNode.GetOrCreateSectionMapping(inputSectionMapping.MappedSectionNumber)

        InputTransformNodes = inputSectionMapping.findall('Transform')

        for InputTransformNode in InputTransformNodes:

            ControlFilters = __ControlFiltersForTransform(InputTransformNode, ControlChannelPattern, ControlFilterPattern)
            MappedFilters = __MappedFiltersForTransform(InputTransformNode, MappedChannelPattern, MappedFilterPattern)

            for (ControlFilter, MappedFilter) in itertools.product(ControlFilters, MappedFilters):

                stosNode = OutputGroupNode.CreateStosTransformNode(ControlFilter,
                                                                 MappedFilter,
                                                                 OutputType=InputTransformNode.Type,
                                                                 OutputPath=__StosFilename(ControlFilter, MappedFilter))

                __RemoveStosFileIfOutdated(stosNode, InputTransformNode)

                if not os.path.exists(stosNode.FullPath):
                    stosGenerated = __GenerateStosFile(InputTransformNode,
                                                                    stosNode.FullPath,
                                                                    OutputDownsample,
                                                                    ControlFilter,
                                                                    MappedFilter)

                    stosNode.InputTransformChecksum = InputTransformNode.Checksum

                SaveBlockNode = SaveBlockNode or stosGenerated

    if SaveBlockNode:
        return GroupParent
    return None


def __RemoveStosFileIfOutdated(OutputStosNode, InputStosNode):
    '''Removes the .stos file from the file system but leaves the meta data alone for reuse.
       Always removes the file if the meta-data does not have an InputTransformChecksum property'''

    if hasattr(OutputStosNode, "InputTransformChecksum"):
        if transforms.IsValueMatched(OutputNode=OutputStosNode,
                                    OutputAttribute="InputTransformChecksum",
                                    TargetValue=InputStosNode.Checksum):
            if os.path.exists(OutputStosNode.FullPath):
                os.remove(OutputStosNode.FullPath)
                return True

    elif os.path.exists(OutputStosNode.FullPath):
        os.remove(OutputStosNode.FullPath)
        return True

    return False


def BuildSliceToVolumeTransforms(StosMapNode, StosGroupNode, OutputMap, OutputGroup, **kwargs):
    '''Build a slice-to-volume transform for each section referenced in the StosMap'''

    BlockNode = StosGroupNode.Parent
    InputStosGroupNode = StosGroupNode
    rt = registrationtree.RegistrationTree()

    for mappingNode in StosMapNode.Mappings:
        for mappedSection in mappingNode.Mapped:
            rt.AddPair(mappingNode.Control, mappedSection)

    if len(rt.RootNodes) == 0:
        return

    OutputGroupNode = VolumeManagerETree.StosGroupNode(OutputGroup, InputStosGroupNode.Downsample)
    (SaveBlockNode, OutputGroupNode) = BlockNode.UpdateOrAddChildByAttrib(OutputGroupNode)

    # build the stos map again if it exists
    OldStosMap = BlockNode.GetChildByAttrib('StosMap', 'Name', OutputMap)
    if not OldStosMap is None:
        BlockNode.remove(OldStosMap)

    OutputStosMap = __RegistrationTreeToStosMap(rt, OutputMap)
    (added, OutputStosMap) = BlockNode.UpdateOrAddChildByAttrib(OutputStosMap)
    OutputStosMap.CenterSection = StosMapNode.CenterSection

    SaveBlockNode = SaveBlockNode or added

    for sectionNumber in rt.RootNodes:
        Node = rt.Nodes[sectionNumber]
        SliceToVolumeFromRegistrationTreeNode(rt, Node, InputGroupNode=InputStosGroupNode, OutputGroupNode=OutputGroupNode, ControlToVolumeTransform=None)

    if SaveBlockNode:
        return BlockNode
    else:
        return OutputGroupNode


def BuildMosaicToVolumeTransforms(StosMapNode, StosGroupNode, TransformNode, OutputTransformName, Logger, **kwargs):
    '''Build a slice-to-volume transform for each section referenced in the StosMap'''

    MosaicTransformParent = TransformNode.Parent

    SectionNode = TransformNode.FindParent('Section')
    if SectionNode is None:
        Logger.error("No section found for transform: " + str(TransformNode))
        return

    # Create transform node for the output
    OutputTransformNode = VolumeManagerETree.TransformNode(Name=OutputTransformName, Type="MosaicToVolume", Path=OutputTransformName + '.mosaic')
    (added, OutputTransformNode) = MosaicTransformParent.UpdateOrAddChildByAttrib(OutputTransformNode)

    OutputTransformNode.InputTransformChecksum = TransformNode.Checksum
    OutputTransformNode.OutputTransformName = OutputTransformName

    files.RemoveOutdatedFile(TransformNode.FullPath, OutputTransformNode.FullPath)

    MappedSectionNumber = SectionNode.Number
    ControlSectionNumber = StosMapNode.FindControlForMapped(MappedSectionNumber)
    if ControlSectionNumber is None:

        # Create transform node for the output
        shutil.copyfile(TransformNode.FullPath, OutputTransformNode.FullPath)

        if StosMapNode.CenterSection == MappedSectionNumber:
            OutputTransformNode.Checksum = TransformNode.Checksum

        else:
            # Probably we are the center of the volume, so create a node for our mosaicToVolume transform
            Logger.info("No SectionMappings found for section: " + str(MappedSectionNumber))

        return MosaicTransformParent

    SliceToVolumeTransform = FindTransformForMapping(StosGroupNode, ControlSectionNumber, MappedSectionNumber)
    if SliceToVolumeTransform is None:
        Logger.error("No SliceToVolumeTransform found for: " + str(MappedSectionNumber) + " -> " + ControlSectionNumber.Control)
        return

    files.RemoveOutdatedFile(SliceToVolumeTransform.FullPath, OutputTransformNode.FullPath)

    if os.path.exists(OutputTransformNode.FullPath):
        return

    SToV = stosfile.StosFile.Load(SliceToVolumeTransform.FullPath)
    # Make sure we are not using a downsampled transform
    SToV = SToV.ChangeStosGridPixelSpacing(StosGroupNode.Downsample, 1.0)
    StoVTransform = factory.LoadTransform(SToV.Transform)

    mosaic = mosaicfile.MosaicFile.Load(TransformNode.FullPath)

    Pool = pools.GetGlobalThreadPool()

    Tasks = []

    ControlImageBounds = SToV.ControlImageDim

    for imagename, transform in mosaic.ImageToTransformString.iteritems():
        MosaicToSectionTransform = factory.LoadTransform(transform)

        task = Pool.add_task(imagename, StoVTransform.AddTransform, MosaicToSectionTransform)
        task.imagename = imagename
        task.dimX = MosaicToSectionTransform.gridWidth
        task.dimY = MosaicToSectionTransform.gridHeight
        Tasks.append(task)

    # Find the bounding box of the original transform in case we want to crop the output from ir-assemble to produce images of identical size
    minX = float('Inf')
    minY = float('Inf')
    maxX = -float('Inf')
    maxY = -float('Inf')

    for task in Tasks:
        MosaicToVolume = task.wait_return()
        bbox = MosaicToVolume.ControlPointBoundingBox

        minX = min(minX, bbox[0])
        minY = min(minY, bbox[1])
        maxX = max(maxX, bbox[2])
        maxY = max(maxY, bbox[3])

        mosaic.ImageToTransformString[task.imagename] = factory.TransformToIRToolsGridString(MosaicToVolume, task.dimX, task.dimY)

    CropBoxString = ','.join(str(x) for x in (minX, minY, ControlImageBounds[2], ControlImageBounds[3]))
    OutputTransformNode.CropBox = CropBoxString

    mosaic.Save(OutputTransformNode.FullPath)

    return MosaicTransformParent

if __name__ == '__main__':
    pass
