"""
Created on Jun 4, 2012

@author: Jamesan
"""

import os
import shutil
import subprocess

import nornir_buildmanager
from nornir_buildmanager.validation import transforms
from nornir_buildmanager.operations.transform_refine_orchestrator import TransformRefineOrchestrator
import nornir_buildmanager.volumemanager.datanode
import nornir_buildmanager.volumemanager.mosaicbasenode
import nornir_buildmanager.volumemanager.transformnode
import nornir_imageregistration
import nornir_pools
from nornir_shared import *


def TransformNodeToZeroOrigin(transform_node, **kwargs):
    """:return: transform_node if the mosaic was adjusted.  None if the transform_node already had a zero origin"""
    if nornir_imageregistration.Mosaic.TranslateMosaicFileToZeroOrigin(transform_node.FullPath):
        transform_node.ResetChecksum()

        logger = kwargs.get("Logger", None)
        if not logger is None:
            logger.info("Moved %s to zero origin" % transform_node.FullPath)

        return transform_node

    return None


def TranslateTransform(Parameters, TransformNode, FilterNode,
                       RegistrationDownsample,
                       min_overlap: float | None = None,
                       excess_scalar: float | None = None,
                       feature_score_threshold: float | None = None,
                       min_translate_iterations: int | None = None,
                       max_translate_iterations: int | None = None,
                       offset_acceptance_threshold: float | None = None,
                       max_relax_iterations: int | None = None,
                       max_relax_tension_cutoff: float | None = None,
                       first_pass_inter_tile_distance_scale: float | None = None,
                       min_offset_weight=None,
                       max_offset_weight=None,
                       inter_tile_distance_scale: float | None = None,
                       scale_weight_by_feature_score: bool | None = None,
                       exclude_diagonal_overlaps: bool | None = None,
                       Logger=None, **kwargs):
    settings = nornir_imageregistration.settings.TranslateSettings(
        min_overlap=min_overlap,
        max_relax_iterations=max_relax_iterations,
        max_relax_tension_cutoff=max_relax_tension_cutoff,
        feature_score_threshold=feature_score_threshold,
        offset_acceptance_threshold=offset_acceptance_threshold,
        min_translate_iterations=min_translate_iterations,
        max_translate_iterations=max_translate_iterations,
        inter_tile_distance_scale=inter_tile_distance_scale,
        first_pass_inter_tile_distance_scale=None,
        first_pass_excess_scalar=None,
        min_offset_weight=min_offset_weight,
        max_offset_weight=max_offset_weight,
        excess_scalar=excess_scalar,
        use_feature_score=scale_weight_by_feature_score,
        exclude_diagonal_overlaps=exclude_diagonal_overlaps,
        known_offsets=None
    )

    '''@ChannelNode'''
    OutputTransformName = kwargs.get('OutputTransform', 'Translated_' + TransformNode.Name)
    InputTransformNode = TransformNode

    [added_level, LevelNode] = FilterNode.TilePyramid.GetOrCreateLevel(RegistrationDownsample)

    MangledName = misc.GenNameFromDict(Parameters)

    # Check if there is an existing prune map, and if it exists if it is out of date
    TransformParentNode = InputTransformNode.Parent

    SaveRequired = added_level
    OutputTransformPath = nornir_buildmanager.volumemanager.mosaicbasenode.MosaicBaseNode.GetFilename(
        OutputTransformName, "_Max0.5")  # The hardcoded string is legacy to prevent duplicate transform elements
    OutputTransformNode = transforms.LoadOrCleanExistingTransformForInputTransform(channel_node=TransformParentNode,
                                                                                   InputTransformNode=InputTransformNode,
                                                                                   OutputTransformPath=OutputTransformPath)

    OutputTransformSettingsPath = nornir_buildmanager.volumemanager.mosaicbasenode.MosaicBaseNode.GetFilename(
        OutputTransformName, "_Settings", Ext=".json")
    settings_data_node = nornir_buildmanager.volumemanager.datanode.DataNode.Create(Name="Translate Mosaic Settings",
                                                                                    Path=OutputTransformSettingsPath)
    [added_transform_settings_node, settings_data_node] = TransformParentNode.UpdateOrAddChildByAttrib(
        settings_data_node, 'Name')

    ManualOffsetsPath = nornir_buildmanager.volumemanager.mosaicbasenode.MosaicBaseNode.GetFilename(OutputTransformName,
                                                                                                    "_ManualOffsets",
                                                                                                    Ext=".csv")
    manual_offsets_data_node = nornir_buildmanager.volumemanager.datanode.DataNode.Create(Name="Manual Offsets",
                                                                                          Path=ManualOffsetsPath)
    [added_manual_offsets_node, manual_offsets_data_node] = TransformParentNode.UpdateOrAddChildByAttrib(
        manual_offsets_data_node, 'Name')

    settings = nornir_imageregistration.settings.GetOrSaveTranslateSettings(settings, settings_data_node.FullPath)

    TranslateReportPath = nornir_buildmanager.volumemanager.mosaicbasenode.MosaicBaseNode.GetFilename(
        OutputTransformName, "_TranslateReport", Ext=".json")
    translate_report_full_path = os.path.join(TransformParentNode.FullPath, TranslateReportPath)

    # Now that the settings file exists, check if we have offsets to load
    if not os.path.exists(manual_offsets_data_node.FullPath) and \
            not _RestoreManualOffsets(manual_offsets_data_node.FullPath, translate_report_full_path,
                                      OutputTransformNode, Logger):
        # The mosaic was built with manual offsets that are now gone and cannot be restored.
        # Rebuilding would silently drop them, so leave the existing mosaic alone.
        return None

    if os.path.exists(manual_offsets_data_node.FullPath):
        mosaic_offsets = nornir_imageregistration.settings.LoadMosaicOffsets(manual_offsets_data_node.FullPath)
        settings.known_offsets = mosaic_offsets
    else:
        nornir_imageregistration.settings.SaveMosaicOffsets(None, manual_offsets_data_node.FullPath)

    manual_offsets_checksum = nornir_imageregistration.settings.MosaicOffsetsChecksum(settings.known_offsets)

    if OutputTransformNode is not None:
        if OutputTransformNode.Locked:
            if Logger is not None:
                Logger.info("Skipping locked transform %s" % OutputTransformNode.FullPath)
            return None
        else:
            recorded_offsets_checksum = OutputTransformNode.attrib.get('ManualOffsetsChecksum')
            if recorded_offsets_checksum is not None:
                # Compare content, not timestamps: a restored or copied offsets file is newer
                # than the mosaic without being different from what built it.
                if recorded_offsets_checksum != manual_offsets_checksum:
                    OutputTransformNode.Clean(f"{manual_offsets_data_node.FullPath} manual offsets changed")
                    OutputTransformNode = None
            elif files.RemoveOutdatedFile(manual_offsets_data_node.FullPath, OutputTransformNode.FullPath):  # type: ignore[union-attr]
                # Mosaics built before the checksum was recorded fall back to the timestamp test.
                OutputTransformNode.Clean(f"{manual_offsets_data_node.FullPath} settings file was updated")
                OutputTransformNode = None

            if OutputTransformNode is not None and \
                    files.RemoveOutdatedFile(settings_data_node.FullPath, OutputTransformNode.FullPath):  # type: ignore[union-attr]
                OutputTransformNode.Clean(f"{settings_data_node.FullPath} settings file was updated")
                OutputTransformNode = None

    if OutputTransformNode is None:
        OutputTransformNode = nornir_buildmanager.volumemanager.transformnode.TransformNode.Create(
            Name=OutputTransformName, Path=OutputTransformPath, Type=MangledName,
            attrib={'InputImageDir': LevelNode.FullPath})
        OutputTransformNode.SetTransform(InputTransformNode)
        (SaveRequired, OutputTransformNode) = TransformParentNode.UpdateOrAddChildByAttrib(OutputTransformNode, 'Path')

    if not os.path.exists(OutputTransformNode.FullPath):
        # Tired of dealing with ir-refine-translate crashing when a tile is missing, load the mosaic and ensure the tile names are correct before running ir-refine-translate

        haveTempFile = False
        try:
            # Still strip missing tiles before ArrangeTilesWithTranslate; a mosaic that
            # names deleted images otherwise fails mid-arrange.
            tempMosaicFullPath = os.path.join(InputTransformNode.Parent.FullPath, "Temp" + InputTransformNode.Path)
            mfileObj = nornir_imageregistration.MosaicFile.Load(InputTransformNode.FullPath)
            if mfileObj is None:
                if Logger is not None:
                    Logger.warning("Could not load %s" % InputTransformNode.FullPath)
                return None

            invalidFiles = mfileObj.RemoveInvalidMosaicImages(LevelNode.FullPath)

            mosaicToLoadPath = InputTransformNode.FullPath
            if invalidFiles:
                haveTempFile = True
                mfileObj.Save(tempMosaicFullPath)
                mosaicToLoadPath = tempMosaicFullPath

            mosaicObj = nornir_imageregistration.Mosaic.LoadFromMosaicFile(mosaicToLoadPath)
            tileset = nornir_imageregistration.mosaic_tileset.CreateFromMosaic(mosaicObj,
                                                                               image_folder=LevelNode.FullPath,
                                                                               image_to_source_space_scale=LevelNode.Downsample)
            translate_report = nornir_imageregistration.TranslateReport()
            firstpass_translated_mosaicObj_tileset = tileset.ArrangeTilesWithTranslate(settings,
                                                                                       report=translate_report)

            firstpass_translated_mosaicObj_tileset.SaveMosaic(OutputTransformNode.FullPath)

            SaveRequired = SaveRequired or os.path.exists(OutputTransformNode.FullPath)

            # Settings staleness is handled by RemoveOutdatedFile above, manual offsets by this
            # content checksum. Do not write path-string DataChecksums as
            # TranslateSettingsChecksum / ManualMosaicOffsetsChecksum: they never change when
            # file contents change (#219).
            OutputTransformNode.attrib['ManualOffsetsChecksum'] = manual_offsets_checksum
            OutputTransformNode.attrib['ManualOffsetsCount'] = str(len(settings.known_offsets))
            OutputTransformNode.ResetChecksum()

            _SaveTranslateReport(translate_report, translate_report_full_path, TranslateReportPath,
                                 TransformParentNode, OutputTransformNode, Logger)

            print("%s -> %s" % (OutputTransformNode.FullPath,
                                nornir_imageregistration.MosaicFile.LoadChecksum(OutputTransformNode.FullPath)))
        except Exception as e:
            OutputTransformNode.Clean(f"Exception generating mosaic tile translations:\n{e}")
            raise
        finally:
            try:
                if haveTempFile:
                    os.remove(tempMosaicFullPath)
            except FileNotFoundError:
                pass

    if SaveRequired or added_transform_settings_node or added_manual_offsets_node:
        nornir_pools.ReleaseStagePools()
        return TransformParentNode
    else:
        return None


def _RestoreManualOffsets(offsets_path: str, report_path: str, output_transform_node, Logger) -> bool:
    """Recreate a missing manual offsets file from the last translate report.

    :return: False when the existing mosaic was built with manual offsets that cannot be
        restored, so the caller must not rebuild it; True otherwise.
    """
    if os.path.exists(report_path):
        restored = nornir_imageregistration.translate_report.load_known_offsets(report_path)
        if restored:
            nornir_imageregistration.settings.SaveMosaicOffsets(restored, offsets_path)
            message = (f"Manual offsets file {offsets_path} was missing; restored {len(restored)} offsets "
                       f"from {report_path}")
            prettyoutput.LogErr(message)
            if Logger is not None:
                Logger.warning(message)
            return True

    if output_transform_node is None:
        return True

    built_with = int(output_transform_node.attrib.get('ManualOffsetsCount', '0'))
    if built_with == 0:
        return True

    message = (f"Manual offsets file {offsets_path} is missing, and {output_transform_node.FullPath} was built "
               f"with {built_with} manual offsets that cannot be restored. Not rebuilding it; restore the "
               f"offsets file, or delete the mosaic to rebuild without them.")
    prettyoutput.LogErr(message)
    if Logger is not None:
        Logger.error(message)
    return False


def _SaveTranslateReport(report, report_full_path: str, report_path: str, transform_parent_node,
                         output_transform_node, Logger) -> None:
    """Write the translate report beside the mosaic and surface a join that needs review."""
    report.save(report_full_path)
    report_node = nornir_buildmanager.volumemanager.datanode.DataNode.Create(Name="Translate Report",
                                                                             Path=report_path)
    transform_parent_node.UpdateOrAddChildByAttrib(report_node, 'Name')

    if report.needs_review:
        output_transform_node.attrib['NeedsReview'] = '1'
        for reason in report.review_reasons:
            message = f"{output_transform_node.FullPath} needs review: {reason}"
            prettyoutput.LogErr(message)
            if Logger is not None:
                Logger.warning(message)
    elif 'NeedsReview' in output_transform_node.attrib:
        del output_transform_node.attrib['NeedsReview']


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
    """@ChannelNode"""
    Iterations = Parameters.get('it', 10)
    Cell = Parameters.get('Cell', None)
    MeshWidth = Parameters.get('MeshWidth',
                               8)  # These should be large enough numbers that there is overlap between cells, ~50% though 25% is often used for speed.
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

    # Check if there is an existing prune map, and if it exists if it is out of date
    TransformParentNode = InputTransformNode.Parent

    SaveRequired = added_level

    OutputTransformPath = nornir_buildmanager.volumemanager.mosaicbasenode.MosaicBaseNode.GetFilename(
        OutputTransformName, MangledName)
    OutputTransformNode = transforms.LoadOrCleanExistingTransformForInputTransform(channel_node=TransformParentNode,
                                                                                   InputTransformNode=InputTransformNode,
                                                                                   OutputTransformPath=OutputTransformPath)
    if OutputTransformNode is None:
        OutputTransformNode = nornir_buildmanager.volumemanager.transformnode.TransformNode.Create(
            Name=OutputTransformName, Path=OutputTransformPath, Type=MangledName,
            attrib={'InputImageDir': LevelNode.FullPath})
        OutputTransformNode.SetTransform(InputTransformNode)
        (SaveRequired, OutputTransformNode) = TransformParentNode.UpdateOrAddChildByAttrib(OutputTransformNode, 'Path')
    elif OutputTransformNode.Locked:
        Logger.info("Skipping locked transform %s" % OutputTransformNode.FullPath)
        return None

    orchestrator = TransformRefineOrchestrator(logger=Logger)
    decision = orchestrator.should_skip_refine(InputTransformNode, OutputTransformNode)
    if decision.skip and os.path.exists(OutputTransformNode.FullPath):
        Logger.info("Skipping mosaic grid refine; %s: %s", decision.reason, OutputTransformNode.FullPath)
    elif not decision.skip and os.path.exists(OutputTransformNode.FullPath):
        orchestrator.invalidate_stale_output(OutputTransformNode, decision.reason)
        OutputTransformNode = transforms.LoadOrCleanExistingTransformForInputTransform(
            channel_node=TransformParentNode,
            InputTransformNode=InputTransformNode,
            OutputTransformPath=OutputTransformPath)
        if OutputTransformNode is None:
            OutputTransformNode = nornir_buildmanager.volumemanager.transformnode.TransformNode.Create(
                Name=OutputTransformName, Path=OutputTransformPath, Type=MangledName,
                attrib={'InputImageDir': LevelNode.FullPath})
            OutputTransformNode.SetTransform(InputTransformNode)
            (SaveRequired, OutputTransformNode) = TransformParentNode.UpdateOrAddChildByAttrib(
                OutputTransformNode, 'Path')

    if not os.path.exists(OutputTransformNode.FullPath):
        try:
            cell_size = None if Cell is None else (int(Cell), int(Cell))
            # RefineGridMosaic expects (rows, cols)
            mesh_shape = (int(MeshHeight), int(MeshWidth))
            displacement_threshold = 1.0 if Threshold is None else float(Threshold)
            image_scale = 1.0 / float(PixelSpacing)

            refined_mosaic, diagnostics = nornir_imageregistration.RefineGridMosaic(
                InputTransformNode.FullPath,
                LevelNode.FullPath,
                iterations=int(Iterations),
                cell_size=cell_size,
                mesh_shape=mesh_shape,
                displacement_threshold=displacement_threshold,
                imageScale=image_scale,
                return_diagnostics=True,
            )

            refined_mosaic.SaveToMosaicFile(OutputTransformNode.FullPath)

            OutputTransformNode.cmd = (
                "python:nornir_imageregistration.RefineGridMosaic("
                f"input='{InputTransformNode.FullPath}', image_dir='{LevelNode.FullPath}', "
                f"iterations={int(Iterations)}, cell_size={cell_size}, mesh_shape={mesh_shape}, "
                f"displacement_threshold={displacement_threshold:g}, imageScale={image_scale:g})"
            )
            OutputTransformNode.SetTransform(InputTransformNode)
            SaveRequired = True

            if Logger is not None:
                last_displacement = (
                    diagnostics.average_displacement_per_iteration[-1]
                    if diagnostics.average_displacement_per_iteration
                    else 0.0
                )
                last_max = (
                    diagnostics.max_displacement_per_iteration[-1]
                    if diagnostics.max_displacement_per_iteration
                    else 0.0
                )
                Logger.info(
                    "RefineGridMosaic completed %d pass(es), converged=%s, last mean displacement=%g, max=%g",
                    diagnostics.iterations_completed,
                    diagnostics.converged,
                    last_displacement,
                    last_max,
                )

            TransformNodeToZeroOrigin(OutputTransformNode)
        except Exception as e:
            OutputTransformNode.Clean(f"Exception generating mosaic grid transform:\n{e}")
            raise

    if SaveRequired:
        nornir_pools.ReleaseStagePools()
        return TransformParentNode
    else:
        return None


def CompressTransforms(Parameters, TransformNode, **kwargs):
    """Rewrite the provided transform node to represent the same data with less text
       This will change the checksum of the transform, so it may cause portions of the
       pipeline to execute again if used unwisely"""

    if TransformNode is None:
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
