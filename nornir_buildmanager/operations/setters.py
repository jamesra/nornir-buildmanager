'''
Created on Feb 25, 2014

@author: James Anderson
'''

import logging
import math


def SetPruneThreshold(PruneNode, Value, **kwargs):

    logger = kwargs.get('Logger', logging.getLogger('setters'))

    PruneNode.UserRequestedCutoff = str(Value)

    logger.info("Set Prune Threshold to %g on %s" % (Value, PruneNode.Parent.FullPath))

    return PruneNode.Parent


def SetContrastRange(HistogramElement, MinValue, MaxValue, GammaValue, **kwargs):

    logger = kwargs.get('Logger', logging.getLogger('setters'))

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

    logger.info("Set contrast min: %s max: %s, gamma: %s on %s" % (minStr, maxStr, gammaStr, HistogramElement.Parent.FullPath))

    return HistogramElement


if __name__ == '__main__':
    pass