'''
Created on Apr 9, 2019

@author: u0490822
'''

import sys
import re
import struct
import enum
import pickle as pickle
import nornir_buildmanager.templates
from nornir_buildmanager.VolumeManagerETree import *
from nornir_buildmanager.operations.tile import VerifyTiles
import nornir_buildmanager.importers
from nornir_imageregistration.files import mosaicfile
from nornir_imageregistration.mosaic import Mosaic
from nornir_imageregistration import image_stats
from nornir_shared.images import *
import nornir_shared.files as files
from nornir_shared.histogram import *
from nornir_shared.mathhelper import ListMedian
from nornir_shared.files import RemoveOutdatedFile
import nornir_shared.plot as plot
import logging
import collections
import nornir_pools
import numpy
import nornir_buildmanager.importers.serialemlog
from pyglet.resource import file

class MRCImport(object):
    '''
    Imports an .MRC file into a volume
    '''
    @classmethod
    def ToMosaic(cls, VolumeObj, idocFileFullPath, ContrastCutoffs,
                 OutputPath=None, Extension=None, OutputImageExt=None, TileOverlap=None, TargetBpp=None, FlipList=None, ContrastMap=None, CameraBpp=None, debug=None):
        pass

    def __init__(self, params):
        '''
        Constructor
        '''
        
    def CreateMosaic(self, mrcfile):
        mosaic = nornir_imageregistration.Mosaic()
        
        pixel_position = [t.pixel_coords ]
        for t in mrcfile.tile_meta:
            
        
        pass 
    
class MRCFile(object):
    '''
    Reads a SerialEM mrc file
    http://bio3d.colorado.edu/imod/doc/mrc_format.txt
    '''
    HeaderLength = 1024
    
    @staticmethod
    def IsBigEndian(Header):
        '''Returns true if the mrc header indicates the file is big endian'''
        (EndianStamp,) = struct.unpack('I', Header[0xD4:0xD8]);
        if (EndianStamp == 17):
            return True
        elif(EndianStamp == 68):
            return False
        else:
            Warning('ENDIAN Value in MRC header incorrect, defaulting to Little Endian')
            return False  
          
    
    @classmethod
    def Load(cls, filename):
        '''Read the header of an MRC file from disk and return an object for access'''
         
        mrc = open(filename, 'rb');
        
        #The mrc file header is always 1024, mostly empty space
        Header = mrc.read(cls.HeaderLength);
        IsBigEndian = cls.IsBigEndian(Header)
        obj = MRCFile(mrc, IsBigEndian)
          
        (obj.img_XDim, obj.img_YDim, obj.num_tiles, obj.img_pixel_mode) = struct.unpack(obj.EndianChar + 'IIII', Header[0x00:0x10])
        
        (grid_XDim,grid_YDim,grid_ZDim) = struct.unpack(obj.EndianChar + 'III', Header[0x1C:0x28])
        obj.grid_dim = numpy.asarray((grid_XDim, grid_YDim, grid_ZDim), dtype=numpy.int32)
        
        (grid_cell_XDim,grid_cell_YDim,grid_cell_ZDim,) = struct.unpack(obj.EndianChar + 'fff', Header[0x28: 0x34])
        obj.grid_cell_dim = numpy.asarray((grid_cell_XDim, grid_cell_YDim, grid_cell_ZDim), dtype=numpy.float32)
        
        obj.pixel_spacing = obj.grid_cell_dim / obj.grid_dim.astype(numpy.float64)
        obj.pixel_spacing = obj.pixel_spacing / 10.0 #convert pixel spacing in nanometers
        
        (obj.mapx,obj.mapy,obj.mapz,) = struct.unpack(obj.EndianChar + 'III', Header[0x40: 0x4C])
        assert(obj.mapx == 1) #If these asserts fail the image is stored in an untested orientation
        assert(obj.mapy == 2)
        assert(obj.mapz == 3)
        
        (obj.min_pixel_value, obj.max_pixel_value, obj.mean_pixel_value,) = struct.unpack(obj.EndianChar + 'fff', Header[0x4C:0x58])
        
        (obj.extended_header_size, ) = struct.unpack(obj.EndianChar + 'I', Header[0x5C: 0x60])
        
        (obj.tile_header_size, obj.tile_header_flags,) = struct.unpack(obj.EndianChar + 'HH', Header[0x80:0x84])
        
        (imod_stamp, ) = struct.unpack(obj.EndianChar + 'I', Header[0x98: 0x9C])
        if(imod_stamp == 1146047817):
            obj.imod_flags = struct.unpack(obj.EndianChar + 'I', Header[0x9C: 0xA0])
        
        (img_origin_x,img_origin_y,img_origin_z,) = struct.unpack(obj.EndianChar + 'fff', Header[0xC4: 0xD0])
        obj.img_origin = numpy.asarray((img_origin_x,img_origin_y,img_origin_z), numpy.float32)
        
        for i in range(obj.num_tiles):
            tile_meta = obj.ReadTileMeta(mrc, i)
            obj.tile_meta.append(tile_meta)
        
        return obj
    
    @property
    def EndianChar(self):
        if self.IsBigEndian:
            return '>'
        else:
            return '<'
        
    @property
    def BytesPerPixel(self):
        if(self.img_pixel_mode == 0):
            return 1
        elif(self.img_pixel_mode == 1):
            return 2
        elif(self.img_pixel_mode == 2):
            return 2
        elif(self.img_pixel_mode == 3):
            return 4
        elif(self.img_pixel_mode == 4):
            return 4
        elif(self.img_pixel_mode == 6):
            return 2
        elif(self.img_pixel_mode == 16):
            return 3
        else:
            raise ValueError("Unknown pixel format")
        
    @property
    def pixel_dtype(self):
        '''
        :return: The numpy dtype pixels are encoded in
        '''
        if(self.img_pixel_mode == 0):
            dtype = numpy.uint8
        elif(self.img_pixel_mode == 1):
            dtype = numpy.int16
        elif(self.img_pixel_mode == 2):
            dtype = numpy.float
        elif(self.img_pixel_mode == 3):
            dtype = numpy.dtype([('real',numpy.uint16), ('i',numpy.uint16)])
        elif(self.img_pixel_mode == 4):
            dtype = numpy.complex
        elif(self.img_pixel_mode == 6):
            dtype = numpy.uint16
        elif(self.img_pixel_mode == 16):
            dtype = numpy.dtype([('R',numpy.uint8), ('G',numpy.uint8), ('B',numpy.uint8)])
        else:
            raise ValueError("Unknown pixel format")
        
        sys_is_be = sys.byteorder == 'big'
        if sys_is_be != self.IsBigEndian:
            #OK, change the byte order of the numpy type
            dtype = dtype.newbyteorder(self.EndianChar)
            
        
        return dtype
             
    def GetTileImage(self, mrc, iTile):
        first_image_offset = MRCFile.HeaderLength + self.extended_header_size
        image_byte_size = self.img_shape.prod() * self.BytesPerPixel
        
        image_offset = first_image_offset + (image_byte_size * iTile)
        mrc.seek(image_offset)
        image_bytes = mrc.read(image_byte_size)
        while len(image_bytes) < image_byte_size:
            image_bytes = image_bytes + mrc.read(image_byte_size - len(image_bytes));
        
        img = numpy.frombuffer(image_bytes, dtype=self.pixel_dtype, count=self.img_shape.prod()).reshape(self.img_shape)
        return img
          
    def ReadTileMeta(self, mrc, iTile):
        mrc.seek(MRCFile.HeaderLength + (iTile * self.tile_header_size))
        TileHeader = mrc.read(self.tile_header_size) 
        while len(TileHeader) < self.tile_header_size:
            TileHeader = TileHeader + mrc.read(self.tile_header_size - len(TileHeader));
        
        return MRCTileHeader.Load(iTile, TileHeader,
                                  tile_flags=self.tile_header_flags,
                                  nm_per_pixel=self.pixel_spacing[0:2],
                                  big_endian=self.IsBigEndian)
         
    @property
    def img_shape(self): 
        return numpy.asarray((self.img_XDim, self.img_YDim), dtype=numpy.int32)
        
    def __init__(self, mrc, isBigEndian=False):
        self.mrc = mrc 
        self.IsBigEndian = isBigEndian #True for big-endian
        
        self.img_XDim = None
        self.img_YDim = None
        self.num_tiles = None #Should be one unless we are seeing an mrc file not used for the RC1 dataset this class was made for
        
        self.img_pixel_mode = None 
        
        self.grid_dim = None
        self.grid_cell_dim = None
        self.pixel_spacing = None #Pixel spacing in nanometers 
        
        self.min_pixel_value = None
        self.max_pixel_value = None
        self.mean_pixel_value = None
        
        self.extended_header_size = None #Length of extended header, needed to find offset to first mrc image
        self.imod_flags = None
        
        self.img_origin = None
        
        self.tile_header_size = None
        self.tile_header_flags = None
        
        self.tile_meta = []
        
class MRCTileHeaderFlags(enum.IntFlag):
    TiltAngle = 1
    PieceCoord = 2
    StageCoord = 4
    Magnification = 8
    Intensity = 16
    Exposure = 32
    
class MRCTileHeader(object):
    
    @staticmethod
    def calculate_pixel_coords(stage_coords, nm_per_pixel):
        
        #stage_coords is in um, so convert to nm and then pixels
        nm_coord = stage_coords.astype(numpy.float64) * 1000.0 #Convert to nm
        pixel_coord = nm_coord / nm_per_pixel #convert to pixels
        return pixel_coord
         
    @staticmethod
    def Load(tile_id, header, tile_flags, nm_per_pixel, big_endian=False):
        '''
        :param tile_id: Arbitrary name for the tile we will load
        :param header: MRC File header
        :param int tile_number: Tile Number to read header for
        '''
        obj = MRCTileHeader(tile_id, nm_per_pixel)
        offset = 0
        
        Endian = '<'
        if big_endian:
            Endian = '>'
        
        if(tile_flags & MRCTileHeaderFlags.TiltAngle):
            (obj.tilt_angle,) = struct.unpack(Endian + 'H', header[offset:offset+2])
            obj.tilt_angle = float(obj.tilt_angle) / 100.0
            offset += 2
            
        if(tile_flags & MRCTileHeaderFlags.PieceCoord):
            (px,py,pz,) = struct.unpack(Endian + 'HHH', header[offset:offset+6])
            obj.piece_coords = numpy.asarray((px,py,pz))
            offset += 6
            
        if(tile_flags & MRCTileHeaderFlags.StageCoord):
            (sx,sy,) = struct.unpack(Endian + 'HH', header[offset:offset+4])
            obj.stage_coords = numpy.asarray((sx,sy), dtype=numpy.float32) / 25.0
            obj.pixel_coords = MRCTileHeader.calculate_pixel_coords(obj.stage_coords, nm_per_pixel)
            offset += 4
            
        if(tile_flags & MRCTileHeaderFlags.Magnification):
            (obj.mag,) = struct.unpack(Endian + 'H', header[offset:offset+2])
            obj.mag = float(obj.mag) * 100.0
            offset += 2
            
        if(tile_flags & MRCTileHeaderFlags.Intensity):
            (obj.intensity,) = struct.unpack(Endian + 'H', header[offset:offset+2])
            obj.intensity = float(obj.intensity) / 25000.0
            offset += 2
            
        if(tile_flags & MRCTileHeaderFlags.Exposure):
            (obj.exposure,) = struct.unpack(Endian + 'f', header[offset:offset+4])
            offset += 4
        
        return obj
          
    def __init__(self, ID, nm_per_pixel):
        self.ID = ID
        self.nm_per_pixel = nm_per_pixel
        self.tilt_angle = None
        self.piece_coords = None
        self.stage_coords = None #In Microns
        self.mag = None
        self.intensity = None
        self.exposure = None
        
        self.pixel_coords = None #Calculated value
        
    def __str__(self, *args, **kwargs):
        output = str(self.ID)
        
        if self.stage_coords is not None:
            output = output + " Stage: {0},{1}".format(self.stage_coords[0], self.stage_coords[1])
        
        return output
        
if __name__ == '__main__':
    #obj = MRCFile.Load('C:/Data/RC1/0022_150X.mrc')
    obj = MRCFile.Load('C:/Data/RC1/0060.mrc')