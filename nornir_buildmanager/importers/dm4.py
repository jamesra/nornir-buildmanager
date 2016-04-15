'''
Created on Aug 7, 2015

@author: u0490822
'''

import glob
import os
import collections
import nornir_buildmanager.importers 
import dm4reader
import logging
import PIL
import nornir_imageregistration
import nornir_imageregistration.transforms.factory

import numpy as np

import nornir_buildmanager.templates
import nornir_imageregistration.core 
from nornir_buildmanager.VolumeManagerETree import *
import nornir_shared.plot as plot
import nornir_shared.files as files
import nornir_shared.prettyoutput as prettyoutput
import nornir_pools

from nornir_shared.files import RemoveOutdatedFile

DimensionScale = collections.namedtuple('DimensionScale', ('UnitsPerPixel', 'Units'))

TileExtension = 'png' #Pillow does not support 16-bit png files, so we use the npy extension

def Import(VolumeElement, ImportPath, extension=None, *args, **kwargs):
    '''Import the specified directory into the volume'''
    
    if extension is None:
        extension = 'dm4'
        
    FlipList = nornir_buildmanager.importers.GetFlipList(ImportPath)
    histogramFilename = os.path.join(ImportPath, nornir_buildmanager.importers.DefaultHistogramFilename)
    ContrastMap = nornir_buildmanager.importers.LoadHistogramCutoffs(histogramFilename)
    if len(ContrastMap) == 0:
        nornir_buildmanager.importers.CreateDefaultHistogramCutoffFile(histogramFilename)

    DirList = files.RecurseSubdirectoriesGenerator(ImportPath, RequiredFiles="*." + extension, ExcludeNames=[], ExcludedDownsampleLevels=[])
    for path in DirList:
        for idocFullPath in glob.glob(os.path.join(path, '*.dm4')):
            for obj in DigitalMicrograph4Import.ToMosaic(VolumeElement, idocFullPath, VolumeElement.FullPath, FlipList=FlipList, ContrastMap=ContrastMap):
                yield obj
            
    

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
        return DimensionScale(self.dm4file.read_tag_data(UnitsPerPixelTag), self.dm4file.read_tag_data(ScaleUnitsTag))
    
    def ReadXYUnitsPerPixel(self, tags):
        DM4DimensionTag = self.DimensionScaleTag
        
        XDimensionScale = self._ReadDimensionScaleTag(DM4DimensionTag, 0)
        YDimensionScale = self._ReadDimensionScaleTag(DM4DimensionTag, 1)
        
        return (XDimensionScale, YDimensionScale)
    

    def ReadImageShape(self): 
        DM4ImageDimensionsTag = self.ImageDimensionsTag
        XDim = self.dm4file.read_tag_data(DM4ImageDimensionsTag.unnamed_tags[0])
        YDim = self.dm4file.read_tag_data(DM4ImageDimensionsTag.unnamed_tags[1])
        
        return (YDim, XDim)
    
    def ReadImage(self): 
        image_shape = self.ReadImageShape()     
        np_array = np.array(self.dm4file.read_tag_data(self.ImageDataTag), dtype=np.uint16)
        np_array = np.reshape(np_array, image_shape)
        
        return np_array

    def ReadMontageGridSize(self):
        XDim_tag = self.tags.named_subdirs['ImageList'].unnamed_subdirs[1].named_subdirs['ImageTags'].named_subdirs['Montage'].named_subdirs['Acquisition'].named_tags['Number of X Steps']
        YDim_tag = self.tags.named_subdirs['ImageList'].unnamed_subdirs[1].named_subdirs['ImageTags'].named_subdirs['Montage'].named_subdirs['Acquisition'].named_tags['Number of Y Steps']
    
        XDim = self.dm4file.read_tag_data(XDim_tag)
        YDim = self.dm4file.read_tag_data(YDim_tag)
        
        return (YDim, XDim)
    
    def ReadMontageOverlap(self):
        ''':return: Overlap scalar from 0 to 1.0''' 
        Overlap_tag = self.tags.named_subdirs['ImageList'].unnamed_subdirs[1].named_subdirs['ImageTags'].named_subdirs['Montage'].named_subdirs['Acquisition'].named_tags['Overlap between images (%)']
        return self.dm4file.read_tag_data(Overlap_tag) / 100.0
        
    
    def ReadImageBpp(self):
        return int(self.dm4file.read_tag_data(self.ImageBppTag) * 8)
        

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
    def GetFileNameForTileNumber(cls, tile_number, ext):
        return (nornir_buildmanager.templates.Current.TileCoordFormat % tile_number) + '.' + ext
    
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
    def ToMosaic(cls, VolumeObj, dm4FileFullPath, OutputPath=None, Extension=None, OutputImageExt=None, TileOverlap=None, TargetBpp=None, FlipList=None, ContrastMap=None, debug=None):
        '''
        This function will convert an idoc file in the given path to a .mosaic file.
        It will also rename image files to the requested extension and subdirectory.
        TargetBpp is calculated based on the number of bits required to encode the values
        between the median min and max values
        :param list FlipList: List of section numbers which should have images flipped
        :param dict ContrastMap: Dictionary mapping section number to (Min, Max, Gamma) tuples 
        '''
        
        logger = logging.getLogger(__name__ + '.' + str(cls.__name__) + "ToMosaic")
        
        prettyoutput.CurseString('Stage', "Digital Micrograph to Mosaic " + str(dm4FileFullPath))
        
        (section_number, tile_number) = DigitalMicrograph4Import.GetMetaFromFilename(dm4FileFullPath)
        
        #Open the DM4 file
        dm4data = DM4FileHandler(dm4FileFullPath)
          
        
        InputImageBpp = dm4data.ReadImageBpp()
        
        BlockObj = BlockNode('SEM')
        [saveBlock, BlockObj] = VolumeObj.UpdateOrAddChild(BlockObj)
        if saveBlock:
            yield VolumeObj
        
        [saveSection, SectionObj] = BlockObj.GetOrCreateSection(section_number)
        if saveSection:
            yield BlockObj
            
        [saveChannel, ChannelObj] = SectionObj.GetOrCreateChannel('SEM')
        if saveChannel:
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
        cls.AddTileToMosaic(transformObj, dm4data, tile_number)
        yield ChannelObj
        
        #histogramdatafullpath = cls.CreateImageHistogram(dm4data, dm4FileFullPath)
        #cls.PlotHistogram(histogramdatafullpath, section_number,0,1)
        
        TilePyramidObj = cls.AddImageToTilePyramid(TilePyramidObj, dm4data, tile_number)
        yield TilePyramidObj
        
    @classmethod
    def GetOrCreateStageTransform(cls, channelObj):
        transformObj = channelObj.GetTransform('stage')
        if transformObj is None:
            return channelObj.UpdateOrAddChildByAttrib(TransformNode(Name='Stage',
                                                                             Path='Stage.mosaic',
                                                                             Type='Stage'),
                                                                             'Path')
        else:
            return (False, transformObj)
        
    @classmethod
    def AddImageToTilePyramid(cls, TilePyramidObj, dm4data, tile_number):
        TilePyramidObj.ImageFormatExt = '.' + TileExtension
        TilePyramidObj.NumberOfTiles += 1
        InputImageBpp = dm4data.ReadImageBpp()
        LevelObj = TilePyramidObj.GetOrCreateLevel(1, GenerateData=False)
        tempfilename = cls.GetFileNameForTileNumber(tile_number, ext='tif') #Pillow does not support 16-bit PNG
        temp_output_fullpath = os.path.join(LevelObj.FullPath,tempfilename)
        
        filename = cls.GetFileNameForTileNumber(tile_number, ext=TileExtension) #Pillow does not support 16-bit PNG
        output_fullpath = os.path.join(LevelObj.FullPath,filename)
        
        if not os.path.exists(LevelObj.FullPath):
            os.makedirs(LevelObj.FullPath)
        
        image_data = dm4data.ReadImage()   
          
        im = PIL.Image.fromarray(image_data, 'I;%d' % InputImageBpp)
        im.save(temp_output_fullpath)
        
        cmd = "convert %s %s" % (temp_output_fullpath, output_fullpath)
        
        pools = nornir_pools.GetGlobalLocalMachinePool()
        pools.add_process(output_fullpath, cmd)
        
        pools.wait_completion()
        
        os.remove(temp_output_fullpath)
                 
        return TilePyramidObj
    
        
    @classmethod
    def AddTileToMosaic(cls, transformObj, dm4data, tile_number):
        tile_filename = cls.GetFileNameForTileNumber(tile_number, ext=TileExtension) 
        (YDim, XDim) = dm4data.ReadMontageGridSize()
        
        (ImageHeight, ImageWidth) = dm4data.ReadImageShape()
        
        grid_position = (tile_number % XDim, tile_number // XDim)
        assert(grid_position[1] < YDim) #Make sure the grid position is not off the grid
        
        mosaicObj = None
        if os.path.exists(transformObj.FullPath):
            mosaicObj = nornir_imageregistration.Mosaic.LoadFromMosaicFile(transformObj.FullPath)
        else:
            mosaicObj = nornir_imageregistration.Mosaic()
            
        overlapScalar = dm4data.ReadMontageOverlap()
        
        PerGridXOffset = ImageWidth * (1.0 - overlapScalar)
        PerGridYOffset =  ImageHeight * (1.0 - overlapScalar)
        
        XPosition = grid_position[0] * PerGridXOffset
        YPosition = grid_position[1] * PerGridYOffset
        
        tile_transform = nornir_imageregistration.transforms.factory.CreateRigidTransform((ImageHeight, ImageWidth), (ImageHeight, ImageWidth), 0, (YPosition, XPosition))
        
        mosaicObj.ImageToTransform[tile_filename] = tile_transform
        
        mosaicObj.SaveToMosaicFile(transformObj.FullPath)
#         
#     @classmethod
#     def CreateImageHistogram(cls, dm4data, dm4fullpath):
#         histogramdatafullpath = cls.GetHistogramDataFullPath(dm4fullpath)
#         InputImageBpp = dm4data.ReadImageBpp()
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

        