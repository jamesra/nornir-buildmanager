'''
Created on Jun 4, 2012

@author: Jamesan
'''

import logging
import os
import shutil
import subprocess

from nornir_buildmanager import *
from nornir_buildmanager.validation import transforms
from nornir_imageregistration.files import *
from nornir_shared import *
from nornir_shared.processoutputinterceptor import ProcessOutputInterceptor, \
    ProgressOutputInterceptor

import nornir_imageregistration.mosaic as mosaic

def TransformNodeToZeroOrigin(transform_node, **kwargs):
    ''':return: transform_node if the mosaic was adjusted.  None if the transform_node already had a zero origin'''
    if mosaic.Mosaic.TranslateMosaicFileToZeroOrigin(transform_node.FullPath):
        transform_node.ResetChecksum()

        logger = kwargs.get("Logger", None)
        if not logger is None:
            logger.info("Moved %s to zero origin" % transform_node.FullPath)

        return transform_node

    return None


def TranslateTransform(Parameters, TransformNode, FilterNode, RegistrationDownsample, Logger, **kwargs):
    '''@ChannelNode'''
    MaxOffsetX = Parameters.get('MaxOffsetX', 0.1)
    MaxOffsetY = Parameters.get('MaxOffsetY', 0.1)
    BlackMaskX = Parameters.get('BlackMaskX', None)
    BlackMaskY = Parameters.get('BlackMaskY', None)

    OutputTransformName = kwargs.get('OutputTransform', 'Translated_' + TransformNode.Name)
    InputTransformNode = TransformNode

    LevelNode = FilterNode.TilePyramid.GetOrCreateLevel(RegistrationDownsample)

    MangledName = misc.GenNameFromDict(Parameters)

    if MaxOffsetX < 1:
        MaxOffsetX = MaxOffsetX * 100

    if MaxOffsetY < 1:
        MaxOffsetY = MaxOffsetY * 100

    MaxOffsetString = ''
    if (not MaxOffsetX is None) and (not MaxOffsetY is None):
        MaxOffsetString = ' -max_offset ' + str(MaxOffsetX) + '%% ' + str(MaxOffsetY) + '%% '

    BlackMaskString = ''
    if (not BlackMaskX is None) and (not BlackMaskY is None):
        BlackMaskString = '-black_mask ' + str(BlackMaskX) + '%% ' + str(BlackMaskY) + '%% '

    # Check if there is an existing prune map, and if it exists if it is out of date
    TransformParentNode = TransformNode.Parent

    SaveRequired = False

    OutputTransformNode = TransformParentNode.GetChildByAttrib('Transform', 'Path', VolumeManagerETree.MosaicBaseNode.GetFilename(OutputTransformName, MangledName))
    OutputTransformNode = transforms.RemoveIfOutdated(OutputTransformNode, TransformNode, Logger)

    if OutputTransformNode is None:
        OutputTransformNode = VolumeManagerETree.TransformNode(Name=OutputTransformName, Type=MangledName, attrib={'InputTransform' : InputTransformNode.Name,
                                                                                                               'InputImageDir' : LevelNode.FullPath,
                                                                                                               'InputTransformChecksum' : InputTransformNode.Checksum})

        (SaveRequired, OutputTransformNode) = TransformParentNode.UpdateOrAddChildByAttrib(OutputTransformNode, 'Path')

    if not os.path.exists(OutputTransformNode.FullPath):

        # Tired of dealing with ir-refine-translate crashing when a tile is missing, load the mosaic and ensure the tile names are correct before running ir-refine-translate
        try:
            mosaicFullPath = os.path.join(InputTransformNode.Parent.FullPath, "Temp" + InputTransformNode.Path)
            mfileObj = mosaicfile.MosaicFile.Load(InputTransformNode.FullPath)
            invalidFiles = mfileObj.RemoveInvalidMosaicImages(LevelNode.FullPath)
            if invalidFiles:
                mfileObj.Save(mosaicFullPath)
            else:
                shutil.copy(InputTransformNode.FullPath, mosaicFullPath)

            CmdLineTemplate = "ir-refine-translate -load %(InputMosaic)s -save %(OutputMosaic)s -image_dir %(ImageDir)s -noclahe  -sp " + str(LevelNode.Downsample) + MaxOffsetString + BlackMaskString
            cmd = CmdLineTemplate % {'InputMosaic' : mosaicFullPath, 'OutputMosaic' : OutputTransformNode.FullPath, 'ImageDir' : LevelNode.FullPath}
            prettyoutput.CurseString('Cmd', cmd)
            NewP = subprocess.Popen(cmd + " && exit", shell=True, stdout=subprocess.PIPE)
            ProcessOutputInterceptor.Intercept(ProgressOutputInterceptor(NewP))
            OutputTransformNode.cmd = cmd

            if os.path.exists(OutputTransformNode.FullPath):
                stats = os.stat(OutputTransformNode.FullPath)
                if stats.st_size == 0:
                    os.remove(OutputTransformNode.FullPath)
                    errmsg = "ir-refine-translate output zero size translate file.  Output deleted: " + OutputTransformNode.FullPath
                    Logger.error(errmsg)

                    # raise Exception(errmsg)
                else:
                    # Thigs are OK, translate to a zero origin.
                    TransformNodeToZeroOrigin(OutputTransformNode)

            SaveRequired = os.path.exists(OutputTransformNode.FullPath)

        finally:
            if os.path.exists(mosaicFullPath):
                os.remove(mosaicFullPath)

    if SaveRequired:
        return TransformParentNode
    else:
        return None

def GridTransform(Parameters, TransformNode, FilterNode, RegistrationDownsample, Logger, **kwargs):
    '''@ChannelNode'''
    Iterations = Parameters.get('it', 10)
    Cell = Parameters.get('Cell', None)
    MeshWidth = Parameters.get('MeshWidth', 6)
    MeshHeight = Parameters.get('MeshHeight', 6)
    Threshold = Parameters.get('Threshold', None)

    LevelNode = FilterNode.TilePyramid.GetOrCreateLevel(RegistrationDownsample)

    Parameters['sp'] = int(RegistrationDownsample)

    OutputTransformName = kwargs.get('OutputTransform', 'Refined_' + TransformNode.Name)

    MangledName = misc.GenNameFromDict(Parameters)

    PixelSpacing = int(LevelNode.Downsample)

    CellString = ''
    if not (Cell  is None):
        CellString = ' -cell ' + str(Cell) + ' '

    ThresholdString = ''
    if not Threshold is None:
        ThresholdString = ' -displacement_threshold %g ' % Threshold

    MeshString = ' -mesh ' + str(MeshWidth) + ' ' + str(MeshHeight) + ' '
    ItString = ' -it ' + str(Iterations) + ' '
    SpacingString = ' -sp ' + str(PixelSpacing) + ' '

    # Check if there is an existing prune map, and if it exists if it is out of date
    TransformParentNode = TransformNode.Parent

    SaveRequired = False

    OutputTransformNode = TransformParentNode.GetChildByAttrib('Transform', 'Path', VolumeManagerETree.MosaicBaseNode.GetFilename(OutputTransformName, MangledName))
    OutputTransformNode = transforms.RemoveIfOutdated(OutputTransformNode, TransformNode, Logger)

    if OutputTransformNode is None:
        OutputTransformNode = VolumeManagerETree.TransformNode(Name=OutputTransformName, Type=MangledName, attrib={'InputTransform' : TransformNode.Name,
                                                                                                                   'InputImageDir' : LevelNode.FullPath,
                                                                                                                   'InputTransformChecksum' : TransformNode.Checksum})

        (SaveRequired, OutputTransformNode) = TransformParentNode.UpdateOrAddChildByAttrib(OutputTransformNode, 'Path')

    if not os.path.exists(OutputTransformNode.FullPath):
        CmdLineTemplate = "ir-refine-grid -load %(InputMosaic)s -save %(OutputMosaic)s -image_dir %(ImageDir)s " + ThresholdString + ItString + CellString + MeshString + SpacingString
        cmd = CmdLineTemplate % {'InputMosaic' : TransformNode.FullPath, 'OutputMosaic' : OutputTransformNode.FullPath, 'ImageDir' : LevelNode.FullPath}
        prettyoutput.CurseString('Cmd', cmd)
        NewP = subprocess.Popen(cmd + " && exit", shell=True, stdout=subprocess.PIPE)
        ProcessOutputInterceptor.Intercept(ProgressOutputInterceptor(NewP))
        OutputTransformNode.cmd = cmd
        SaveRequired = True

        TransformNodeToZeroOrigin(OutputTransformNode)

    if SaveRequired:
        return TransformParentNode
    else:
        return None

def CompressTransforms(Parameters, TransformNode, **kwargs):
    '''Rewrite the provided transform node to represent the same data with less text
       This will change the checksum of the transform, so it may cause portions of the 
       pipeline to execute again if used unwisely'''

    if(TransformNode is None):
        return

    if 'Compressed' in TransformNode.attrib:
        return

    prettyoutput.CurseString('Stage', "CompressTransform")

    if not os.path.exists(TransformNode.FullPath):
        prettyoutput.Log("Input transform file not found: " + TransformNode.FullPath)
        return

    InputFileFullPath = TransformNode.FullPath
    MosaicBaseName = os.path.basename(InputFileFullPath)
    TempMosaicFilename = 'Temp_' + MosaicBaseName
    TempMosaicFileFullPath = os.path.join(os.path.dirname(InputFileFullPath), TempMosaicFilename)

    mFile = mosaicfile.MosaicFile.Load(InputFileFullPath)
    mFile.CompressTransforms()
    mFile.Save(TempMosaicFileFullPath)

    if os.path.exists(TempMosaicFileFullPath):
        shutil.move(TempMosaicFileFullPath, InputFileFullPath)
        TransformNode.ResetChecksum()
        TransformNode.attrib['Compressed'] = 'True'

    return TransformNode.Parent

