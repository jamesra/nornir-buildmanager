'''
Created on Aug 27, 2013

@author: u0490822
'''

import sys
import os
from nornir_buildmanager import *
from nornir_buildmanager.VolumeManagerETree import *
from nornir_imageregistration.io import  mosaicfile
import subprocess
from nornir_shared.histogram import Histogram
from nornir_shared import *
from nornir_shared.files import RemoveOutdatedFile
from nornir_imageregistration.transforms import *
from nornir_buildmanager.validation import transforms
from nornir_shared.misc import SortedListFromDelimited


def Rename(OldNode, NewName, **kwargs):
    OldNode.Name = NewName
    return OldNode.Parent


def MovePath(Node, NewPath, **kwargs):

    if os.path.exists(Node.FullPath):
        oldFullPath = Node.FullPath
        Node.Path = NewPath
        
        logger = kwargs.get('Logger', logging.getLogger('MovePath'))
        
        logger.info("Moving " + oldFullPath + "\n  to " + Node.FullPath)
        
        if(os.path.exists(Node.FullPath)):
            os.removedirs(Node.FullPath)
            os.rename(oldFullPath, Node.FullPath)
        else:
            shutil.move(oldFullPath, Node.FullPath)
        
    return Node.Parent
