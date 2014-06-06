'''
Created on Feb 25, 2014

@author: James Anderson
'''

import logging
import math


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

    return FilterNode

if __name__ == '__main__':
    pass