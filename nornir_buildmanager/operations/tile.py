"""
Created on May 22, 2012

@author: Jamesan
"""

import concurrent.futures
import datetime
import glob
import math
import multiprocessing
import shutil
import subprocess
import tempfile

import numpy
from numpy.typing import NDArray

import nornir_buildmanager as nb
from nornir_buildmanager.exceptions import NornirUserException
import nornir_buildmanager.templates
from nornir_buildmanager.validation import image, transforms
from nornir_buildmanager.volumemanager import *
import nornir_imageregistration
from nornir_imageregistration import tileset_functions
from nornir_imageregistration.files import mosaicfile
import nornir_imageregistration.spatial as spatial
import nornir_imageregistration.tileset as tiles
import nornir_imageregistration.tileset_functions
from nornir_imageregistration.transforms import *
import nornir_pools
from nornir_shared import prettyoutput
import nornir_shared.files
from nornir_shared.files import OutdatedFile, RemoveInvalidImageFile, RemoveOutdatedFile
from nornir_shared.histogram import Histogram
import nornir_shared.images
import nornir_shared.misc
import nornir_shared.plot

HistogramTagStr = "HistogramData"

ContrastMinCutoffDefault = 0.1
ContrastMaxCutoffDefault = 0.5

DefaultImageExtension = '.png'

'''
The maximum size of a temporary image we should be comfortable allocating if 
all computer cores are allocating temporary images.
'''
estimated_max_temp_image_area = None


def EstimateMaxTempImageArea() -> int:
    global estimated_max_temp_image_area

    if estimated_max_temp_image_area is None:
        try:
            import psutil
        except ImportError:
            prettyoutput.LogErr(
                "Could not import psutil to estimate available memory.  Set -MaxWorkingImageArea manually or run 'pip install psutil'")
            return 1 << 30  # Try a gigabyte of memory

        memory_data = psutil.virtual_memory()
        bytes_per_pixel = int(2)  # We use float16 for each pixel
        num_images_per_tile = int(2)  # The assembled image and the distance image
        num_duplicate_copies_in_memory = int(3)
        # At most we need the raw tile (and distance image), the assembled individual tile (and distance image), and the full composite image (and distance image) we are building
        safety_factor = int(2)
        estimated_max_temp_image_area = (memory_data.available / (
                bytes_per_pixel * num_images_per_tile * num_duplicate_copies_in_memory * safety_factor))
        prettyoutput.Log("Maximum per-core temporary image size calculated a {0:g}MB limit.".format(
            float(estimated_max_temp_image_area) / float(1 << 20)))

    return estimated_max_temp_image_area


def VerifyImages(TilePyramidNode: TilePyramidNode, **kwargs):
    """Eliminate any image files which cannot be parsed by Image Magick's identify command"""
    PyramidLevels = nornir_shared.misc.SortedListFromDelimited(kwargs.get('Levels', [1, 2, 4, 8, 16, 32, 64, 128, 256]))

    Levels = TilePyramidNode.Levels
    LNodeSaveList = []
    for LNode in Levels:
        Downsample = int(LNode.attrib.get('Downsample', None))
        if Downsample not in PyramidLevels:
            continue

        LNode = VerifyTiles(LevelNode=LNode)
        if LNode is not None:
            LNodeSaveList.append(LNode)

    # Save the channelNode if a level node was changed
    if len(LNodeSaveList) > 0:
        return TilePyramidNode

    return None


def VerifyTiles(LevelNode: LevelNode | None = None, **kwargs) -> tuple[bool, list[str]]:
    """ @LevelNode
    Eliminate any image files which cannot be parsed by Image Magick's identify command.
    This function is going to do the work regardless of whether meta-data in the
    tile pyramid indicates it should and will update the level node with the results

    :return: A tuple indicating:
             1. bool - Does the level contains images?
             2. list - Which tiles were found to be invalid?
    :rtype: (bool, list)

    """
    logger = logging.getLogger(__name__ + '.VerifyTiles')

    InputLevelNode = LevelNode
    InputPyramidNode = InputLevelNode.FindParent('TilePyramid')
    TileExt = InputPyramidNode.attrib.get('ImageFormatExt', '.png')

    TileImageDir = InputLevelNode.FullPath
    LevelFiles = glob.glob(os.path.join(TileImageDir, '*' + TileExt))

    if len(LevelFiles) == 0:
        InputLevelNode.TilesValidated = 0
        try:
            InputLevelNode.ValidationTime = datetime.datetime.utcfromtimestamp(os.stat(LevelNode.FullPath).st_mtime)
        except FileNotFoundError:
            InputLevelNode.ValidationTime = None

        logger.info('No tiles found in level {0}'.format(LevelNode.FullPath))
        return False, []
    #
    #     if TilesValidated == len(LevelFiles):
    #         logger.warning('Tiles in level {0} already validated, but we had to check because # validated tiles is not equal to the expected number'.format(LevelNode.FullPath))
    #         return None

    InvalidTiles = nornir_shared.images.AreValidImages(LevelFiles, TileImageDir)

    for InvalidTile in InvalidTiles:
        InvalidTilePath = os.path.join(TileImageDir, InvalidTile)
        try:
            prettyoutput.LogErr('*** Deleting invalid tile: ' + InvalidTilePath)
            logger.warning('*** Deleting invalid tile: ' + InvalidTilePath)
            os.remove(InvalidTilePath)
        except FileNotFoundError:  # It is OK if the file is missing, that was our goal
            pass

    if len(InvalidTiles) == 0:
        logger.info('Tiles all valid {0}'.format(LevelNode.FullPath))

    # We can't just save the directory modified time because it will change when the VolumeData.xml is saved

    InputLevelNode.ValidationTime = LevelNode.LastFileSystemModificationTime
    InputLevelNode.TilesValidated = len(LevelFiles) - len(InvalidTiles)

    if InputLevelNode.ElementHasChangesToSave:
        InputLevelNode.Save()
    return True, InvalidTiles


def FilterIsPopulated(InputFilterNode, Downsample, MosaicFullPath, OutputFilterName) -> bool:
    """
    :return: True if the filter has all the tiles the mosaic file indicates it should have at the provided downsample level
    """
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
    if mFile is None:
        raise Exception("Unable to load mosaic file: %s" % MosaicFullPath)

    if OutputPyramidNode.NumberOfTiles < mFile.NumberOfImages:
        return False

    OutputLevelNode = OutputFilterNode.TilePyramid.GetLevel(Downsample)
    if OutputLevelNode is None:
        return False

    # No need to check if OutputLevelNode.FullPath exists, because glob will return
    # empty list if it does not
    # if not os.path.exists(OutputLevelNode.FullPath):
    #    return False

    # Find out if the number of predicted images matches the number of actual images
    ImageFiles = glob.iglob(OutputLevelNode.FullPath + os.sep + '*' + InputPyramidNode.ImageFormatExt)
    fileSystemImageFiles = frozenset(map(os.path.basename, ImageFiles))
    transformImageFiles = frozenset(mFile.ImageToTransformString.keys())

    missingTransformFiles = transformImageFiles - fileSystemImageFiles
    for i in missingTransformFiles:
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


def Evaluate(Parameters, FilterNode, OutputImageName=None, Level=1, PreEvaluateSequenceArg=None,
             EvaluateSequenceArg=None, PostEvaluateSequenceArg=None, **kwargs):
    PyramidNode = FilterNode.find('TilePyramid')
    assert (PyramidNode is not None)
    levelNode = PyramidNode.GetChildByAttrib('Level', 'Downsample', Level)
    assert (levelNode is not None)

    if PreEvaluateSequenceArg is None:
        PreEvaluateSequenceArg = ''

    if EvaluateSequenceArg is None:
        EvaluateSequenceArg = ''

    if OutputImageName is None:
        OutputImageName = EvaluateSequenceArg

    assert (OutputImageName is not None)

    FinalTargetPath = OutputImageName + PyramidNode.ImageFormatExt
    PreFinalTargetPath = 'Pre-' + OutputImageName + PyramidNode.ImageFormatExt

    PreFinalTargetFullPath = os.path.join(FilterNode.FullPath, PreFinalTargetPath)

    OutputImageNode = FilterNode.GetChildByAttrib('Image', 'Name', OutputImageName)
    if OutputImageNode is not None:
        cleaned, reason = OutputImageNode.CleanIfInvalid()
        if cleaned:
            OutputImageNode = None

    # Find out if the output image node exists already
    OutputImageNode = nornir_buildmanager.volumemanager.ImageNode.Create(Path=FinalTargetPath,
                                                                         attrib={'Name': OutputImageName})
    (ImageNodeCreated, OutputImageNode) = FilterNode.UpdateOrAddChildByAttrib(OutputImageNode, 'Name')

    prettyoutput.CurseString('Stage', FilterNode.Name + " ImageMagick -Evaluate-Sequence " + EvaluateSequenceArg)

    CmdTemplate = "convert %(Images)s %(PreEvaluateSequenceArg)s -evaluate-sequence %(EvaluateSequenceArg)s %(OutputFile)s"

    TileFullPath = os.path.join(levelNode.FullPath, '*' + PyramidNode.ImageFormatExt)

    Cmd = CmdTemplate % {'Images': TileFullPath,
                         'PreEvaluateSequenceArg': PreEvaluateSequenceArg,
                         'EvaluateSequenceArg': EvaluateSequenceArg,
                         'OutputFile': PreFinalTargetFullPath}

    prettyoutput.Log(Cmd)
    subprocess.call(Cmd + " && exit", shell=True)

    if PostEvaluateSequenceArg is not None:
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
    """Creates an image from the source image whose min pixel value is zero"""

    ParentNode = ImageNode.Parent
    OutputFile = OutputImageName + ".png"

    # Find out if the output image node exists already
    OutputImageNode = nornir_buildmanager.volumemanager.ImageNode.Create(Path=OutputFile,
                                                                         attrib={'Name': OutputImageName})
    (ImageNodeCreated, OutputImageNode) = ParentNode.UpdateOrAddChildByAttrib(OutputImageNode, 'Name')

    nornir_shared.files.RemoveOutdatedFile(ImageNode.FullPath, OutputImageNode.FullPath)

    if os.path.exists(OutputImageNode.FullPath):
        return OutputImageNode

    [Min, Mean, Max, StdDev] = nornir_shared.images.GetImageStats(ImageNode.FullPath)

    # Temp file with a uniform value set to the minimum pixel value of ImageNode
    OutputFileUniformFullPath = os.path.join(ParentNode.FullPath, 'UniformMinBackground_' + OutputFile)
    CreateBackgroundCmdTemplate = 'convert %(OperatorImage)s  +matte -background "gray(%(BackgroundIntensity)f)" -compose Dst -flatten %(OutputFile)s'
    CreateBackgroundCmd = CreateBackgroundCmdTemplate % {'OperatorImage': ImageNode.FullPath,
                                                         'BackgroundIntensity': float(Min / 256.0),
                                                         # TODO This only works for 8-bit
                                                         'OutputFile': OutputFileUniformFullPath}
    prettyoutput.Log(CreateBackgroundCmd)
    subprocess.call(CreateBackgroundCmd + " && exit", shell=True)

    # Create the zerod image
    CmdBase = "convert %(OperatorImage)s %(InputFile)s %(InvertOperator)s -compose %(ComposeOperator)s -composite %(OutputFile)s"
    CreateZeroedImageCmd = CmdBase % {'OperatorImage': OutputFileUniformFullPath,
                                      'InputFile': ImageNode.FullPath,
                                      'InvertOperator': '',
                                      'ComposeOperator': 'minus_Dst',
                                      'OutputFile': OutputImageNode.FullPath}

    prettyoutput.Log(CreateZeroedImageCmd)
    subprocess.call(CreateZeroedImageCmd + " && exit", shell=True)

    return OutputImageNode


def CorrectTiles(Parameters, CorrectionType, FilterNode=None, OutputFilterName=None, **kwargs):
    """Create a corrected version of a filter by applying the operation/image to all tiles"""

    correctionType = None
    if CorrectionType.lower() == 'brightfield':
        correctionType = tiles.ShadeCorrectionTypes.BRIGHTFIELD
    elif CorrectionType.lower() == 'darkfield':
        correctionType = tiles.ShadeCorrectionTypes.DARKFIELD

    assert (FilterNode is not None)
    InputPyramidNode = FilterNode.find('TilePyramid')
    assert (InputPyramidNode is not None)

    InputLevelNode = InputPyramidNode.MaxResLevel
    assert (InputLevelNode is not None)

    FilterParent = FilterNode.Parent

    SaveFilterParent = False

    # Find out if the output filter already exists
    [SaveFilterParent, OutputFilterNode] = FilterParent.UpdateOrAddChildByAttrib(
        nornir_buildmanager.volumemanager.FilterNode.Create(OutputFilterName, OutputFilterName))

    if SaveFilterParent:
        OutputFilterNode.BitsPerPixel = FilterNode.BitsPerPixel

    # Check if the output node exists
    OutputPyramidNode = nornir_buildmanager.volumemanager.TilePyramidNode.Create(Type=InputPyramidNode.Type,
                                                                                 NumberOfTiles=InputPyramidNode.NumberOfTiles,
                                                                                 LevelFormat=InputPyramidNode.LevelFormat,
                                                                                 ImageFormatExt=InputPyramidNode.ImageFormatExt)

    [added, OutputPyramidNode] = OutputFilterNode.UpdateOrAddChildByAttrib(OutputPyramidNode, 'Path')

    OutputLevelNode = nornir_buildmanager.volumemanager.LevelNode.Create(Level=InputLevelNode.Downsample)
    [OutputLevelAdded, OutputLevelNode] = OutputPyramidNode.UpdateOrAddChildByAttrib(OutputLevelNode, 'Downsample')

    # Make sure the destination directory exists
    correctionImage = None
    os.makedirs(OutputLevelNode.FullPath, exist_ok=True)

    OutputImageNode = nornir_buildmanager.volumemanager.ImageNode.Create(Path='Correction.png',
                                                                         attrib={'Name': 'ShadeCorrection'})
    (ImageNodeCreated, OutputImageNode) = FilterNode.UpdateOrAddChildByAttrib(OutputImageNode, 'Name')

    InputTiles = glob.glob(os.path.join(InputLevelNode.FullPath, '*' + InputPyramidNode.ImageFormatExt))

    if not os.path.exists(OutputImageNode.FullPath):
        correctionImage = tiles.CalculateShadeImage(InputTiles, correction_type=correctionType)
        nornir_imageregistration.SaveImage(OutputImageNode.FullPath, correctionImage, bpp=OutputFilterNode.BitsPerPixel)
    else:
        correctionImage = nornir_imageregistration.LoadImage(OutputImageNode.FullPath)

    tiles.ShadeCorrect(InputTiles, correctionImage, OutputLevelNode.FullPath, correction_type=correctionType,
                       bpp=OutputFilterNode.BitsPerPixel)
    if SaveFilterParent:
        return FilterParent

    return FilterNode


def _CorrectTilesDeprecated(Parameters, FilterNode=None, ImageNode=None, OutputFilterName=None, InvertSource=False,
                            ComposeOperator=None, **kwargs):
    """Create a corrected version of a filter by applying the operation/image to all tiles"""

    assert (FilterNode is not None)
    InputPyramidNode = FilterNode.find('TilePyramid')
    assert (InputPyramidNode is not None)

    InputLevelNode = InputPyramidNode.MaxResLevel
    assert (InputLevelNode is not None)

    assert (ImageNode is not None)

    if ComposeOperator is None:
        ComposeOperator = 'minus'

    InvertOperator = ''
    if InvertSource is not None:
        InvertOperator = '-negate'

    FilterParent = FilterNode.Parent

    SaveFilterParent = False

    # Find out if the output filter already exists
    [SaveFilterParent, OutputFilterNode] = FilterParent.UpdateOrAddChildByAttrib(
        nornir_buildmanager.volumemanager.FilterNode.Create(OutputFilterName, OutputFilterName))
    OutputFilterNode.BitsPerPixel = FilterNode.BitsPerPixel

    # Check if the output node exists
    OutputPyramidNode = nornir_buildmanager.volumemanager.TilePyramidNode.Create(Type=InputPyramidNode.Type,
                                                                                 NumberOfTiles=InputPyramidNode.NumberOfTiles,
                                                                                 LevelFormat=InputPyramidNode.LevelFormat,
                                                                                 ImageFormatExt=InputPyramidNode.ImageFormatExt)

    [added, OutputPyramidNode] = OutputFilterNode.UpdateOrAddChildByAttrib(OutputPyramidNode, 'Path')

    OutputLevelNode = nornir_buildmanager.volumemanager.LevelNode.Create(Level=InputLevelNode.Downsample)
    [OutputLevelAdded, OutputLevelNode] = OutputPyramidNode.UpdateOrAddChildByAttrib(OutputLevelNode, 'Downsample')

    # Make sure the destination directory exists
    os.makedirs(OutputLevelNode.FullPath, exist_ok=True)

    CmdTemplate = "convert %(OperatorImage)s %(InputFile)s %(InvertOperator)s -compose %(ComposeOperator)s -composite %(OutputFile)s"

    InputTiles = glob.glob(os.path.join(InputLevelNode.FullPath, '*' + InputPyramidNode.ImageFormatExt))

    ZeroedImageNode = _CreateMinCorrectionImage(ImageNode, 'Zeroed' + ImageNode.Name)

    Pool = nornir_pools.GetGlobalProcessPool()

    for InputTileFullPath in InputTiles:
        inputTile = os.path.basename(InputTileFullPath)
        OutputTileFullPath = os.path.join(OutputLevelNode.FullPath, inputTile)

        RemoveOutdatedFile(InputTileFullPath, OutputTileFullPath)

        if os.path.exists(OutputTileFullPath):
            continue

        Cmd = CmdTemplate % {'OperatorImage': ZeroedImageNode.FullPath,
                             'InputFile': InputTileFullPath,
                             'InvertOperator': InvertOperator,
                             'ComposeOperator': ComposeOperator,
                             'OutputFile': OutputTileFullPath}
        prettyoutput.Log(Cmd)
        Pool.add_process(inputTile, Cmd + " && exit", shell=True)

    Pool.wait_completion()

    if SaveFilterParent:
        return FilterParent

    return FilterNode


def TranslateToZeroOrigin(ChannelNode, TransformNode, OutputTransform, Logger, **kwargs):
    """ @ChannelNode  """

    outputFilename = OutputTransform + ".mosaic"
    outputFileFullPath = os.path.join(os.path.dirname(TransformNode.FullPath), outputFilename)

    OutputTransformNode = TransformNode.Parent.GetChildByAttrib('Transform', 'Path', outputFilename)
    if not OutputTransformNode.CleanIfInputTransformMismatched(TransformNode):
        return None

    if os.path.exists(outputFileFullPath) and (OutputTransformNode is not None):
        return None

    prettyoutput.Log("Moving origin to 0,0 - " + TransformNode.FullPath)

    mosaic = mosaicfile.MosaicFile.Load(TransformNode.FullPath)

    # Find the min,max values from all the transforms
    minX = float('Inf')
    minY = float('Inf')
    maxX = -float('Inf')
    maxY = -float('Inf')

    Transforms = {}
    for imagename, transform in list(mosaic.ImageToTransformString.items()):
        MosaicToSectionTransform = factory.LoadTransform(transform)
        Transforms[imagename] = MosaicToSectionTransform
        bbox = MosaicToSectionTransform.FixedBoundingBox.ToArray()

        minX = min(minX, bbox[spatial.iRect.MinX])
        minY = min(minY, bbox[spatial.iRect.MinY])
        maxX = max(maxX, bbox[spatial.iRect.MaxX])
        maxY = max(maxY, bbox[spatial.iRect.MaxY])

    if OutputTransformNode is None:
        # OutputTransformNode = copy.deepcopy(TransformNode)
        OutputTransformNode = TransformNode.Copy()
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

    # Adjust all the control points such that the origin is at 0,0
    for imagename in list(Transforms.keys()):
        transform = Transforms[imagename]
        transform.TranslateFixed((-minY, -minX))
        mosaic.ImageToTransformString[imagename] = factory.TransformToIRToolsString(transform)

    mosaic.Save(OutputTransformNode.FullPath)
    # This is a minor optimization to save the trouble of reloading the transform
    # from disk... I left it but it could be replaced by ResetChecksum.
    OutputTransformNode.attrib['Checksum'] = mosaic.Checksum
    OutputTransformNode._AttributesChanged = True

    return ChannelNode


def CutoffValuesForHistogram(HistogramElement, MinCutoffPercent, MaxCutoffPercent, Gamma, Bpp=8):
    """Returns the (Min, Max, Gamma) values for a histogram.  If an AutoLevelHint node is available those values are used.

    :param HistogramElement HistogramElement: Histogram data node
    :param float MinCutoffPercent: Percent of low intensity pixels to remove
    :param float MaxCutoffPercent: Percent of high intensity pixels to remove
    :param float Gamma: Desired Gamma value
    :param int Bpp: Bits per pixel
    :return: (Min, Max, Gamma) Contrast values based on passed values and user overrides in HistogramElement
    :rtype: tuple
    """

    AutoLevelDataNode = HistogramElement.GetOrCreateAutoLevelHint()
    MinIntensityCutoff = AutoLevelDataNode.UserRequestedMinIntensityCutoff
    MaxIntensityCutoff = AutoLevelDataNode.UserRequestedMaxIntensityCutoff
    UserRequestedGamma = AutoLevelDataNode.UserRequestedGamma

    if UserRequestedGamma is not None:
        Gamma = UserRequestedGamma

    if isinstance(Gamma, str):
        if Gamma == 'None':
            Gamma = None

    if Gamma is not None:
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
            prettyoutput.LogErr(
                "*** No histogram data found to create filter with: " + HistogramElement.DataFullPath + "***")
            raise Exception(
                "*** No histogram data found to create filter with: " + HistogramElement.DataFullPath + "***")

        if MinIntensityCutoff is None or MaxIntensityCutoff is None:
            [CalculatedMinCutoff, CalculatedMaxCutoff] = histogram.AutoLevel(MinCutoffPercent, MaxCutoffPercent)

            if MinIntensityCutoff is None:
                MinIntensityCutoff = int(math.floor(CalculatedMinCutoff))
            if MaxIntensityCutoff is None:
                MaxIntensityCutoff = int(math.ceil(CalculatedMaxCutoff))

        if MinIntensityCutoff > MaxIntensityCutoff:
            raise nb.NornirUserException(
                "%g > %g Max intensity is less than min intensity for histogram correction. %s" % (
                    MinIntensityCutoff, MaxIntensityCutoff, HistogramElement.DataFullPath))

        if Gamma is None:
            # We look for the largest peak that is not at either extrema
            # peakVal = histogram.PeakValue(MinIntensityCutoff + 1, MaxIntensityCutoff - 1)
            peakVal = histogram.Mean(MinIntensityCutoff + 1, MaxIntensityCutoff - 1)
            if peakVal is None:
                Gamma = 1.0
            else:
                Gamma = histogram.GammaAtValue(peakVal, minVal=MinIntensityCutoff, maxVal=MaxIntensityCutoff)

    return MinIntensityCutoff, MaxIntensityCutoff, Gamma


def _ClearInvalidHistogramElements(filterObj: FilterNode, transform_node: TransformNode, checksum: str) -> (
bool, HistogramNode):
    """I believe this is complicated due to legacy.  In the past multiple
       histogram nodes would accumulate.  This function deletes the excess nodes
       and returns the valid one."""
    histogram_element = None
    HistogramElementRemoved = False
    check_type = 'Thr' in transform_node.Type  # If a threshold value is encoded in the transform type, ensure it matches
    for histogram_element in filterObj.findall(
            "Histogram[@InputTransformChecksum='" + checksum + "']"):  # type: HistogramNode | None
        if histogram_element is None:
            raise NornirUserException(
                "Missing input histogram in %s.  Did you run the histogram pipeline?" % filterObj.FullPath)
        cleaned, reason = histogram_element.CleanIfInvalid()
        # Check that the type matches in input transform node type so we don't use the wrong histogram
        if cleaned or (check_type and histogram_element.Type != transform_node.Type):
            histogram_element = None
            HistogramElementRemoved = HistogramElementRemoved or cleaned
            continue

    return HistogramElementRemoved, histogram_element


def AutolevelTiles(Parameters, InputFilter: FilterNode, TransformNode: TransformNode, Downsample: float = 1,
                   OutputFilterName: str | None = None, **kwargs):
    """Create a new filter using the histogram of the input filter
       @ChannelNode"""

    [added_level, InputLevelNode] = InputFilter.TilePyramid.GetOrCreateLevel(Downsample)
    InputTransformNode = TransformNode
    InputPyramidNode = InputFilter.TilePyramid

    ChannelNode = InputFilter.Parent  # type: ChannelNode

    if OutputFilterName is None:
        OutputFilterName = 'Leveled'

    (HistogramElementRemoved, HistogramElement) = _ClearInvalidHistogramElements(InputFilter,
                                                                                 TransformNode,
                                                                                 InputTransformNode.Checksum)
    if HistogramElementRemoved:
        (yield InputFilter)

    if HistogramElement is None:
        raise nb.NornirUserException("No histograms available for auto-leveling of section: %s" % InputFilter.FullPath)

    MinCutoffPercent = float(Parameters.get('MinCutoff', ContrastMinCutoffDefault)) / 100.0
    MaxCutoffPercent = float(Parameters.get('MaxCutoff', ContrastMaxCutoffDefault)) / 100.0
    Gamma = Parameters.get('Gamma', None)
    OutputBpp = kwargs.get(
        'OutputBpp')  # None is passed if the user does not specify a value, a default value will never be read

    if OutputBpp is None:
        OutputBpp = InputFilter.BitsPerPixel

    (MinIntensityCutoff, MaxIntensityCutoff, Gamma) = CutoffValuesForHistogram(HistogramElement, MinCutoffPercent,
                                                                               MaxCutoffPercent, Gamma,
                                                                               Bpp=InputFilter.BitsPerPixel)

    # If the output filter already exists, find out if the user has specified the min and max pixel values explicitely.
    (saveFilter, OutputFilterNode) = ChannelNode.GetOrCreateFilter(OutputFilterName)
    if saveFilter:
        OutputFilterNode.BitsPerPixel = OutputBpp
        yield ChannelNode

    EntireTilePyramidNeedsBuilding = OutputFilterNode is None

    if OutputFilterNode is not None:

        FilterPopulated = FilterIsPopulated(InputFilter, InputLevelNode.Downsample, InputTransformNode.FullPath,
                                            OutputFilterName)
        # Check that the existing filter is valid
        if OutputFilterNode.Locked and FilterPopulated:
            prettyoutput.Log("Skipping contrast on existing locked filter %s" % OutputFilterNode.FullPath)
            return

        (yield GenerateHistogramImage(HistogramElement, MinIntensityCutoff, MaxIntensityCutoff, Gamma=Gamma,
                                      Async=True))

        if ChannelNode.RemoveFilterOnContrastMismatch(OutputFilterName, MinIntensityCutoff, MaxIntensityCutoff,
                                                      Gamma) or \
                ChannelNode.RemoveFilterOnBppMismatch(OutputFilterName, OutputBpp):

            EntireTilePyramidNeedsBuilding = True
            (yield ChannelNode.Parent)

            (added_filter, OutputFilterNode) = ChannelNode.GetOrCreateFilter(OutputFilterName)
            OutputFilterNode.SetContrastValues(MinIntensityCutoff, MaxIntensityCutoff, Gamma)
            OutputFilterNode.BitsPerPixel = OutputBpp
            (yield ChannelNode)
        elif FilterPopulated:
            # Ensure that the filter is populated with current images
            if nornir_buildmanager.operations.filter.RemoveTilePyramidIfOutdated(InputFilter, OutputFilterNode):
                EntireTilePyramidNeedsBuilding = True
                (yield OutputFilterNode)
            else:
                return
    else:
        (yield GenerateHistogramImage(HistogramElement, MinIntensityCutoff, MaxIntensityCutoff, Gamma=Gamma,
                                      Async=True))

    # TODO: Verify parameters match... if(OutputFilterNode.Gamma != Gamma)
    #     DictAttributes = {'BitsPerPixel' : 8,
    #                         'MinIntensityCutoff' : str(MinIntensityCutoff),
    #                         'MaxIntensityCutoff' : str(MaxIntensityCutoff),
    #                         'Gamma' : str(Gamma),
    #                         'HistogramChecksum' : str(HistogramElement.Checksum)}

    (filter_created, OutputFilterNode) = ChannelNode.GetOrCreateFilter(OutputFilterName)
    os.makedirs(OutputFilterNode.FullPath, exist_ok=True)

    OutputFilterNode.BitsPerPixel = OutputBpp
    OutputFilterNode.HistogramChecksum = str(HistogramElement.Checksum)
    OutputFilterNode.SetContrastValues(MinIntensityCutoff, MaxIntensityCutoff, Gamma)

    Input = mosaicfile.MosaicFile.Load(InputTransformNode.FullPath)
    ImageFiles = sorted(Input.ImageToTransformString.keys())

    InputImagePath = InputLevelNode.FullPath

    OutputPyramidNode = nornir_buildmanager.volumemanager.TilePyramidNode.Create(
        NumberOfTiles=InputPyramidNode.NumberOfTiles,
        LevelFormat=InputPyramidNode.LevelFormat,
        ImageFormatExt=InputPyramidNode.ImageFormatExt)
    [pyramid_created, OutputPyramidNode] = OutputFilterNode.UpdateOrAddChildByAttrib(OutputPyramidNode, 'Path')

    OutputLevelNode = nornir_buildmanager.volumemanager.LevelNode.Create(Level=InputLevelNode.Downsample)
    [level_created, OutputLevelNode] = OutputPyramidNode.UpdateOrAddChildByAttrib(OutputLevelNode, 'Downsample')

    OutputImageDir = OutputLevelNode.FullPath

    TilesToBuild = list()

    # Make sure output isn't outdated.

    try:
        # Try to create the output directory.  If we succeed we need to rebuild the entire pyramid. 
        # If the path does exist make sure it is a directory 
        os.makedirs(OutputImageDir)
        EntireTilePyramidNeedsBuilding = True
    except (OSError, WindowsError, FileExistsError):
        if not os.path.isdir(OutputImageDir):
            raise

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

    # Pool = None
    # if len(TilesToBuild) > 0:
    #    Pool = nornir_pools.GetGlobalClusterPool()

    if InputFilter.BitsPerPixel == 8:
        MinIntensityCutoff16bpp = MinIntensityCutoff
        MaxIntensityCutoff16bpp = MaxIntensityCutoff
    else:
        MinIntensityCutoff16bpp = MinIntensityCutoff
        MaxIntensityCutoff16bpp = MaxIntensityCutoff

    # In case the user swaps min/max cutoffs swap these values if needed
    if MaxIntensityCutoff16bpp < MinIntensityCutoff16bpp:
        temp = MaxIntensityCutoff16bpp
        MaxIntensityCutoff16bpp = MinIntensityCutoff16bpp
        MinIntensityCutoff16bpp = temp

    SampleCmdPrinted = False
    TilesToConvert = {}
    for imageFile in TilesToBuild:
        InputImageFullPath = os.path.join(InputLevelNode.FullPath, imageFile)
        ImageSaveFilename = os.path.join(OutputImageDir, os.path.basename(imageFile))

        TilesToConvert[InputImageFullPath] = ImageSaveFilename

    nornir_imageregistration.ConvertImagesInDict(TilesToConvert,
                                                 MinMax=(MinIntensityCutoff16bpp, MaxIntensityCutoff16bpp),
                                                 Gamma=Gamma,
                                                 InputBpp=InputFilter.BitsPerPixel,
                                                 OutputBpp=OutputBpp)
    #
    # #         cmd = 'convert \"' + InputImageFullPath + '\" ' + \
    # #                '-level ' + str(MinIntensityCutoff16bpp) + \
    # #                ',' + str(MaxIntensityCutoff16bpp) + \
    # #                ' -gamma ' + str(Gamma) + \
    # #                ' -colorspace Gray -depth 8 -type optimize ' + \
    # #                ' \"' + ImageSaveFilename + '\"'
    #
    #         cmd_template = 'magick convert %(InputFile)s -level %(min)d,%(max)d -gamma %(gamma)f -colorspace Gray -depth 8 -type optimize %(OutputFile)s'
    #         cmd = cmd_template % {'InputFile': InputImageFullPath,
    #                               'min': MinIntensityCutoff16bpp,
    #                               'max': MaxIntensityCutoff16bpp,
    #                               'gamma': Gamma,
    #                               'OutputFile': ImageSaveFilename};
    #
    #         if not SampleCmdPrinted:
    #             SampleCmdPrinted = True
    #             prettyoutput.CurseString('Cmd', cmd)
    #         Pool.add_process('AutoLevel: ' + cmd, cmd)
    #
    #     if not Pool is None:
    #         Pool.wait_completion()

    OutputPyramidNode.NumberOfTiles = len(ImageFiles)

    # Save the channel node so the new filter is recorded
    (yield ChannelNode)


def InvertFilter(Parameters, InputFilterNode, OutputFilterName, **kwargs):
    """Create a new filter by inverting the input filter
       @ChannelNode"""

    # Find out if the output filter already exists
    [addedOutputFilter, OutputFilterNode] = InputFilterNode.Parent.GetOrCreateFilter(OutputFilterName)
    OutputFilterNode.BitsPerPixel = InputFilterNode.BitsPerPixel
    InputPyramidNode = InputFilterNode.TilePyramid
    InputLevelNode = InputPyramidNode.MaxResLevel

    # Use the highest resolution of the input pyramid as our downsample level
    Downsample = InputLevelNode.Downsample

    if addedOutputFilter:
        yield InputFilterNode.Parent

    [addedOutputTilePyramid, OutputPyramidNode] = OutputFilterNode.GetOrCreateTilePyramid()
    if addedOutputTilePyramid:
        OutputPyramidNode.Type = InputPyramidNode.Type
        OutputPyramidNode.NumberOfTiles = InputPyramidNode.NumberOfTiles
        OutputPyramidNode.LevelFormat = InputPyramidNode.LevelFormat
        OutputPyramidNode.ImageFormatExt = InputPyramidNode.ImageFormatExt
        yield OutputFilterNode

    [addedLevel, OutputLevelNode] = OutputPyramidNode.GetOrCreateLevel(1, False)
    if addedLevel:
        yield OutputPyramidNode

    # Make sure the destination directory exists 
    os.makedirs(OutputLevelNode.FullPath, exist_ok=True)

    InputTiles = glob.glob(os.path.join(InputLevelNode.FullPath, '*' + InputPyramidNode.ImageFormatExt))
    OutputLevelFullPath = OutputLevelNode.FullPath

    pool = nornir_pools.GetGlobalThreadPool()

    tasks = []
    tilesConverted = False
    for InputTileFullPath in InputTiles:
        Basename = os.path.basename(InputTileFullPath)
        OutputTileFullPath = os.path.join(OutputLevelFullPath, Basename)
        t = pool.add_task("Invert {0}".format(InputTileFullPath), nornir_shared.images.InvertImage, InputTileFullPath,
                          OutputTileFullPath)
        tasks.append(t)
        tilesConverted = True

    while len(tasks) > 0:
        t = tasks.pop(0)
        try:
            t.wait()
            tilesConverted = True
        except OSError as e:
            prettyoutput.LogErr("Unable to invert {0}\n{1}".format(t.name, e))
            pass

    if tilesConverted:
        yield InputFilterNode.Parent

    return


def HistogramFilter(Parameters, FilterNode, Downsample, TransformNode, **kwargs):
    """Construct the intensity histogram for a filter
       @FilterNode"""
    NodeToSave = None

    [added_level, LevelNode] = FilterNode.TilePyramid.GetOrCreateLevel(Downsample)

    if TransformNode is None:
        prettyoutput.LogErr("Missing TransformNode attribute on PruneTiles")
        return

    InputMosaicFullPath = TransformNode.FullPath

    MangledName = nornir_shared.misc.GenNameFromDict(Parameters) + TransformNode.Type
    HistogramBaseName = "Histogram" + MangledName
    OutputHistogramXmlFilename = HistogramBaseName + ".xml"

    HistogramElement = nornir_buildmanager.volumemanager.HistogramNode.Create(TransformNode, Type=MangledName,
                                                                              attrib=Parameters)
    [HistogramElementCreated, HistogramElement] = FilterNode.UpdateOrAddChildByAttrib(HistogramElement, "Type")

    ElementCleaned = False

    if not HistogramElementCreated:
        cleaned = HistogramElement.CleanIfInputTransformMismatched(TransformNode)
        if not cleaned:
            cleaned, _ = HistogramElement.CleanIfInvalid()
        if cleaned:
            HistogramElement = None
            ElementCleaned = True

    if HistogramElement is None:
        HistogramElement = nornir_buildmanager.volumemanager.HistogramNode.Create(TransformNode, Type=MangledName,
                                                                                  attrib=Parameters)
        [HistogramElementCreated, HistogramElement] = FilterNode.UpdateOrAddChildByAttrib(HistogramElement, "Type")

    DataNode = nornir_buildmanager.volumemanager.DataNode.Create(OutputHistogramXmlFilename)
    [DataElementCreated, DataNode] = HistogramElement.UpdateOrAddChild(DataNode)

    AutoLevelDataNode = HistogramElement.GetOrCreateAutoLevelHint()

    if os.path.exists(HistogramElement.DataFullPath) and os.path.exists(
            HistogramElement.ImageFullPath) and HistogramElement.InputTransformChecksum == TransformNode.Checksum:
        if HistogramElementCreated or ElementCleaned or DataElementCreated:
            yield FilterNode

    # Check the folder for changes, not the .mosaic file
    # RemoveOutdatedFile(TransformNode.FullPath, OutputHistogramXmlFullPath)
    # RemoveOutdatedFile(TransformNode.FullPath, OutputHistogramPngFilename)

    Bpp = 16
    if FilterNode.BitsPerPixel is not None:
        Bpp = int(FilterNode.BitsPerPixel)

    NumBins = 2 << Bpp
    if NumBins > 2048:
        NumBins = 2048

    ImageCreated = False
    if not os.path.exists(DataNode.FullPath):
        mosaic = mosaicfile.MosaicFile.Load(InputMosaicFullPath)

        FullTilePath = LevelNode.FullPath
        fulltilepaths = list()
        for k in list(mosaic.ImageToTransformString.keys()):
            fulltilepaths.append(os.path.join(FullTilePath, k))

        histogramObj = nornir_imageregistration.Histogram(fulltilepaths, Bpp=Bpp, numBins=NumBins)
        histogramObj.Save(DataNode.FullPath)

        # Create a data node for the histogram
        # DataObj = VolumeManager.DataNode.Create(Path=)

        ImageCreated = (GenerateHistogramImageFromPercentage(HistogramElement) is not None)

    HistogramElement.InputTransformChecksum = TransformNode.Checksum

    if ElementCleaned or HistogramElementCreated or DataElementCreated or ImageCreated or added_level:
        yield FilterNode
    else:
        return


def GenerateHistogramImageFromPercentage(HistogramElement, MinCutoffPercent=None, MaxCutoffPercent=None, Gamma=1):
    if MinCutoffPercent is None:
        MinCutoffPercent = ContrastMinCutoffDefault / 100.0
    if MaxCutoffPercent is None:
        MaxCutoffPercent = ContrastMaxCutoffDefault / 100.0

    LineColors = ['green', 'green']
    AutoLevelDataNode = HistogramElement.GetOrCreateAutoLevelHint()

    if AutoLevelDataNode.UserRequestedMinIntensityCutoff is not None:
        MinCutoffLine = float(AutoLevelDataNode.UserRequestedMinIntensityCutoff)
        MinValue = MinCutoffLine
        LineColors[0] = 'red'

    if AutoLevelDataNode.UserRequestedMaxIntensityCutoff is not None:
        MaxCutoffLine = float(AutoLevelDataNode.UserRequestedMaxIntensityCutoff)
        MaxValue = MaxCutoffLine
        LineColors[1] = 'red'

    (MinValue, MaxValue, Gamma) = CutoffValuesForHistogram(HistogramElement, MinCutoffPercent, MaxCutoffPercent, Gamma)
    return GenerateHistogramImage(HistogramElement, MinValue, MaxValue, Gamma, LineColors=LineColors)


def GenerateHistogramImage(HistogramElement: HistogramNode,
                           MinValue: float, MaxValue: float, Gamma: float, LineColors=None,
                           Async: bool = True):
    """
    :param MaxValue:
    :param Async:
    :param object HistogramElement: Histogram element to pull histogram data from
    :param float MinValue: Minimum value line
    :param float MinValue: Maximum value line
    :param float Gamma: Gamma value for use in title
    :param list LineColors: List of color strings to assign to MinValue and MaxValue lines
    """
    added = False
    HistogramImage = HistogramElement.find('Image')

    if HistogramImage is not None:
        HistogramImage = transforms.RemoveOnMismatch(HistogramImage, 'MinIntensityCutoff', MinValue)
        HistogramImage = transforms.RemoveOnMismatch(HistogramImage, 'MaxIntensityCutoff', MaxValue)
        HistogramImage = transforms.RemoveOnMismatch(HistogramImage, 'Gamma', Gamma, Precision=3)

    if HistogramImage is None:
        OutputHistogramPngFilename = "Histogram" + HistogramElement.Type + ".png"
        HistogramImage = nornir_buildmanager.volumemanager.ImageNode.Create(OutputHistogramPngFilename)
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
        # pool = nornir_pools.GetThreadPool("Histograms")
        # pool.add_task("Create Histogram %s" % DataNode.FullPath, nornir_shared.plot.Histogram, DataNode.FullPath, HistogramImage.FullPath, MinCutoffPercent, MaxCutoffPercent, LinePosList=LinePositions, LineColorList=LineColors, Title=TitleStr)
        # else:
        nornir_shared.plot.Histogram(DataNode.FullPath, HistogramImage.FullPath, MinCutoffPercent, MaxCutoffPercent,
                                     LinePosList=LinePositions, LineColorList=LineColors, Title=TitleStr)

        HistogramImage.MinIntensityCutoff = str(MinValue)
        HistogramImage.MaxIntensityCutoff = str(MaxValue)
        HistogramImage.Gamma = str(Gamma)

        prettyoutput.Log("Done!")

    if added:
        return HistogramElement
    else:
        return None


def AssembleTransform(Parameters, Logger, FilterNode, TransformNode, OutputChannelPrefix=None, UseCluster=True,
                      ThumbnailSize=256, Interlace=True, CropBox=None, **kwargs):
    return AssembleTransformScipy(Parameters, Logger, FilterNode, TransformNode, OutputChannelPrefix,
                                  UseCluster, ThumbnailSize, Interlace, CropBox=CropBox, **kwargs)


def __GetOrCreateOutputChannelForPrefix(prefix, InputChannelNode):
    """If prefix is empty return the input channel.  Otherwise create a new channel with the prefix"""

    OutputName = InputChannelNode.Name

    if prefix is not None and len(prefix) > 0:
        OutputName = prefix + OutputName
    else:
        return False, InputChannelNode

    return InputChannelNode.Parent.UpdateOrAddChildByAttrib(
        nornir_buildmanager.volumemanager.ChannelNode.Create(OutputName, OutputName))


def GetOrCreateCleanedImageNode(imageset_node, transform_node, level, image_name):
    [added_level, image_level_node] = imageset_node.GetOrCreateLevel(level, GenerateData=False)

    os.makedirs(image_level_node.FullPath, exist_ok=True)

    image_node = image_level_node.find('Image')
    # ===========================================================================
    # if not image_node is None:
    #     if image_node.CleanIfInputTransformMismatched(transform_node):
    #         image_node = None
    # ===========================================================================

    if image_node is None:
        image_node = nornir_buildmanager.volumemanager.ImageNode.Create(image_name)
        image_level_node.append(image_node)

    return image_node


def UpdateImageName(imageNode, ExpectedImageName):
    """Rename the image file on disk to match our expected image file name
    :param imageNode:
    """

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

    SourceImageFullPath = imageNode.FullPath
    DestImageFullPath = os.path.join(dirname, ExpectedImageName)

    shutil.move(SourceImageFullPath, DestImageFullPath)
    imageNode.Path = ExpectedImageName

    return True


def AddDotToExtension(Extension=None):
    """Ensure that the extension has a . prefix"""
    if not Extension[0] == '.':
        return '.' + Extension

    return Extension


def GetImageName(SectionNumber, ChannelName, FilterName, Extension=None) -> str:
    Extension = AddDotToExtension(Extension)
    return (nornir_buildmanager.templates.Current.SectionTemplate % int(
        SectionNumber)) + "_" + ChannelName + "_" + FilterName + Extension


def VerifyAssembledImagePathIsCorrect(Parameters, Logger, FilterNode, extension=None, **kwargs):
    """Updates the names of image files, these can be incorrect if the sections are re-ordered"""

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


def AssembleTransformScipy(Parameters, Logger, FilterNode, TransformNode, OutputChannelPrefix=None, UseCluster=True,
                           ThumbnailSize=256, Interlace=True, CropBox=None, **kwargs):
    """@ChannelNode - TransformNode lives under ChannelNode"""

    if CropBox is not None:
        RequestedBoundingBox = [CropBox[1], CropBox[0], CropBox[3], CropBox[2]]
    else:
        RequestedBoundingBox = None

    image_ext = DefaultImageExtension
    InputChannelNode = FilterNode.FindParent('Channel')
    InputFilterMaskName = FilterNode.GetOrCreateMaskName()

    if not FilterNode.HasTilePyramid:
        Logger.warn("Input filter %s has no tile pyramid" % FilterNode.FullPath)
        return

    [AddedChannel, OutputChannelNode] = __GetOrCreateOutputChannelForPrefix(OutputChannelPrefix, InputChannelNode)
    if AddedChannel:
        yield OutputChannelNode.Parent

    AddedFilters = not (
            OutputChannelNode.HasFilter(FilterNode.Name) and OutputChannelNode.HasFilter(InputFilterMaskName))
    (added_input_mask_filter, InputMaskFilterNode) = FilterNode.GetOrCreateMaskFilter()
    (added_output_filter, OutputFilterNode) = OutputChannelNode.GetOrCreateFilter(FilterNode.Name)
    (added_output_mask_filter, OutputMaskFilterNode) = OutputChannelNode.GetOrCreateFilter(InputFilterMaskName)

    if added_output_filter:
        OutputFilterNode.BitsPerPixel = FilterNode.BitsPerPixel

    if added_output_mask_filter:
        OutputMaskFilterNode.BitsPerPixel = 1

    PyramidLevels = nornir_shared.misc.SortedListFromDelimited(kwargs.get('Levels', [1, 2, 4, 8, 16, 32, 64, 128, 256]))

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
    [added_level, ImageLevelNode] = OutputFilterNode.Imageset.GetOrCreateLevel(thisLevel, GenerateData=False)
    [added_mask_level, ImageMaskLevelNode] = OutputMaskFilterNode.Imageset.GetOrCreateLevel(thisLevel,
                                                                                            GenerateData=False)

    os.makedirs(ImageLevelNode.FullPath, exist_ok=True)
    os.makedirs(ImageMaskLevelNode.FullPath, exist_ok=True)

    ImageNode = GetOrCreateCleanedImageNode(OutputFilterNode.Imageset, TransformNode, thisLevel,
                                            OutputImageNameTemplate)
    MaskImageNode = GetOrCreateCleanedImageNode(OutputMaskFilterNode.Imageset, TransformNode, thisLevel,
                                                OutputImageMaskNameTemplate)

    # We no longer use MaskPath, it is set at the filter level now.
    # ImageNode.MaskPath = MaskImageNode.FullPath

    # ===========================================================================
    # if hasattr(ImageNode, 'InputTransformChecksum'):
    #     if not transforms.IsValueMatched(ImageNode, 'InputTransformChecksum', TransformNode.Checksum):
    #         if os.path.exists(ImageNode.FullPath):
    #             os.remove(ImageNode.FullPath)
    # else:
    #     if os.path.exists(ImageNode.FullPath):
    #         os.remove(ImageNode.FullPath)
    # ===========================================================================

    # image.RemoveOnTransformCropboxMismatched(TransformNode, ImageNode, thisLevel)
    # image.RemoveOnTransformCropboxMismatched(TransformNode, MaskImageNode, thisLevel)

    if os.path.exists(ImageNode.FullPath):
        image.RemoveOnDimensionMismatch(MaskImageNode.FullPath,
                                        nornir_imageregistration.GetImageSize(ImageNode.FullPath))

    if not (os.path.exists(ImageNode.FullPath) and os.path.exists(MaskImageNode.FullPath)):

        # LevelFormatStr = LevelFormatTemplate % thisLevel
        [added_input_level, InputLevelNode] = FilterNode.TilePyramid.GetOrCreateLevel(thisLevel)

        ImageDir = InputLevelNode.FullPath
        # ImageDir = os.path.join(FilterNode.TilePyramid.FullPath, LevelFormatStr)

        tempOutputFullPath = os.path.join(ImageDir, 'Temp' + image_ext)
        tempMaskOutputFullPath = os.path.join(ImageDir, 'TempMask' + image_ext)

        Logger.info("Assembling " + TransformNode.FullPath)
        mosaic = nornir_imageregistration.Mosaic.LoadFromMosaicFile(TransformNode.FullPath)
        mosaicTileset = nornir_imageregistration.mosaic_tileset.CreateFromMosaic(mosaic, ImageDir,
                                                                                 image_to_source_space_scale=thisLevel)

        (mosaicImage, maskImage) = mosaicTileset.AssembleImage(FixedRegion=RequestedBoundingBox,
                                                               usecluster=True,
                                                               target_space_scale=1.0 / thisLevel)

        if mosaicImage is None or maskImage is None:
            Logger.error("No output produced assembling " + TransformNode.FullPath)
            return

        # Cropping based on the transform usually enlarges the image to match the largest transform in the volume.  We don't crop if a specfic region was already requested
        if TransformNode.CropBox is not None and RequestedBoundingBox is None:
            (Xo, Yo, Width, Height) = TransformNode.CropBoxDownsampled(thisLevel)

            Logger.warn("Cropping assembled image to volume boundary")

            mosaicImage = nornir_imageregistration.CropImage(mosaicImage, Xo, Yo, Width, Height)
            maskImage = nornir_imageregistration.CropImage(maskImage, Xo, Yo, Width, Height)

        nornir_imageregistration.SaveImage(tempOutputFullPath, mosaicImage, bpp=OutputFilterNode.BitsPerPixel)
        nornir_imageregistration.SaveImage(tempMaskOutputFullPath, maskImage)

        # Run convert on the output to make sure it is interlaced
        if Interlace:
            ConvertCmd = 'magick convert ' + tempOutputFullPath + ' -quality 106 -interlace PNG ' + tempOutputFullPath
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
    if ImageSet is not None:
        yield ImageSet

    MaskImageSet = BuildImagePyramid(OutputMaskFilterNode.Imageset, Interlace=Interlace, **kwargs)
    if MaskImageSet is not None:
        yield MaskImageSet


#
# 
# def AssembleTransformIrTools(Parameters, Logger, FilterNode, TransformNode, ThumbnailSize=256, Interlace=True, **kwargs):
#     '''Assemble a transform using the ir-tools
#        @ChannelNode - TransformNode lives under ChannelNode
#        '''
#     Feathering = Parameters.get('Feathering', 'binary')
# 
#     (added_mask_filter, MaskFilterNode) = FilterNode.GetOrCreateMaskFilter(FilterNode.MaskName)
#     ChannelNode = FilterNode.FindParent('Channel')
#     SectionNode = ChannelNode.FindParent('Section')
# 
#     NodesToSave = []
# 
#     MangledName = misc.GenNameFromDict(Parameters) + TransformNode.Type
# 
#     PyramidLevels = nornir_shared.misc.SortedListFromDelimited(kwargs.get('Levels', [1, 2, 4, 8, 16, 32, 64, 128, 256]))
# 
#     OutputImageNameTemplate = nornir_buildmanager.templates.Current.SectionTemplate % SectionNode.Number + "_" + ChannelNode.Name + "_" + FilterNode.Name + ".png"
#     OutputImageMaskNameTemplate = nornir_buildmanager.templates.Current.SectionTemplate % SectionNode.Number + "_" + ChannelNode.Name + "_" + MaskFilterNode.Name + ".png"
# 
#     FilterNode.Imageset.SetTransform(TransformNode)
#     MaskFilterNode.Imageset.SetTransform(TransformNode)
# 
#     argstring = misc.ArgumentsFromDict(Parameters)
#     irassembletemplate = 'ir-assemble ' + argstring + ' -sh 1 -sp %(pixelspacing)i -save %(OutputImageFile)s -load %(InputFile)s -mask %(OutputMaskFile)s -image_dir %(ImageDir)s '
# 
#     LevelFormatTemplate = FilterNode.TilePyramid.attrib.get('LevelFormat', nornir_buildmanager.templates.Current.LevelFormat)
# 
#     thisLevel = PyramidLevels[0]
# 
#     # Create a node for this level
#     [added_image_level, ImageLevelNode] = FilterNode.Imageset.GetOrCreateLevel(thisLevel)
#     [added_mask_level, ImageMaskLevelNode] = MaskFilterNode.Imageset.GetOrCreateLevel(thisLevel)
# 
#     os.makedirs(ImageLevelNode.FullPath, exist_ok=True)
#     os.makedirs(ImageMaskLevelNode.FullPath, exist_ok=True)
# 
#     # Should Replace any child elements
#     ImageNode = ImageLevelNode.find('Image')
#     if(ImageNode is None):
#         ImageNode = nb.VolumeManager.ImageNode.Create(OutputImageNameTemplate)
#         ImageLevelNode.append(ImageNode)
# 
#     MaskImageNode = ImageMaskLevelNode.find('Image')
#     if(MaskImageNode is None):
#         MaskImageNode = nb.VolumeManager.ImageNode.Create(OutputImageMaskNameTemplate)
#         ImageMaskLevelNode.append(MaskImageNode)
# 
#     ImageNode.MaskPath = MaskImageNode.FullPath
# 
#     if not (os.path.exists(ImageNode.FullPath) and os.path.exists(MaskImageNode.FullPath)):
#         LevelFormatStr = LevelFormatTemplate % thisLevel
#         ImageDir = os.path.join(FilterNode.TilePyramid.FullPath, LevelFormatStr)
# 
#         tempOutputFullPath = os.path.join(ImageDir, 'Temp.png')
#         tempMaskOutputFullPath = os.path.join(ImageDir, 'TempMask.png')
# 
#         cmd = irassembletemplate % {'pixelspacing' : thisLevel,
#                                     'OutputImageFile' : tempOutputFullPath,
#                                     'OutputMaskFile' : tempMaskOutputFullPath,
#                                     'InputFile' : TransformNode.FullPath,
#                                     'ImageDir' : ImageDir}
#         prettyoutput.Log(cmd)
#         subprocess.call(cmd + " && exit", shell=True)
# 
#         if hasattr(TransformNode, 'CropBox'):
#             cmdTemplate = "magick convert %(Input)s -crop %(width)dx%(height)d%(Xo)+d%(Yo)+d! -background black -flatten %(Output)s"
#             (Xo, Yo, Width, Height) = nornir_shared.misc.ListFromAttribute(TransformNode.CropBox)
# 
#             # Figure out the downsample level, adjust the crop box, and crop
#             Xo = Xo / float(thisLevel)
#             Yo = Yo / float(thisLevel)
#             Width = Width / float(thisLevel)
#             Height = Height / float(thisLevel)
# 
#             cmd = cmdTemplate % {'Input' : tempOutputFullPath,
#                                  'Output' : tempOutputFullPath,
#                                  'Xo' :-Xo,
#                                  'Yo' :-Yo,
#                                  'width' : Width,
#                                  'height' : Height}
# 
#             maskcmd = cmdTemplate % {'Input' : tempMaskOutputFullPath,
#                                  'Output' : tempMaskOutputFullPath,
#                                  'Xo' :-Xo,
#                                  'Yo' :-Yo,
#                                  'width' : Width,
#                                  'height' : Height}
# 
#             Logger.warn("Cropping assembled image to volume boundary")
#             # subprocess.call(cmd + " && exit", shell=True)
#             # subprocess.call(maskcmd + " && exit", shell=True)
# 
#         # Run convert on the output to make sure it is interlaced
#         if(Interlace):
#             ConvertCmd = 'magick convert ' + tempOutputFullPath + ' -quality 106 -interlace PNG ' + tempOutputFullPath
#             Logger.warn("Interlacing assembled image")
#             subprocess.call(ConvertCmd + " && exit", shell=True)
# 
#         if os.path.exists(tempOutputFullPath):
#             shutil.move(tempOutputFullPath, ImageNode.FullPath)
#             shutil.move(tempMaskOutputFullPath, MaskImageNode.FullPath)
#         else:
#             Logger.error("Assemble produced no output " + ImageNode.FullPath)
# 
#         # ImageNode.Checksum = nornir_shared.Checksum.FilesizeChecksum(ImageNode.FullPath)
#         # MaskImageNode.Checksum = nornir_shared.Checksum.FilesizeChecksum(MaskImageNode.FullPath)
#     
#     yield FilterNode
# 
#     ImageSet = BuildImagePyramid(FilterNode.Imageset, Logger, **kwargs)
#     if not ImageSet is None:
#         yield ImageSet 
#         
#     MaskImageSet = BuildImagePyramid(MaskFilterNode.Imageset, Logger, **kwargs)
#     if not MaskImageSet is None:
#         yield MaskImageSet


def AssembleTileset(Parameters, FilterNode, PyramidNode, TransformNode, TileShape=None, TileSetName=None, Logger=None,
                    **kwargs):
    """Create full resolution tiles of specfied size for the mosaics
       @FilterNode
       @TransformNode"""
    prettyoutput.CurseString('Stage', "Assemble Tile Pyramids")

    TileWidth = TileShape[0]
    TileHeight = TileShape[1]

    Feathering = Parameters.get('Feathering', 'binary')

    InputTransformNode = TransformNode
    FilterNode = PyramidNode.FindParent('Filter')

    if TileSetName is None:
        TileSetName = 'Tileset'

    InputLevelNode = PyramidNode.GetLevel(1)
    if InputLevelNode is None:
        Logger.warning("No input tiles found for assembletiles")
        return

    TileSetNode = nornir_buildmanager.volumemanager.TilesetNode.Create()
    [added, TileSetNode] = FilterNode.UpdateOrAddChildByAttrib(TileSetNode, 'Path')

    TileSetNode.TileXDim = str(TileWidth)
    TileSetNode.TileYDim = str(TileHeight)
    TileSetNode.FilePostfix = '.png'
    TileSetNode.FilePrefix = FilterNode.Name + '_'
    TileSetNode.CoordFormat = nornir_buildmanager.templates.Current.GridTileCoordFormat

    os.makedirs(TileSetNode.FullPath, exist_ok=True)

    # OK, check if the first level of the tileset exists
    LevelOne = TileSetNode.GetChildByAttrib('Level', 'Downsample', 1)
    if LevelOne is None:
        # Need to call ir-assemble
        LevelOne = nornir_buildmanager.volumemanager.LevelNode.Create(Level=1)
        [added, LevelOne] = TileSetNode.UpdateOrAddChildByAttrib(LevelOne, 'Downsample')

        os.makedirs(LevelOne.FullPath, exist_ok=True)

        # The output file name is used as a prefix for the tiles written
        OutputPath = os.path.join(LevelOne.FullPath, FilterNode.Name + '.png')
        OutputXML = os.path.join(LevelOne.FullPath, FilterNode.Name + '.xml')

        assembleTemplate = 'ir-assemble -load %(transform)s -save %(LevelPath)s -image_dir %(ImageDir)s -feathering %(feathering)s -load_as_needed -tilesize %(width)d %(height)d -sp 1'
        cmd = assembleTemplate % {'transform': InputTransformNode.FullPath,
                                  'LevelPath': OutputPath,
                                  'ImageDir': InputLevelNode.FullPath,
                                  'feathering': Feathering,
                                  'width': TileWidth,
                                  'height': TileHeight}

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

        Info = nornir_buildmanager.metadata.tilesetinfo.TilesetInfo.Load(OutputXML, Logger=Logger)
        LevelOne.GridDimX = Info.GridDimX
        LevelOne.GridDimY = Info.GridDimY

    return FilterNode


def _SaveImageAndCopy(ImageFullPath: str, temp_output_tile_fullpath: str, tile_image: NDArray, bpp: int | None, optimize=True):
    """Used to pass to the thread pool.  Saves the image to a temporary path and copies the image to final output location.
    """
    nornir_imageregistration.SaveImage(ImageFullPath=temp_output_tile_fullpath, image=tile_image, bpp=bpp,
                                       optimize=True)
    shutil.copyfile(temp_output_tile_fullpath, ImageFullPath)
    return


def AssembleTilesetNumpy(Parameters, FilterNode, PyramidNode, TransformNode, TileShape, TileSetName=None,
                         max_temp_image_area=None, ignore_stale=False, Logger=None, **kwargs):
    """Create full resolution tiles of specfied size for the mosaics
       @FilterNode
       @TransformNode"""
    prettyoutput.CurseString('Stage', "Assemble Tile Pyramids")

    TileWidth = TileShape[0]
    TileHeight = TileShape[1]

    downsample_level = 1

    tile_dims = numpy.asarray((TileWidth, TileHeight), dtype=numpy.int64)

    #    Feathering = Parameters.get('Feathering', 'binary')

    InputTransformNode = TransformNode
    FilterNode = PyramidNode.FindParent('Filter')

    SectionNode = FilterNode.FindParent('Section')

    if TileSetName is None:
        TileSetName = 'Tileset'

    InputLevelNode = PyramidNode.GetLevel(1)
    if InputLevelNode is None:
        Logger.warning("No input tiles found for assembletiles")
        return

    TileSetNode = nornir_buildmanager.volumemanager.TilesetNode.Create()
    [added, TileSetNode] = FilterNode.UpdateOrAddChildByAttrib(TileSetNode, 'Path')

    # Check if the tileset is older than the transform we are building from
    if not added and not ignore_stale and InputTransformNode.CreationTime > TileSetNode.CreationTime:
        TileSetNode.Clean("Input Transform was created after the existing optimized tileset")
        TileSetNode = nornir_buildmanager.volumemanager.TilesetNode.Create()
        [added, TileSetNode] = FilterNode.UpdateOrAddChildByAttrib(TileSetNode, 'Path')

    # TODO: Validate that the tileset is populated in a more robust way

    TileSetNode.TileXDim = str(TileWidth)
    TileSetNode.TileYDim = str(TileHeight)
    TileSetNode.FilePostfix = '.png'
    TileSetNode.FilePrefix = FilterNode.Name + '_'
    TileSetNode.CoordFormat = nornir_buildmanager.templates.Current.GridTileCoordFormat
    TileSetNode.SetTransform(InputTransformNode)

    os.makedirs(TileSetNode.FullPath, exist_ok=True)

    # OK, check if the first level of the tileset exists
    LevelOne = TileSetNode.GetChildByAttrib('Level', 'Downsample', downsample_level)

    bpp = FilterNode.BitsPerPixel

    if LevelOne is None:
        # Need to call ir-assemble
        LevelOne = nornir_buildmanager.volumemanager.LevelNode.Create(Level=1)
        [added, LevelOne] = TileSetNode.UpdateOrAddChildByAttrib(LevelOne, 'Downsample')

        os.makedirs(LevelOne.FullPath, exist_ok=True)

        # The output file name is used as a prefix for the tiles written
        # OutputPath = os.path.join(LevelOne.FullPath, FilterNode.Name + '.png')
        # OutputXML = os.path.join(LevelOne.FullPath, FilterNode.Name + '.xml')

        # pool = nornir_pools.GetGlobalThreadPool()
        pool = nornir_pools.GetThreadPool("IOPool", num_threads=multiprocessing.cpu_count() * 2)

        mosaic = nornir_imageregistration.Mosaic.LoadFromMosaicFile(InputTransformNode.FullPath)
        expected_scale = 1.0 / LevelOne.Downsample

        mosaicTileset = nornir_imageregistration.mosaic_tileset.CreateFromMosaic(mosaic,
                                                                                 image_folder=InputLevelNode.FullPath,
                                                                                 image_to_source_space_scale=expected_scale)

        scaled_fixed_bounding_box_shape = numpy.ceil(
            mosaicTileset.TargetBoundingBox.shape / (1.0 / expected_scale)).astype(numpy.int64)
        expected_grid_dims = nornir_imageregistration.TileGridShape(scaled_fixed_bounding_box_shape,
                                                                    tile_size=tile_dims)

        prettyoutput.Log("Section {4}: Generating a {0}x{1} grid of {2}x{3} tiles".format(expected_grid_dims[1],
                                                                                          expected_grid_dims[0],
                                                                                          tile_dims[1], tile_dims[0],
                                                                                          SectionNode.Number))

        temp_level_dir = get_temp_dir_for_tileset_level(LevelOne)
        os.makedirs(temp_level_dir, exist_ok=True)

        if max_temp_image_area is None:
            max_temp_image_area = EstimateMaxTempImageArea()
            prettyoutput.Log("No memory limit specified, calculated {0:g}MB limit.".format(
                float(max_temp_image_area) / float(2 << 20)))
            
        task_timer = nornir_shared.tasktimer.TaskTimer()
        task_timer.Start(f"Assemble Optimized Tiles Level {InputLevelNode.Downsample}")
        for iRow, iCol, tile_image in mosaicTileset.GenerateOptimizedTiles(target_space_scale=1.0 / InputLevelNode.Downsample,
                                                         tile_dims=tile_dims,
                                                         max_temp_image_area=max_temp_image_area,
                                                         usecluster=True):

            tilename = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix': TileSetNode.FilePrefix,
                                                                                     'X': iCol,
                                                                                     'Y': iRow,
                                                                                     'postfix': TileSetNode.FilePostfix}
            temp_output_tile_fullpath = os.path.join(temp_level_dir,
                                                     tilename)  # A temporary output file, this is cached for building pyramids later, and allows moving to a network location in one step
            output_tile_fullpath = os.path.join(LevelOne.FullPath, tilename)
            # pool.add_task(tilename, nornir_imageregistration.SaveImage, ImageFullPath=temp_output_tile_fullpath, image=tile_image, bpp=bpp, optimize=True)

            pool.add_task(tilename, _SaveImageAndCopy, ImageFullPath=output_tile_fullpath,
                          temp_output_tile_fullpath=temp_output_tile_fullpath, tile_image=tile_image, bpp=bpp,
                          optimize=True)

        # Wait for the tiles to save
        pool.wait_completion()
        task_timer.End(f"Assemble Optimized Tiles Level {InputLevelNode.Downsample}")
        prettyoutput.Log("Generation of tileset complete")
        #         else:
        #             Logger.info("Assemble tiles output already exists")

        # if not os.path.exists(OutputXML):
        # Something went wrong, do not save
        #    return None

        # Info = nornir_buildmanager.metadata.tilesetinfo.TilesetInfo.Load(OutputXML, Logger=Logger)
        LevelOne.GridDimX = expected_grid_dims[1]
        LevelOne.GridDimY = expected_grid_dims[0]

    return FilterNode


def BuildImagePyramid(ImageSetNode, Levels=None, Interlace=True, **kwargs):
    """@ImageSetNode"""

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
        if SourceImageNode is None:
            Logger.error('Source image not found in level' + str(SourceLevel))
            return None

        thisLevel = PyramidLevels[i]
        assert (SourceLevel != thisLevel)
        TargetImageNode = ImageSetNode.GetOrCreateImage(thisLevel, SourceImageNode.Path, GenerateData=False)

        os.makedirs(TargetImageNode.Parent.FullPath, exist_ok=True)

        if os.path.exists(TargetImageNode.FullPath):
            RemoveOutdatedFile(SourceImageNode.FullPath, TargetImageNode.FullPath)
            RemoveInvalidImageFile(TargetImageNode.FullPath)

        buildLevel = False
        if os.path.exists(TargetImageNode.FullPath):
            if 'InputImageChecksum' in SourceImageNode.attrib:
                TargetImageNode = transforms.RemoveOnMismatch(TargetImageNode, "InputImageChecksum",
                                                              SourceImageNode.InputImageChecksum)

                if TargetImageNode is None:
                    buildLevel = True
                    # Recreate the node if needed 
                    TargetImageNode = ImageSetNode.GetOrCreateImage(thisLevel, GenerateData=False)

        #            RemoveOnMismatch()
        #            if(TargetImageNode.attrib["InputImageChecksum"] != SourceImageNode.InputImageChecksum):
        #                os.remove(TargetImageNode.FullPath)

        else:
            buildLevel = True

        if buildLevel:
            scale = SourceLevel / thisLevel
            nornir_imageregistration.Shrink(SourceImageNode.FullPath, TargetImageNode.FullPath, scale)
            SaveImageSet = True

            if 'InputImageChecksum' in SourceImageNode.attrib:
                TargetImageNode.InputImageChecksum = str(SourceImageNode.InputImageChecksum)

            Logger.info('Shrunk ' + TargetImageNode.FullPath)

            if Interlace:
                ConvertCmd = 'magick convert ' + TargetImageNode.FullPath + ' -quality 106 -interlace PNG ' + TargetImageNode.FullPath
                Logger.info('Interlacing start ' + TargetImageNode.FullPath)
                prettyoutput.Log(ConvertCmd)
                subprocess.call(ConvertCmd + " && exit", shell=True)

            # TargetImageNode.Checksum = nornir_shared.Checksum.FilesizeChecksum(TargetImageNode.FullPath)

    if SaveImageSet:
        return ImageSetNode

    return None


def BuildTilePyramids(PyramidNode=None, Levels=None, **kwargs):
    """ @PyramidNode
        Build the image pyramid for the specified path.  We expect the "001" level of the pyramid to be pre-populated"""
    prettyoutput.CurseString('Stage', "BuildPyramids")

    SavePyramidNode = False
    Pool = None
    PyramidLevels = _SortedNumberListFromLevelsParameter(Levels)

    if PyramidNode is None:
        prettyoutput.LogErr("No volume element available for BuildTilePyramids")
        return

    local_thread_pool = nornir_pools.GetThreadPool('BuildTilePyramids')

    LevelFormatStr = PyramidNode.attrib.get('LevelFormat', nornir_buildmanager.templates.Current.LevelFormat)

    InputPyramidFullPath = PyramidNode.FullPath

    prettyoutput.Log("Checking path for unbuilt pyramids: " + InputPyramidFullPath)

    PyramidLevels = _InsertExistingLevelIfMissing(PyramidNode, PyramidLevels)

    # Ensure each level is unique
    PyramidLevels = sorted(frozenset(PyramidLevels))

    # This is the set of files we have generated during this call for a previous
    # level.  We do not need to interrogate the disk for the validity and
    # existence of these files on the assumption we'd have an error earlier if
    # they failed to generate or write to disk
    PreviouslyGeneratedOutputFiles = frozenset()

    for i in range(1, len(PyramidLevels)):

        LevelHeaderPrinted = False

        upLevel = PyramidLevels[i - 1]
        thisLevel = PyramidLevels[i]

        upLevelPathStr = LevelFormatStr % upLevel
        thisLevePathlStr = LevelFormatStr % thisLevel

        shrinkFactor = float(upLevel) / float(thisLevel)

        upLevelNode = nornir_buildmanager.volumemanager.LevelNode.Create(upLevel)
        [LevelNodeCreated, upLevelNode] = PyramidNode.UpdateOrAddChildByAttrib(upLevelNode, "Downsample")
        if LevelNodeCreated:
            SavePyramidNode = True

        thisLevelNode = nornir_buildmanager.volumemanager.LevelNode.Create(thisLevel)
        [LevelNodeCreated, thisLevelNode] = PyramidNode.UpdateOrAddChildByAttrib(thisLevelNode, "Downsample")
        if LevelNodeCreated:
            SavePyramidNode = True

        InputTileDir = os.path.join(InputPyramidFullPath, upLevelPathStr)
        OutputTileDir = os.path.join(InputPyramidFullPath, thisLevePathlStr)

        InputGlobPattern = os.path.join(InputTileDir, "*" + PyramidNode.ImageFormatExt)
        OutputGlobPattern = os.path.join(OutputTileDir, "*" + PyramidNode.ImageFormatExt)

        taskList = []

        # Simply a speedup so we aren't constantly hitting the server with exist requests for populated directories
        SourceFilesTask = local_thread_pool.add_task(f"Get Source Files {InputGlobPattern}", glob.glob,
                                                     InputGlobPattern)
        DestFiles = glob.glob(OutputGlobPattern)
        DestFileBaseNames = frozenset([os.path.basename(x) for x in DestFiles])
        SourceFiles = SourceFilesTask.wait_return()

        # Create directories if we have source files and the directories are missing
        if len(SourceFiles) > 0:
            os.makedirs(OutputTileDir, exist_ok=True)

        if (len(DestFiles) == PyramidNode.NumberOfTiles and
                len(SourceFiles) == len(DestFiles)):

            # If the first pair of files are not out of data assume the 
            # rest are current and check the next pyramid level 
            if not OutdatedFile(SourceFiles[0], DestFiles[0]):
                continue

        # Use a frozenset to optimize the 'in' keyword use in the upcoming loop
        SourceFileBaseNames = frozenset([os.path.basename(x) for x in SourceFiles])

        MissingDestFiles = SourceFileBaseNames - DestFileBaseNames

        # Go collect all the information we are going to need to collect with File I/O using threads
        ExistingDestFiles = SourceFileBaseNames.intersection(DestFileBaseNames)
        DestNeedsReplacmentTasks = []
        DestFileIsValid = []

        with concurrent.futures.ThreadPoolExecutor() as executor:
            tasks = []
            for filename in ExistingDestFiles:
                outputFile = os.path.join(OutputTileDir, filename)
                inputFile = os.path.join(InputTileDir, filename)
                assert (outputFile != inputFile)

                t = executor.submit(
                    lambda infile, outfile: not RemoveOutdatedFile(infile, outfile) and not RemoveInvalidImageFile(
                        outfile), inputFile, outputFile)
                t.filename = filename
                tasks.append(t)

            for t in concurrent.futures.as_completed(tasks):
                try:
                    DestFileIsValid.append((t.filename, t.result()))
                except Exception as e:
                    DestFileIsValid = (t.filename, False)
                    prettyoutput.error(
                        f'Could not validate tile, regenerating {os.path.join(OutputTileDir, t.filename)}')

        del DestNeedsReplacmentTasks
        DestNeedsReplacment = list(filter(lambda disv: not disv[1], DestFileIsValid))
        DestIsValid = set([d[0] for d in filter(lambda disv: disv[1], DestFileIsValid)])
        DestNeedsReplacment.extend([(f, True) for f in MissingDestFiles])

        DestFileNeedsReplacmentSet = frozenset([d[0] for d in DestNeedsReplacment])
        del DestNeedsReplacment

        # For the destinations needs replacement, check if the input is valid.
        # Do not check the output of previous iterations in earlier loops since
        # we can assume they are valid or we'd get an exception earlier
        SourceFileIsValidTasks = []
        for filename in DestFileNeedsReplacmentSet - PreviouslyGeneratedOutputFiles:
            inputFile = os.path.join(InputTileDir, filename)

            t = local_thread_pool.add_task(f'Check if {filename} is valid', os.path.getsize, inputFile)
            t.filename = filename
            SourceFileIsValidTasks.append(t)

        SourceFileIsValidSet = set(PreviouslyGeneratedOutputFiles)
        for t in SourceFileIsValidTasks:
            try:
                # Check that the input file has a non-zero size
                if t.wait_return() > 0:
                    SourceFileIsValidSet.add(t.filename)
            except Exception as e:
                prettyoutput.error(f'Input file getsize excepption {e}')

        # Files we can update
        FilesToUpdate = DestFileNeedsReplacmentSet.intersection(SourceFileIsValidSet)

        for filename in FilesToUpdate:
            # filename = os.path.basename(f)

            outputFile = os.path.join(OutputTileDir, filename)
            inputFile = os.path.join(InputTileDir, filename)

            # Just because it exists doesn't mean it is valid
            # DestFileExists = filename in DestFileBaseNames
            # if DestFileExists:
            #     DestFileExists = DestFileExists and not RemoveOutdatedFile(inputFile, outputFile) and not RemoveInvalidImageFile(outputFile)
            #
            # if DestFileExists:
            #     continue
            #
            # # Don't process if the input is temp file
            # try:
            #     if(os.path.getsize(inputFile) <= 0):
            #         continue
            # except:
            #     continue

            if Pool is None:
                # Pool = nornir_pools.GetThreadPool('BuildTilePyramids {0}'.format(OutputTileDir), multiprocessing.cpu_count() * 2)
                num_threads = multiprocessing.cpu_count() * 2
                if num_threads > len(SourceFiles):
                    num_threads = len(SourceFiles) + 1
                # Pool = nornir_pools.GetMultithreadingPool("Shrink", num_threads=num_threads)
                Pool = nornir_pools.GetMultithreadingPool("Shrink", num_threads=num_threads)

            if not LevelHeaderPrinted:
                #         prettyoutput.Log(str(upLevel) + ' -> ' + str(thisLevel) + '\n')
                LevelHeaderPrinted = True

            taskStr = "{0} -> {1}".format(inputFile, outputFile)
            t = Pool.add_task(taskStr, nornir_imageregistration.Shrink, inputFile, outputFile, shrinkFactor)
            t.filename = filename
            t.inputFile = inputFile
            taskList.append(t)

        if Pool is not None:

            for t in taskList:
                RemoveSource = False
                if hasattr(t, 'returncode'):
                    if t.returncode > 0:
                        RemoveSource = True
                        DestIsValid.remove(t.filename)
                        prettyoutput.LogErr(
                            '\n*** Suspected bad input file to pyramid, deleting the source image.  Rerun scripts to attempt adding the file again.\n')
                        try:
                            os.remove(t.inputFile)
                        except:
                            pass
                else:
                    try:
                        t.wait()  # We do this to ensure any exeptions are raised
                        RemoveSource = False
                        DestIsValid.add(t.filename)
                    except:
                        RemoveSource = True
                        if t.filename in DestIsValid:
                            DestIsValid.remove(t.filename)

                if RemoveSource:
                    if not nornir_shared.images.IsValidImage(t.inputFile):
                        prettyoutput.LogErr(
                            '\n*** Suspected bad input file to pyramid, deleting the source image.  Rerun scripts to attempt adding the file again.\n')
                        try:
                            os.remove(t.inputFile)
                        except:
                            pass

        # Save the list of files we know are good as input the next iteration
        PreviouslyGeneratedOutputFiles = frozenset(DestIsValid)

    if Pool is not None:
        Pool.shutdown()
        Pool = None

    if local_thread_pool is not None:
        local_thread_pool.shutdown()
        local_thread_pool = None

    if SavePyramidNode:
        return PyramidNode

    return None


def _InsertExistingLevelIfMissing(PyramidNode, Levels):
    """If the first level in the list does not exist, insert it into the list so a source is available to build from"""

    if not PyramidNode.HasLevel(Levels[0]):
        MoreDetailedLevel = PyramidNode.MoreDetailedLevel(Levels[0])
        if MoreDetailedLevel is None:
            raise Exception(
                "No pyramid level available with more detail than %d in %s" % (Levels[0], PyramidNode.FullPath))

        Levels.insert(0, MoreDetailedLevel.Downsample)
    elif PyramidNode.MaxResLevel.Downsample != Levels[0]:
        # There is an existing level.  We will attempt to add a more detailed level is available we will not throw an exception. , so we will continue. Highest resolution is probably already included.
        MoreDetailedLevel = PyramidNode.MoreDetailedLevel(Levels[0])
        if MoreDetailedLevel is not None:
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


def BuildTilesetLevel(SourcePath: str, DestPath: str, DestGridDimensions: (int, int), TileDim: (int, int),
                      FilePrefix: str, FilePostfix: str, Pool=None, **kwargs):
    """
    :param SourcePath:
    :param DestPath:
    :param TileDim: (Y, X) Size of cell (tile) in the grid
    :param DestGridDimensions:  (GridDimY,GridDimX) Number of tiles along each axis
    :param FilePrefix:
    :param FilePostfix:
    :param Pool:
    """

    os.makedirs(DestPath, exist_ok=True)

    if Pool is None:
        Pool = nornir_pools.GetGlobalThreadPool()

    # Merge all the tiles we can find into tiles of the same size
    for iY in range(0, DestGridDimensions[0]):
        # We wait for the last task we queued for each row so we do not swamp the ProcessPool but are not waiting for the entire pool to empty
        FirstTaskForRow = None

        for iX in range(0, DestGridDimensions[1]):

            X1 = iX * 2
            X2 = X1 + 1
            Y1 = iY * 2
            Y2 = Y1 + 1

            # OutputFile = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % iX + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % iY + FilePostfix
            OutputFile = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix': FilePrefix,
                                                                                       'X': iX,
                                                                                       'Y': iY,
                                                                                       'postfix': FilePostfix}

            OutputFileFullPath = os.path.join(DestPath, OutputFile)

            # Skip if file already exists
            # if(os.path.exists(OutputFileFullPath)):
            #    continue

            # TopLeft = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % X1 + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % Y1 + FilePostfix
            # TopRight = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % X2 + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % Y1 + FilePostfix
            # BottomLeft = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % X1 + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % Y2 + FilePostfix
            # BottomRight = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % X2 + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % Y2 + FilePostfix
            TopLeft = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix': FilePrefix,
                                                                                    'X': X1,
                                                                                    'Y': Y1,
                                                                                    'postfix': FilePostfix}
            TopRight = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix': FilePrefix,
                                                                                     'X': X2,
                                                                                     'Y': Y1,
                                                                                     'postfix': FilePostfix}
            BottomLeft = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix': FilePrefix,
                                                                                       'X': X1,
                                                                                       'Y': Y2,
                                                                                       'postfix': FilePostfix}
            BottomRight = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix': FilePrefix,
                                                                                        'X': X2,
                                                                                        'Y': Y2,
                                                                                        'postfix': FilePostfix}

            TopLeft = os.path.join(SourcePath, TopLeft)
            TopRight = os.path.join(SourcePath, TopRight)
            BottomLeft = os.path.join(SourcePath, BottomLeft)
            BottomRight = os.path.join(SourcePath, BottomRight)

            nullCount = 0

            if os.path.exists(TopLeft) is False:
                TopLeft = 'null:'
                nullCount = nullCount + 1
            if os.path.exists(TopRight) is False:
                TopRight = 'null:'
                nullCount = nullCount + 1
            if os.path.exists(BottomLeft) is False:
                BottomLeft = 'null:'
                nullCount = nullCount + 1
            if os.path.exists(BottomRight) is False:
                BottomRight = 'null:'
                nullCount = nullCount + 1

            if nullCount == 4:
                continue

            # Complicated ImageMagick call reads in up to four adjacent tiles, merges them, and shrinks
            # BUG this assumes we only downsample by a factor of two
            #             cmd = ("magick montage " + TopLeft + ' ' + TopRight + ' ' +
            #                   BottomLeft + ' ' + BottomRight +
            #                   ' -geometry %dx%d' % (TileDim[1] / 2, TileDim[0] / 2)
            #                   + ' -set colorspace RGB  -mode Concatenate -tile 2x2 -background black '
            #                   + ' -depth 8 -type Grayscale -define png:format=png8 ' + OutputFileFullPath)
            # prettyoutput.CurseString('Cmd', cmd)
            # prettyoutput.Log(
            # TestOutputFileFullPath = os.path.join(NextLevelNode.FullPath, 'Test_' + OutputFile)

            cmd_template = 'magick montage %(TopLeft)s %(TopRight)s %(BottomLeft)s %(BottomRight)s -geometry %(TileXDim)dx%(TileYDim)d ' + \
                           '-mode Concatenate -tile 2x2 -background black -type Grayscale tif:- | ' \
                           'magick convert tif:- -depth 8 -resize %(TileXDim)dx%(TileYDim)d -set colorspace Gray -type Grayscale -define png:format=png8 %(OutputFile)s'

            # montageBugFixCmd_template = 'magick convert %(OutputFile)s -set colorspace RGB -type Grayscale -resize %(TileXDim)dx%(TileYDim)d %(OutputFile)s'

            cmd = cmd_template % {'TopLeft': TopLeft,
                                  'TopRight': TopRight,
                                  'BottomLeft': BottomLeft,
                                  'BottomRight': BottomRight,
                                  'TileXDim': TileDim[1],
                                  'TileYDim': TileDim[0],
                                  'OutputFile': OutputFileFullPath}

            # montageBugFixCmd_ = 'magick ' + OutputFileFullPath + ' -set colorspace RGB -type Grayscale ' + OutputFileFullPath
            # montageBugFixCmd = montageBugFixCmd_template % {'OutputFile': OutputFileFullPath,
            #                                                'TileXDim': TileDim[1],
            #                                                'TileYDim': TileDim[0]}

            # montageBugFixCmd_template = 
            # task = Pool.add_process(cmd, cmd + " && " + montageBugFixCmd + " && exit", shell=True)
            task = Pool.add_process(cmd, cmd + " && exit", shell=True)

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

    if Pool is not None:
        Pool.wait_completion()


def BuildTilesetLevelWithPillow(SourcePath: str, DestPath: str, DestGridDimensions: (int, int), TileDim: (int, int),
                                FilePrefix: str, FilePostfix: str, temp_input_dir: str, temp_output_dir: str, Pool=None, **kwargs):
    """
    :param SourcePath:
    :param DestPath:
    :param DestGridDimensions:
    :param TileDim: Dimensions of tile (Y,X)
    :param FilePrefix:
    :param FilePostfix:
    :param Pool:
    """

    os.makedirs(DestPath, exist_ok=True)

    ############################################################################
    #Pre-create directories so we aren't calling exist and create for every tile
    # temp_dir = tempfile.gettempdir()
    # level_dir = os.path.basename(SourcePath)
    # temp_input_dir = os.path.join(temp_dir, level_dir)
    os.makedirs(temp_input_dir, exist_ok=True)
    #
    # output_level_dir = os.path.basename(DestPath)
    # temp_output_dir = os.path.join(temp_dir, output_level_dir)
    os.makedirs(temp_output_dir, exist_ok=True)
    ############################################################################

    if Pool is None:
        # Pool = nornir_pools.GetMultithreadingPool("IOPool", num_threads=multiprocessing.cpu_count() * 16)
        # Pool = nornir_pools.GetGlobalMultithreadingPool()
        # Pool = nornir_pools.GetGlobalThreadPool()
        Pool = nornir_pools.GetThreadPool("IOPool", num_threads=multiprocessing.cpu_count() * 2)
        #Pool = nornir_pools.GetGlobalSerialPool()

    # Merge all the tiles we can find into tiles of the same size

    # tile_params = []

    for iY in range(0, DestGridDimensions[0]):

        # We wait for the last task we queued for each row so we do not swamp the ProcessPool but are not waiting for the entire pool to empty
        FirstTaskForRow = None

        for iX in range(0, DestGridDimensions[1]):

            X1 = iX * 2
            X2 = X1 + 1
            Y1 = iY * 2
            Y2 = Y1 + 1

            # OutputFile = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % iX + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % iY + FilePostfix
            OutputFile = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix': FilePrefix,
                                                                                       'X': iX,
                                                                                       'Y': iY,
                                                                                       'postfix': FilePostfix}

            OutputFileFullPath = os.path.join(DestPath, OutputFile)

            # Skip if file already exists
            # if(os.path.exists(OutputFileFullPath)):
            #    continue

            # TopLeft = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % X1 + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % Y1 + FilePostfix
            # TopRight = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % X2 + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % Y1 + FilePostfix
            # BottomLeft = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % X1 + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % Y2 + FilePostfix
            # BottomRight = FilePrefix + 'X' + nornir_buildmanager.templates.GridTileCoordFormat % X2 + '_Y' + nornir_buildmanager.templates.GridTileCoordFormat % Y2 + FilePostfix
            TopLeft = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix': FilePrefix,
                                                                                    'X': X1,
                                                                                    'Y': Y1,
                                                                                    'postfix': FilePostfix}
            TopRight = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix': FilePrefix,
                                                                                     'X': X2,
                                                                                     'Y': Y1,
                                                                                     'postfix': FilePostfix}
            BottomLeft = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix': FilePrefix,
                                                                                       'X': X1,
                                                                                       'Y': Y2,
                                                                                       'postfix': FilePostfix}
            BottomRight = nornir_buildmanager.templates.Current.GridTileNameTemplate % {'prefix': FilePrefix,
                                                                                        'X': X2,
                                                                                        'Y': Y2,
                                                                                        'postfix': FilePostfix}

            TopLeft = os.path.join(SourcePath, TopLeft)
            TopRight = os.path.join(SourcePath, TopRight)
            BottomLeft = os.path.join(SourcePath, BottomLeft)
            BottomRight = os.path.join(SourcePath, BottomRight)

            #             tile_params.append([TileDim,
            #                            TopLeft, TopRight,
            #                            BottomLeft, BottomRight,
            #                            OutputFileFullPath])
            # task = Pool.add_task(OutputFileFullPath, tileset_functions.CreateOneTilesetTileWithPillow, TileDim,
            task = Pool.add_task(OutputFileFullPath, tileset_functions.CreateOneTilesetTileWithPillowOverNetwork,
                                 TileDim,
                                 TopLeft=TopLeft, TopRight=TopRight,
                                 BottomLeft=BottomLeft, BottomRight=BottomRight,
                                 OutputFileFullPath=OutputFileFullPath,
                                 input_level_temp_dir=temp_input_dir,
                                 output_level_temp_dir=temp_output_dir)

            if FirstTaskForRow is None:
                FirstTaskForRow = task

    # Pool.starmap_async(name=DestPath, func=tileset_functions.CreateOneTilesetTileWithPillow, iterable=tile_params)

    # TaskString = "Building tiles for downsample %g" % NextLevelNode.Downsample
    # prettyoutput.CurseProgress(TaskString, iY + 1, newYDim)

    # We can easily saturate the pool with hundreds of thousands of tasks.
    # If the pool has a reasonable number of tasks then we should wait for
    # a task from a row to complete before queueing more.
    #             if hasattr(Pool, 'num_active_tasks'):
    #                 if Pool.num_active_tasks > 2048:#multiprocessing.cpu_count() * 8:
    #                     FirstTaskForRow.wait()
    #                     FirstTaskForRow = None
    #         elif hasattr(Pool, 'tasks') and isinstance(Pool.tasks, queue.Queue):
    #             if Pool.num_active_tasks > multiprocessing.cpu_count() * 8:
    #                 FirstTaskForRow.wait()
    #                 FirstTaskForRow = None
    #         elif hasattr(Pool, 'ActiveTasks'):
    #             if Pool.num_active_tasks > multiprocessing.cpu_count() * 8:
    #                 FirstTaskForRow.wait()
    #                 FirstTaskForRow = None

    # prettyoutput.Log("\nBeginning Row %d of %d" % (iY + 1, DestGridDimensions[0]))

    if Pool is not None:
        Pool.wait_completion()
        # Pool.shutdown()
        
        
def get_temp_dir_for_tileset_level(level: nornir_buildmanager.volumemanager.LevelNode):
    
    section = level.FindParent('Section')
    section_name = section.Path if section is not None else 'section_root'
    root = level.FindParent('Volume')
    root_name = 'assemble_root' if root is None else os.path.basename(root.Path)  
    
    return os.path.join(tempfile.gettempdir(), 'nornir', 'assemble_tiles', root_name, section_name, os.path.basename(level.FullPath))


# OK, now build/check the remaining levels of the tile pyramids
def BuildTilesetPyramid(TileSetNode, HighestDownsample=None, Pool=None, **kwargs):
    """@TileSetNode"""

    MinResolutionLevel = TileSetNode.MaxResLevel

    input_temp_dir = get_temp_dir_for_tileset_level(MinResolutionLevel)
    temp_level_paths = [input_temp_dir]  # Paths to levels we generate to ensure temp directories are cleaned later

    while MinResolutionLevel is not None:

        # The grid attributes are missing if the meta-data was created but there are no tiles
        if not (hasattr(MinResolutionLevel, 'GridDimX') and hasattr(MinResolutionLevel, 'GridDimY')):
            prettyoutput.Log("Tileset incomplete: " + TileSetNode.FullPath)
            break

            # If the tileset is already a single tile, then do not downsample
        if MinResolutionLevel.GridDimX == 1 and MinResolutionLevel.GridDimY == 1:
            break

        if HighestDownsample is not None and (MinResolutionLevel.Downsample >= float(HighestDownsample)):
            break

        ShrinkFactor = 0.5
        newYDim = float(MinResolutionLevel.GridDimY) * ShrinkFactor
        newXDim = float(MinResolutionLevel.GridDimX) * ShrinkFactor

        newXDim = int(math.ceil(newXDim))
        newYDim = int(math.ceil(newYDim))

        # If there is only one tile in the next level, try to find a thumbnail image and change the downsample level
        if newXDim == 1 and newYDim == 1:
            break

        # Need to call ir-assemble
        NextLevelNode = nornir_buildmanager.volumemanager.LevelNode.Create(MinResolutionLevel.Downsample * 2)
        [added, NextLevelNode] = TileSetNode.UpdateOrAddChildByAttrib(NextLevelNode, 'Downsample')
        NextLevelNode.GridDimX = newXDim
        NextLevelNode.GridDimY = newYDim
        if added is True:
            yield TileSetNode

        output_temp_dir = get_temp_dir_for_tileset_level(NextLevelNode)
        
        # Check to make sure the level hasn't already been generated and we've just missed the
        [Valid, Reason] = NextLevelNode.IsValid()
        if not Valid:
            temp_level_paths.append(output_temp_dir)
            
            # XMLOutput = os.path.join(NextLevelNode, os.path.basename(XmlFilePath))
            BuildTilesetLevelWithPillow(MinResolutionLevel.FullPath, NextLevelNode.FullPath,
                                        DestGridDimensions=(newYDim, newXDim),
                                        TileDim=(TileSetNode.TileYDim, TileSetNode.TileXDim),
                                        FilePrefix=TileSetNode.FilePrefix,
                                        FilePostfix=TileSetNode.FilePostfix,
                                        temp_input_dir=input_temp_dir,
                                        temp_output_dir=output_temp_dir,
                                        Pool=Pool)
            
            # This was a lot of work, make sure it is saved before queueing the next level
            yield TileSetNode
            prettyoutput.Log("\nTileset level %d completed" % NextLevelNode.Downsample)
        else:
            logging.info("Level was already generated " + str(TileSetNode))

        input_temp_dir = output_temp_dir
        MinResolutionLevel = NextLevelNode

    tileset_functions.ClearTempDirectories(temp_level_paths)
    return


if __name__ == "__main__":
    TestImageDir = 'D:/BuildScript/Test/Images'
    Pool = nornir_pools.GetGlobalProcessPool()

    BadTestImage = os.path.join(TestImageDir, 'Bad101.png')
    BadTestImageOut = os.path.join(TestImageDir, 'Bad101Shrink.png')

    task = nornir_imageregistration.Shrink(BadTestImage, BadTestImageOut, 0.5)
    print(('Bad image return value: ' + str(task.returncode)))
    Pool.wait_completion()

    GoodTestImage = os.path.join(TestImageDir, '400.png')
    GoodTestImageOut = os.path.join(TestImageDir, '400Shrink.png')

    task = nornir_imageregistration.Shrink(GoodTestImage, GoodTestImageOut, 0.5)
    Pool.wait_completion()
    print(('Good image return value: ' + str(task.returncode)))
