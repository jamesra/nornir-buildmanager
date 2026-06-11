"""
Created on Aug 27, 2013

@author: u0490822
"""

import logging
import importlib
import os
from typing import Any

from nornir_buildmanager.exceptions import NornirUserException
import nornir_buildmanager.operations.tile
from nornir_buildmanager.validation import transforms
from nornir_buildmanager.volumemanager import *
import nornir_shared
from nornir_shared import *


def _blob_filter_module():
    return importlib.import_module("nornir_imageregistration.blob_filter")


def _run_python_blob(InputImagePath: str,
                     OutputImagePath: str,
                     Radius: int,
                     Median: int,
                     Max: float,
                     InputMaskPath: str | None):
    return _blob_filter_module().BlobFilterImageFile(
        InputImagePath,
        OutputImagePath,
        radius=Radius,
        median_radius=Median,
        max_value=Max,
        mask_path=InputMaskPath,
        return_diagnostics=True)


def CreateBlobFilter(Parameters: dict[str, Any], Logger: logging.Logger,
                     InputFilter: FilterNode,
                     OutputFilterName: str, ImageExtension: str | None = None,
                     **kwargs) -> XElementWrapper | None:
    """@FilterNode.  Create  a new filter which has been processed with blob"""
    Radius = Parameters.get('r', '3')
    Median = Parameters.get('median', '3')
    Max = Parameters.get('max', '3')
    if ImageExtension is None:
        ImageExtension = '.png'

    if hasattr(ImageSetNode, 'Type'):  # type: ignore[attr-defined]
        MangledName = misc.GenNameFromDict(Parameters) + ImageSetNode.Type  # type: ignore[attr-defined]
    else:
        MangledName = misc.GenNameFromDict(Parameters)

    PyramidLevels = nornir_shared.misc.SortedListFromDelimited(kwargs.get('Levels', [1, 2, 4, 8, 16, 32, 64, 128, 256]))

    ###########################################
    # STOPPED HERE.  NEED TO CREATE A FILTER  #
    ###########################################
    SaveFilterNode = False
    (SaveFilterNode, OutputFilterNode) = InputFilter.Parent.UpdateOrAddChildByAttrib(  # type: ignore[union-attr]
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

    InputMaskPath = None
    if InputFilter.HasMask:
        InputMaskImageNode = InputFilter.GetOrCreateMaskImage(thisLevel)
        if not os.path.exists(InputMaskImageNode.FullPath):
            InputMaskImageNode = None

        if not InputMaskImageNode is None:
            OutputFilterNode.MaskName = InputFilter.MaskName
            InputMaskPath = InputMaskImageNode.FullPath

    BlobImageNode = OutputFilterNode.Imageset.GetImage(thisLevel)
    if BlobImageNode is not None and BlobImageNode.InputImageChecksum is not None:
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

        try:
            diagnostics = _run_python_blob(
                InputImageNode.FullPath,
                BlobImageNode.FullPath,
                Radius=int(Radius),
                Median=int(Median),
                Max=float(Max),
                InputMaskPath=InputMaskPath)

            if diagnostics is not None and Logger is not None:
                Logger.info("Created blob image with python backend=%s numpy_fallback=%s",
                            diagnostics.backend, diagnostics.used_numpy_fallback)
        except Exception:
            SaveFilterNode = False
            raise

        if not os.path.exists(BlobImageNode.FullPath):
            SaveFilterNode = False
            raise NornirUserException("Unable to create blob image at path: %s" % BlobImageNode.FullPath)

        SaveFilterNode = True

    if not hasattr(BlobImageNode, 'InputImageChecksum'):
        BlobImageNode.InputImageChecksum = InputImageNode.Checksum
        SaveFilterNode = True

    BlobPyramidImageSet = nornir_buildmanager.operations.tile.BuildImagePyramid(OutputFilterNode.Imageset, **kwargs)
    SaveFilterNode = SaveFilterNode or (not BlobPyramidImageSet is None)

    if SaveFilterNode:
        return InputFilter.Parent

    return None
