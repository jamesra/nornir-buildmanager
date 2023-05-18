'''
Created on Aug 27, 2013

@author: u0490822
'''

import os
import shutil
import logging

import nornir_buildmanager.volumemanager
from nornir_buildmanager import *
from nornir_buildmanager.validation import transforms
from nornir_imageregistration.files import mosaicfile
from nornir_imageregistration.transforms import *
import nornir_shared
from nornir_shared import *

from nornir_shared.files import RemoveOutdatedFile
from nornir_shared.histogram import Histogram
from nornir_shared.misc import SortedListFromDelimited


def Rename(OldNode, NewName, **kwargs):
    if OldNode.Name != NewName:
        OldNode.Name = NewName
        return OldNode.Parent
    
    return None

def MovePath(Node, NewPath, **kwargs):

    if Node.Path == NewPath:
        return None

    if os.path.exists(Node.FullPath):
        oldFullPath = Node.FullPath
        Node.Path = NewPath

        logger = logging.getLogger(__name__ + '.MovePath')

        logger.info("Moving " + oldFullPath + "\n  to " + Node.FullPath)


        if os.path.exists(Node.FullPath):
            if os.path.isdir(Node.FullPath):
                shutil.move(oldFullPath, Node.FullPath)
            else:
                os.remove(Node.FullPath)
                os.rename(oldFullPath, Node.FullPath)
        else:
            shutil.move(oldFullPath, Node.FullPath)
    else:
        Node.Path = NewPath

    return Node.Parent


def CleanNodeIfInvalid(node: nornir_buildmanager.volumemanager.XElementWrapper, **kwargs):
    parent = node.Parent
    (cleaned, reason) = node.CleanIfInvalid()
    if cleaned:
        logger = logging.getLogger(__name__ + '.CleanNodeIfInvalid')
        logger.info(f"Removing node {node}: {reason}")
        return parent
    return None

def RemoveDuplicateLinks(ParentNode, ChildNodeName, ChildAttrib=None, **kwargs):
    '''Find all child nodes with duplicate entries for the ChildAttrib and remove the duplicates'''

    if ChildAttrib is None:
        ChildAttrib = "Name"

    knownValues = []
    NodesToDelete = []
    for c in ParentNode:
        if not c.tag == ChildNodeName:
            continue

        if ChildAttrib in c.attrib:
            val = c.attrib[ChildAttrib]
            if val in knownValues:
                NodesToDelete.append(c)
            else:
                knownValues.append(val)

    print("Found %s nodes " % len(NodesToDelete))

    if len(NodesToDelete) > 0:
        for n in NodesToDelete:
            ParentNode.remove(n)

        return ParentNode
    else:
        return None
    
def RemoveNode(Node):
    '''Simply delete the node'''
    Parent = Node.Parent
     
    prettyoutput.Log(f"Removing {Node}")
    #Node.Clean()
    return Parent

def RemoveChannelChildNode(Node, IgnoreLock, **kwargs):
    '''Delete a child element of a filter, respecting locks if necessary'''
    Parent = Node.Parent
    
    if not isinstance(Node, nornir_buildmanager.volumemanager.FilterNode):
        ParentFilter = Node.FindParent('Channel')
        
    if not ParentFilter is None:
        Locked = ParentFilter.Locked
        if Locked and not IgnoreLock:
            nornir_shared.prettyoutput.Log(f"Filter Locked. Ignoring: {Node.FullPath}")
            return
    else:
        nornir_shared.prettyoutput.LogErr("Unable to find filter for node {0}".format(str(Node)))
        
    if Node.Locked and not IgnoreLock:
        nornir_shared.prettyoutput.Log(f"Node Locked. Ignoring: {Node.FullPath}")
        return
    
    prettyoutput.Log(f"Removing {Node.FullPath}")
    Node.Clean()
    return Parent

def SaveVolumeDataToSingleFile(VolumeNode, save_filename=None, **kwargs):

    VolumeNode = kwargs.get('VolumeElement', None)
    if VolumeNode is None:
        print("No volume node was passed to the function.")
        return
    
    if save_filename is None:
        save_filename = os.path.join(VolumeNode.FullPath, 'VolumeData.SingleFileBackup.xml')

    VolumeNode.LoadAllLinkedNodes()
    VolumeManager.SaveSingleFile(VolumeNode, save_filename)