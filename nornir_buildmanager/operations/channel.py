'''
Created on Aug 27, 2013

@author: u0490822
'''

import subprocess

from nornir_buildmanager.exceptions import NornirUserException
import nornir_buildmanager.operations.tile
from nornir_buildmanager.validation import transforms
from nornir_buildmanager.volumemanager import *
import nornir_shared
from nornir_shared import *


def CreateBlobFilter(Parameters, Logger, InputFilter, OutputFilterName, ImageExtension=None, **kwargs):
    '''@FilterNode.  Create  a new filter which has been processed with blob'''
    Radius = Parameters.get('r', '3')
    Median = Parameters.get('median', '3')
    Max = Parameters.get('max', '3')
    if ImageExtension is None:
        ImageExtension = '.png'

    if hasattr(ImageSetNode, 'Type'):
        MangledName = misc.GenNameFromDict(Parameters) + ImageSetNode.Type
    else:
        MangledName = misc.GenNameFromDict(Parameters)

    ArgString = misc.ArgumentsFromDict(Parameters)

    PyramidLevels = nornir_shared.misc.SortedListFromDelimited(kwargs.get('Levels', [1, 2, 4, 8, 16, 32, 64, 128, 256]))

    ###########################################
    # STOPPED HERE.  NEED TO CREATE A FILTER  #
    ###########################################
    SaveFilterNode = False
    (SaveFilterNode, OutputFilterNode) = InputFilter.Parent.UpdateOrAddChildByAttrib(
        FilterNode.Create(Name=OutputFilterName), "Name")

    # DownsampleSearchTemplate = "Level[@Downsample='%(Level)d']/Image"

    OutputBlobName = OutputFilterNode.DefaultImageName(ImageExtension)

    BlobImageSet = OutputFilterNode.Imageset

    # OutputImageSet.

    # BlobSetNode = VolumeManagerETree.ImageSetNode.Create('blob', MangledName, 'blob', {'MaskName' :  ImageSetNode.MaskName})
    # [added, BlobSetNode] = FilterNode.UpdateOrAddChildByAttrib(BlobSetNode, 'Path')
    # BlobSetNode.MaskName = ImageSetNode.MaskName

    os.makedirs(BlobImageSet.FullPath, exist_ok=True)

    # BlobSetNode.Type = ImageSetNode.Type + '_' + MangledName

    irblobtemplate = 'ir-blob ' + ArgString + ' -sh 1 -save %(OutputImageFile)s -load %(InputFile)s '

    thisLevel = PyramidLevels[0]

    # DownsampleSearchString = DownsampleSearchTemplate % {'Level': thisLevel}
    # InputMaskLevelNode = MaskSetNode.find(DownsampleSearchString)

    InputImageNode = None

    try:
        InputImageNode = InputFilter.GetOrCreateImage(thisLevel)
    except NornirUserException as e:
        prettyoutput.LogErr("Missing input level nodes for blob level: " + str(thisLevel))
        Logger.error("Missing input level nodes for blob level: " + str(thisLevel) + ' ' + InputFilter.FullPath)
        return

    if InputImageNode is None:
        prettyoutput.LogErr("Missing input level nodes for blob level: " + str(thisLevel))
        Logger.error("Missing input level nodes for blob level: " + str(thisLevel) + ' ' + InputFilter.FullPath)
        return

    MaskStr = ""
    if InputFilter.HasMask:
        InputMaskImageNode = InputFilter.GetOrCreateMaskImage(thisLevel)
        if not os.path.exists(InputMaskImageNode.FullPath):
            InputMaskImageNode = None

        if not InputMaskImageNode is None:
            OutputFilterNode.MaskName = InputFilter.MaskName
            MaskStr = ' -mask %s ' % InputMaskImageNode.FullPath

    BlobImageNode = OutputFilterNode.Imageset.GetImage(thisLevel)
    if not BlobImageNode is None:
        BlobImageNode = transforms.RemoveOnMismatch(BlobImageNode, "InputImageChecksum", InputImageNode.Checksum)

    if BlobImageNode is None:
        try:
            BlobImageNode = OutputFilterNode.Imageset.GetOrCreateImage(thisLevel, OutputBlobName, GenerateData=False)
        except NornirUserException as e:
            prettyoutput.Log("Missing input blob image for blob level: " + str(thisLevel))
            Logger.warning("Missing input blob image for blob level: " + str(thisLevel) + ' ' + InputFilter.FullPath)
            return

    if not os.path.exists(BlobImageNode.FullPath):

        os.makedirs(os.path.dirname(BlobImageNode.FullPath), exist_ok=True)

        cmd = irblobtemplate % {'OutputImageFile': BlobImageNode.FullPath,
                                'InputFile': InputImageNode.FullPath} + MaskStr

        prettyoutput.Log(cmd)
        proc = subprocess.Popen(cmd + " && exit", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (stdout, stderr) = proc.communicate()
        if proc.returncode < 0 or not os.path.exists(BlobImageNode.FullPath):
            SaveFilterNode = False
            prettyoutput.LogErr("Unable to create blob using command:\ncmd:%s\nerr: %s" % (cmd, stdout))
            raise RuntimeError("Unable to create blob using command:\ncmd:%s\nerr: %s" % (cmd, stdout))
        else:
            SaveFilterNode = True

    if not hasattr(BlobImageNode, 'InputImageChecksum'):
        BlobImageNode.InputImageChecksum = InputImageNode.Checksum
        SaveFilterNode = True

    BlobPyramidImageSet = nornir_buildmanager.operations.tile.BuildImagePyramid(OutputFilterNode.Imageset, **kwargs)
    SaveFilterNode = SaveFilterNode or (not BlobPyramidImageSet is None)

    if SaveFilterNode:
        return InputFilter.Parent

    return None
