'''
Created on Aug 27, 2013

@author: u0490822
'''

import logging
import os
import shutil

import nornir_buildmanager
from nornir_buildmanager.volumemanager import XElementWrapper, FilterNode
import nornir_shared
from nornir_shared import *


def Rename(OldNode, NewName, **kwargs):
    if OldNode.Name != NewName:
        OldNode.Name = NewName
        return OldNode.Parent

    return None


def MovePath(Node, NewPath: str, **kwargs):
    """Move a node's file or directory to ``NewPath`` (a sibling name) and update its Path.

    The move happens before ``Node.Path`` is set: a container's Path setter creates the new directory, and
    ``shutil.move`` into an existing directory nests the source inside it (RC1_Crush 2026-11-11). An empty
    destination directory is replaced; a non-empty one is left alone and the node is not moved.
    """
    if Node.Path == NewPath:
        return None

    oldFullPath = Node.FullPath
    if not os.path.exists(oldFullPath):
        Node.Path = NewPath
        return Node.Parent

    logger = logging.getLogger(__name__ + '.MovePath')
    newFullPath = os.path.join(os.path.dirname(oldFullPath.rstrip('/\\')), NewPath)

    if os.path.isdir(newFullPath):
        if any(os.scandir(newFullPath)):
            logger.error(f"Not moving {oldFullPath}: destination {newFullPath} already exists and is not empty")
            return None
        os.rmdir(newFullPath)
    elif os.path.exists(newFullPath):
        os.remove(newFullPath)

    logger.info(f"Moving {oldFullPath}\n  to {newFullPath}")
    shutil.move(oldFullPath, newFullPath)
    Node.Path = NewPath
    return Node.Parent


def CleanNodeIfInvalid(node: XElementWrapper, **kwargs):
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
    # Node.Clean()
    return Parent


def RemoveChannelChildNode(Node, IgnoreLock, **kwargs):
    '''Delete a child element of a filter, respecting locks if necessary'''
    Parent = Node.Parent

    if not isinstance(Node, FilterNode):
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
    nornir_buildmanager.volumemanager.VolumeManager.SaveSingleFile(VolumeNode, save_filename)
