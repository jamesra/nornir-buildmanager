'''
Created on Feb 11, 2013

@author: u0490822
'''

import logging
import os


def __GetAttribOrDefault(Node, Attribute, Default):
    if hasattr(Node, Attribute):
        OutputValue = getattr(Node, Attribute)
        if isinstance(OutputValue, str):
            if len(OutputValue) == 0:
                OutputValue = None
            else:
                try:
                    OutputValue = float(OutputValue)
                except:
                    # Leave it as a string if it does not convert
                    pass

        return OutputValue
    else:
        return Default


def IsValueMatched(OutputNode, OutputAttribute, TargetValue, Precision=None):
    '''Used to test if the output matches an specified target, where the values can be none, an empty string
       or a number.'''

    if OutputNode is None:
        return False

    OutputValue = None
    OutputValue = __GetAttribOrDefault(OutputNode, OutputAttribute, None)

    if isinstance(TargetValue, str):
        if len(TargetValue) == 0:
            TargetValue = None
        else:
            try:
                TargetValue = float(TargetValue)
            except:
                # Leave it as a string if it does not convert
                pass

    if isinstance(OutputValue, float) and not Precision is None:
        OutputValue = round(OutputValue, Precision)

    if isinstance(TargetValue, float) and not Precision is None:
        TargetValue = round(TargetValue, Precision)

    return TargetValue == OutputValue


def RemoveOnMismatch(OutputNode, OutputAttribute, TargetValue, Precision=None):
    '''Remove an element if the attribute does not have the expected value'''

    if OutputNode is None:
        return None

    if IsValueMatched(OutputNode, OutputAttribute, TargetValue, Precision):
        return OutputNode

    reasonStr = OutputAttribute + " = " + str(__GetAttribOrDefault(OutputNode, OutputAttribute, "None")) + " unequal to target value of " + str(TargetValue)
    OutputNode.Clean(reason=reasonStr)
    return None


def IsOutdated(OutputNode, InputNode, Logger=None):
    '''Works for any nodes with corresponding Checksum and InputTranformChecksum attributes and 
       a FullPath attribute to the file system.'''
    assert(hasattr(OutputNode, 'FullPath'))
    assert(hasattr(OutputNode, 'InputTransformChecksum'))
    assert(hasattr(InputNode, 'Checksum'))

    if Logger is None:
        Logger = logging.getLogger(__name__ + ".IsOutdated")

    if not os.path.exists(OutputNode.FullPath):
        Logger.info("Output transform did not exist: " + OutputNode.FullPath)
        OutputNode.Clean()
        return True

    if not OutputNode.InputTransformChecksum == InputNode.Checksum:
        Logger.info("Checksum mismatch: " + InputNode.FullPath + " -> " + OutputNode.FullPath)
        return True

    return False


def RemoveIfOutdated(OutputNode, InputNode, Logger=None):
    '''Removes the output transform node if the input node has changed.
       Returns None if the node did not exist or was removed, otherwise returns the output node'''

    if OutputNode is None:
        return None

    remove = IsOutdated(OutputNode, InputNode , Logger)

    if remove:
        if os.path.exists(OutputNode.FullPath):
            os.remove(OutputNode.FullPath)
        OutputNode.Clean(reason="Pipeline.validation.transforms.IsOutdated returned true")
        return None

    return OutputNode

if __name__ == '__main__':
    pass
