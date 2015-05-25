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

def PrintSectionsDamaged(block_node, **kwargs):
    print(','.join(list(map(str,block_node.NonStosSectionNumbers))))
    return None

def MarkSectionsAsDamaged(block_node, SectionNumbers, **kwargs):
    block_node.MarkSectionsAsDamaged(SectionNumbers)
    return block_node.Parent
    
def MarkSectionsAsUndamaged(block_node, SectionNumbers, **kwargs):
    block_node.MarkSectionsAsUndamaged(SectionNumbers)
    return block_node.Parent

if __name__ == '__main__':
    pass