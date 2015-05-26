'''
Created on May 26, 2015

@author: u0490822
'''
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