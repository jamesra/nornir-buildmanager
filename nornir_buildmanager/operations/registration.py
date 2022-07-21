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
import nornir_imageregistration 
from nornir_shared import *
import nornir_pools
from nornir_shared.processoutputinterceptor import ProcessOutputInterceptor, \
    ProgressOutputInterceptor


def TransformNodeToZeroOrigin(transform_node, **kwargs):
    ''':return: transform_node if the mosaic was adjusted.  None if the transform_node already had a zero origin'''
    if nornir_imageregistration.Mosaic.TranslateMosaicFileToZeroOrigin(transform_node.FullPath):
        transform_node.ResetChecksum()

        logger = kwargs.get("Logger", None)
        if not logger is None:
            logger.info("Moved %s to zero origin" % transform_node.FullPath)

        return transform_node

    return None


def TranslateTransform(Parameters, TransformNode, FilterNode,
                       RegistrationDownsample,
                       min_overlap=None,
                       excess_scalar=None,
                       feature_score_threshold=None,
                       min_translate_iterations=None,
                       offset_acceptance_threshold=None,
                       max_relax_iterations=None,
                       max_relax_tension_cutoff=None,
                       first_pass_inter_tile_distance_scale=None,
                       inter_tile_distance_scale=None,
                       Logger=None, **kwargs):
    '''@ChannelNode'''
    OutputTransformName = kwargs.get('OutputTransform', 'Translated_' + TransformNode.Name)
    InputTransformNode = TransformNode

    [added_level, LevelNode] = FilterNode.TilePyramid.GetOrCreateLevel(RegistrationDownsample)
    
    if added_level or  LevelNode.AttributesChanged:
        (yield LevelNode.Parent)

    MangledName = misc.GenNameFromDict(Parameters)

    # Check if there is an existing prune map, and if it exists if it is out of date
    TransformParentNode = InputTransformNode.Parent

    SaveRequired = added_level
    OutputTransformPath = VolumeManagerETree.MosaicBaseNode.GetFilename(OutputTransformName, "_Max0.5")  # The hardcoded string is legacy to prevent duplicate transform elements
    OutputTransformNode = transforms.LoadOrCleanExistingTransformForInputTransform(channel_node=TransformParentNode,
                                                                                   InputTransformNode=InputTransformNode,
                                                                                   OutputTransformPath=OutputTransformPath)

    if OutputTransformNode is None:
        OutputTransformNode = VolumeManagerETree.TransformNode.Create(Name=OutputTransformName, Path=OutputTransformPath, Type=MangledName, attrib={'InputImageDir': LevelNode.FullPath})
        OutputTransformNode.SetTransform(InputTransformNode)
        (SaveRequired, OutputTransformNode) = TransformParentNode.UpdateOrAddChildByAttrib(OutputTransformNode, 'Path')
    elif OutputTransformNode.Locked:
        Logger.info("Skipping locked transform %s" % OutputTransformNode.FullPath)
        return None

    if not os.path.exists(OutputTransformNode.FullPath):

        # Tired of dealing with ir-refine-translate crashing when a tile is missing, load the mosaic and ensure the tile names are correct before running ir-refine-translate
    
        # TODO: This check for invalid tiles may no longer be needed since we do not use ir-refine-translate anymore
        tempMosaicFullPath = os.path.join(InputTransformNode.Parent.FullPath, "Temp" + InputTransformNode.Path)
        mfileObj = nornir_imageregistration.MosaicFile.Load(InputTransformNode.FullPath)
        if mfileObj is None:
            Logger.warning("Could not load %s" % (InputTransformNode.FullPath))
            return None
        
        invalidFiles = mfileObj.RemoveInvalidMosaicImages(LevelNode.FullPath)
        
        mosaicToLoadPath = InputTransformNode.FullPath
        if invalidFiles:
            mfileObj.Save(tempMosaicFullPath)
            mosaicToLoadPath = tempMosaicFullPath
            
        mosaicObj = nornir_imageregistration.Mosaic.LoadFromMosaicFile(mosaicToLoadPath)
        tileset = nornir_imageregistration.mosaic_tileset.CreateFromMosaic(mosaicObj, image_folder=LevelNode.FullPath, image_to_source_space_scale=LevelNode.Downsample)
        firstpass_translated_mosaicObj_tileset = tileset.ArrangeTilesWithTranslate(  excess_scalar=excess_scalar,
                                                                             min_overlap=min_overlap,
                                                                             feature_score_threshold=feature_score_threshold,
                                                                             min_translate_iterations=min_translate_iterations,
                                                                             offset_acceptance_threshold=offset_acceptance_threshold,
                                                                             max_relax_iterations=max_relax_iterations,
                                                                             max_relax_tension_cutoff=max_relax_tension_cutoff,
                                                                             first_pass_inter_tile_distance_scale=first_pass_inter_tile_distance_scale,
                                                                             inter_tile_distance_scale=inter_tile_distance_scale)
        
        firstpass_translated_mosaicObj_tileset.SaveMosaic(OutputTransformNode.FullPath)

        SaveRequired = SaveRequired or os.path.exists(OutputTransformNode.FullPath)
        
        print("%s -> %s" % (OutputTransformNode.FullPath, nornir_imageregistration.MosaicFile.LoadChecksum(OutputTransformNode.FullPath)))
        
        if os.path.exists(tempMosaicFullPath):
            os.remove(tempMosaicFullPath)
 
    if SaveRequired:
        nornir_pools.ClosePools()  # A workaround to avoid running out of memory
        return TransformParentNode
    else:
        return None

# 
# def TranslateTransform_IrTools(Parameters, TransformNode, FilterNode, RegistrationDownsample, Logger, **kwargs):
#     '''@ChannelNode'''
#     MaxOffsetX = Parameters.get('MaxOffsetX', 0.1)
#     MaxOffsetY = Parameters.get('MaxOffsetY', 0.1)
#     BlackMaskX = Parameters.get('BlackMaskX', None)
#     BlackMaskY = Parameters.get('BlackMaskY', None)
# 
#     OutputTransformName = kwargs.get('OutputTransform', 'Translated_' + TransformNode.Name)
#     InputTransformNode = TransformNode
# 
#     [added_level, LevelNode] = FilterNode.TilePyramid.GetOrCreateLevel(RegistrationDownsample)
# 
#     MangledName = misc.GenNameFromDict(Parameters)
# 
#     if MaxOffsetX < 1:
#         MaxOffsetX = MaxOffsetX * 100
# 
#     if MaxOffsetY < 1:
#         MaxOffsetY = MaxOffsetY * 100
# 
#     MaxOffsetString = ''
#     if (not MaxOffsetX is None) and (not MaxOffsetY is None):
#         MaxOffsetString = ' -max_offset ' + str(MaxOffsetX) + '%% ' + str(MaxOffsetY) + '%% '
# 
#     BlackMaskString = ''
#     if (not BlackMaskX is None) and (not BlackMaskY is None):
#         BlackMaskString = '-black_mask ' + str(BlackMaskX) + '%% ' + str(BlackMaskY) + '%% '
# 
#     # Check if there is an existing prune map, and if it exists if it is out of date
#     TransformParentNode = TransformNode.Parent
# 
#     SaveRequired = added_level
# 
#     OutputTransformNode = TransformParentNode.GetChildByAttrib('Transform', 'Path', VolumeManagerETree.MosaicBaseNode.GetFilename(OutputTransformName, MangledName))
#     if OutputTransformNode is None:
#         OutputTransformNode = VolumeManagerETree.TransformNode.Create(Name=OutputTransformName, Path=OutputTransformPath, Type=MangledName, attrib={'InputImageDir' : LevelNode.FullPath})
#         OutputTransformNode.SetTransform(InputTransformNode)
#         (SaveRequired, OutputTransformNode) = TransformParentNode.UpdateOrAddChildByAttrib(OutputTransformNode, 'Path')
#     elif OutputTransformNode.Locked:
#         Logger.info("Skipping locked transform %s" % OutputTransformNode.FullPath)
#         return None
# 
#     if not os.path.exists(OutputTransformNode.FullPath):
# 
#         # Tired of dealing with ir-refine-translate crashing when a tile is missing, load the mosaic and ensure the tile names are correct before running ir-refine-translate
#         try:
#             mosaicFullPath = os.path.join(InputTransformNode.Parent.FullPath, "Temp" + InputTransformNode.Path)
#             mfileObj = nornir_imageregistration.MosaicFile.Load(InputTransformNode.FullPath)
#             invalidFiles = mfileObj.RemoveInvalidMosaicImages(LevelNode.FullPath)
#             if invalidFiles:
#                 mfileObj.Save(mosaicFullPath)
#             else:
#                 shutil.copy(InputTransformNode.FullPath, mosaicFullPath)
# 
#             CmdLineTemplate = "ir-refine-translate -load %(InputMosaic)s -save %(OutputMosaic)s -image_dir %(ImageDir)s -noclahe  -sp " + str(LevelNode.Downsample) + MaxOffsetString + BlackMaskString
#             cmd = CmdLineTemplate % {'InputMosaic' : mosaicFullPath, 'OutputMosaic' : OutputTransformNode.FullPath, 'ImageDir' : LevelNode.FullPath}
#             prettyoutput.CurseString('Cmd', cmd)
#             NewP = subprocess.Popen(cmd + " && exit", shell=True, stdout=subprocess.PIPE)
#             ProcessOutputInterceptor.Intercept(ProgressOutputInterceptor(NewP))
#             OutputTransformNode.cmd = cmd
# 
#             if os.path.exists(OutputTransformNode.FullPath):
#                 stats = os.stat(OutputTransformNode.FullPath)
#                 if stats.st_size == 0:
#                     os.remove(OutputTransformNode.FullPath)
#                     errmsg = "ir-refine-translate output zero size translate file.  Output deleted: " + OutputTransformNode.FullPath
#                     Logger.error(errmsg)
# 
#                     # raise Exception(errmsg)
#                 else:
#                     # Thigs are OK, translate to a zero origin.
#                     TransformNodeToZeroOrigin(OutputTransformNode)
# 
#             SaveRequired = os.path.exists(OutputTransformNode.FullPath)
# 
#         finally:
#             if os.path.exists(mosaicFullPath):
#                 os.remove(mosaicFullPath)
# 
#     if SaveRequired:
#         return TransformParentNode
#     else:
#         return None


def GridTransform(Parameters, TransformNode, FilterNode, RegistrationDownsample, Logger, **kwargs):
    '''@ChannelNode'''
    Iterations = Parameters.get('it', 10)
    Cell = Parameters.get('Cell', None)
    MeshWidth = Parameters.get('MeshWidth', 8)  # These should be large enough nubmers that there is overlap between cells, ~50% though 25% is often used for speed.
    MeshHeight = Parameters.get('MeshHeight', 8)
    Threshold = Parameters.get('Threshold', None)
    
    InputTransformNode = TransformNode
    
    (Valid, Reason) = InputTransformNode.IsValid()
    if not Valid:
        Logger.warning("Skipping refinement of invalid transform %s\n%s" % (InputTransformNode.FullPath, Reason))
        return None

    [added_level, LevelNode] = FilterNode.TilePyramid.GetOrCreateLevel(RegistrationDownsample)

    Parameters['sp'] = int(RegistrationDownsample)

    OutputTransformName = kwargs.get('OutputTransform', 'Refined_' + InputTransformNode.Name)

    MangledName = misc.GenNameFromDict(Parameters)

    PixelSpacing = int(LevelNode.Downsample)

    CellString = ''
    if not (Cell  is None):
        CellString = f' -cell {Cell} '

    ThresholdString = ''
    if not Threshold is None:
        ThresholdString = ' -displacement_threshold %g ' % Threshold

    MeshString = f' -mesh {MeshWidth} {MeshHeight} '
    ItString = f' -it {Iterations} '
    SpacingString = f' -sp {PixelSpacing} '

    # Check if there is an existing prune map, and if it exists if it is out of date
    TransformParentNode = InputTransformNode.Parent

    SaveRequired = added_level

    OutputTransformPath = VolumeManagerETree.MosaicBaseNode.GetFilename(OutputTransformName, MangledName)
    OutputTransformNode = transforms.LoadOrCleanExistingTransformForInputTransform(channel_node=TransformParentNode, InputTransformNode=InputTransformNode, OutputTransformPath=OutputTransformPath)
    if OutputTransformNode is None:
        OutputTransformNode = VolumeManagerETree.TransformNode.Create(Name=OutputTransformName, Path=OutputTransformPath, Type=MangledName, attrib={'InputImageDir': LevelNode.FullPath})
        OutputTransformNode.SetTransform(InputTransformNode)
        (SaveRequired, OutputTransformNode) = TransformParentNode.UpdateOrAddChildByAttrib(OutputTransformNode, 'Path')
    elif OutputTransformNode.Locked:
        Logger.info("Skipping locked transform %s" % OutputTransformNode.FullPath)
        return None

    if not os.path.exists(OutputTransformNode.FullPath):
        #This is a workaround for ir-refine-grid appearing to reverse the X,Y coordinates from the documented order on ITK's website for Rigid and and CenteredSimiliarity transforms
        InputMosaic = InputTransformNode.FullPath
        try:
            #TempInputMosaic = InputMosaic

            TempInputMosaic = os.path.join(os.path.dirname(InputMosaic), f'Corrected_{os.path.basename(InputMosaic)}')
            mosaic = nornir_imageregistration.MosaicFile.Load(InputTransformNode.FullPath)
            #NeedsXYSwap = mosaic.HasTransformsNotUnderstoodByIrTools()
            workaroundMosaic = mosaic.CreateCorrectedXYMosaicForStupidITKWorkaround()
            workaroundMosaic.Save(TempInputMosaic)
            
            CmdLineTemplate = "ir-refine-grid -load %(InputMosaic)s -save %(OutputMosaic)s -image_dir %(ImageDir)s " + ThresholdString + ItString + CellString + MeshString + SpacingString
            cmd = CmdLineTemplate % {'InputMosaic': TempInputMosaic, 'OutputMosaic': OutputTransformNode.FullPath, 'ImageDir': LevelNode.FullPath}
            prettyoutput.CurseString('Cmd', cmd)
            NewP = subprocess.Popen(cmd + " && exit", shell=True, stdout=subprocess.PIPE)
            output = ProcessOutputInterceptor.Intercept(ProgressOutputInterceptor(NewP))
            
            if len(output) == 0:
                raise RuntimeError("No output from ir-refine-grid.  Ensure that ir-refine-grid executable from the SCI ir-tools package is on the system path.")
    
            OutputTransformNode.cmd = cmd
            SaveRequired = True
    
            TransformNodeToZeroOrigin(OutputTransformNode)
            
            #Reverse the output of ir-refine-grid on the X,Y axis dues ot 
        finally:
            os.remove(TempInputMosaic)
            pass

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

    mFile = nornir_imageregistration.MosaicFile.Load(InputFileFullPath)
    mFile.CompressTransforms()
    mFile.Save(TempMosaicFileFullPath)

    if os.path.exists(TempMosaicFileFullPath):
        shutil.move(TempMosaicFileFullPath, InputFileFullPath)
        TransformNode.ResetChecksum()
        TransformNode.Compressed = True

    return TransformNode.Parent

