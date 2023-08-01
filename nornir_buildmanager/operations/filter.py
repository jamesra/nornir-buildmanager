import math
import os
import shutil
import subprocess

import nornir_buildmanager
import nornir_shared
import nornir_shared.prettyoutput as prettyoutput


def RemoveTilePyramidIfOutdated(input_filter: nornir_buildmanager.volumemanager.FilterNode, output_filter: nornir_buildmanager.volumemanager.FilterNode ):
    """

    :param input_filter:
    :param output_filter:
    :return: True if the input_filter's TilePyramid is newer than the output_filters tile pyramid.  output_filter TilePyramid will be cleaned if true
    """
    output_pyramid = output_filter.TilePyramid

    if input_filter.TilePyramid.CreationTime > output_pyramid.CreationTime:
        output_pyramid.Clean(
            f"TilePyramid at {output_pyramid.FullPath} is older than TilePyramid at {input_filter.TilePyramid.FullPath}")
        return True

    return False


def AssembleTilesetFromImageSet(Parameters, image_set_node: nornir_buildmanager.volumemanager.ImageSetNode,
                                TileShape=None, Logger=None, **kwargs):
    '''Create full resolution tiles of specfied size for the mosaics'''
    prettyoutput.CurseString('Stage', "Assemble Tile Pyramids")

    TileWidth = TileShape[0]
    TileHeight = TileShape[1]

    filter_node = image_set_node.FindParent('Filter')

    input_level_node = image_set_node.MaxResLevel
    if input_level_node is None:
        Logger.warning("No input image level found for AssembleTilesetFromImageSet")
        return

    # Remove our tileset if our image is newer than the tileset
    image_node = image_set_node.GetImage(input_level_node.Downsample)
    if image_node is None:
        Logger.warning("No input image found for AssembleTilesetFromImageSet")
        return

    if filter_node.HasTileset:
        tile_set_node = filter_node.Tileset

    #         if files.IsOutdated(ImageNode.FullPath, TileSetNode.Levels[0].FullPath):
    #             TileSetNode.Clean("Input image was newer than tileset")
    #         else:
    #             return

    if not filter_node.HasTileset:
        tile_set_node = nornir_buildmanager.volumemanager.TilesetNode.Create()
        [added, tile_set_node] = filter_node.UpdateOrAddChildByAttrib(tile_set_node, 'Path')

        tile_set_node.TileXDim = TileWidth
        tile_set_node.TileYDim = TileHeight
        tile_set_node.FilePostfix = '.png'
        tile_set_node.FilePrefix = filter_node.Name + '_'
        tile_set_node.CoordFormat = nornir_buildmanager.templates.Current.GridTileCoordFormat

    os.makedirs(tile_set_node.FullPath, exist_ok=True)

    # OK, check if the first level of the tileset exists
    (added_outputlevel, output_level) = tile_set_node.GetOrCreateLevel(input_level_node.Downsample, GenerateData=False)

    added_outputlevel = True
    if added_outputlevel:
        [YDim, XDim] = nornir_shared.images.GetImageSize(image_node.FullPath)

        tile_output = os.path.join(tile_set_node.FullPath, 'Tile%d.png')

        # Need to call ir-assemble
        cmd_template = 'magick convert %(InputPath)s -background black -crop %(TileSizeX)dx%(TileSizeY)d -depth 8 -quality 106 -type Grayscale -extent %(XDim)dx%(YDim)d %(TilePrefix)s'

        cmd = cmd_template % {'InputPath': image_node.FullPath,
                              'TileSizeX': TileWidth,
                              'TileSizeY': TileHeight,
                              'XDim': TileWidth,
                              'YDim': TileHeight,
                              'TilePrefix': tile_output}

        prettyoutput.CurseString('Cmd', cmd)
        subprocess.call(cmd + ' && exit', shell=True)

        FilePostfix = ''

        GridDimY = int(math.ceil(YDim / float(TileHeight)))
        GridDimX = int(math.ceil(XDim / float(TileWidth)))

        GridTileNameTemplate = nornir_buildmanager.templates.Current.GridTileNameTemplate

        os.makedirs(output_level.FullPath, exist_ok=True)

        iFile = 0
        for iY in range(0, GridDimY):
            for iX in range(0, GridDimX):
                tileFileName = os.path.join(tile_set_node.FullPath, tile_output % iFile)
                gridTileFileName = GridTileNameTemplate % {'prefix': tile_set_node.FilePrefix,
                                                           'X': iX,
                                                           'Y': iY,
                                                           'postfix': tile_set_node.FilePostfix}

                gridTileFileName = os.path.join(output_level.FullPath, gridTileFileName)

                shutil.move(tileFileName, gridTileFileName)

                iFile += 1

        output_level.GridDimX = GridDimX
        output_level.GridDimY = GridDimY

    return filter_node
