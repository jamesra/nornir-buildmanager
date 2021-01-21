'''
Created on Aug 7, 2015

@author: u0490822
'''

import collections
import glob
import logging
import os
import tempfile

import PIL
import dm4reader
from nornir_buildmanager.VolumeManagerETree import *
import nornir_buildmanager.importers 
import nornir_buildmanager.templates
import nornir_imageregistration
import nornir_imageregistration.core 
import nornir_imageregistration.transforms.factory
import nornir_pools
from nornir_shared.files import RemoveOutdatedFile

import nornir_shared.files as files
import nornir_shared.plot as plot
import nornir_shared.prettyoutput as prettyoutput
import numpy as np
from . import GetFileNameForTileNumber

DimensionScale = collections.namedtuple('DimensionScale', ('UnitsPerPixel', 'Units'))

TileExtension = 'png'  # Pillow does not support 16-bit png files, so we use the npy extension

mosaics_loaded = {} # A cache of mosaics we've already loaded during import
transforms_changed = {} # A cache of transforms that need updated checksums


def Import(VolumeElement, ImportPath, extension=None, *args, **kwargs):
    '''Import the specified directory into the volume'''
    
    if extension is None:
        extension = 'dm4'
        
    if not os.path.exists(ImportPath):
        raise Exception("Import directory not found: " + ImportPath)
        return
    
    tile_overlap = kwargs.get('tile_overlap', None)
    if tile_overlap is not None:
        #Convert the percentage parameter to a 0-1.0 float, and reverse the X,Y ordering to match the rest of Nornir
        tile_overlap = np.asarray((tile_overlap[1], tile_overlap[0]), np.float32) / 100.0
        
    FlipList = nornir_buildmanager.importers.GetFlipList(ImportPath)
    histogramFilename = os.path.join(ImportPath, nornir_buildmanager.importers.DefaultHistogramFilename)
    ContrastMap = nornir_buildmanager.importers.LoadHistogramCutoffs(histogramFilename)
    if len(ContrastMap) == 0:
        nornir_buildmanager.importers.CreateDefaultHistogramCutoffFile(histogramFilename)

    DirList = files.RecurseSubdirectoriesGenerator(ImportPath, RequiredFiles="*." + extension, ExcludeNames=[], ExcludedDownsampleLevels=[])
    for path in DirList:
        prettyoutput.CurseString("DM4Import", "Importing *.dm4 from {0}".format(path))
        for idocFullPath in glob.glob(os.path.join(path, '*.dm4')):
            yield from DigitalMicrograph4Import.ToMosaic(VolumeElement, idocFullPath, VolumeElement.FullPath, FlipList=FlipList, ContrastMap=ContrastMap, tile_overlap=tile_overlap)
    
    nornir_pools.WaitOnAllPools()
    
    for transform_fullpath in mosaics_loaded:
        mosaicObj = mosaics_loaded[transform_fullpath]
        mosaicObj.SaveToMosaicFile(transform_fullpath)
        
    for transform_fullpath in transforms_changed:
        transformObj = transforms_changed[transform_fullpath]
        transformObj.ResetChecksum()
        yield transformObj.Parent

    
'''Convert a DM4 file to another image format.  Intended to be called from a multithreading pool'''


def ConvertDM4ToPng(dm4FileFullPath, output_fullpath):
    # (section_number, tile_number) = DigitalMicrograph4Import.GetMetaFromFilename(dm4FileFullPath)
    dm4data = DM4FileHandler(dm4FileFullPath)
                   
    #tempdir = tempfile.mkdtemp(prefix="DM4")
     
    #tempfilename = os.path.basename(output_fullpath + '.tif')  # cls.GetFileNameForTileNumber(tile_number, ext='tif') #Pillow does not support 16-bit PNG.  We save to TIF and convert
    #temp_output_fullpath = os.path.join(tempdir, tempfilename)
    
    #image_data = dm4data.ReadImage()
    #InputImageBpp = dm4data.image_bpp
    #im = PIL.Image.fromarray(image_data, 'I;%d' % InputImageBpp)
    #im = im.convert(mode='I')
    #im.save(output_fullpath)
    
    im = dm4data.ReadImageAsPIL()
    im.save(output_fullpath)
    
    #cmd = "magick convert %s %s" % (temp_output_fullpath, output_fullpath)
    
    #pools = nornir_pools.GetGlobalLocalMachinePool()
    #pools.add_process(output_fullpath, cmd)
    #pools.wait_completion()
    
    #os.remove(temp_output_fullpath)
    #os.removedirs(tempdir)
    

class DM4FileHandler():
    '''Handler for the DM4 file format used by the scope at the Neitz lab'''
    
    @property 
    def dm4file(self):
        return self._dm4file
    
    @property 
    def tags(self):
        return self._tags
    
    @property
    def DimensionScaleTag(self):
        return self.tags.named_subdirs['ImageList'].unnamed_subdirs[1].named_subdirs['ImageData'].named_subdirs['Calibrations'].named_subdirs['Dimension']
    
    @property
    def ImageDimensionsTag(self):
        return self.tags.named_subdirs['ImageList'].unnamed_subdirs[1].named_subdirs['ImageData'].named_subdirs['Dimensions']
    
    @property
    def ImageDataTag(self):
        return self.tags.named_subdirs['ImageList'].unnamed_subdirs[1].named_subdirs['ImageData'].named_tags['Data']
    
    @property
    def ImageBppTag(self):
        return self.tags.named_subdirs['ImageList'].unnamed_subdirs[1].named_subdirs['ImageData'].named_tags['PixelDepth']
    
    def __init__(self, dm4fullpath):
        self._dm4file = dm4reader.DM4File.open(dm4fullpath)
        self._tags = self._dm4file.read_directory()
        
    def _ReadDimensionScaleTag(self, DM4DimensionTag, index):
        '''Read the scale for a particular index'''
        UnitsPerPixelTag = DM4DimensionTag.unnamed_subdirs[index].named_tags['Scale']
        ScaleUnitsTag = DM4DimensionTag.unnamed_subdirs[index].named_tags['Units']
        ScaleUnitsRawArray = self.dm4file.read_tag_data(ScaleUnitsTag)
        ScaleUnits = "".join(map(chr, ScaleUnitsRawArray)) #Convert byte array into a string containing unit of measure as a string
                                 
        return DimensionScale(self.dm4file.read_tag_data(UnitsPerPixelTag), ScaleUnits)
    
    def ReadXYUnitsPerPixel(self):
        DM4DimensionTag = self.DimensionScaleTag
        
        XDimensionScale = self._ReadDimensionScaleTag(DM4DimensionTag, 0)
        YDimensionScale = self._ReadDimensionScaleTag(DM4DimensionTag, 1)
        
        return (XDimensionScale, YDimensionScale)

    def ReadImageShape(self):
        ''':return: Image shape as array, [YDim,XDim] as uint64''' 
        DM4ImageDimensionsTag = self.ImageDimensionsTag
        XDim = self.dm4file.read_tag_data(DM4ImageDimensionsTag.unnamed_tags[0])
        YDim = self.dm4file.read_tag_data(DM4ImageDimensionsTag.unnamed_tags[1])
        
        return np.asarray((YDim, XDim), dtype=np.uint64)
    
    def ReadImageAsNumpy(self):
        image_shape = self.ReadImageShape()     
        np_array = np.array(self.dm4file.read_tag_data(self.ImageDataTag), dtype=self.image_dtype)
        np_array = np.reshape(np_array, image_shape)
        
        return np_array
    
    def ReadImageAsPIL(self):
        image_shape = self.ReadImageShape()     
        im = PIL.Image.frombytes(data=self.dm4file.read_tag_data(self.ImageDataTag).tobytes(), mode='I;%d' % self.image_bpp, size=(image_shape[1], image_shape[0]))
        im = im.convert(mode='I')
    
        return im

    def ReadMontageGridSize(self):
        ''':return: Image grid dimensions as array, [YDim,XDim] as uint64''' 
        XDim_tag = self.tags.named_subdirs['ImageList'].unnamed_subdirs[1].named_subdirs['ImageTags'].named_subdirs['Montage'].named_subdirs['Acquisition'].named_tags['Number of X Steps']
        YDim_tag = self.tags.named_subdirs['ImageList'].unnamed_subdirs[1].named_subdirs['ImageTags'].named_subdirs['Montage'].named_subdirs['Acquisition'].named_tags['Number of Y Steps']
    
        XDim = self.dm4file.read_tag_data(XDim_tag)
        YDim = self.dm4file.read_tag_data(YDim_tag)
        
        return np.asarray((YDim, XDim), dtype=np.uint64)
    
    def ReadMontageOverlap(self):
        ''':return: Overlap scalar array, [Y,X] from 0 to 1.0''' 
        Overlap_tag = self.tags.named_subdirs['ImageList'].unnamed_subdirs[1].named_subdirs['ImageTags'].named_subdirs['Montage'].named_subdirs['Acquisition'].named_tags['Overlap between images (%)']
        overlap = self.dm4file.read_tag_data(Overlap_tag) / 100.0
        return np.asarray((overlap, overlap), dtype=np.float32)  
    
    @property
    def image_bpp(self):
        return int(self.dm4file.read_tag_data(self.ImageBppTag) * 8)
    
    @property
    def image_dtype(self):
        bpp = self.image_bpp
        if 8 >= bpp:
            return np.uint8
        elif 16 >= bpp:
            return np.uint16
        elif 32 >= bpp: 
            return np.uint32
        elif 64 >= bpp:
            return np.uint64
        else:  
            raise ValueError("Unexpectedly large bits-per-pixel value")
        

class DigitalMicrograph4Import(object):
    '''
    classdocs
    '''
    
    @classmethod
    def GetMetaFromFilename(cls, fileName):
        fileName = os.path.basename(fileName)

        # Make sure extension is present in the filename
        [fileName, ext] = os.path.splitext(fileName)

        section_number = None
        tile_number = None
        parts = fileName.split("_")
        try:
            section_number = int(parts[-1])
        except:
            # We really can't recover from this, so maybe an exception should be thrown instead
            section_number = None
            
        try:
            tile_number = int(parts[-3])
        except:
            tile_number = None 

        return (section_number, tile_number)
    
    def __init__(self, params):
        '''
        Constructor
        '''
        
    def Dm4DataTag(self, tags):
        data_tag = tags.named_subdirs['ImageList'].unnamed_subdirs[1].named_subdirs['ImageData'].named_tags['Data'] 
        return data_tag
    
    @classmethod
    def GetHistogramDataFullPath(cls, dm4fullpath):
        dirname = os.path.dirname(dm4fullpath)
        filename = os.path.basename(dm4fullpath)
        (root, ext) = os.path.splitext(filename)
        return os.path.join(dirname, root + '_histogram.xml')
    
    @classmethod
    def GetHistogramImageFullPath(cls, dm4fullpath):
        dirname = os.path.dirname(dm4fullpath)
        filename = os.path.basename(dm4fullpath)
        (root, ext) = os.path.splitext(filename)
        return os.path.join(dirname, root + '_histogram.png')
            
    @classmethod
    def ToMosaic(cls, VolumeObj, dm4FileFullPath, OutputPath=None, Extension=None, OutputImageExt=None, tile_overlap=None, TargetBpp=None, FlipList=None, ContrastMap=None, debug=None):
        '''
        This function will convert an idoc file in the given path to a .mosaic file.
        It will also rename image files to the requested extension and subdirectory.
        TargetBpp is calculated based on the number of bits required to encode the values
        between the median min and max values
        :param tuple tile_overlap: Tuple of percentages of overlap in (X,Y) for each tile, or None to read from DM4 file
        :param list FlipList: List of section numbers which should have images flipped
        :param dict ContrastMap: Dictionary mapping section number to (Min, Max, Gamma) tuples 
        '''
         
        logger = logging.getLogger(__name__ + '.' + str(cls.__name__) + "ToMosaic")
     #   prettyoutput.CurseString('Stage', "Digital Micrograph to Mosaic " + str(dm4FileFullPath))
        
        (section_number, tile_number) = DigitalMicrograph4Import.GetMetaFromFilename(dm4FileFullPath)
        
        # Open the DM4 file
        dm4data = DM4FileHandler(dm4FileFullPath)
        InputImageBpp = dm4data.image_bpp
        
        if tile_overlap is not None:
            tile_overlap = np.asarray(tile_overlap, dtype=np.float32)
        else:
            tile_overlap = dm4data.ReadMontageOverlap()
        
        BlockObj = BlockNode.Create('SEM')
        [saveBlock, BlockObj] = VolumeObj.UpdateOrAddChild(BlockObj)
        if saveBlock:
            yield VolumeObj
        
        [saveSection, SectionObj] = BlockObj.GetOrCreateSection(section_number)
        if saveSection:
            yield BlockObj
            
        [saveChannel, ChannelObj] = SectionObj.GetOrCreateChannel('SEM')
        if saveChannel:
            yield SectionObj
            
        #Temporary fix for legacy DM4 imports without the scale embedded in the Nornir meta-data
        if ChannelObj.Scale is None:
            (XDim,YDim) = dm4data.ReadXYUnitsPerPixel()
            scalar = 1
            if XDim.Units == 'µm':
                scalar = 1000.0
            elif XDim.Units == 'um':
                scalar = 1000.0
            
            ChannelObj.SetScale(XDim.UnitsPerPixel * scalar)
            yield SectionObj
            
        FilterName = 'Raw' + str(InputImageBpp)
        if(InputImageBpp is None):
            FilterName = 'Raw'
            
        [saveFilter, FilterObj] = ChannelObj.GetOrCreateFilter(FilterName)
        if saveFilter:
            yield ChannelObj
            
        [savePyramid, TilePyramidObj] = FilterObj.GetOrCreateTilePyramid() 
        if savePyramid:
            yield FilterObj
            
        [saveTransformObj, transformObj] = cls.GetOrCreateStageTransform(ChannelObj)
        if saveTransformObj:            
            yield ChannelObj
            
        cls.AddTileToMosaic(transformObj, dm4data, tile_number, tile_overlap)
            
        # histogramdatafullpath = cls.CreateImageHistogram(dm4data, dm4FileFullPath)
        # cls.PlotHistogram(histogramdatafullpath, section_number,0,1)
        
        TilePyramidObj = cls.AddAndImportImageToTilePyramid(TilePyramidObj, dm4FileFullPath, tile_number)
        if TilePyramidObj is not None:
            yield TilePyramidObj
        
    @classmethod
    def GetOrCreateStageTransform(cls, channelObj):
        transformObj = channelObj.GetTransform('stage')
        if transformObj is None:
            return channelObj.UpdateOrAddChildByAttrib(TransformNode.Create(Name='Stage',
                                                                             Path='Stage.mosaic',
                                                                             Type='Stage'),
                                                                             'Path')
        else:
            return (False, transformObj)
        
    @classmethod
    def AddImageToTilePyramidMetaData(cls, TilePyramidObj):
        TilePyramidObj.ImageFormatExt = '.' + TileExtension
        TilePyramidObj.NumberOfTiles += 1
        LevelObj = TilePyramidObj.GetOrCreateLevel(1, GenerateData=False)
        
        os.makedirs(LevelObj.FullPath, exist_ok=True)
                 
        return TilePyramidObj
        
    @classmethod
    def ImportImageToTilePyramid(cls, TilePyramidObj, dm4FileFullPath, tile_number):
        '''Adds a DM4 file to the tile pyramid directory without updating the meta-data'''
        LevelObj = TilePyramidObj.GetOrCreateLevel(1, GenerateData=False)
        filename = GetFileNameForTileNumber(tile_number, ext=TileExtension)  # Pillow does not support 16-bit PNG
        output_fullpath = os.path.join(LevelObj.FullPath, filename)
                
        os.makedirs(LevelObj.FullPath, exist_ok=True)
            
        if os.path.exists(output_fullpath):
            return
            
        pools = nornir_pools.GetGlobalLocalMachinePool()
        pools.add_task(os.path.basename(dm4FileFullPath) + " -> " + os.path.basename(output_fullpath), ConvertDM4ToPng, dm4FileFullPath, output_fullpath)
        
    @classmethod
    def AddAndImportImageToTilePyramid(cls, TilePyramidObj, dm4FileFullPath, tile_number):
        [created, LevelObj] = TilePyramidObj.GetOrCreateLevel(1, GenerateData=False)
        filename = GetFileNameForTileNumber(tile_number, ext=TileExtension)  # Pillow does not support 16-bit PNG
        output_fullpath = os.path.join(LevelObj.FullPath, filename)
        
        os.makedirs(LevelObj.FullPath, exist_ok=True)
        
        if nornir_shared.images.IsValidImage(output_fullpath):
            return None
        
        TilePyramidObj.NumberOfTiles += 1
        TilePyramidObj.ImageFormatExt = '.' + TileExtension
                    
        pools = nornir_pools.GetGlobalLocalMachinePool()
        pools.add_task(os.path.basename(dm4FileFullPath) + " -> " + os.path.basename(output_fullpath), ConvertDM4ToPng, dm4FileFullPath, output_fullpath)
        return TilePyramidObj
    
    @classmethod
    def AddTileToMosaic(cls, transformObj, dm4data, tile_number, tile_overlap=None):
        tile_filename = GetFileNameForTileNumber(tile_number, ext=TileExtension) 
        (YDim, XDim) = dm4data.ReadMontageGridSize()
        
        image_shape = dm4data.ReadImageShape()
        
        grid_position = (tile_number // XDim, tile_number % XDim) #Position as (Y,X)
        assert(grid_position[1] < YDim), 'Grid position is off the grid'
        
        mosaicObj = None
        if transformObj.FullPath in mosaics_loaded:
            mosaicObj = mosaics_loaded[transformObj.FullPath]
        elif os.path.exists(transformObj.FullPath):
            mosaicObj = nornir_imageregistration.Mosaic.LoadFromMosaicFile(transformObj.FullPath)
            mosaics_loaded[transformObj.FullPath] = mosaicObj
        else:
            mosaicObj = nornir_imageregistration.Mosaic()
            mosaics_loaded[transformObj.FullPath] = mosaicObj
            
        if tile_overlap is None:
            tile_overlap = dm4data.ReadMontageOverlap()
        
        PerGridOffset = image_shape * (1.0 - tile_overlap)
        
        Position = grid_position * PerGridOffset
         
        tile_transform = nornir_imageregistration.transforms.RigidNoRotation(Position)
        
        if tile_filename in mosaicObj.ImageToTransform:
            transform_changed = mosaicObj.ImageToTransform[tile_filename] != tile_transform
        else:  
            transform_changed = True
        
        if transform_changed:
            mosaicObj.ImageToTransform[tile_filename] = tile_transform
            if not transformObj.FullPath in transforms_changed:
                transforms_changed[transformObj.FullPath] = transformObj
            #mosaicObj.SaveToMosaicFile(transformObj.FullPath)
            
        return transform_changed
#         
#     @classmethod
#     def CreateImageHistogram(cls, dm4data, dm4fullpath):
#         histogramdatafullpath = cls.GetHistogramDataFullPath(dm4fullpath)
#         InputImageBpp = dm4data.image_bpp()
#         histogramObj = nornir_imageregistration.image_stats.__HistogramFileSciPy__(Bpp=InputImageBpp, Scale=.125, numBins=2048)
#         if not histogramdatafullpath is None:
#             histogramObj.Save(histogramdatafullpath)       
# 
#         
#         
#     def PlotHistogram(cls, histogramFullPath, sectionNumber, minCutoff, maxCutoff):    
#         HistogramImageFullPath = os.path.join(os.path.dirname(histogramFullPath), cls.GetHistogramDataFullPath(histogramFullPath))
#         ImageRemoved = RemoveOutdatedFile(histogramFullPath, HistogramImageFullPath)
#         if ImageRemoved or not os.path.exists(HistogramImageFullPath):
#             #pool = nornir_pools.GetGlobalMultithreadingPool()
#             #pool.add_task(HistogramImageFullPath, plot.Histogram, histogramFullPath, HistogramImageFullPath, Title="Section %d Raw Data Pixel Intensity" % (sectionNumber), LinePosList=[minCutoff, maxCutoff])
#             plot.Histogram(histogramFullPath, HistogramImageFullPath, Title="Section %d Raw Data Pixel Intensity" % (sectionNumber), LinePosList=[minCutoff, maxCutoff])
        
