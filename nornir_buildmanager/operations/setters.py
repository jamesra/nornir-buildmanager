'''
Created on Feb 25, 2014

@author: James Anderson
'''

import logging
import math
import os
from nornir_buildmanager import VolumeManagerHelpers

def SetFilterLock(Node, Locked):
    ParentFilter = Node.FindParent('Filter')
    if not ParentFilter is None:
        ParentFilter.Locked = Locked


def SetPruneThreshold(PruneNode, Value, **kwargs):

    logger = logging.getLogger(__name__ + '.SetPruneThreshold')

    PruneNode.UserRequestedCutoff = str(Value)

    SetFilterLock(PruneNode, False)

    logger.info("Set Prune Threshold to %g on %s" % (Value, PruneNode.Parent.FullPath))

    return PruneNode.FindParent('Filter')


def SetContrastRange(HistogramElement, MinValue, MaxValue, GammaValue, **kwargs):

    logger = logging.getLogger(__name__ + '.SetContrastRange')

    if math.isnan(MinValue):
        minStr = "Default"
    else:
        minStr = MinValue

    if math.isnan(MaxValue):
        maxStr = "Default"
    else:
        maxStr = MaxValue

    if math.isnan(GammaValue):
        gammaStr = "Default"
    else:
        gammaStr = GammaValue

    AutoLevelHint = HistogramElement.GetOrCreateAutoLevelHint()

    AutoLevelHint.UserRequestedMinIntensityCutoff = MinValue
    AutoLevelHint.UserRequestedMaxIntensityCutoff = MaxValue
    AutoLevelHint.UserRequestedGamma = GammaValue

    SetFilterLock(AutoLevelHint, False)

    logger.info("Set contrast min: %s max: %s, gamma: %s on %s" % (minStr, maxStr, gammaStr, HistogramElement.Parent.FullPath))

    return HistogramElement.FindParent('Filter')


def SetFilterContrastLocked(FilterNode, Locked, **kwargs):

    logger = logging.getLogger(__name__ + '.SetFilterContrastLocked')

    FilterNode.Locked = bool(int(Locked))

    logger.info("Setting filter to locked = " + str(Locked) + "  " + FilterNode.FullPath)

    return FilterNode.Parent

def SetFilterMaskName(FilterNode, MaskName, **kwargs):
    
    logger = logging.getLogger(__name__ + '.SetFilterContrastLocked')

    FilterNode.MaskName = MaskName

    logger.info("Setting filter MaskName to " + MaskName + "  " + FilterNode.FullPath)

    return FilterNode.Parent

def _MapTransformToCurrentType(transform_node, name, old_type, new_type):
    '''Rename a transform if it matches the name parameter.  Adjust both the path and type attributes'''
    
    if transform_node.Name != name:
        return False
    
    if transform_node.Type != old_type:
        return False

    (filename, ext) = os.path.splitext(transform_node.Path)
    transform_node.Type = new_type
    new_path = transform_node.Name + new_type + ext
    
    if os.path.exists(transform_node.FullPath):
        output_transform_path = os.path.join(transform_node.Parent.FullPath, new_path)
        if os.path.exists(output_transform_path):
            os.remove(transform_node.FullPath)
        else:
            os.rename(transform_node.FullPath, os.path.join(transform_node.Parent.FullPath, new_path))
            transform_node.Path = new_path
            
    return True

def MigrateChannel_1p2_to_1p3(channel_node, **kwargs):
    
    for transform_node in channel_node.findall('Transform'):
        MigrateTransforms_1p2_to_1p3(transform_node)
        
    print("Saving %s" % (channel_node.Parent.FullPath))
    return channel_node

def MigrateTransforms_1p2_to_1p3(transform_node, **kwargs):
    '''Update the checksums to use the new algorithm.  Then rename grid transforms to use the sorted type name'''
    
    if not os.path.exists(transform_node.FullPath):
        transform_node.Clean()
        return transform_node.Parent
    
    original_checksum = transform_node.Checksum
    original_type = transform_node.Type
    
    transform_node.ResetChecksum()
    transform_node.Locked = True

    _MapTransformToCurrentType(transform_node, name='Grid', old_type='_Cel128_Mes8_sp4_Mes8_Thr0.25', new_type='_Cel128_Mes8_Mes8_Thr0.25_it10_sp4')
    _MapTransformToCurrentType(transform_node, name='Grid', old_type='_Cel96_Mes8_sp4_Mes8_Thr0.5', new_type='_Cel128_Mes8_Mes8_Thr0.25_it10_sp4')
    
    #All done changing the transforms meta-data.  Now update transforms which depend on us with correct information
    for dependent in VolumeManagerHelpers.InputTransformHandler.EnumerateTransformDependents(transform_node.Parent, original_checksum, original_type, recursive=True):
        if dependent.HasInputTransform:
            dependent.SetTransform(transform_node)
            
    return transform_node.Parent
        

if __name__ == '__main__':
    pass