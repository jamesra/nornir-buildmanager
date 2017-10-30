import os
import subprocess
import math
import shutil

from nornir_shared import *
import nornir_imageregistration
import nornir_pools
import nornir_buildmanager
 

def AssembleTilesetFromImageSet(Parameters, ImageSetNode, TileShape=None, Logger=None, **kwargs):
    '''Create full resolution tiles of specfied size for the mosaics'''
    prettyoutput.CurseString('Stage', "Assemble Tile Pyramids")   

    TileWidth = TileShape[0]
    TileHeight = TileShape[1]

    FilterNode = ImageSetNode.FindParent('Filter')

    InputLevelNode = ImageSetNode.MaxResLevel
    if InputLevelNode is None:
        Logger.warning("No input image level found for AssembleTilesetFromImageSet")
        return

    # Remove our tileset if our image is newer than the tileset
    ImageNode = ImageSetNode.GetImage(InputLevelNode.Downsample)
    if ImageNode is None:
        Logger.warning("No input image found for AssembleTilesetFromImageSet")
        return

    if FilterNode.HasTileset:
        TileSetNode = FilterNode.Imageset
 
#         if files.IsOutdated(ImageNode.FullPath, TileSetNode.Levels[0].FullPath):
#             TileSetNode.Clean("Input image was newer than tileset")
#         else:
#             return

    if not FilterNode.HasTileset:
        TileSetNode = nornir_buildmanager.VolumeManager.TilesetNode()
        [added, TileSetNode] = FilterNode.UpdateOrAddChildByAttrib(TileSetNode, 'Path')

        TileSetNode.TileXDim = TileWidth
        TileSetNode.TileYDim = TileHeight
        TileSetNode.FilePostfix = '.png'
        TileSetNode.FilePrefix = FilterNode.Name + '_'
        TileSetNode.CoordFormat = nornir_buildmanager.templates.Current.GridTileCoordFormat

    if not os.path.exists(TileSetNode.FullPath):
        Logger.info("Creating Directory: " + TileSetNode.FullPath)
        os.makedirs(TileSetNode.FullPath)

    # OK, check if the first level of the tileset exists
    (added_outputlevel, OutputLevel) = TileSetNode.GetOrCreateLevel(InputLevelNode.Downsample, GenerateData=False)
    
    added_outputlevel = True
    if(added_outputlevel):
        [XDim, YDim] = images.GetImageSize(ImageNode.FullPath)

        tile_output = os.path.join(TileSetNode.FullPath, 'Tile%d.png')

        # Need to call ir-assemble
        cmd_template = 'magick convert %(InputPath)s -background black -crop %(TileSizeX)dx%(TileSizeY)d -depth 8 -quality 106 -type Grayscale -extent %(XDim)dx%(YDim)d %(TilePrefix)s'

        cmd = cmd_template % {'InputPath' : ImageNode.FullPath,
                              'TileSizeX' : TileWidth,
                              'TileSizeY' : TileHeight,
                              'XDim' : TileWidth,
                              'YDim' : TileHeight,
                              'TilePrefix' : tile_output}

        prettyoutput.CurseString('Cmd', cmd)
        subprocess.call(cmd + ' && exit', shell=True)

        FilePostfix = ''

        GridDimY = int(math.ceil(YDim / float(TileHeight)))
        GridDimX = int(math.ceil(XDim / float(TileWidth)))

        GridTileNameTemplate = nornir_buildmanager.templates.Current.GridTileNameTemplate

        if not os.path.exists(OutputLevel.FullPath):
            os.makedirs(OutputLevel.FullPath)

        iFile = 0
        for iY in range(0, GridDimY):
            for iX in range(0, GridDimX):
                tileFileName = os.path.join(TileSetNode.FullPath, tile_output % (iFile))
                gridTileFileName = GridTileNameTemplate % {'prefix' : TileSetNode.FilePrefix,
                                                           'X' : iX,
                                                           'Y' : iY,
                                                           'postfix' : TileSetNode.FilePostfix}

                gridTileFileName = os.path.join(OutputLevel.FullPath, gridTileFileName)

                shutil.move(tileFileName, gridTileFileName)

                iFile = iFile + 1

        OutputLevel.GridDimX = GridDimX
        OutputLevel.GridDimY = GridDimY


    return FilterNode
