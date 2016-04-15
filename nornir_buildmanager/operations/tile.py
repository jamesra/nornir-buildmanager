'''
Created on May 22, 2012

@author: Jamesan
'''

import math
import subprocess
import xml.dom
import logging
import glob
import os
import shutil
import copy
import nornir_buildmanager as nb 


from nornir_buildmanager.validation import transforms, image
import nornir_imageregistration.core as core
import nornir_imageregistration.image_stats as image_stats
import nornir_imageregistration.tileset as tiles
from nornir_imageregistration.files import mosaicfile
from nornir_imageregistration.mosaic import Mosaic
from nornir_imageregistration.transforms import *
import nornir_imageregistration.spatial as spatial
from nornir_shared import *
from nornir_shared.files import RemoveOutdatedFile, OutdatedFile
from nornir_shared.histogram import Histogram
from nornir_shared.misc import SortedListFromDelimited
import nornir_shared.plot
import nornir_buildmanager.templates

import nornir_pools as Pools

from nornir_imageregistration.tileset import ShadeCorrectionTypes
from nornir_buildmanager.exceptions import NornirUserException
import nornir_imageregistration

HistogramTagStr = "HistogramData"

ContrastMinCutoffDefault = 0.1
ContrastMaxCutoffDefault = 0.5

DefaultImageExtension = '.png'

def _ShrinkNumpyImageFile(Pool, InFile, OutFile, ShrinkFactor):
    image = nornir_imageregistration.core.LoadImage(InFile)
    resized_image = nornir_imageregistration.core.ResizeImage(image, ShrinkFactor)
    nornir_imageregistration.core.SaveImage(OutFile, resized_image)

# Shrinks the passed image file, return procedure handle of invoked command
def Shrink(Pool, InFile, OutFile, ShrinkFactor):
    (root, ext) = os.path.splitext(InFile)
    if ext == '.npy':
        image = nornir_imageregistration.core.LoadImage(InFile)
        resized_image = nornir_imageregistration.core.ResizeImage(image, 1 / float(ShrinkFactor))
        nornir_imageregistration.core.SaveImage(OutFile, resized_image)
        return Pool.add_task(_ShrinkNumpyImageFile, InFile, OutFile, ShrinkFactor)
    else:
        Percentage = (1 / float(ShrinkFactor)) * 100.0
        cmd = "Convert " + InFile + " -scale \"" + str(Percentage) + "%\" -quality 106  -colorspace gray " + OutFile
        # prettyoutput.CurseString('Cmd', cmd)
        # NewP = subprocess.Popen(cmd + " && exit", shell=True)
        return Pool.add_process('Shrink: ' + InFile, cmd)


def VerifyImages(TilePyramidNode, **kwargs):
    '''Eliminate any image files which cannot be parsed by Image Magick's identify command'''
    logger = kwargs.get('Logger', None)

    if(logger is None):
        logger.error('VerifyImages. InputPyramidNode not found')
        return None

    PyramidLevels = nornir_shared.misc.SortedListFromDelimited(kwargs.get('Levels', [1, 2, 4, 8, 16, 32, 64, 128, 256]))

    Levels = TilePyramidNode.Levels
    LNodeSaveList = []
    for LNode in Levels:
        Downsample = int(LNode.attrib.get('Downsample', None))
        if not Downsample in PyramidLevels:
            continue

        LNode = VerifyTiles(LevelNode=LNode)
        if not LNode is None:
            LNodeSaveList.append(LNode)

    # Save the channelNode if a level node was changed
    if len(LNodeSaveList) > 0:
        return TilePyramidNode

    return None


def VerifyTiles(LevelNode=None, **kwargs):
    ''' @LevelNode
    Eliminate any image files which cannot be parsed by Image Magick's identify command
    '''
    logger = logging.getLogger(__name__ + '.VerifyTiles')

    InputLevelNode = LevelNode
    TilesValidated = int(InputLevelNode.attrib.get('TilesValidated', 0))
    InputPyramidNode = InputLevelNode.FindParent('TilePyramid')
    TileExt = InputPyramidNode.attrib.get('ImageFormatExt', '.png')

    TileImageDir = InputLevelNode.FullPath
    LevelFiles = glob.glob(os.path.join(TileImageDir, '*' + TileExt))

    if(len(LevelFiles) == 0):
        logger.info('No tiles found in level')
        return None

    if TilesValidated == len(LevelFiles):
        logger.info('Tiles already validated')
        return None

    InvalidTiles = nornir_shared.images.IsValidImage(LevelFiles, TileImageDir)
    for InvalidTile in InvalidTiles:
        InvalidTilePath = os.path.join(TileImageDir, InvalidTile)
        if os.path.exists(InvalidTilePath):
            prettyoutput.LogErr('*** Deleting invalid tile: ' + InvalidTilePath)
            logger.warning('*** Deleting invalid tile: ' + InvalidTilePath)
            os.remove(InvalidTilePath)

    if len(InvalidTiles) == 0:
        logger.info('Tiles all valid')

    InputLevelNode.TilesValidated = len(LevelFiles) - len(InvalidTiles)

    return InputLevelNode


def FilterIsPopulated(InputFilterNode, Downsample, MosaicFullPath, OutputFilterName):
    '''
    :return: True if the filter has all of the tiles the mosaic file indicates it should have at the provided downsample level
    ''' 
    ChannelNode = InputFilterNode.Parent
    InputPyramidNode = InputFilterNode.find('TilePyramid')
    InputLevelNode = InputPyramidNode.GetLevel(Downsample)
    OutputFilterNode = ChannelNode.GetFilter(OutputFilterName)
    if OutputFilterNode is None:
        return False

    OutputPyramidNode = OutputFilterNode.find('TilePyramid')
    if OutputPyramidNode is None:
        return False

    mFile = mosaicfile.MosaicFile.Load(MosaicFullPath)
    if OutputPyramidNode.NumberOfTiles < mFile.NumberOfImages:
        return False

    OutputLevelNode = OutputFilterNode.TilePyramid.GetLevel(Downsample)
    if OutputLevelNode is None:
        return False

    if not os.path.exists(OutputLevelNode.FullPath):
        return False

    # Find out if the number of predicted images matches the number of actual images
    ImageFiles = glob.glob(OutputLevelNode.FullPath + os.sep + '*' + InputPyramidNode.ImageFormatExt)
    basenameImageFiles = map(os.path.basename, ImageFiles)

    for i in mFile.ImageToTransformString:
        if not i in basenameImageFiles:
            # Don't return false unless the input exists
            if os.path.exists(os.path.join(InputLevelNode.FullPath, i)):
                return False

#    FileCountEqual = len(ImageFiles) == OutputPyramidNode.NumberOfTiles
#    return   FileCountEqual
    return True

# 
# def EvaluateImageList(ImageList, CmdTemplate):
#     # FileList = list()
#     OutputNumber = 0
#     TempFileList = []
# 
#     CmdLineFileList = ""
#     while len(TempFileList) == 0 and CmdLineFileList == "":
# 
#         # First we process all of the original tiles.  We can't run them all because Windows has command line length limits.  I haven't tried passing the arguments as an array though...
#         if(len(ImageList) > 0):
#             while len(ImageList) > 0:
#                 # basefilename = os.path.basename(tilefullpath)
#             #    FileList.append(basefilename)
#             #    CmdList.append(basefilename)
#                 if(len(CmdLineFileList) + len(ImageList[0]) < 900):
#                     TileFileName = ImageList.pop()
#                     CmdLineFileList = CmdLineFileList + ' ' + os.path.basename(str(TileFileName))
#                 else:
#                     break
# 
#         # This only runs after we've processed all of the original tiles
#         elif(len(TempFileList) > 1):
#             while len(TempFileList) > 1:
#                 if(len(CmdLineFileList) + len(TempFileList[0]) < 900):
#                     CmdLineFileList = CmdLineFileList + ' ' + str(TempFileList[0])
#                     del TempFileList[0]
#                 else:
#                     break
# 
#         TempFinalTarget = os.path.join(Path, 'Temp' + str(OutputNumber) + '.png')
#         OutputNumber = OutputNumber + 1
#         ImageList.append(TempFinalTarget)
#         TempFileList.append(TempFinalTarget)
# 
#         # CmdList.append(FileList)
# #        CmdList.append(PreEvaluateSequenceArg)
# #        CmdList.append("-evaluate-sequence")
# #        CmdList.append(EvaluateSequenceArg)
# #        CmdList.append(PostEvaluateSequenceArg)
# #        CmdList.append(FinalTarget)
# 
#         Cmd = CmdBase % {'Images' : CmdLineFileList,
#                           'PreEvaluateSequenceArg' : PreEvaluateSequenceArg,
#                           'EvaluateSequenceArg' :  EvaluateSequenceArg,
#                           'OutputFile' : TempFinalTarget}
# 
#         prettyoutput.Log(Cmd)
#         subprocess.call(Cmd + " && exit", shell=True, cwd=TileDir)
#         CmdLineFileList = ""


def Evaluate(Parameters, FilterNode, OutputImageName=None, Level=1, PreEvaluateSequenceArg=None, EvaluateSequenceArg=None, PostEvaluateSequenceArg=None, **kwargs):
    PyramidNode = FilterNode.find('TilePyramid')
    assert(not PyramidNode is None)
    levelNode = PyramidNode.GetChildByAttrib('Level', 'Downsample', Level)
    assert(not levelNode is None)

    if PreEvaluateSequenceArg is None:
        PreEvaluateSequenceArg = ''

    if EvaluateSequenceArg is None:
        EvaluateSequenceArg = ''

    if OutputImageName is None:
        OutputImageName = EvaluateSequenceArg

    assert(not OutputImageName is None)

    FinalTargetPath = OutputImageName + PyramidNode.ImageFormatExt
    PreFinalTargetPath = 'Pre-' + OutputImageName + PyramidNode.ImageFormatExt

    PreFinalTargetFullPath = os.path.join(FilterNode.FullPath, PreFinalTargetPath)

    OutputImageNode = FilterNode.GetChildByAttrib('Image', 'Name', OutputImageName)
    if not OutputImageNode is None:
        if OutputImageNode.CleanIfInvalid():
            OutputImageNode = None

    # Find out if the output image node exists already
    OutputImageNode = nb.VolumeManager.ImageNode(Path=FinalTargetPath, attrib={'Name' : OutputImageName})
    (ImageNodeCreated, OutputImageNode) = FilterNode.UpdateOrAddChildByAttrib(OutputImageNode, 'Name')

    prettyoutput.CurseString('Stage', FilterNode.Name + " ImageMagick -Evaluate-Sequence " + EvaluateSequenceArg)

    CmdTemplate = "convert %(Images)s %(PreEvaluateSequenceArg)s -evaluate-sequence %(EvaluateSequenceArg)s %(OutputFile)s"

    TileFullPath = os.path.join(levelNode.FullPath, '*' + PyramidNode.ImageFormatExt)


    Cmd = CmdTemplate % {'Images' : TileFullPath,
                             'PreEvaluateSequenceArg' : PreEvaluateSequenceArg,
                             'EvaluateSequenceArg' :  EvaluateSequenceArg,
                             'OutputFile' : PreFinalTargetFullPath}

    prettyoutput.Log(Cmd)
    subprocess.call(Cmd + " && exit", shell=True)

    if not PostEvaluateSequenceArg is None:
        PostCmd = 'convert ' + PreFinalTargetFullPath + ' ' + PostEvaluateSequenceArg + ' ' + OutputImageNode.FullPath
        prettyoutput.Log(Cmd)
        proc = subprocess.call(PostCmd + " && exit", shell=True)
        os.remove(PreFinalTargetFullPath)
    else:
        shutil.move(PreFinalTargetFullPath, OutputImageNode.FullPath)

    if ImageNodeCreated:
        return FilterNode

    return None


def _CreateMinCorrectionImage(ImageNode, OutputImageName, **kwargs):
    '''Creates an image from the source image whose min pixel value is zero'''

    ParentNode = ImageNode.Parent
    OutputFile = OutputImageName + ".png"

    # Find out if the output image node exists already
    OutputImageNode = nb.VolumeManager.ImageNode(Path=OutputFile, attrib={'Name' : OutputImageName})
    (ImageNodeCreated, OutputImageNode) = ParentNode.UpdateOrAddChildByAttrib(OutputImageNode, 'Name')

    nornir_shared.files.RemoveOutdatedFile(ImageNode.FullPath, OutputImageNode.FullPath)

    if os.path.exists(OutputImageNode.FullPath):
        return OutputImageNode

    [Min, Mean, Max, StdDev] = nornir_shared.images.GetImageStats(ImageNode.FullPath)

    # Temp file with a uniform value set to the minimum pixel value of ImageNode
    OutputFileUniformFullPath = os.path.join(ParentNode.FullPath, 'UniformMinBackground_' + OutputFile)
    CreateBackgroundCmdTemplate = 'convert %(OperatorImage)s  +matte -background "gray(%(BackgroundIntensity)f)" -compose Dst -flatten %(OutputFile)s'
    CreateBackgroundCmd = CreateBackgroundCmdTemplate % {'OperatorImage': ImageNode.FullPath,
                                                         'BackgroundIntensity': float(Min / 256.0),  # TODO This only works for 8-bit
                                                         'OutputFile' : OutputFileUniformFullPath}
    prettyoutput.Log(CreateBackgroundCmd)
    subprocess.call(CreateBackgroundCmd + " && exit", shell=True)

    # Create the zerod image
    CmdBase = "convert %(OperatorImage)s %(InputFile)s %(InvertOperator)s -compose %(ComposeOperator)s -composite %(OutputFile)s"
    CreateZeroedImageCmd = CmdBase % {'OperatorImage' : OutputFileUniformFullPath,
                                      'InputFile' :  ImageNode.FullPath,
                                      'InvertOperator' : '',
                                      'ComposeOperator' : 'minus_Dst',
                                      'OutputFile' : OutputImageNode.FullPath}

    prettyoutput.Log(CreateZeroedImageCmd)
    subprocess.call(CreateZeroedImageCmd + " && exit", shell=True)

    return OutputImageNode


def CorrectTiles(Parameters, CorrectionType, FilterNode=None, OutputFilterName=None, **kwargs):
    '''Create a corrected version of a filter by applying the operation/image to all tiles'''

    correctionType = None
    if CorrectionType.lower() == 'brightfield':
        correctionType = tiles.ShadeCorrectionTypes.BRIGHTFIELD
    elif CorrectionType.lower() == 'darkfield':
        correctionType = tiles.ShadeCorrectionTypes.DARKFIELD

    assert(not FilterNode is None)
    InputPyramidNode = FilterNode.find('TilePyramid')
    assert(not InputPyramidNode is None)

    InputLevelNode = InputPyramidNode.MaxResLevel
    assert(not InputLevelNode is None)


    FilterParent = FilterNode.Parent

    SaveFilterParent = False

    # Find out if the output filter already exists
    [SaveFilterParent, OutputFilterNode] = FilterParent.UpdateOrAddChildByAttrib(nb.VolumeManager.FilterNode(OutputFilterName, OutputFilterName))
    OutputFilterNode.BitsPerPixel = FilterNode.BitsPerPixel

    # Check if the output node exists
    OutputPyramidNode = nb.VolumeManager.TilePyramidNode(Type=InputPyramidNode.Type,
                                                           NumberOfTiles=InputPyramidNode.NumberOfTiles,
                                                           LevelFormat=InputPyramidNode.LevelFormat,
                                                           ImageFormatExt=InputPyramidNode.ImageFormatExt)

    [added, OutputPyramidNode] = OutputFilterNode.UpdateOrAddChildByAttrib(OutputPyramidNode, 'Path')

    OutputLevelNode = nb.VolumeManager.LevelNode(Level=InputLevelNode.Downsample)
    [OutputLevelAdded, OutputLevelNode] = OutputPyramidNode.UpdateOrAddChildByAttrib(OutputLevelNode, 'Downsample')

    # Make sure the destination directory exists
    correctionImage = None
    if not os.path.exists(OutputLevelNode.FullPath):
        os.makedirs(OutputLevelNode.FullPath)

    OutputImageNode = nb.VolumeManager.ImageNode(Path='Correction.png', attrib={'Name' : 'ShadeCorrection'})
    (ImageNodeCreated, OutputImageNode) = FilterNode.UpdateOrAddChildByAttrib(OutputImageNode, 'Name')

    InputTiles = glob.glob(os.path.join(InputLevelNode.FullPath, '*' + InputPyramidNode.ImageFormatExt))

    if not os.path.exists(OutputImageNode.FullPath):
        correctionImage = tiles.CalculateShadeImage(InputTiles, type=correctionType)
        core.SaveImage(OutputImageNode.FullPath, correctionImage)
    else:
        correctionImage = core.LoadImage(OutputImageNode.FullPath)

    tiles.ShadeCorrect(InputTiles, correctionImage, OutputLevelNode.FullPath, type=correctionType)
    if SaveFilterParent:
        return FilterParent

    return FilterNode


def _CorrectTilesDeprecated(Parameters, FilterNode=None, ImageNode=None, OutputFilterName=None, InvertSource=False, ComposeOperator=None, **kwargs):
    '''Create a corrected version of a filter by applying the operation/image to all tiles'''

    assert(not FilterNode is None)
    InputPyramidNode = FilterNode.find('TilePyramid')
    assert(not InputPyramidNode is None)

    InputLevelNode = InputPyramidNode.MaxResLevel
    assert(not InputLevelNode is None)

    assert(not ImageNode is None)

    if ComposeOperator is None:
        ComposeOperator = 'minus'

    InvertOperator = ''
    if(not InvertSource is None):
        InvertOperator = '-negate'

    FilterParent = FilterNode.Parent

    SaveFilterParent = False

    # Find out if the output filter already exists
    [SaveFilterParent, OutputFilterNode] = FilterParent.UpdateOrAddChildByAttrib(nb.VolumeManager.FilterNode(OutputFilterName, OutputFilterName))
    OutputFilterNode.BitsPerPixel = FilterNode.BitsPerPixel

    # Check if the output node exists
    OutputPyramidNode = nb.VolumeManager.TilePyramidNode(Type=InputPyramidNode.Type,
                                                           NumberOfTiles=InputPyramidNode.NumberOfTiles,
                                                           LevelFormat=InputPyramidNode.LevelFormat,
                                                           ImageFormatExt=InputPyramidNode.ImageFormatExt)

    [added, OutputPyramidNode] = OutputFilterNode.UpdateOrAddChildByAttrib(OutputPyramidNode, 'Path')

    OutputLevelNode = nb.VolumeManager.LevelNode(Level=InputLevelNode.Downsample)
    [OutputLevelAdded, OutputLevelNode] = OutputPyramidNode.UpdateOrAddChildByAttrib(OutputLevelNode, 'Downsample')

    # Make sure the destination directory exists
    if not os.path.exists(OutputLevelNode.FullPath):
        os.makedirs(OutputLevelNode.FullPath)

    CmdTemplate = "convert %(OperatorImage)s %(InputFile)s %(InvertOperator)s -compose %(ComposeOperator)s -composite %(OutputFile)s"

    InputTiles = glob.glob(os.path.join(InputLevelNode.FullPath, '*' + InputPyramidNode.ImageFormatExt))

    ZeroedImageNode = _CreateMinCorrectionImage(ImageNode, 'Zeroed' + ImageNode.Name)

    Pool = Pools.GetGlobalClusterPool()

    for InputTileFullPath in InputTiles:
        inputTile = os.path.basename(InputTileFullPath)
        OutputTileFullPath = os.path.join(OutputLevelNode.FullPath, inputTile)

        RemoveOutdatedFile(InputTileFullPath, OutputTileFullPath)

        if os.path.exists(OutputTileFullPath):
            continue

        Cmd = CmdTemplate % {'OperatorImage' : ZeroedImageNode.FullPath,
                             'InputFile' :  InputTileFullPath,
                             'InvertOperator' : InvertOperator,
                             'ComposeOperator' : ComposeOperator,
                             'OutputFile' : OutputTileFullPath}
        prettyoutput.Log(Cmd)
        Pool.add_process(inputTile, Cmd + " && exit", shell=True)

    Pool.wait_completion()

    if SaveFilterParent:
        return FilterParent

    return FilterNode



def TranslateToZeroOrigin(ChannelNode, TransformNode, OutputTransform, Logger, **kwargs):
    ''' @ChannelNode  '''

    outputFilename = OutputTransform + ".mosaic"
    outputFileFullPath = os.path.join(os.path.dirname(TransformNode.FullPath), outputFilename)

    OutputTransformNode = TransformNode.Parent.GetChildByAttrib('Transform', 'Path', outputFilename)
    if not OutputTransformNode.CleanIfInputTransformMismatched(TransformNode):
        return None

    if os.path.exists(outputFileFullPath) and (not OutputTransformNode is None):
        return None

    prettyoutput.Log("Moving origin to 0,0 - " + TransformNode.FullPath)

    mosaic = mosaicfile.MosaicFile.Load(TransformNode.FullPath)

    # Find the min,max values from all of the transforms
    minX = float('Inf')
    minY = float('Inf')
    maxX = -float('Inf')
    maxY = -float('Inf')

    Transforms = {}
    for imagename, transform in mosaic.ImageToTransformString.iteritems():
        MosaicToSectionTransform = factory.LoadTransform(transform)
        Transforms[imagename] = MosaicToSectionTransform
        bbox = MosaicToSectionTransform.FixedBoundingBox.ToArray()

        minX = min(minX, bbox[spatial.iRect.MinX])
        minY = min(minY, bbox[spatial.iRect.MinY])
        maxX = max(maxX, bbox[spatial.iRect.MaxX])
        maxY = max(maxY, bbox[spatial.iRect.MaxY])

    if OutputTransformNode is None:
        OutputTransformNode = copy.deepcopy(TransformNode)
        OutputTransformNode.Path = outputFilename
        OutputTransformNode.Name = OutputTransform
        OutputTransformNode.InputTransform = TransformNode.Name
        [SaveRequired, OutputTransformNode] = TransformNode.Parent.UpdateOrAddChildByAttrib(OutputTransformNode, 'Path')
    else:
        OutputTransformNode.Path = outputFilename
        OutputTransformNode.Name = OutputTransform
        OutputTransformNode.InputTransform = TransformNode.Name

    OutputTransformNode.InputTransformChecksum = TransformNode.Checksum

    Logger.info("Translating mosaic: " + str(minX) + ", " + str(minY) + "\t\t" + TransformNode.FullPath)

    # Adjust all of the control points such that the origin is at 0,0
    for imagename in Transforms.keys():
        transform = Transforms[imagename]
        transform.TranslateFixed((-minY, -minX))
        mosaic.ImageToTransformString[imagename] = factory.TransformToIRToolsString(transform)


    mosaic.Save(OutputTransformNode.FullPath)
    OutputTransformNode.attrib['Checksum'] = mosaic.Checksum

    return ChannelNode



def CutoffValuesForHistogram(HistogramElement, MinCutoffPercent, MaxCutoffPercent, Gamma, Bpp=8):
    '''Returns the (Min, Max, Gamma) values for a histogram.  If an AutoLevelHint node is available those values are used.
    
    :param HistogramElement HistogramElement: Histogram data node
    :param float MinCutoffPercent: Percent of low intensity pixels to remove
    :param float MaxCutoffPercent: Percent of high intensity pixels to remove
    :param float Gamma: Desired Gamma value
    :param int Bpp: Bits per pixel
    :return: (Min, Max, Gamma) Contrast values based on passed values and user overrides in HistogramElement
    :rtype: tuple
    '''

    AutoLevelDataNode = HistogramElement.GetOrCreateAutoLevelHint()
    MinIntensityCutoff = AutoLevelDataNode.UserRequestedMinIntensityCutoff
    MaxIntensityCutoff = AutoLevelDataNode.UserRequestedMaxIntensityCutoff
    UserRequestedGamma = AutoLevelDataNode.UserRequestedGamma

    if not UserRequestedGamma is None:
        Gamma = UserRequestedGamma

    if isinstance(Gamma, str):
        if Gamma == 'None':
            Gamma = None

    if not Gamma is None:
        try:
            Gamma = float(Gamma)
        except:
            prettyoutput.LogErr("Invalid gamma value passed to AutoLevel function: " + str(Gamma))
            Gamma = None

    # Calculate min or max pixel values if they are needed
    # The bummer here is we always load the histogram file to check that the filter levels are correct
    if MinIntensityCutoff is None or MaxIntensityCutoff is None or Gamma is None:

        # Check if we've already created the filter before loading files.  It saves a lot of time
        histogram = Histogram.Load(HistogramElement.DataFullPath)

        if histogram is None:
            prettyoutput.LogErr("*** No histogram data found to create filter with: " + HistogramElement.DataFullPath + "***")
            raise Exception("*** No histogram data found to create filter with: " + HistogramElement.DataFullPath + "***")

        if MinIntensityCutoff is None or MaxIntensityCutoff is None:
            [CalculatedMinCutoff, CalculatedMaxCutoff] = histogram.AutoLevel(MinCutoffPercent, MaxCutoffPercent)

            if MinIntensityCutoff is None:
                MinIntensityCutoff = int(math.floor(CalculatedMinCutoff))
            if MaxIntensityCutoff is None:
                MaxIntensityCutoff = int(math.ceil(CalculatedMaxCutoff))

        if MinIntensityCutoff > MaxIntensityCutoff:
            raise nb.NornirUserException("%g > %g Max intensity is less than min intensity for histogram correction. %s" % (MinIntensityCutoff, MaxIntensityCutoff, HistogramElement.DataFullPath))

        if Gamma is None:
            # We look for the largest peak that is not at either extrema
            peakVal = histogram.PeakValue(MinIntensityCutoff + 1, MaxIntensityCutoff - 1)
            if peakVal is None:
                Gamma = 1.0
            else:
                Gamma = histogram.GammaAtValue(peakVal, minVal=MinIntensityCutoff, maxVal=MaxIntensityCutoff)

    return (MinIntensityCutoff, MaxIntensityCutoff, Gamma)


def _ClearInvalidHistogramElements(filterObj, checksum):
    '''I believe this is complicated due to legacy.  In the past multiple histogram nodes would accumulate.  This function deletes the excess nodes and returns 
       the valid one.'''
    HistogramElement = None
    HistogramElementRemoved = False
    while HistogramElement is None:
        HistogramElement = filterObj.find("Histogram[@InputTransformChecksum='" + checksum + "']")
        if HistogramElement is None:
            raise NornirUserException("Missing input histogram in %s.  Did you run the histogram pipeline?" % filterObj.FullPath) 
        if HistogramElement.CleanIfInvalid(): 
            HistogramElement = None
            HistogramElementRemoved = True
            
    return (HistogramElementRemoved, HistogramElement)


def AutolevelTiles(Parameters, InputFilter, Downsample=1, TransformNode=None, OutputFilterName=None, **kwargs):
    '''Create a new filter using the histogram of the input filter
       @ChannelNode'''

    InputLevelNode = InputFilter.TilePyramid.GetOrCreateLevel(Downsample)
    InputTransformNode = TransformNode
    InputPyramidNode = InputFilter.TilePyramid

    ChannelNode = InputFilter.Parent

    if OutputFilterName is None:
        OutputFilterName = 'Leveled'

    (HistogramElementRemoved, HistogramElement) = _ClearInvalidHistogramElements(InputFilter, InputTransformNode.Checksum)
    if HistogramElementRemoved:
        yield InputFilter
        
    if HistogramElement is None:
        raise nb.NornirUserException("No histograms available for autoleveling of section: %s" % InputFilter.FullPath)
    
    MinCutoffPercent = float(Parameters.get('MinCutoff', ContrastMinCutoffDefault)) / 100.0
    MaxCutoffPercent = float(Parameters.get('MaxCutoff', ContrastMaxCutoffDefault)) / 100.0
    Gamma = Parameters.get('Gamma', None)

    (MinIntensityCutoff, MaxIntensityCutoff, Gamma) = CutoffValuesForHistogram(HistogramElement, MinCutoffPercent, MaxCutoffPercent, Gamma, Bpp=InputFilter.BitsPerPixel)

    # If the output filter already exists, find out if the user has specified the min and max pixel values explicitely.
    (saveFilter, OutputFilterNode) = ChannelNode.GetOrCreateFilter(OutputFilterName)
    if saveFilter:
        yield OutputFilterNode
        
    EntireTilePyramidNeedsBuilding = OutputFilterNode is None
    
    if(OutputFilterNode is not None):
        
        FilterPopulated = FilterIsPopulated(InputFilter, InputLevelNode.Downsample, InputTransformNode.FullPath, OutputFilterName)
        # Check that the existing filter is valid
        if OutputFilterNode.Locked and FilterPopulated:
            prettyoutput.Log("Skipping contrast on existing locked filter %s" % OutputFilterNode.FullPath)
            return
        
        yield GenerateHistogramImage(HistogramElement, MinIntensityCutoff, MaxIntensityCutoff, Gamma=Gamma, Async=True)
        
        if ChannelNode.RemoveFilterOnContrastMismatch(OutputFilterName, MinIntensityCutoff, MaxIntensityCutoff, Gamma):
            EntireTilePyramidNeedsBuilding = True
            yield ChannelNode.Parent
            
            (added_filter, OutputFilterNode) = ChannelNode.GetOrCreateFilter(OutputFilterName)
            OutputFilterNode.SetContrastValues(MinIntensityCutoff, MaxIntensityCutoff, Gamma)
            yield ChannelNode
        elif FilterPopulated:
            # Nothing to do, contrast matches and the filter is populated
            return 
    else:
        yield GenerateHistogramImage(HistogramElement, MinIntensityCutoff, MaxIntensityCutoff, Gamma=Gamma, Async=True)
        
        
    # TODO: Verify parameters match... if(OutputFilterNode.Gamma != Gamma)
#     DictAttributes = {'BitsPerPixel' : 8,
#                         'MinIntensityCutoff' : str(MinIntensityCutoff),
#                         'MaxIntensityCutoff' : str(MaxIntensityCutoff),
#                         'Gamma' : str(Gamma),
#                         'HistogramChecksum' : str(HistogramElement.Checksum)}

    (filter_created, OutputFilterNode) = ChannelNode.GetOrCreateFilter(OutputFilterName)
    if not os.path.exists(OutputFilterNode.FullPath):
        os.makedirs(OutputFilterNode.FullPath)
        
    OutputFilterNode.BitsPerPixel = 8
    OutputFilterNode.HistogramChecksum = str(HistogramElement.Checksum)
    OutputFilterNode.SetContrastValues(MinIntensityCutoff, MaxIntensityCutoff, Gamma)
        
    Input = mosaicfile.MosaicFile.Load(InputTransformNode.FullPath)
    ImageFiles = sorted(Input.ImageToTransformString.keys())

    InputImagePath = InputLevelNode.FullPath

    OutputPyramidNode = nb.VolumeManager.TilePyramidNode(NumberOfTiles=InputPyramidNode.NumberOfTiles,
                                                         LevelFormat=InputPyramidNode.LevelFormat,
                                                         ImageFormatExt=InputPyramidNode.ImageFormatExt)
    [pyramid_created, OutputPyramidNode] = OutputFilterNode.UpdateOrAddChildByAttrib(OutputPyramidNode, 'Path')

    OutputLevelNode = nb.VolumeManager.LevelNode(Level=InputLevelNode.Downsample)
    [level_created, OutputLevelNode] = OutputPyramidNode.UpdateOrAddChildByAttrib(OutputLevelNode, 'Downsample')

    OutputImageDir = OutputLevelNode.FullPath

    TilesToBuild = list()

    # Make sure output isn't outdated
    if(not os.path.exists(OutputImageDir)):
        os.makedirs(OutputImageDir)
        EntireTilePyramidNeedsBuilding = True

    for tile in ImageFiles:
        InputTile = os.path.join(InputImagePath, tile)
        if EntireTilePyramidNeedsBuilding:
            TilesToBuild.append(InputTile)
            continue
        else:
            PredictedOutput = os.path.join(OutputImageDir, os.path.basename(tile))
            RemoveOutdatedFile(InputTile, PredictedOutput)
            if not os.path.exists(PredictedOutput):
                TilesToBuild.append(InputTile)
                
    Pool = None
    if len(TilesToBuild) > 0:
        Pool = Pools.GetGlobalClusterPool()

    if InputFilter.BitsPerPixel == 8:
        MinIntensityCutoff16bpp = MinIntensityCutoff * 256
        MaxIntensityCutoff16bpp = MaxIntensityCutoff * 256
    else:
        MinIntensityCutoff16bpp = MinIntensityCutoff
        MaxIntensityCutoff16bpp = MaxIntensityCutoff
    

    # In case the user swaps min/max cutoffs swap these values if needed
    if MaxIntensityCutoff16bpp < MinIntensityCutoff16bpp:
        temp = MaxIntensityCutoff16bpp
        MaxIntensityCutoff16bpp = MinIntensityCutoff16bpp
        MinIntensityCutoff16bpp = temp

    SampleCmdPrinted = False
    for imageFile in TilesToBuild:
        InputImageFullPath = os.path.join(InputLevelNode.FullPath, imageFile)
        ImageSaveFilename = os.path.join(OutputImageDir, os.path.basename(imageFile))

        cmd = 'convert \"' + InputImageFullPath + '\" ' + \
               '-level ' + str(MinIntensityCutoff16bpp) + \
               ',' + str(MaxIntensityCutoff16bpp) + \
               ' -gamma ' + str(Gamma) + \
               ' -colorspace Gray -depth 8 -type optimize ' + \
               ' \"' + ImageSaveFilename + '\"'


        if not SampleCmdPrinted:
            SampleCmdPrinted = True
            prettyoutput.CurseString('Cmd', cmd)
        Pool.add_process('AutoLevel: ' + cmd, cmd)

    if not Pool is None:
        Pool.wait_completion()

    OutputPyramidNode.NumberOfTiles = len(ImageFiles)

    # Save the channel node so the new filter is recorded
    yield ChannelNode


def HistogramFilter(Parameters, FilterNode, Downsample, TransformNode, **kwargs):
    '''Construct the intensity histogram for a filter
       @FilterNode'''
    NodeToSave = None

    LevelNode = FilterNode.TilePyramid.GetOrCreateLevel(Downsample)

    if(TransformNode is None):
        prettyoutput.LogErr("Missing TransformNode attribute on PruneTiles")
        return None

    InputMosaicFullPath = TransformNode.FullPath

    MangledName = nornir_shared.misc.GenNameFromDict(Parameters) + TransformNode.Type
    HistogramBaseName = "Histogram" + MangledName
    OutputHistogramXmlFilename = HistogramBaseName + ".xml"

    HistogramElement = nb.VolumeManager.HistogramNode(TransformNode, Type=MangledName, attrib=Parameters)
    [HistogramElementCreated, HistogramElement] = FilterNode.UpdateOrAddChildByAttrib(HistogramElement, "Type")

    ElementCleaned = False

    if not HistogramElementCreated:
        HistogramElement.CleanIfInputTransformMismatched(TransformNode)
        if HistogramElement.CleanIfInvalid():
            HistogramElement = None
            ElementCleaned = True
        elif HistogramElement.InputTransformChecksum != TransformNode.Checksum:
            HistogramElement.Clean(reason="Checksum mismatch with requested transform")
            HistogramElement = None
            ElementCleaned = True

    if HistogramElement is None:
        HistogramElement = nb.VolumeManager.HistogramNode(TransformNode, Type=MangledName, attrib=Parameters)
        [HistogramElementCreated, HistogramElement] = FilterNode.UpdateOrAddChildByAttrib(HistogramElement, "Type")

    DataNode = nb.VolumeManager.DataNode(OutputHistogramXmlFilename)
    [DataElementCreated, DataNode] = HistogramElement.UpdateOrAddChild(DataNode)

    AutoLevelDataNode = HistogramElement.GetOrCreateAutoLevelHint()

    if os.path.exists(HistogramElement.DataFullPath) and os.path.exists(HistogramElement.ImageFullPath) and HistogramElement.InputTransformChecksum == TransformNode.Checksum:
        if HistogramElementCreated or ElementCleaned or DataElementCreated:
            return FilterNode

    # Check the folder for changes, not the .mosaic file
    # RemoveOutdatedFile(TransformNode.FullPath, OutputHistogramXmlFullPath)
    # RemoveOutdatedFile(TransformNode.FullPath, OutputHistogramPngFilename)

    Bpp = 16
    if(not FilterNode.BitsPerPixel is None):
        Bpp = int(FilterNode.BitsPerPixel)

    NumBins = 2 << Bpp
    if(NumBins > 2048):
        NumBins = 2048

    ImageCreated = False
    if not os.path.exists(DataNode.FullPath):
        mosaic = mosaicfile.MosaicFile.Load(InputMosaicFullPath)

        FullTilePath = LevelNode.FullPath
        fulltilepaths = list()
        for k in mosaic.ImageToTransformString.keys():
            fulltilepaths.append(os.path.join(FullTilePath, k))

        histogramObj = image_stats.Histogram(fulltilepaths, Bpp=Bpp)
        histogramObj.Save(DataNode.FullPath)

        # Create a data node for the histogram
        # DataObj = VolumeManager.DataNode(Path=)

        ImageCreated = (GenerateHistogramImageFromPercentage(HistogramElement) is not None)

    HistogramElement.InputTransformChecksum = TransformNode.Checksum

    if ElementCleaned or HistogramElementCreated or DataElementCreated or ImageCreated:
        return FilterNode
    else:
        return None


def GenerateHistogramImageFromPercentage(HistogramElement, MinCutoffPercent=None, MaxCutoffPercent=None, Gamma=1):
    if MinCutoffPercent is None:
        MinCutoffPercent = ContrastMinCutoffDefault
    if MaxCutoffPercent is None:
        MaxCutoffPercent = ContrastMaxCutoffDefault
        
    LineColors = ['green', 'green']
    AutoLevelDataNode = HistogramElement.GetOrCreateAutoLevelHint()
     
    if not AutoLevelDataNode.UserRequestedMinIntensityCutoff is None:
        MinCutoffLine = float(AutoLevelDataNode.UserRequestedMinIntensityCutoff)
        MinValue = MinCutoffLine 
        LineColors[0] = 'red'

    if not AutoLevelDataNode.UserRequestedMaxIntensityCutoff is None:
        MaxCutoffLine = float(AutoLevelDataNode.UserRequestedMaxIntensityCutoff)
        MaxValue = MaxCutoffLine 
        LineColors[1] = 'red'
        
    (MinValue, MaxValue, Gamma) = CutoffValuesForHistogram(HistogramElement, MinCutoffPercent, MaxCutoffPercent, Gamma)
    return GenerateHistogramImage(HistogramElement, MinValue, MaxValue, Gamma, LineColors=LineColors)
     
    
def GenerateHistogramImage(HistogramElement, MinValue, MaxValue, Gamma, LineColors=None, Async=True):
    '''
    :param object HistogramElement: Histogram element to pull histogram data from
    :param float MinValue: Minimum value line
    :param float MinValue: Maximum value line
    :param float Gamma: Gamma value for use in title
    :param list LineColors: List of color strings to assign to MinValue and MaxValue lines
    '''    
    added = False 
    HistogramImage = HistogramElement.find('Image')
    
    if not HistogramImage is None:
        HistogramImage = transforms.RemoveOnMismatch(HistogramImage, 'MinIntensityCutoff', MinValue)
        HistogramImage = transforms.RemoveOnMismatch(HistogramImage, 'MaxIntensityCutoff', MaxValue)
        HistogramImage = transforms.RemoveOnMismatch(HistogramImage, 'Gamma', Gamma, Precision=3)
 
    if HistogramImage is None:
        OutputHistogramPngFilename = "Histogram" + HistogramElement.Type + ".png"
        HistogramImage = nb.VolumeManager.ImageNode(OutputHistogramPngFilename)
        [added, HistogramImage] = HistogramElement.UpdateOrAddChild(HistogramImage)

    if not os.path.exists(HistogramImage.FullPath):
        added = True
        LinePositions = []
        if LineColors is None:
            LineColors = ['blue', 'blue']
         
        MinCutoffPercent = None
        MaxCutoffPercent = None 
        
        LinePositions.append(MinValue)
        LinePositions.append(MaxValue)

        FilterNode = HistogramElement.FindParent('Filter')
        ChannelNode = HistogramElement.FindParent('Channel')
        SectionNode = ChannelNode.FindParent('Section')
        DataNode = HistogramElement.find('Data')

        TitleStr = str(SectionNode.Number) + " " + ChannelNode.Name + " " + FilterNode.Name + " histogram"

        prettyoutput.Log("Creating Section Autoleveled Histogram Image: " + DataNode.FullPath)
        
        # if Async:
            # pool = Pools.GetThreadPool("Histograms")
            # pool.add_task("Create Histogram %s" % DataNode.FullPath, nornir_shared.plot.Histogram, DataNode.FullPath, HistogramImage.FullPath, MinCutoffPercent, MaxCutoffPercent, LinePosList=LinePositions, LineColorList=LineColors, Title=TitleStr)
        # else:
        nornir_shared.plot.Histogram(DataNode.FullPath, HistogramImage.FullPath, MinCutoffPercent, MaxCutoffPercent, LinePosList=LinePositions, LineColorList=LineColors, Title=TitleStr)

        HistogramImage.MinIntensityCutoff = str(MinValue)
        HistogramImage.MaxIntensityCutoff = str(MaxValue)
        HistogramImage.Gamma = str(Gamma)
        
        prettyoutput.Log("Done!")

    if added:
        return HistogramElement
    else:
        return None

def AssembleTransform(Parameters, Logger, FilterNode, TransformNode, OutputChannelPrefix=None, UseCluster=True, ThumbnailSize=256, Interlace=True, **kwargs):
    for yieldval in AssembleTransformScipy(Parameters, Logger, FilterNode, TransformNode, OutputChannelPrefix, UseCluster, ThumbnailSize, Interlace, **kwargs):
        yield yieldval


def __GetOrCreateOutputChannelForPrefix(prefix, InputChannelNode):
    '''If prefix is empty return the input channel.  Otherwise create a new channel with the prefix'''

    OutputName = InputChannelNode.Name

    if not prefix is None and len(prefix) > 0:
        OutputName = prefix + OutputName
    else:
        return (False, InputChannelNode)

    return InputChannelNode.Parent.UpdateOrAddChildByAttrib(nb.VolumeManager.ChannelNode(OutputName, OutputName))


def GetOrCreateCleanedImageNode(imageset_node, transform_node, level, image_name):
    image_level_node = imageset_node.GetOrCreateLevel(level, GenerateData=False)

    if not os.path.exists(image_level_node.FullPath):
        os.makedirs(image_level_node.FullPath)
        
    image_node = image_level_node.find('Image')
    #===========================================================================
    # if not image_node is None:
    #     if image_node.CleanIfInputTransformMismatched(transform_node):
    #         image_node = None
    #===========================================================================
            
    if(image_node is None):
        image_node = nb.VolumeManager.ImageNode(image_name)
        image_level_node.append(image_node)
        
    return image_node
        
 
def UpdateImageName(imageNode, ExpectedImageName):
    '''Rename the image file on disk to match our expected image file name
    :param ImageNode ImageNode: Filter node to update to the expected name
    :param str ExpectedImageName: The image name we expect to see'''
    
    if imageNode.Path == ExpectedImageName:
        return False
    
    if not os.path.exists(imageNode.FullPath):
        return False
    
    (root, ext) = os.path.splitext(imageNode.Path)
    (root, new_ext) = os.path.splitext(ExpectedImageName)
    # We can't change image types with a simple rename'
    if ext != new_ext:
        return False 
    
    dirname = os.path.dirname(imageNode.FullPath)
    
    SourceImageFullPath = imageNode.FullPath; 
    DestImageFullPath = os.path.join(dirname, ExpectedImageName)
    
    shutil.move(SourceImageFullPath, DestImageFullPath)
    imageNode.Path = ExpectedImageName
    
    return True

def AddDotToExtension(Extension=None):
    '''Ensure that the extension has a . prefix'''
    if not Extension[0] == '.':
        return '.' + Extension
    
    return Extension 
        
def GetImageName(SectionNumber, ChannelName, FilterName, Extension=None):
    
    Extension = AddDotToExtension(Extension)
    return nornir_buildmanager.templates.Current.SectionTemplate % int(SectionNumber) + "_" + ChannelName + "_" + FilterName + Extension

def VerifyAssembledImagePathIsCorrect(Parameters, Logger, FilterNode, extension=None, **kwargs):
    '''Updates the names of image files, these can be incorrect if the sections are re-ordered'''
    
    if not FilterNode.HasImageset:
        return 
    
    if extension is None:
        extension = DefaultImageExtension
     
    ExpectedImageName = FilterNode.DefaultImageName(extension)
     
    imageSet = FilterNode.Imageset
    for imageNode in imageSet.Images:
        original_image_name = imageNode.Path
        if UpdateImageName(imageNode, ExpectedImageName):
            Logger.warn("Renamed image file: %s -> %s " % (original_image_name, ExpectedImageName))
            yield imageSet
    
        
def AssembleTransformScipy(Parameters, Logger, FilterNode, TransformNode, OutputChannelPrefix=None, UseCluster=True, ThumbnailSize=256, Interlace=True, **kwargs):
    '''@ChannelNode - TransformNode lives under ChannelNode'''
    
    image_ext = DefaultImageExtension    
    InputChannelNode = FilterNode.FindParent('Channel')
    InputFilterMaskName = FilterNode.GetOrCreateMaskName()
    
    if not FilterNode.HasTilePyramid:
        Logger.warn("Input filter %s has no tile pyramid" % FilterNode.FullPath)
        return
    
    [AddedChannel, OutputChannelNode] = __GetOrCreateOutputChannelForPrefix(OutputChannelPrefix, InputChannelNode)
    if AddedChannel:
        yield OutputChannelNode.Parent

    AddedFilters = not (OutputChannelNode.HasFilter(FilterNode.Name) and OutputChannelNode.HasFilter(InputFilterMaskName))
    (added_input_mask_filter, InputMaskFilterNode) = FilterNode.GetOrCreateMaskFilter()
    (added_output_filter, OutputFilterNode) = OutputChannelNode.GetOrCreateFilter(FilterNode.Name)
    (added_output_mask_filter, OutputMaskFilterNode) = OutputChannelNode.GetOrCreateFilter(InputFilterMaskName)
    
    PyramidLevels = SortedListFromDelimited(kwargs.get('Levels', [1, 2, 4, 8, 16, 32, 64, 128, 256]))

    OutputImageNameTemplate = FilterNode.DefaultImageName(image_ext)
    OutputImageMaskNameTemplate = InputMaskFilterNode.DefaultImageName(image_ext)  
    
    if OutputFilterNode.HasImageset:
        OutputFilterNode.Imageset.CleanIfInputTransformMismatched(TransformNode)
    if OutputMaskFilterNode.HasImageset:
        OutputMaskFilterNode.Imageset.CleanIfInputTransformMismatched(TransformNode)

    OutputFilterNode.Imageset.SetTransform(TransformNode)
    OutputMaskFilterNode.Imageset.SetTransform(TransformNode)

    thisLevel = PyramidLevels[0]

    # Create a node for this level
    ImageLevelNode = OutputFilterNode.Imageset.GetOrCreateLevel(thisLevel, GenerateData=False)
    ImageMaskLevelNode = OutputMaskFilterNode.Imageset.GetOrCreateLevel(thisLevel, GenerateData=False)

    if not os.path.exists(ImageLevelNode.FullPath):
        os.makedirs(ImageLevelNode.FullPath)

    if not os.path.exists(ImageMaskLevelNode.FullPath):
        os.makedirs(ImageMaskLevelNode.FullPath)
 
    ImageNode = GetOrCreateCleanedImageNode(OutputFilterNode.Imageset, TransformNode, thisLevel, OutputImageNameTemplate)
    MaskImageNode = GetOrCreateCleanedImageNode(OutputMaskFilterNode.Imageset, TransformNode, thisLevel, OutputImageMaskNameTemplate)
     
    ImageNode.MaskPath = MaskImageNode.FullPath
  
    #===========================================================================
    # if hasattr(ImageNode, 'InputTransformChecksum'):
    #     if not transforms.IsValueMatched(ImageNode, 'InputTransformChecksum', TransformNode.Checksum):
    #         if os.path.exists(ImageNode.FullPath):
    #             os.remove(ImageNode.FullPath)
    # else:
    #     if os.path.exists(ImageNode.FullPath):
    #         os.remove(ImageNode.FullPath)
    #===========================================================================

    # image.RemoveOnTransformCropboxMismatched(TransformNode, ImageNode, thisLevel)
    # image.RemoveOnTransformCropboxMismatched(TransformNode, MaskImageNode, thisLevel)
    
    if os.path.exists(ImageNode.FullPath):
        image.RemoveOnDimensionMismatch(MaskImageNode.FullPath, core.GetImageSize(ImageNode.FullPath))

    if not (os.path.exists(ImageNode.FullPath) and os.path.exists(MaskImageNode.FullPath)):

        # LevelFormatStr = LevelFormatTemplate % thisLevel
        InputLevelNode = FilterNode.TilePyramid.GetOrCreateLevel(thisLevel)

        ImageDir = InputLevelNode.FullPath
        # ImageDir = os.path.join(FilterNode.TilePyramid.FullPath, LevelFormatStr)

        tempOutputFullPath = os.path.join(ImageDir, 'Temp' + image_ext)
        tempMaskOutputFullPath = os.path.join(ImageDir, 'TempMask' + image_ext)

        Logger.info("Assembling " + TransformNode.FullPath)
        mosaic = Mosaic.LoadFromMosaicFile(TransformNode.FullPath)
        (mosaicImage, maskImage) = mosaic.AssembleTiles(ImageDir, usecluster=True)

        if mosaicImage is None or maskImage is None:
            Logger.error("No output produced assembling " + TransformNode.FullPath)
            return

        if not TransformNode.CropBox is None:
            cmdTemplate = "convert %(Input)s -crop %(width)dx%(height)d%(Xo)+d%(Yo)+d! -background black -flatten %(Output)s"
            (Xo, Yo, Width, Height) = TransformNode.CropBoxDownsampled(thisLevel)

            Logger.warn("Cropping assembled image to volume boundary")

            mosaicImage = core.CropImage(mosaicImage, Xo, Yo, Width, Height)
            maskImage = core.CropImage(maskImage, Xo, Yo, Width, Height)

        core.SaveImage(tempOutputFullPath, mosaicImage)
        core.SaveImage(tempMaskOutputFullPath, maskImage)

        # Run convert on the output to make sure it is interlaced
        if(Interlace):
            ConvertCmd = 'Convert ' + tempOutputFullPath + ' -quality 106 -interlace PNG ' + tempOutputFullPath
            Logger.warn("Interlacing assembled image")
            subprocess.call(ConvertCmd + " && exit", shell=True)

        shutil.move(tempOutputFullPath, ImageNode.FullPath)
        shutil.move(tempMaskOutputFullPath, MaskImageNode.FullPath)

        # ImageNode.Checksum = nornir_shared.Checksum.FilesizeChecksum(ImageNode.FullPath)
        # MaskImageNode.Checksum = nornir_shared.Checksum.FilesizeChecksum(MaskImageNode.FullPath)
    if AddedFilters:
        yield OutputChannelNode
    else:
        yield OutputFilterNode
        yield OutputMaskFilterNode

    ImageSet = BuildImagePyramid(OutputFilterNode.Imageset, Interlace=Interlace, **kwargs)
    if not ImageSet is None:
        yield ImageSet 
        
    MaskImageSet = BuildImagePyramid(OutputMaskFilterNode.Imageset, Interlace=Interlace, **kwargs)
    if not MaskImageSet is None:
        yield MaskImageSet
 

def AssembleTransformIrTools(Parameters, Logger, FilterNode, TransformNode, ThumbnailSize=256, Interlace=True, **kwargs):
    '''Assemble a transform using the ir-tools
       @ChannelNode - TransformNode lives under ChannelNode
       '''
    Feathering = Parameters.get('Feathering', 'binary')

    (added_mask_filter, MaskFilterNode) = FilterNode.GetOrCreateMaskFilter(FilterNode.MaskName)
    ChannelNode = FilterNode.FindParent('Channel')
    SectionNode = ChannelNode.FindParent('Section')

    NodesToSave = []

    MangledName = misc.GenNameFromDict(Parameters) + TransformNode.Type

    PyramidLevels = SortedListFromDelimited(kwargs.get('Levels', [1, 2, 4, 8, 16, 32, 64, 128, 256]))

    OutputImageNameTemplate = nornir_buildmanager.templates.Current.SectionTemplate % SectionNode.Number + "_" + ChannelNode.Name + "_" + FilterNode.Name + ".png"
    OutputImageMaskNameTemplate = nornir_buildmanager.templates.Current.SectionTemplate % SectionNode.Number + "_" + ChannelNode.Name + "_" + MaskFilterNode.Name + ".png"

    FilterNode.Imageset.SetTransform(TransformNode)
    MaskFilterNode.Imageset.SetTransform(TransformNode)

    argstring = misc.ArgumentsFromDict(Parameters)
    irassembletemplate = 'ir-assemble ' + argstring + ' -sh 1 -sp %(pixelspacing)i -save %(OutputImageFile)s -load %(InputFile)s -mask %(OutputMaskFile)s -image_dir %(ImageDir)s '

    LevelFormatTemplate = FilterNode.TilePyramid.attrib.get('LevelFormat', nornir_buildmanager.templates.Current.LevelFormat)

    thisLevel = PyramidLevels[0]

    # Create a node for this level
    ImageLevelNode = FilterNode.Imageset.GetOrCreateLevel(thisLevel)
    ImageMaskLevelNode = MaskFilterNode.Imageset.GetOrCreateLevel(thisLevel)

    if not os.path.exists(ImageLevelNode.FullPath):
        os.makedirs(ImageLevelNode.FullPath)

    if not os.path.exists(ImageMaskLevelNode.FullPath):
        os.makedirs(ImageMaskLevelNode.FullPath)

    # Should Replace any child elements
    ImageNode = ImageLevelNode.find('Image')
    if(ImageNode is None):
        ImageNode = nb.VolumeManager.ImageNode(OutputImageNameTemplate)
        ImageLevelNode.append(ImageNode)

    MaskImageNode = ImageMaskLevelNode.find('Image')
    if(MaskImageNode is None):
        MaskImageNode = nb.VolumeManager.ImageNode(OutputImageMaskNameTemplate)
        ImageMaskLevelNode.append(MaskImageNode)

    ImageNode.MaskPath = MaskImageNode.FullPath

    if not (os.path.exists(ImageNode.FullPath) and os.path.exists(MaskImageNode.FullPath)):
        LevelFormatStr = LevelFormatTemplate % thisLevel
        ImageDir = os.path.join(FilterNode.TilePyramid.FullPath, LevelFormatStr)

        tempOutputFullPath = os.path.join(ImageDir, 'Temp.png')
        tempMaskOutputFullPath = os.path.join(ImageDir, 'TempMask.png')

        cmd = irassembletemplate % {'pixelspacing' : thisLevel,
                                    'OutputImageFile' : tempOutputFullPath,
                                    'OutputMaskFile' : tempMaskOutputFullPath,
                                    'InputFile' : TransformNode.FullPath,
                                    'ImageDir' : ImageDir}
        prettyoutput.Log(cmd)
        subprocess.call(cmd + " && exit", shell=True)

        if hasattr(TransformNode, 'CropBox'):
            cmdTemplate = "convert %(Input)s -crop %(width)dx%(height)d%(Xo)+d%(Yo)+d! -background black -flatten %(Output)s"
            (Xo, Yo, Width, Height) = nornir_shared.misc.ListFromAttribute(TransformNode.CropBox)

            # Figure out the downsample level, adjust the crop box, and crop
            Xo = Xo / float(thisLevel)
            Yo = Yo / float(thisLevel)
            Width = Width / float(thisLevel)
            Height = Height / float(thisLevel)

            cmd = cmdTemplate % {'Input' : tempOutputFullPath,
                                 'Output' : tempOutputFullPath,
                                 'Xo' :-Xo,
                                 'Yo' :-Yo,
                                 'width' : Width,
                                 'height' : Height}

            maskcmd = cmdTemplate % {'Input' : tempMaskOutputFullPath,
                                 'Output' : tempMaskOutputFullPath,
                                 'Xo' :-Xo,
                                 'Yo' :-Yo,
                                 'width' : Width,
                                 'height' : Height}

            Logger.warn("Cropping assembled image to volume boundary")
            # subprocess.call(cmd + " && exit", shell=True)
            # subprocess.call(maskcmd + " && exit", shell=True)

        # Run convert on the output to make sure it is interlaced
        if(Interlace):
            ConvertCmd = 'Convert ' + tempOutputFullPath + ' -quality 106 -interlace PNG ' + tempOutputFullPath
            Logger.warn("Interlacing assembled image")
            subprocess.call(ConvertCmd + " && exit", shell=True)

        if os.path.exists(tempOutputFullPath):
            shutil.move(tempOutputFullPath, ImageNode.FullPath)
            shutil.move(tempMaskOutputFullPath, MaskImageNode.FullPath)
        else:
            Logger.error("Assemble produced no output " + ImageNode.FullPath)

        # ImageNode.Checksum = nornir_shared.Checksum.FilesizeChecksum(ImageNode.FullPath)
        # MaskImageNode.Checksum = nornir_shared.Checksum.FilesizeChecksum(MaskImageNode.FullPath)
    
    yield FilterNode

    ImageSet = BuildImagePyramid(FilterNode.Imageset, Logger, **kwargs)
    if not ImageSet is None:
        yield ImageSet 
        
    MaskImageSet = BuildImagePyramid(MaskFilterNode.Imageset, Logger, **kwargs)
    if not MaskImageSet is None:
        yield MaskImageSet

def AssembleTileset(Parameters, FilterNode, PyramidNode, TransformNode, TileShape=None, TileSetName=None, Logger=None, **kwargs):
    '''Create full resolution tiles of specfied size for the mosaics
       @FilterNode
       @TransformNode'''
    prettyoutput.CurseString('Stage', "Assemble Tile Pyramids")   
    
    TileWidth = TileShape[0]
    TileHeight = TileShape[1]

    Feathering = Parameters.get('Feathering', 'binary')

    InputTransformNode = TransformNode
    FilterNode = PyramidNode.FindParent('Filter')

    if(TileSetName is None):
        TileSetName = 'Tileset'

    InputLevelNode = PyramidNode.GetLevel(1)
    if InputLevelNode is None:
        Logger.warning("No input tiles found for assembletiles")
        return

    TileSetNode = nb.VolumeManager.TilesetNode()
    [added, TileSetNode] = FilterNode.UpdateOrAddChildByAttrib(TileSetNode, 'Path')

    TileSetNode.TileXDim = str(TileWidth)
    TileSetNode.TileYDim = str(TileHeight)
    TileSetNode.FilePostfix = '.png'
    TileSetNode.FilePrefix = FilterNode.Name + '_'
    TileSetNode.CoordFormat = nornir_buildmanager.templates.Current.GridTileCoordFormat

    if not os.path.exists(TileSetNode.FullPath):
        Logger.info("Creating Directory: " + TileSetNode.FullPath)
        os.makedirs(TileSetNode.FullPath)

    # OK, check if the first level of the tileset exists
    LevelOne = TileSetNode.GetChildByAttrib('Level', 'Downsample', 1)
    if(LevelOne is None):
        # Need to call ir-assemble
        LevelOne = nb.VolumeManager.LevelNode(Level=1)
        [added, LevelOne] = TileSetNode.UpdateOrAddChildByAttrib(LevelOne, 'Downsample')

        if not os.path.exists(LevelOne.FullPath):
            os.makedirs(LevelOne.FullPath)

        # The output file name is used as a prefix for the tiles written
        OutputPath = os.path.join(LevelOne.FullPath, FilterNode.Name + '.png')
        OutputXML = os.path.join(LevelOne.FullPath, FilterNode.Name + '.xml')

        assembleTemplate = 'ir-assemble -load %(transform)s -save %(LevelPath)s -image_dir %(ImageDir)s -feathering %(feathering)s -load_as_needed -tilesize %(width)d %(height)d -sp 1'
        cmd = assembleTemplate % {'transform' : InputTransformNode.FullPath,
                                  'LevelPath' : OutputPath,
                                  'ImageDir' : InputLevelNode.FullPath,
                                  'feathering' : Feathering,
                                  'width' : TileWidth,
                                  'height' : TileHeight}

        if not os.path.exists(OutputXML):
            Logger.info(cmd)
            prettyoutput.Log(cmd)
            subprocess.call(cmd + " && exit", shell=True)

            # Figure out the grid tile format

        else:
            Logger.info("Assemble tiles output already exists")


        if not os.path.exists(OutputXML):
            # Something went wrong, do not save
            return None

        Info = __LoadAssembleTilesXML(XmlFilePath=OutputXML, Logger=Logger)
        LevelOne.GridDimX = str(Info.GridDimX)
        LevelOne.GridDimY = str(Info.GridDimY)

    return FilterNode


def BuildImagePyramid(ImageSetNode, Levels=None, Interlace=True, **kwargs):
    '''@ImageSetNode'''

    PyramidLevels = _SortedNumberListFromLevelsParameter(Levels)

    Logger = kwargs.get('Logger', None)
    if Logger is None:
        Logger = logging.getLogger('BuildImagePyramid')

    SaveImageSet = False

    PyramidLevels = _InsertExistingLevelIfMissing(ImageSetNode, PyramidLevels)
    
    # Ensure each level is unique
    PyramidLevels = sorted(frozenset(PyramidLevels))

    # Build downsampled images for every level below the input image level node
    for i in range(1, len(PyramidLevels)):
        
        # OK, check for a node with the previous downsample level. If it exists use it to build this level if it does not exist
        SourceLevel = PyramidLevels[i - 1]
        SourceImageNode = ImageSetNode.GetImage(SourceLevel)
        if(SourceImageNode is None):
            Logger.error('Source image not found in level' + str(SourceLevel))
            return None

        thisLevel = PyramidLevels[i]
        assert(SourceLevel != thisLevel)
        TargetImageNode = ImageSetNode.GetOrCreateImage(thisLevel, SourceImageNode.Path, GenerateData=False)
        if not os.path.exists(TargetImageNode.Parent.FullPath):
            os.makedirs(TargetImageNode.Parent.FullPath)
        
        RemoveOutdatedFile(SourceImageNode.FullPath, TargetImageNode.FullPath)
        
        buildLevel = False
        if os.path.exists(TargetImageNode.FullPath):
            if 'InputImageChecksum' in SourceImageNode.attrib:
                TargetImageNode = transforms.RemoveOnMismatch(TargetImageNode, "InputImageChecksum", SourceImageNode.InputImageChecksum)

                if TargetImageNode is None:
                    buildLevel = True
                    # Recreate the node if needed
                    TargetImageNode = ImageSetNode.GetOrCreateImage(thisLevel)

    #            RemoveOnMismatch()
    #            if(TargetImageNode.attrib["InputImageChecksum"] != SourceImageNode.InputImageChecksum):
    #                os.remove(TargetImageNode.FullPath)
             
            
        else:
            buildLevel = True

        if buildLevel:
            scale = thisLevel / SourceLevel
            NewP = images.Shrink(SourceImageNode.FullPath, TargetImageNode.FullPath, scale)
            NewP.wait()
            SaveImageSet = True
            
            if 'InputImageChecksum' in SourceImageNode.attrib:
                TargetImageNode.attrib['InputImageChecksum'] = str(SourceImageNode.InputImageChecksum)

            Logger.info('Shrunk ' + TargetImageNode.FullPath)

            if(Interlace):
                ConvertCmd = 'Convert ' + TargetImageNode.FullPath + ' -quality 106 -interlace PNG ' + TargetImageNode.FullPath
                Logger.info('Interlacing start ' + TargetImageNode.FullPath)
                prettyoutput.Log(ConvertCmd)
                subprocess.call(ConvertCmd + " && exit", shell=True)
                

            # TargetImageNode.Checksum = nornir_shared.Checksum.FilesizeChecksum(TargetImageNode.FullPath)

    if SaveImageSet:
        return ImageSetNode

    return None



def BuildTilePyramids(PyramidNode=None, Levels=None, **kwargs):
    ''' @PyramidNode
        Build the image pyramid for the specified path.  We expect the "001" level of the pyramid to be pre-populated'''
    prettyoutput.CurseString('Stage', "BuildPyramids")

    SavePyramidNode = False

    PyramidLevels = _SortedNumberListFromLevelsParameter(Levels) 

    Pool = None

    if(PyramidNode is None):
        prettyoutput.LogErr("No volume element available for BuildTilePyramids")
        return

    LevelFormatStr = PyramidNode.attrib.get('LevelFormat', nornir_buildmanager.templates.Current.LevelFormat)

    InputPyramidFullPath = PyramidNode.FullPath

    prettyoutput.Log("Checking path for unbuilt pyramids: " + InputPyramidFullPath)

    PyramidLevels = _InsertExistingLevelIfMissing(PyramidNode, PyramidLevels)
    
    # Ensure each level is unique
    PyramidLevels = sorted(frozenset(PyramidLevels))

    for i in range(1, len(PyramidLevels)):

        LevelHeaderPrinted = False

        upLevel = PyramidLevels[i - 1]
        thisLevel = PyramidLevels[i]

        upLevelPathStr = LevelFormatStr % upLevel
        thisLevePathlStr = LevelFormatStr % thisLevel

        shrinkFactor = float(thisLevel) / float(upLevel)

        upLevelNode = nb.VolumeManager.LevelNode(upLevel)
        [LevelNodeCreated, upLevelNode] = PyramidNode.UpdateOrAddChildByAttrib(upLevelNode, "Downsample")
        if LevelNodeCreated:
            SavePyramidNode = True

        thisLevelNode = nb.VolumeManager.LevelNode(thisLevel)
        [LevelNodeCreated, thisLevelNode] = PyramidNode.UpdateOrAddChildByAttrib(thisLevelNode, "Downsample")
        if LevelNodeCreated:
            SavePyramidNode = True

        InputTileDir = os.path.join(InputPyramidFullPath, upLevelPathStr)
        OutputTileDir = os.path.join(InputPyramidFullPath, thisLevePathlStr)

        InputGlobPattern = os.path.join(InputTileDir, "*" + PyramidNode.ImageFormatExt)
        OutputGlobPattern = os.path.join(OutputTileDir, "*" + PyramidNode.ImageFormatExt)

        SourceFiles = glob.glob(InputGlobPattern)

        taskList = []

        # Create directories if we have source files and the directories are missing
        if(len(SourceFiles) > 0):
            if not os.path.exists(OutputTileDir):
                os.makedirs(OutputTileDir)

        # Simply a speedup so we aren't constantly hitting the server with exist requests for populated directories
        DestFiles = glob.glob(OutputGlobPattern)
        if(len(DestFiles) == PyramidNode.NumberOfTiles and
           len(SourceFiles) == len(DestFiles)):
            
            # Double check that the files aren't out of date
            if not OutdatedFile(SourceFiles[0], DestFiles[0]):
                continue

        DestFiles = [os.path.basename(x) for x in DestFiles ]

        for f in SourceFiles:
            filename = os.path.basename(f)

            outputFile = os.path.join(OutputTileDir, filename)
            inputFile = os.path.join(InputTileDir, filename)

            if(filename in DestFiles):
                continue

            # Don't process if the input is temp file
            try:
                if(os.path.getsize(inputFile) <= 0):
                    continue
            except:
                continue

            RemoveOutdatedFile(inputFile, outputFile)

            if(os.path.exists(outputFile)):
                continue

            if Pool is None:
                Pool = Pools.GetGlobalClusterPool()

            if not LevelHeaderPrinted:
       #         prettyoutput.Log(str(upLevel) + ' -> ' + str(thisLevel) + '\n')
                LevelHeaderPrinted = True

            task = Shrink(Pool, inputFile, outputFile, shrinkFactor)
            task.inputFile = inputFile
            taskList.append(task)

        if not Pool is None:
            Pool.wait_completion()

            for task in taskList:
                if hasattr(task, 'returncode'):
                    if task.returncode > 0:
                        prettyoutput.LogErr('\n*** Suspected bad input file to pyramid, deleting the source image.  Rerun scripts to attempt adding the file again.\n')
                        try:
                            os.remove(task.inputFile)
                        except:
                            pass

    if SavePyramidNode:
        return PyramidNode

    return None

def _InsertExistingLevelIfMissing(PyramidNode, Levels):
    '''If the first level in the list does not exist, insert it into the list so a source is available to build from'''

    if not PyramidNode.HasLevel(Levels[0]):
        MoreDetailedLevel = PyramidNode.MoreDetailedLevel(Levels[0])
        if MoreDetailedLevel is None:
            raise Exception("No pyramid level available with more detail than %d in %s" % (Levels[0], PyramidNode.FullPath))

        Levels.insert(0, MoreDetailedLevel.Downsample)

    return Levels

def _SortedNumberListFromLevelsParameter(Levels=None):

    if Levels is None:
        Levels = [1, 2, 4, 8, 16, 32, 64, 128, 256]
    elif isinstance(Levels, str):
        Levels = nornir_shared.misc.SortedListFromDelimited(Levels)
    elif isinstance(Levels, int) or isinstance(Levels, float):
        Levels = [Levels]

    return sorted(Levels)

# def UpdateNode(Parameters, Logger, Node):
 #    '''This is a placeholder for patching up volume.xml files on a case-by-case basis'''
    # return

def __LoadAssembleTilesXML(XmlFilePath, Logger=None):

    class TilesetInfo:
        pass

    Info = TilesetInfo()

    try:
        dom = xml.dom.minidom.parse(XmlFilePath)
        levels = dom.getElementsByTagName("Level")
        level = levels[0]

        Info.GridDimX = int(level.getAttribute('GridDimX'))
        Info.GridDimY = int(level.getAttribute('GridDimY'))
        Info.TileXDim = int(level.getAttribute('TileXDim'))
        Info.TileYDim = int(level.getAttribute('TileYDim'))
        Info.FilePrefix = level.getAttribute('FilePrefix')
        Info.FilePostfix = level.getAttribute('FilePostfix')
        Info.Downsample = float(level.getAttribute('Downsample'))
    except Exception as e:
        Logger.warning("Failed to parse XML File: " + XmlFilePath)
        Logger.warning(str(e))
        return

    return Info


def BuildTilesetLevel(SourcePath, DestPath, DestGridDimensions, TileDim, FilePrefix, FilePostfix, Pool=None, **kwargs):
    '''
    :param tuple SourceGridDimensions: (GridDimY,GridDimX) Number of tiles along each axis
    :param ndarray TileDim: Dimensions of tile (Y,X)
    
    '''
    
    try:
        os.makedirs(DestPath)
    except:
        e = 1  # Just a garbage statement, not sure how to swallow an exception
        
    if Pool is None:
        Pool = Pools.GetGlobalLocalMachinePool()

    # Merge all the tiles we can find into tiles of the same size
    for iY in range(0, DestGridDimensions[0]):

        '''We wait for the last task we queued for each row so we do not swamp the ProcessPool but are not waiting for the entire pool to empty'''
        FirstTaskForRow = None

        for iX in range(0, DestGridDimensions[1]):

            X1 = iX * 2
            X2 = X1 + 1
            Y1 = iY * 2
            Y2 = Y1 + 1

            # OutputFile = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % iX + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % iY + FilePostfix
            OutputFile = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix' : FilePrefix,
                                                 'X' : iX,
                                                 'Y' : iY,
                                                 'postfix' : FilePostfix }

            OutputFileFullPath = os.path.join(DestPath, OutputFile)

            # Skip if file already exists
            # if(os.path.exists(OutputFileFullPath)):
            #    continue

            # TopLeft = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % X1 + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % Y1 + FilePostfix
            # TopRight = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % X2 + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % Y1 + FilePostfix
            # BottomLeft = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % X1 + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % Y2 + FilePostfix
            # BottomRight = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % X2 + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % Y2 + FilePostfix
            TopLeft = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix' : FilePrefix,
                                                 'X' : X1,
                                                 'Y' : Y1,
                                                 'postfix' : FilePostfix }
            TopRight = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix' : FilePrefix,
                                                 'X' : X2,
                                                 'Y' : Y1,
                                                 'postfix' : FilePostfix }
            BottomLeft = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix' : FilePrefix,
                                                 'X' : X1,
                                                 'Y' : Y2,
                                                 'postfix' : FilePostfix }
            BottomRight = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix' : FilePrefix,
                                                 'X' : X2,
                                                 'Y' : Y2,
                                                 'postfix' : FilePostfix }


            TopLeft = os.path.join(SourcePath, TopLeft)
            TopRight = os.path.join(SourcePath, TopRight)
            BottomLeft = os.path.join(SourcePath, BottomLeft)
            BottomRight = os.path.join(SourcePath, BottomRight)

            nullCount = 0

            if(os.path.exists(TopLeft) == False):
                TopLeft = 'null:'
                nullCount = nullCount + 1
            if(os.path.exists(TopRight) == False):
                TopRight = 'null:'
                nullCount = nullCount + 1
            if(os.path.exists(BottomLeft) == False):
                BottomLeft = 'null:'
                nullCount = nullCount + 1
            if(os.path.exists(BottomRight) == False):
                BottomRight = 'null:'
                nullCount = nullCount + 1

            if(nullCount == 4):
                continue

            # Complicated ImageMagick call reads in up to four adjacent tiles, merges them, and shrinks
            # BUG this assumes we only downsample by a factor of two
            cmd = ("montage " + TopLeft + ' ' + TopRight + ' ' + 
                  BottomLeft + ' ' + BottomRight + 
                  ' -geometry %dx%d' % (TileDim[1] / 2, TileDim[0] / 2)
                  + ' -set colorspace RGB  -mode Concatenate -tile 2x2 -background black '
                  + ' -depth 8 -type Grayscale -define png:format=png8 ' + OutputFileFullPath)
            # prettyoutput.CurseString('Cmd', cmd)
            # prettyoutput.Log(
            # TestOutputFileFullPath = os.path.join(NextLevelNode.FullPath, 'Test_' + OutputFile)

            montageBugFixCmd = 'convert ' + OutputFileFullPath + ' -set colorspace RGB -type Grayscale ' + OutputFileFullPath

            task = Pool.add_process(cmd, cmd + " && " + montageBugFixCmd + " && exit", shell=True)

            if FirstTaskForRow is None:
                FirstTaskForRow = task


        # TaskString = "Building tiles for downsample %g" % NextLevelNode.Downsample
        # prettyoutput.CurseProgress(TaskString, iY + 1, newYDim)

        # We can easily saturate the pool with hundreds of thousands of tasks.
        # If the pool has a reasonable number of tasks then we should wait for
        # a task from a row to complete before queueing more.
        if hasattr(Pool, 'tasks'):
            if Pool.tasks.qsize() > 256:
                FirstTaskForRow.wait()
                FirstTaskForRow = None
        elif hasattr(Pool, 'ActiveTasks'):
            if Pool.ActiveTasks > 512:
                FirstTaskForRow.wait()
                FirstTaskForRow = None

        prettyoutput.Log("\nBeginning Row %d of %d" % (iY + 1, DestGridDimensions[0]))

    if not Pool is None:
        Pool.wait_completion()
    

# OK, now build/check the remaining levels of the tile pyramids
def BuildTilesetPyramid(TileSetNode, Pool=None, **kwargs):
    '''@TileSetNode'''
    
    MinResolutionLevel = TileSetNode.MinResLevel

    while not MinResolutionLevel is None:
        # If the tileset is already a single tile, then do not downsample
        if(MinResolutionLevel.GridDimX == 1 and MinResolutionLevel.GridDimY == 1):
            return

        ShrinkFactor = 0.5
        newYDim = float(MinResolutionLevel.GridDimY) * ShrinkFactor
        newXDim = float(MinResolutionLevel.GridDimX) * ShrinkFactor
    
        newXDim = int(math.ceil(newXDim))
        newYDim = int(math.ceil(newYDim))
    
        # If there is only one tile in the next level, try to find a thumbnail image and change the downsample level
        if(newXDim == 1 and newYDim == 1):
            return
    
        # Need to call ir-assemble
        NextLevelNode = nb.VolumeManager.LevelNode(MinResolutionLevel.Downsample * 2)
        [added, NextLevelNode] = TileSetNode.UpdateOrAddChildByAttrib(NextLevelNode, 'Downsample')
        NextLevelNode.GridDimX = str(newXDim)
        NextLevelNode.GridDimY = str(newYDim)
        if added:
            yield TileSetNode
    
        # Check to make sure the level hasn't already been generated and we've just missed the
        [Valid, Reason] = NextLevelNode.IsValid()
        if not Valid:
            # XMLOutput = os.path.join(NextLevelNode, os.path.basename(XmlFilePath))
            BuildTilesetLevel(MinResolutionLevel.FullPath, NextLevelNode.FullPath, 
                               DestGridDimensions=(newYDim, newXDim),
                               TileDim=(TileSetNode.TileYDim, TileSetNode.TileXDim),
                               FilePrefix=TileSetNode.FilePrefix,
                               FilePostfix=TileSetNode.FilePostfix,
                               Pool=Pool)
            # This was a lot of work, make sure it is saved before queueing the next level
            yield TileSetNode
            prettyoutput.Log("\nTileset level %d completed" % NextLevelNode.Downsample)
        else:
            logging.info("Level was already generated " + str(TileSetNode))
    
        MinResolutionLevel = TileSetNode.MinResLevel

if __name__ == "__main__":

    TestImageDir = 'D:/BuildScript/Test/Images'
    Pool = Pools.GetGlobalProcessPool()

    BadTestImage = os.path.join(TestImageDir, 'Bad101.png')
    BadTestImageOut = os.path.join(TestImageDir, 'Bad101Shrink.png')

    task = Shrink(Pool, BadTestImage, BadTestImageOut, 0.5)
    print 'Bad image return value: ' + str(task.returncode)
    Pool.wait_completion()

    GoodTestImage = os.path.join(TestImageDir, '400.png')
    GoodTestImageOut = os.path.join(TestImageDir, '400Shrink.png')

    task = Shrink(Pool, GoodTestImage, GoodTestImageOut, 0.5)
    Pool.wait_completion()
    print 'Good image return value: ' + str(task.returncode)
