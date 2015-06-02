'''
Created on May 26, 2015

@author: u0490822
'''
import os
import glob 
import nornir_shared.files 

num_contrast_pad_chars = 64

def PrintContrastValuesHeader(**kwargs):
    global num_contrast_pad_chars
    print("Path%sMin    \tMax    \tGamma" % (' ' * num_contrast_pad_chars))
    return None

def PrintContrastValues(node, **kwargs):
    '''Print the contrast values used to generated the filter'''
    global num_contrast_pad_chars
    pathstr = node.FullPath
    num_pad_chars = num_contrast_pad_chars - len(pathstr)
    if num_pad_chars > 0:
        pathstr += ' ' * num_pad_chars
        
    minStr = "None"
    maxStr = "None"
    gammaStr = "None"
    
    if not node.MinIntensityCutoff is None:
        minStr = "%4d" % node.MinIntensityCutoff
        
    if not node.MaxIntensityCutoff is None:
        maxStr = "%4d" % node.MaxIntensityCutoff
        
    if not node.Gamma is None:
        gammaStr = "%4g" % node.Gamma 
    
    print("%s\t%s\t%s\t%s" % (pathstr, minStr, maxStr, gammaStr))
    return None

def GetNewestTile(level_node):
    files = sorted([os.path.join(level_node.FullPath, x) for x in os.listdir(level_node.FullPath)], key=os.path.getctime, reverse=True)
    tile_file = None
    for f in files:
        if f.endswith('.png'):
            tile_file = f
            break
        
    return tile_file
    

def PrintImageSetsOlderThanTilePyramids(node, **kwargs):
    '''Print the contrast values used to generated the filter'''
    global num_contrast_pad_chars
    if not node.HasImageset:
        #print("No Imageset for %s" % node.FullPath)
        return None
        
    if not node.HasTilePyramid:
        #print("No TilePyramid for %s" % node.FullPath)
        return None
        
    if not node.Imageset.HasLevels:
        print("No levels for filter's imageset %s" % node.FullPath)
        return None
    
    if not node.TilePyramid.HasLevels:
        print("No levels for filter's tile pyramid %s" % node.FullPath)
        return None
    
    image_level_node = node.Imageset.GetLevel(node.Imageset.MaxResLevel.Downsample)
    if image_level_node is None:
        print("No image level node for level %d at %s" % (node.Imageset.MaxResLevel.Downsample, node.Imageset.FullPath))
        return None
    
    image_node = node.Imageset.GetImage(node.Imageset.MaxResLevel.Downsample)
    if image_node is None:
        print("No image node in existing level %d at %s" % (node.Imageset.MaxResLevel.Downsample, node.Imageset.FullPath))
        return node.Imageset
        
    pyramid_level_node = node.TilePyramid.GetLevel(node.TilePyramid.MaxResLevel.Downsample)
    if pyramid_level_node is None:
        print("No image at level %d for %s" % (node.TilePyramid.MaxResLevel.Downsample, node.TilePyramid.FullPath))
        return node.TilePyramid
    
    first_tile_path = GetNewestTile(pyramid_level_node)
    
    if first_tile_path == nornir_shared.files.NewestFile(image_node.FullPath, first_tile_path):
        print("Imageset is out of date %s" % image_node.FullPath)
        return node
        
    return None