'''

.. automodule:: nornir_buildmanager.build
.. automodule:: nornir_buildmanager.operations

'''

import os
from exceptions import *
import nornir_buildmanager.VolumeManagerETree as VolumeManager


 
__all__ = ['pipelinemanager', 'ImportManager', 'VolumeManagerETree', 'templates', 'operations']


def GetFlipList(path):
    FlippedSections = list()

    flipFileName = os.path.join(path, 'FlipList.txt')
    if os.path.exists(flipFileName) == False:
        return FlippedSections

    flipFile = open(flipFileName, 'r')
    lines = flipFile.readlines()
    flipFile.close()

    for line in lines:
        sectionNumber = int(line)
        FlippedSections.append(sectionNumber)

    return FlippedSections