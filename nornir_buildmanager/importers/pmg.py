import glob
import logging
import os
import shutil
import sys

from filenameparser import ParseFilename, mapping
from nornir_buildmanager import Config
from nornir_buildmanager.VolumeManagerETree import *
from nornir_buildmanager.importers import filenameparser
from nornir_buildmanager.operations.tile import VerifyTiles
from nornir_imageregistration import image_stats
from nornir_imageregistration.files import mosaicfile
from nornir_shared.files import *
from nornir_shared.images import *
import nornir_shared.prettyoutput as prettyoutput


DEBUG = False

'''#PMG Files are expected to have this naming convention:
       # Slide#_Block#_Initials_Mag_Spot_Probe
       # Only the last two, Spot and Probe, are used as section #
       # and channel name respectively.  The others are appended
       # to the directory name'''
pmgMappings = [ mapping('Slide', typefunc=int),
               mapping('Block', typefunc=str),
               mapping('Section', typefunc=int, default=None),
               mapping('Initials', typefunc=str),
               mapping('Mag', typefunc=str),
               mapping('Spot', typefunc=int),
               mapping('Probe', typefunc=str)]

def ParsePMGFilename(PMGPath):

    return filenameparser.ParseFilename(PMGPath, pmgMappings)


class PMGInfo(filenameparser.FilenameInfo):

    def __init__(self, **kwargs):
        self.Slide = None
        self.Block = None
        self.Initials = None
        self.Mag = None
        self.Spot = None
        self.Probe = None
        self.Section = None
        self.NumberOfImages = None

        super(PMGInfo, self).__init__(**kwargs)



#    @classmethod
#    def PrintPMGUsage(cls):
#        Logger = logging.getLogger("PMG Import")
#        Logger.error("PMG files are expected to follow a naming convention:")
#        Logger.error("Slide#_Block#_Initials_Mag_Spot_Probe")
#        Logger.error("If there are multiple slides for a volume the section number may be prepended to the name")
#        Logger.error("Slide#_Block#_Section#_Initials_Mag_Spot_Probe")
#
#    @classmethod
#    def ParsePMGFilename(cls, PMGPath):
#        '''#PMG Files are expected to have this naming convention:
#           # Slide#_Block#_Initials_Mag_Spot_Probe
#           # Only the last two, Spot and Probe, are used as section #
#           # and channel name respectively.  The others are appended
#           # to the directory name'''
#
#        Logger = logging.getLogger("PMG Import")
#
#        PMGBase = os.path.basename(PMGPath)
#
#        # Make sure there are no spaces in the filename
#
#        [fileName, ext] = os.path.splitext(PMGBase)
#
#        if(ext.lower() != '.pmg'):
#            raise "GetPMGInfo called on non PMG file: " + PMGPath
#
#        # TODO, Regular expression
#        mapping = {'Slide' : {'index' : 0, 'type' : int},
#                   'Block' : {'index' : 1, 'type' : str},
#                   'Initials' : {'index' : 2, 'type' : str},
#                   'Mag' : {'index' : 3, 'type' : str},
#                   'Spot' : {'index' : 4, 'type' : int},
#                   'Probe' : {'index' : 5, 'type' : str}}
#
#        parts = fileName.split("_")
#
#        Output = PMGInfo()
#
#        if len(parts) < len(mapping):
#            cls.PrintPMGUsage()
#            raise Exception("Insufficient arguments in PMG filename " + fileName)
#        elif len(parts) > len(mapping) + 1:
#            cls.PrintPMGUsage()
#            raise Exception("Too many underscores in PMG filename " + fileName)
#        elif len(parts) == len(mapping) + 1:
#            # Add the section to the expected values
#
#            for key, data in mapping.items():
#                index = data['index']
#                if index >= 2:
#                    data['index'] = index + 1
#
#            mapping['Section'] = {'index' : 2, 'type' : int}
#
#
#        for key, data in mapping.items():
#            try:
#                value = parts[data['index']]
#                mapfunc = data['type']
#                convValueList = map(mapfunc, [value])
#
#                ConvValue = convValueList[0]
#
#                setattr(Output, key, ConvValue)
#            except:
#                raise Exception("Cannot convert " + key + " from PMG filename " + fileName)
#                cls.PrintPMGUsage()
#
#        return Output

class PMGImport(object):

    @classmethod
    def ToMosaic(cls, VolumeObj, InputPath, OutputPath=None, Extension=None, OutputImageExt=None, TileOverlap=None, TargetBpp=None, debug=None):

        '''#Converts a PMG
    #PMG files are created by Objective Imaging's Surveyor. 
    #This function expects a directory to contain a single PMG file with the tile images in the same directory
    #Returns the SectionNumber and ChannelName of PMG processed.  Otherwise [None,None]'''


        # Default to the directory above ours if an output path is not specified
        if OutputPath is None:
            OutputPath = os.path.join(InputPath, "..")

        # If the user did not supply a value, use a default
        if(TileOverlap is None):
            TileOverlap = 0.10

        if (TargetBpp is None):
            TargetBpp = 8

        # Report the current stage to the user
        # prettyoutput.CurseString('Stage', "PMGToMosaic " + InputPath)

        # Find the files with a .pmg extension
        pmgFiles = glob.glob(os.path.join(InputPath, '*.pmg'))
        if(len(pmgFiles) == 0):
            # This shouldn't happen, but just in case
            assert len(pmgFiles) > 0, "ToMosaic called without proper target file present in the path: " + str(InputPath)
            return

        ParentDir = os.path.dirname(InputPath)
        sectionDir = os.path.basename(InputPath)

        BlockName = 'TEM'
        BlockObj = XContainerElementWrapper('Block', 'TEM')
        [addedBlock, BlockObj] = VolumeObj.UpdateOrAddChild(BlockObj)

        ChannelName = None

        for filename in pmgFiles:
            # TODO wrap in try except and print nice error on badly named files?
            PMG = ParseFilename(filename, pmgMappings)
            PMGDir = os.path.dirname(filename)

            if(PMG is None):
                raise Exception("Could not parse section from PMG filename: " + filename)

            if PMG.Section is None:
                PMG.Section = PMG.Spot



            sectionObj = SectionNode(PMG.Section)

            [addedSection, sectionObj] = BlockObj.UpdateOrAddChildByAttrib(sectionObj, 'Number')

            # Calculate our output directory.  The scripts expect directories to have section numbers, so use that.
            SectionPath = os.path.join(OutputPath, '{:04d}'.format(PMG.Section))

            ChannelName = PMG.Probe
            ChannelName = ChannelName.replace(' ', '_')
            channelObj = XContainerElementWrapper('Channel', ChannelName)
            [channelAdded, channelObj] = sectionObj.UpdateOrAddChildByAttrib(channelObj, 'Name')

            channelObj.Initials = PMG.Initials
            channelObj.Mag = PMG.Mag
            channelObj.Spot = PMG.Spot
            channelObj.Slide = PMG.Slide
            channelObj.Block = PMG.Block

            FlipList = Config.GetFlipList(ParentDir)
            Flip = PMG.Section in FlipList

            if(Flip):
                prettyoutput.Log("Flipping")


            # TODO: Add scale element


           # OutFilename = ChannelName + "_supertile.mosaic"
           # OutFilepath = os.path.join(SectionPath, OutFilename)

            # Preserve the PMG file
#            PMGBasename = os.path.basename(filename)
#            PMGOutputFile = os.path.join(OutputPath, PMGBasename)
#            ir.RemoveOutdatedFile(filename, PMGOutputFile)
#            if not os.path.exists(PMGOutputFile):
#                shutil.copy(filename, PMGOutputFile)
#
#            #See if we need to remove the old supertile mosaic
#            ir.RemoveOutdatedFile(filename, OutFilepath)
#            if(os.path.exists(OutFilepath)):
#                continue
#
            Tiles = ParsePMG(filename)

            if len(Tiles) == 0:
                raise Exception("No tiles found within PMG file")

            NumImages = len(Tiles)

            # Create a filter and mosaic
            FilterName = 'Raw' + str(TargetBpp)
            if(TargetBpp is None):
                FilterName = 'Raw'

            filterObj = XContainerElementWrapper('Filter', FilterName)
            [addedFilter, filterObj] = channelObj.UpdateOrAddChildByAttrib(filterObj, "Name")

            filterObj.BitsPerPixel = TargetBpp

            SupertileName = 'Stage'
            SupertileTransform = SupertileName + '.mosaic'
            SupertilePath = os.path.join(channelObj.FullPath, SupertileTransform)

            [addedTransform, transformObj] = channelObj.UpdateOrAddChildByAttrib(TransformNode(Name=SupertileName,
                                                                         Path=SupertileTransform,
                                                                         Type='Stage'),
                                                                         'Path')

            PyramidName = 'TilePyramid'
            [added, PyramidNodeObj] = filterObj.UpdateOrAddChildByAttrib(TilePyramidNode(Type='stage',
                                                                                         NumberOfTiles=NumImages),
                                                                                         'Path')

            LevelPath = Config.Current.DownsampleFormat % 1

            [added, LevelObj] = PyramidNodeObj.UpdateOrAddChildByAttrib(LevelNode(Level=1), 'Downsample')

            # Make sure the target LevelObj is verified
            VerifyTiles(LevelNode=LevelObj)

            InputImagePath = InputPath
            OutputImagePath = os.path.join(channelObj.FullPath, filterObj.Path, PyramidNodeObj.Path, LevelObj.Path)

            if not os.path.exists(OutputImagePath):
                os.makedirs(OutputImagePath)

            InputTileToOutputTile = {}
            PngTiles = {}
            TileKeys = Tiles.keys()


            imageSize = []
            for inputTile in TileKeys:
                [base, ext] = os.path.splitext(inputTile)
                pngMosaicTile = base + '.png'

                OutputTileFullPath = os.path.join(LevelObj.FullPath, pngMosaicTile)
                InputTileFullPath = os.path.join(PMGDir, inputTile)



                if not os.path.exists(OutputTileFullPath):
                    InputTileToOutputTile[InputTileFullPath] = OutputTileFullPath

                PngTiles[pngMosaicTile] = Tiles[inputTile]
                imageSize.append(nornir_shared.images.GetImageSize(InputTileFullPath))

            ConvertImagesInDict(InputTileToOutputTile, Flip=False, Bpp=TargetBpp)

            if not os.path.exists(transformObj.FullPath):
                mosaicfile.MosaicFile.Write(transformObj.FullPath, PngTiles, Flip=Flip, ImageSize=imageSize)

        return [PMG.Section, ChannelName]

def ParsePMG(filename, TileOverlapPercent=None):

    if TileOverlapPercent is None:
        TileOverlapPercent = 0.1

    # Create a dictionary to store tile position
    Tiles = dict()
    # OutFilepath = os.path.join(SectionPath, OutFilename)

    PMGDir = os.path.dirname(filename)

    if(DEBUG):
        prettyoutput.Log("Filename: " + filename)
        # prettyoutput.Log("PMG to: " + OutFilepath)

    Data = ''
    with open(filename, 'r') as SourceFile:
        Data = SourceFile.read()
        SourceFile.close()

    Data = Data.replace('\r\n', '\n')

    Tuple = Data.partition('VISPIECES')
    Tuple = Tuple[2].partition('\n')
    NumPieces = int(Tuple[0])

    Tuple = Data.partition('VISSCALE')
    Tuple = Tuple[2].partition('\n')
    ScaleFactor = 1 / float(Tuple[0])

    Tuple = Data.partition('IMAGEREDUCTION')
    Tuple = Tuple[2].partition('\n')
    ReductionFactor = 1 / float(Tuple[0])

    Data = Tuple[2]

    if(DEBUG):
        prettyoutput.Log(("Num Tiles: " + str(NumPieces)))
        prettyoutput.Log(("Scale    : " + str(ScaleFactor)))
        prettyoutput.Log(("Reduction: " + str(ReductionFactor)))

    Tuple = Data.partition('PIECE')
    Data = Tuple[2]

    # What type of files did Syncroscan write?  We have to assume BMP and then switch to tif
    # if the BMP's do not exist.
    FileTypeExt = "BMP"

    TileWidth = 0
    TileHeight = 0

    while Data:
        Tuple = Data.partition('ENDPIECE')
        # Remaining unprocessed file goes into data
        Data = Tuple[2]
        Entry = Tuple[0]

        # Find filename
        Tuple = Entry.partition('PATH')

        # Probably means we skipped the last tile in a PMG
        if(Tuple[1] != "PATH"):
            continue

        Tuple = Tuple[2].partition('<')
        Tuple = Tuple[2].partition('>')
        TileFilename = Tuple[0]

        TileFullPath = os.path.join(PMGDir, TileFilename)
        # PMG files for some reason always claim the tile is a .bmp.  So we trust them first and if it doesn't
        # exist we try to find a .tif
        if not  os.path.exists(TileFullPath):
            [base, ext] = os.path.splitext(TileFilename)
            TileFilename = base + '.tif'
            TileFullPath = os.path.join(PMGDir, TileFilename)
            FileTypeExt = 'tif'

        if not os.path.exists(TileFullPath):
            prettyoutput.Log('Skipping missing tile in PMG: ' + TileFilename)
            continue

        if(TileWidth == 0):
            try:
                [TileWidth, TileHeight] = GetImageSize(TileFullPath)
                if(DEBUG):
                    prettyoutput.Log(str(TileWidth) + ',' + str(TileHeight) + " " + TileFilename)
            except:
                prettyoutput.Log('Could not determine size of tile: ' + TileFilename)
                continue

        # prettyoutput.Log("Adding tile: " + TileFilename)
        # Prevent rounding errors later when we divide these numbers
        TileWidth = float(TileWidth)
        TileHeight = float(TileHeight)

        TileWidthMinusOverlap = TileWidth - (TileWidth * TileOverlapPercent)
        TileHeightMinusOverlap = TileHeight - (TileHeight * TileOverlapPercent)

        # Find Position
        Tuple = Entry.partition('CORNER')
        Tuple = Tuple[2].partition('\n')
        Position = Tuple[0].split()

        # prettyoutput.Log( Position
        X = float(Position[0])
        Y = float(Position[1])

        # Convert Position into pixel units using Reduction and Scale factors
        X = X * ScaleFactor * ReductionFactor
        Y = Y * ScaleFactor * ReductionFactor

        # Syncroscan lays out tiles in grids, so find the nearest grid coordinates of this tile
        # This lets us use the last captured tile per grid cell, so users can manually correct
        # focus
        iX = round(X / TileWidthMinusOverlap)
        iY = round(Y / TileHeightMinusOverlap)

        if(DEBUG):
            prettyoutput.Log(("Name,iX,iY: " + TileFilename + " " + str((iX, iY))))

        # Add tile to dictionary
        # Using the indicies will replace any existing tiles in that location.
        # Syncroscan adds the tiles to the file in order of capture, so the older, blurry
        # tiles will be replaced.

        Tiles[TileFilename] = X, Y

        # Prime for next iteration
        Tuple = Data.partition('PIECE')
        Data = Tuple[2]

    return Tiles


# Test code
if __name__ == "__main__":

    info = PMGInfo.ParsePMGFilename("1234_5678_ja_40x_01_yy.pmg")

    info = PMGInfo.ParsePMGFilename("1234_5678_0001_ja_40x_01_yy.pmg")
