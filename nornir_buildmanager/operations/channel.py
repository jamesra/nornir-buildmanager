'''
Created on Aug 27, 2013

@author: u0490822
'''

import math
import subprocess
import sys
import xml

from nornir_buildmanager import *
from nornir_buildmanager.VolumeManagerETree import *
from nornir_buildmanager.validation import transforms
import nornir_buildmanager.operations.tile
from nornir_imageregistration.files import mosaicfile
from nornir_imageregistration.transforms import *
from nornir_shared import *
from nornir_shared.files import RemoveOutdatedFile
from nornir_shared.histogram import Histogram
from nornir_shared.misc import SortedListFromDelimited


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
    (SaveFilterNode, OutputFilterNode) = InputFilter.Parent.UpdateOrAddChildByAttrib(FilterNode(Name=OutputFilterName), "Name")

    # DownsampleSearchTemplate = "Level[@Downsample='%(Level)d']/Image"

    OutputBlobName = OutputFilterNode.DefaultImageName(ImageExtension)

    BlobImageSet = OutputFilterNode.Imageset

    # OutputImageSet.

    # BlobSetNode = VolumeManagerETree.ImageSetNode('blob', MangledName, 'blob', {'MaskName' :  ImageSetNode.MaskName})
    # [added, BlobSetNode] = FilterNode.UpdateOrAddChildByAttrib(BlobSetNode, 'Path')
    # BlobSetNode.MaskName = ImageSetNode.MaskName

    if not os.path.exists(BlobImageSet.FullPath):
        os.makedirs(BlobImageSet.FullPath)

    # BlobSetNode.Type = ImageSetNode.Type + '_' + MangledName

    irblobtemplate = 'ir-blob ' + ArgString + ' -sh 1 -save %(OutputImageFile)s -load %(InputFile)s '

    thisLevel = PyramidLevels[0]

    # DownsampleSearchString = DownsampleSearchTemplate % {'Level': thisLevel}
    # InputMaskLevelNode = MaskSetNode.find(DownsampleSearchString)

    InputImageNode = InputFilter.GetOrCreateImage(thisLevel)

    if InputImageNode is None:
        prettyoutput.Log("Missing input level nodes for blob level: " + str(thisLevel))
        Logger.warning("Missing input level nodes for blob level: " + str(thisLevel) + ' ' + InputFilter.FullPath)
        return

    InputMaskNode = InputFilter.GetOrCreateMaskImage(thisLevel)
    MaskStr = ""
    if not os.path.exists(InputMaskNode.FullPath):
        InputMaskNode = None

    if not InputMaskNode is None:
        OutputFilterNode.MaskName = InputMaskNode.Name

        MaskStr = ' -mask %s ' % InputMaskNode.FullPath

    BlobImageNode = OutputFilterNode.Imageset.GetImage(thisLevel)
    if not BlobImageNode is None:
        BlobImageNode = transforms.RemoveOnMismatch(BlobImageNode, "InputImageChecksum", InputImageNode.Checksum)

    if BlobImageNode is None:
        BlobImageNode = OutputFilterNode.Imageset.GetOrCreateImage(thisLevel, OutputBlobName, GenerateData=False)

    if not os.path.exists(BlobImageNode.FullPath):

        if not os.path.exists(os.path.dirname(BlobImageNode.FullPath)):
            os.makedirs(os.path.dirname(BlobImageNode.FullPath))

        cmd = irblobtemplate % {'OutputImageFile' : BlobImageNode.FullPath,
                                'InputFile' : InputImageNode.FullPath} + MaskStr

        prettyoutput.Log(cmd)
        subprocess.call(cmd + " && exit", shell=True)
        SaveFilterNode = True

    if(not 'InputImageChecksum' in BlobImageSet):
        BlobImageSet.InputImageChecksum = InputImageNode.Checksum
        SaveFilterNode = True

    if(not 'InputImageChecksum' in BlobImageNode):
        BlobImageNode.InputImageChecksum = InputImageNode.Checksum
        SaveFilterNode = True

    BlobPyramidImageSet = nornir_buildmanager.operations.tile.BuildImagePyramid(OutputFilterNode.Imageset, **kwargs)
    SaveFilterNode = SaveFilterNode or (not BlobPyramidImageSet is None)

    if SaveFilterNode:
        return InputFilter.Parent

    return None
