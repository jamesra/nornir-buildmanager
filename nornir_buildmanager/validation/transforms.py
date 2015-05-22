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


def RemoveOnMismatch(OutputNode, OutputAttribute, TargetValue, Precision=None, NodeToRemove=None):
    '''Remove an element if the attribute does not have the expected value
    :param object NodeToRemove: The node that should be removed on a mismatch, if None remove the OutputNode
    :param object TargetValue: Value we expect the attribute to have
    :param str OutputAttribute: Name of attribute on OutputNode containing value to check
    :param object OutputNode: Node that must have the attributes'''
 
    if OutputNode is None:
        return None

    if IsValueMatched(OutputNode, OutputAttribute, TargetValue, Precision):
        return OutputNode

    reasonStr = OutputAttribute + " = " + str(__GetAttribOrDefault(OutputNode, OutputAttribute, "None")) + " unequal to target value of " + str(TargetValue)
    if NodeToRemove is None:
        NodeToRemove = OutputNode
        
    NodeToRemove.Clean(reason=reasonStr)
    return None


def LoadOrCleanExistingTransformForInputTransform(channel_node, InputTransformNode, OutputTransformPath):
    '''Return the existing transform node if it exists and if the input transform matches the passed input transform node.  If the transform is locked always return the existing transform node'''
    
    OutputTransformNode = channel_node.GetChildByAttrib('Transform', 'Path', OutputTransformPath)
    if not OutputTransformNode is None:
        if not os.path.exists(OutputTransformNode.FullPath):
            OutputTransformNode.Clean("Output transform file does not exist %s" % OutputTransformNode.FullPath)
            OutputTransformNode = None
        elif not OutputTransformNode.Locked:
            if not OutputTransformNode.IsInputTransformMatched(InputTransformNode):
                OutputTransformNode.Clean("New input transform %s was specified" % InputTransformNode.FullPath)
                OutputTransformNode = None
                
    return OutputTransformNode

if __name__ == '__main__':
    pass
