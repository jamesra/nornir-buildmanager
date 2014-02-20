'''
Created on Feb 19, 2014

@author: u0490822
'''

import os
import nornir_imageregistration.core as core


def DimensionsMatch(imageFullPath, area):
    '''Return true if the area matches the area of the image.
    
    '''

    if area is None:
        raise TypeError("RemoveOnDimensionMismatch area parameter should be a (Width,Height) tuple instead of None")

    size = core.GetImageSize(imageFullPath)
    if size is None:
        return False
    else:
        return size[0] == area[1] and size[1] == area[0]

def RemoveOnDimensionMismatch(imageFullPath, area):
    '''Remove the image file if the area does not match.  Return True if removed '''
    if imageFullPath is None:
        return True

    # GetImage returns (Height, Width)
    if DimensionsMatch(imageFullPath, area):
        return False

    if os.path.exists(imageFullPath):
        os.remove(imageFullPath)

    return True

if __name__ == '__main__':
    pass