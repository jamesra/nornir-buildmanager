'''
Created on Aug 27, 2013

@author: u0490822
'''

import os
import subprocess
import sys

from nornir_buildmanager import *
from nornir_buildmanager.VolumeManagerETree import *
from nornir_buildmanager.validation import transforms
from nornir_imageregistration.files import mosaicfile
from nornir_imageregistration.transforms import *
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


        if(os.path.exists(Node.FullPath)):
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


def CleanNodeIfInvalid(node, **kwargs):
    parent = node.Parent
    if node.CleanIfInvalid(): 
        logger = logging.getLogger(__name__ + '.CleanNodeIfInvalid')
        logger.info("Removing node %s" % (str(node)))
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

def SaveVolumeDataToSingleFile(VolumeNode, save_filename, **kwargs):

    VolumeNode = kwargs.get('VolumeElement', None)
    if VolumeNode is None:
        print("No volume node was passed to the function.")
        return

    VolumeNode.LoadAllLinkedNodes()
    VolumeManager.SaveSingleFile(VolumeNode, save_filename)