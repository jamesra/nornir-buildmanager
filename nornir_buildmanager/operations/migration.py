'''
Created on May 15, 2015

@author: u0490822

All the code, often throwaway, to migrate from one version to another.
'''

import os
import glob
import nornir_imageregistration
import nornir_imageregistration.files


def GetTileNumber(filename):
    (tile_number_str, ext) = os.path.splitext(os.path.basename(filename))
    return int(tile_number_str)

def GetTileFormatString(tilefilename):
    basename = os.path.basename(tilefilename)
    (basename, extension) = os.path.splitext(basename)
    
    min_digits = len(basename)
    file_format = '%' + '0%dd%s' % (min_digits, extension)
    return file_format

def GetListOfTileNumbers(tile_filenames):
    tile_number_list = []
    for tile_filename in tile_filenames:
        tile_number = GetTileNumber(tile_filename)
        tile_number_list.append(tile_number)
        
    tile_number_list.sort()
    return tile_number_list

#-------------------------------------
#Begin Fix transform numbering section

def RenumberTransformTilesToStartAtZero(transform_node, **kwargs):
    '''
    
    '''
    (junk, ext) = os.path.splitext(transform_node.FullPath)
    if ext != '.mosaic':
        return
    
    mFile = nornir_imageregistration.files.MosaicFile.Load(transform_node.FullPath)
    sorted_keys = sorted(mFile.ImageToTransformString.keys())
    
    firstTileNumber = GetTileNumber(sorted_keys[0])
    #We only make the first number zero if the the first tile is 1
    if firstTileNumber != 1:
        return
    
    tile_format = GetTileFormatString(sorted_keys[0])
    
    tile_number_list = GetListOfTileNumbers(sorted_keys)
    
    for tile_number in tile_number_list: 
        original_key = tile_format % (tile_number)
        new_tile_name = tile_format % (tile_number - 1)
        
        transform = mFile.ImageToTransformString[original_key]
        del mFile.ImageToTransformString[original_key]
        if new_tile_name in mFile.ImageToTransformString:
            print("Tile %s already exists in mosaic %s" % (new_tile_name, transform_node.FullPath) )
            
        mFile.ImageToTransformString[new_tile_name] = transform
        
    mFile.Save(transform_node.FullPath)
    print("Updated tile numbering for %s" % (transform_node.FullPath))
    
    #return transform_node.Parent
    

#End fix transform numbering section
#-----------------------------------
    
#-------------------------------------------------------
#Begin Fix tile numbering section

def FixFileNumbering(files_list):
    '''
    Ensure filenames start at zero
    '''
    
    sorted_files = sorted(files_list)
    tile_number = GetTileNumber(sorted_files[0])
    if tile_number == 0:
        return
    
    dirname = os.path.dirname(sorted_files[0])
    file_format = GetTileFormatString(sorted_files[0])
    
    
    tile_number_list = GetListOfTileNumbers(sorted_files)
    
    #OK, we need to decrement every tile number by one.
    First = True
    for tile_number in tile_number_list:    
        InputFilename = os.path.join(dirname, file_format % tile_number)
        adjusted_tile_number = tile_number - 1
        
        OutputFilename = os.path.join(dirname, file_format % adjusted_tile_number)
        if First:
            print("%s -> %s" % (InputFilename, OutputFilename))
            First = False
        
        if os.path.exists(OutputFilename):
            print("Cannot rename files %s -> %s" % (InputFilename, OutputFilename))
            return
        else:
            os.rename(InputFilename, OutputFilename)

    
def MoveTilesToStartAtZero(tile_pyramid_node, **kwargs):
    
    for level_node in tile_pyramid_node.Levels:
        tile_list = glob.glob(os.path.join(level_node.FullPath, '*' + tile_pyramid_node.ImageFormatExt))
        
        FixFileNumbering(tile_list)

#End Fix tile numbering section
#-------------------------------------------------------
